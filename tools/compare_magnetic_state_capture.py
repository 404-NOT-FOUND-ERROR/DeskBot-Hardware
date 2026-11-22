#!/usr/bin/env python3
"""Compare manually labelled magnetic capture windows without device I/O."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import sys

from protocol.magnetic_telemetry import TelemetryError, callback_payload_from_line, decode_payload


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = ROOT / "tmp" / "magnetic-state-capture"
MINIMUM_SAMPLES = 8
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_WINDOWS = 100
MAX_SAMPLES = 100_000
VALID_STATES = {"baseline", "slider_up", "slider_down", "removed", "reinstalled"}


def parse_timestamp(value: str) -> dt.datetime:
    try:
        timestamp = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    if timestamp.tzinfo is None:
        raise ValueError("capture timestamps must include a timezone")
    return timestamp.astimezone(dt.timezone.utc)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _load_window_log(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise ValueError(f"missing state log: {path}")
    if path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError(f"state log exceeds {MAX_LOG_BYTES} byte limit: {path}")
    samples: list[dict[str, object]] = []
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
            try:
                callback = callback_payload_from_line(payload)
            except TelemetryError as exc:
                raise ValueError(f"{path.name} line {number}: {exc}") from exc
            if callback is None:
                continue
            sample = decode_payload(
                callback,
                host_received_at=when,
                host_time_source="capture_log_prefix",
            )
            if sample.xyz_available:
                samples.append({"timestamp": when, "kind": sample.kind_name, "x": sample.x, "y": sample.y, "z": sample.z})
    return samples


def _center(samples: list[dict[str, object]]) -> tuple[float, float, float]:
    return tuple(statistics.fmean(float(sample[axis]) for sample in samples) for axis in ("x", "y", "z"))


def _axis_summary(samples: list[dict[str, object]]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for axis in ("x", "y", "z"):
        values = [float(sample[axis]) for sample in samples]
        result[axis] = {
            "mean": statistics.fmean(values),
            "population_stddev": statistics.pstdev(values),
            "min": min(values),
            "max": max(values),
            "range": max(values) - min(values),
        }
    return result


def _window_summary(window: dict[str, object], capture_dir: Path, minimum_samples: int) -> tuple[dict[str, object], list[dict[str, object]]]:
    relative = window.get("body_log")
    if not isinstance(relative, str):
        raise ValueError("split manifest window is missing body_log")
    path = (capture_dir / "labeled-states" / relative.replace("\\", "/")).resolve()
    if not _inside(path, capture_dir / "labeled-states"):
        raise ValueError("split manifest body_log escapes the labeled-states directory")
    samples = _load_window_log(path)
    if not samples:
        raise ValueError(f"window has no XYZ magnetic samples: {relative}")
    kinds = sorted({str(sample["kind"]) for sample in samples})
    if len(kinds) != 1:
        raise ValueError(f"window contains multiple XYZ sensor kinds: {relative}")
    axes = _axis_summary(samples)
    scatter_rms = math.sqrt(sum(float(axes[axis]["population_stddev"]) ** 2 for axis in ("x", "y", "z")))
    first = samples[0]["timestamp"]
    last = samples[-1]["timestamp"]
    assert isinstance(first, dt.datetime) and isinstance(last, dt.datetime)
    return ({
        "state": window.get("state"),
        "window": path.parent.name,
        "sensor": kinds[0],
        "sample_count": len(samples),
        "ready_for_comparison": len(samples) >= minimum_samples,
        "first_sample_utc": first.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "last_sample_utc": last.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "center_xyz": dict(zip(("x", "y", "z"), _center(samples))),
        "axes": axes,
        "within_window_scatter_rms": scatter_rms,
    }, samples)


def compare_capture(capture_dir: Path, minimum_samples: int = MINIMUM_SAMPLES) -> dict[str, object]:
    capture_dir = capture_dir.resolve()
    if not _inside(capture_dir, CAPTURE_ROOT):
        raise ValueError(f"capture must be under {CAPTURE_ROOT}")
    manifest_path = capture_dir / "labeled-states" / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"missing split manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != "deskbot.magnetic.state_split.v1":
        raise ValueError("input is not a magnetic state split manifest")
    if manifest.get("source_capture") != str(capture_dir):
        raise ValueError("split manifest source_capture does not match the capture directory")
    windows = manifest.get("windows")
    if not isinstance(windows, list) or not windows:
        raise ValueError("split manifest has no windows")
    if len(windows) > MAX_WINDOWS:
        raise ValueError(f"split manifest exceeds {MAX_WINDOWS} window limit")

    window_summaries: list[dict[str, object]] = []
    samples_by_state: dict[str, list[dict[str, object]]] = {}
    centers_by_state: dict[str, list[tuple[float, float, float]]] = {}
    all_samples: list[dict[str, object]] = []
    for window in windows:
        if not isinstance(window, dict):
            raise ValueError("split manifest contains a non-object window")
        if window.get("state") not in VALID_STATES:
            raise ValueError(f"split manifest contains an unknown state: {window.get('state')!r}")
        summary, samples = _window_summary(window, capture_dir, minimum_samples)
        label = str(summary["state"])
        if len(all_samples) + len(samples) > MAX_SAMPLES:
            raise ValueError(f"capture exceeds {MAX_SAMPLES} magnetic sample limit")
        window_summaries.append(summary)
        all_samples.extend(samples)
        samples_by_state.setdefault(label, []).extend(samples)
        centers_by_state.setdefault(label, []).append(_center(samples))

    sensor_kinds = sorted({str(sample["kind"]) for sample in all_samples})
    if len(sensor_kinds) != 1:
        raise ValueError("comparison requires every state window to use the same XYZ sensor kind")

    states: dict[str, dict[str, object]] = {}
    for label, samples in samples_by_state.items():
        axes = _axis_summary(samples)
        centers = centers_by_state[label]
        mean_center = tuple(statistics.fmean(center[index] for center in centers) for index in range(3))
        between_window = math.sqrt(sum(statistics.pvariance(center[index] for center in centers) for index in range(3))) if len(centers) > 1 else 0.0
        states[label] = {
            "window_count": len(centers),
            "sample_count": len(samples),
            "all_windows_ready": all(item["ready_for_comparison"] for item in window_summaries if item["state"] == label),
            "sensor_kinds": sorted({str(sample["kind"]) for sample in samples}),
            "mean_window_center_xyz": dict(zip(("x", "y", "z"), mean_center)),
            "all_samples_axes": axes,
            "within_window_scatter_rms_mean": statistics.fmean(float(item["within_window_scatter_rms"]) for item in window_summaries if item["state"] == label),
            "between_window_center_spread_rms": between_window,
        }

    pairwise_states: list[dict[str, object]] = []
    labels = sorted(states)
    for left_index, left in enumerate(labels):
        left_center = tuple(float(states[left]["mean_window_center_xyz"][axis]) for axis in ("x", "y", "z"))
        for right in labels[left_index + 1:]:
            right_center = tuple(float(states[right]["mean_window_center_xyz"][axis]) for axis in ("x", "y", "z"))
            distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(left_center, right_center)))
            pairwise_states.append({"left_state": left, "right_state": right, "center_distance": distance})

    pairwise_windows: list[dict[str, object]] = []
    for left_index, left in enumerate(window_summaries):
        left_center = tuple(float(left["center_xyz"][axis]) for axis in ("x", "y", "z"))
        for right in window_summaries[left_index + 1:]:
            right_center = tuple(float(right["center_xyz"][axis]) for axis in ("x", "y", "z"))
            distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(left_center, right_center)))
            pairwise_windows.append({
                "left_window": left["window"],
                "left_state": left["state"],
                "right_window": right["window"],
                "right_state": right["state"],
                "center_distance": distance,
            })

    return {
        "schema": "deskbot.magnetic.state_comparison.v1",
        "source_capture": str(capture_dir),
        "read_only": True,
        "automatic_identity_decision": False,
        "units": "transport_counts",
        "minimum_samples_per_window": minimum_samples,
        "limits": {"max_windows": MAX_WINDOWS, "max_samples": MAX_SAMPLES, "max_log_bytes_per_window": MAX_LOG_BYTES},
        "all_windows_ready": all(bool(item["ready_for_comparison"]) for item in window_summaries),
        "sensor_kind": sensor_kinds[0],
        "states": states,
        "windows": window_summaries,
        "pairwise_state_center_distances": pairwise_states,
        "pairwise_window_center_distances": pairwise_windows,
        "note": "Distances summarize labelled recordings only. They are not calibrated confidence, identity decisions, or evidence that an identity binding should be persisted.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path, help="capture directory written by capture_magnetic_states.py")
    parser.add_argument("--minimum-samples", type=int, default=MINIMUM_SAMPLES)
    args = parser.parse_args(argv)
    if args.minimum_samples < 1:
        parser.error("--minimum-samples must be positive")
    try:
        report = compare_capture(args.capture, args.minimum_samples)
        output = Path(report["source_capture"]) / "labeled-states" / "comparison.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_windows_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
