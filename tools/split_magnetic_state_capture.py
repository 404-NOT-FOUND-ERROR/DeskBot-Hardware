#!/usr/bin/env python3
"""Split a marked dual-port capture into stationary state log windows."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path


STATES = {"baseline", "slider_up", "slider_down", "removed", "reinstalled"}
MAGNETIC_SAMPLE_RE = re.compile(r"\bcmd=06\b", re.IGNORECASE)
MAX_LOG_BYTES = 64 * 1024 * 1024


def parse_timestamp(value: str) -> dt.datetime:
    try:
        timestamp = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    if timestamp.tzinfo is None:
        raise ValueError("capture and marker timestamps must include a timezone")
    return timestamp.astimezone(dt.timezone.utc)


def load_intervals(capture_dir: Path, minimum_seconds: float) -> list[dict[str, object]]:
    markers_path = capture_dir / "markers.csv"
    if not markers_path.is_file():
        raise ValueError(f"missing state markers: {markers_path}")
    intervals: list[dict[str, object]] = []
    active: dict[str, object] | None = None
    previous_time: dt.datetime | None = None
    with markers_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp_utc", "state", "boundary"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError("markers.csv must contain timestamp_utc, state, and boundary columns")
        for number, row in enumerate(reader, 2):
            when = parse_timestamp(row["timestamp_utc"] or "")
            state = row["state"] or ""
            boundary = row["boundary"] or ""
            if state not in STATES:
                raise ValueError(f"marker row {number}: unknown state {state!r}")
            if boundary not in {"start", "end"}:
                raise ValueError(f"marker row {number}: boundary must be start or end")
            if previous_time is not None and when <= previous_time:
                raise ValueError(f"marker row {number}: timestamps must increase")
            previous_time = when
            if boundary == "start":
                if active is not None:
                    raise ValueError(f"marker row {number}: end {active['state']} before starting another state")
                active = {"state": state, "start": when}
                continue
            if active is None or active["state"] != state:
                raise ValueError(f"marker row {number}: end does not match an active state")
            start = active["start"]
            duration = (when - start).total_seconds()
            if duration < minimum_seconds:
                raise ValueError(f"marker row {number}: {state} lasted {duration:g}s; minimum is {minimum_seconds:g}s")
            intervals.append({"state": state, "start": start, "end": when, "duration_seconds": duration})
            active = None
    if active is not None:
        raise ValueError(f"capture ends before {active['state']} has an end marker")
    if not intervals:
        raise ValueError("no complete state windows were found")
    return intervals


def read_log(path: Path) -> list[tuple[dt.datetime, str]]:
    if not path.is_file():
        raise ValueError(f"missing capture log: {path}")
    if path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError(f"capture log exceeds {MAX_LOG_BYTES} byte limit: {path}")
    rows: list[tuple[dt.datetime, str]] = []
    previous: dt.datetime | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            timestamp_text, separator, payload = line.rstrip("\r\n").partition("\t")
            if not separator:
                raise ValueError(f"{path.name} line {number}: missing timestamp separator")
            when = parse_timestamp(timestamp_text)
            if previous is not None and when < previous:
                raise ValueError(f"{path.name} line {number}: timestamps moved backwards")
            previous = when
            rows.append((when, payload))
    return rows


def split_capture(capture_dir: Path, minimum_samples: int = 8) -> dict[str, object]:
    capture_dir = capture_dir.resolve()
    summary_path = capture_dir / "summary.json"
    if not summary_path.is_file():
        raise ValueError(f"missing capture summary: {summary_path}")
    capture_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(capture_summary, dict):
        raise ValueError("capture summary must be a JSON object")
    if capture_summary.get("schema") != "deskbot.magnetic.state_capture.v1" or capture_summary.get("read_only") is not True:
        raise ValueError("input is not a read-only magnetic state capture")
    if (capture_summary.get("capture_status") != "complete"
            or capture_summary.get("read_errors")
            or capture_summary.get("unfinished_state") is not None):
        raise ValueError("capture is not complete; retain the raw logs and collect marked states again")
    minimum_seconds = capture_summary.get("minimum_state_seconds")
    if not isinstance(minimum_seconds, (int, float)) or minimum_seconds <= 0:
        raise ValueError("capture summary has no valid minimum_state_seconds")
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    intervals = load_intervals(capture_dir, float(minimum_seconds))
    body_rows = read_log(capture_dir / "body.log")
    base_rows = read_log(capture_dir / "base.log")

    target = capture_dir / "labeled-states"
    target.mkdir(exist_ok=False)
    state_counts: dict[str, int] = {}
    output_windows: list[dict[str, object]] = []
    for index, interval in enumerate(intervals, 1):
        state = str(interval["state"])
        state_counts[state] = state_counts.get(state, 0) + 1
        folder = target / f"{index:02d}-{state}-{state_counts[state]:02d}"
        folder.mkdir()
        start = interval["start"]
        end = interval["end"]
        selected_body = [(when, line) for when, line in body_rows if start <= when <= end]
        selected_base = [(when, line) for when, line in base_rows if start <= when <= end]
        for name, rows in (("body.log", selected_body), ("base.log", selected_base)):
            with (folder / name).open("w", encoding="utf-8", newline="") as output:
                for when, line in rows:
                    stamp = when.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    output.write(f"{stamp}\t{line}\n")
        sample_count = sum(1 for _, line in selected_body if MAGNETIC_SAMPLE_RE.search(line))
        output_windows.append({
            "state": state,
            "start_utc": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "end_utc": end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "duration_seconds": interval["duration_seconds"],
            "body_lines": len(selected_body),
            "base_lines": len(selected_base),
            "magnetic_sample_lines": sample_count,
            "m2_window_ready": sample_count >= minimum_samples,
            "body_log": str((folder / "body.log").relative_to(target)),
            "base_log": str((folder / "base.log").relative_to(target)),
        })

    manifest = {
        "schema": "deskbot.magnetic.state_split.v1",
        "source_capture": str(capture_dir),
        "labels_are_operator_reports": True,
        "minimum_state_seconds": float(minimum_seconds),
        "minimum_samples_for_m2_window": minimum_samples,
        "windows": output_windows,
        "all_windows_have_enough_samples": all(row["m2_window_ready"] for row in output_windows),
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path, help="capture directory written by capture_magnetic_states.py")
    parser.add_argument("--minimum-samples", type=int, default=8)
    args = parser.parse_args(argv)
    if args.minimum_samples < 1:
        parser.error("--minimum-samples must be positive")
    try:
        manifest = split_capture(args.capture, args.minimum_samples)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["all_windows_have_enough_samples"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
