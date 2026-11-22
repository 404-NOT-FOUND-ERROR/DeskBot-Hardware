#!/usr/bin/env python3
"""Read COM4/COM8 logs and mark stationary magnetic-state windows."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import queue
import re
import sys
import threading
from pathlib import Path
from typing import Callable

try:
    import serial
except ImportError:  # pragma: no cover - depends on ESP-IDF Python environment
    serial = None


ROOT = Path(__file__).resolve().parents[1]
STATES = ("baseline", "slider_up", "slider_down", "removed", "reinstalled")
MINIMUM_STATE_SECONDS = 10.0
MAGNETIC_SAMPLE_RE = re.compile(r"\bcmd=06\b", re.IGNORECASE)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def format_timestamp(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("marker timestamp must include a timezone")
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StateMarkerTracker:
    def __init__(self, writer: csv.DictWriter, flush: Callable[[], None] | None = None,
                 minimum_seconds: float = MINIMUM_STATE_SECONDS):
        self.writer = writer
        self.flush = flush or (lambda: None)
        self.minimum_seconds = minimum_seconds
        self.active_state: str | None = None
        self.started_at: dt.datetime | None = None
        self.completed: list[str] = []

    def start(self, state: str, when: dt.datetime) -> None:
        if state not in STATES:
            raise ValueError(f"state must be one of: {', '.join(STATES)}")
        if self.active_state is not None:
            raise ValueError(f"end {self.active_state} before starting another state")
        if when.tzinfo is None:
            raise ValueError("marker timestamp must include a timezone")
        self.writer.writerow({"timestamp_utc": format_timestamp(when), "state": state, "boundary": "start"})
        self.flush()
        self.active_state = state
        self.started_at = when

    def end(self, when: dt.datetime) -> float:
        if self.active_state is None or self.started_at is None:
            raise ValueError("there is no active state; start a state first")
        if when.tzinfo is None:
            raise ValueError("marker timestamp must include a timezone")
        duration = (when - self.started_at).total_seconds()
        if duration < self.minimum_seconds:
            raise ValueError(f"keep the state still for at least {self.minimum_seconds:g} seconds")
        state = self.active_state
        self.writer.writerow({"timestamp_utc": format_timestamp(when), "state": state, "boundary": "end"})
        self.flush()
        self.completed.append(state)
        self.active_state = None
        self.started_at = None
        return duration


def open_read_only_serial(port: str, baud: int):
    if serial is None:
        raise RuntimeError("pyserial is required; use the configured ESP-IDF Python environment")
    device = serial.Serial(port=None, baudrate=baud, timeout=0.2, rtscts=False, dsrdtr=False)
    device.dtr = False
    device.rts = False
    device.port = port
    device.open()
    return device


def read_port(device, path: Path, stop: threading.Event, counts: dict[str, int], key: str,
              errors: queue.Queue[str]) -> None:
    try:
        with path.open("w", encoding="utf-8", newline="") as output:
            while not stop.is_set():
                data = device.readline()
                if not data:
                    continue
                line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                output.write(f"{format_timestamp(utc_now())}\t{line}\n")
                output.flush()
                counts[key] += 1
    except (OSError, ValueError) as exc:
        errors.put(f"{key}: {exc}")
        stop.set()


def count_magnetic_sample_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as capture_file:
        return sum(1 for line in capture_file if MAGNETIC_SAMPLE_RE.search(line))


def capture_status(completed_states: list[str], unfinished_state: str | None,
                   body_lines: int, magnetic_sample_lines: int,
                   read_errors: list[str]) -> tuple[str, str | None]:
    if read_errors:
        return "failed", "serial read errors were recorded"
    if unfinished_state:
        return "incomplete", f"end the unfinished {unfinished_state} state before stopping"
    if not completed_states:
        return "incomplete", "no complete state window was marked"
    if body_lines <= 0:
        return "incomplete", "no body UART data was captured"
    if magnetic_sample_lines <= 0:
        return "incomplete", "no body magnetic samples (cmd=06) were captured"
    return "complete", None


def operator_loop(tracker: StateMarkerTracker) -> None:
    print("Commands: start <state>, end, stop")
    print("States: " + ", ".join(STATES))
    print("Set the magnet position and let the base settle before start; keep it still until end.")
    print(f"Hold each marked state for at least {MINIMUM_STATE_SECONDS:g} seconds.")
    while True:
        command = input("state> ").strip().split()
        try:
            if len(command) == 2 and command[0] == "start":
                tracker.start(command[1], utc_now())
                print(f"Started {command[1]} at {format_timestamp(tracker.started_at)}")
            elif command == ["end"]:
                duration = tracker.end(utc_now())
                print(f"Saved state window ({duration:.1f}s)")
            elif command == ["stop"]:
                if tracker.active_state:
                    print(f"End {tracker.active_state} before stopping.")
                    continue
                return
            else:
                print("Use: start <state>, end, or stop")
        except ValueError as exc:
            print(exc)


def default_output() -> Path:
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return ROOT / "tmp" / "magnetic-state-capture" / stamp


def capture(args: argparse.Namespace) -> int:
    if args.body_port.upper() == args.base_port.upper():
        raise ValueError("body and base ports must be different")
    output = args.output or default_output()
    output = output if output.is_absolute() else Path.cwd() / output
    output.mkdir(parents=True, exist_ok=False)

    devices = []
    try:
        devices.append(open_read_only_serial(args.body_port, args.baud))
        devices.append(open_read_only_serial(args.base_port, args.baud))
    except Exception:
        for device in devices:
            device.close()
        raise

    stop = threading.Event()
    counts = {"body": 0, "base": 0}
    errors: queue.Queue[str] = queue.Queue()
    started_at = format_timestamp(utc_now())
    threads = [
        threading.Thread(target=read_port, args=(devices[0], output / "body.log", stop, counts, "body", errors), daemon=True),
        threading.Thread(target=read_port, args=(devices[1], output / "base.log", stop, counts, "base", errors), daemon=True),
    ]
    markers_path = output / "markers.csv"
    tracker = None
    interrupted = False
    print(f"Read-only capture on body {args.body_port} and base {args.base_port} at {args.baud} baud.")
    print(f"Output: {output.resolve()}")
    print("No serial data is sent; DTR/RTS are held low.")
    try:
        for thread in threads:
            thread.start()
        with markers_path.open("w", encoding="utf-8", newline="") as marker_file:
            writer = csv.DictWriter(marker_file, fieldnames=["timestamp_utc", "state", "boundary"])
            writer.writeheader()
            marker_file.flush()
            tracker = StateMarkerTracker(writer, flush=marker_file.flush)
            try:
                operator_loop(tracker)
            except (KeyboardInterrupt, EOFError):
                interrupted = True
                print("Capture interrupted.")
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2.0)
        for device in devices:
            device.close()

    errors_found = []
    while not errors.empty():
        errors_found.append(errors.get())
    magnetic_sample_lines = count_magnetic_sample_lines(output / "body.log")
    status, status_message = capture_status(
        tracker.completed if tracker else [],
        tracker.active_state if tracker else None,
        counts["body"],
        magnetic_sample_lines,
        errors_found,
    )
    summary = {
        "schema": "deskbot.magnetic.state_capture.v1",
        "started_at_utc": started_at,
        "ended_at_utc": format_timestamp(utc_now()),
        "ports": {"body": args.body_port, "base": args.base_port},
        "baud": args.baud,
        "read_only": True,
        "manual_state_markers": True,
        "minimum_state_seconds": MINIMUM_STATE_SECONDS,
        "line_counts": counts,
        "magnetic_sample_lines": magnetic_sample_lines,
        "completed_states": tracker.completed if tracker else [],
        "unfinished_state": tracker.active_state if tracker else None,
        "capture_status": status,
        "interrupted": interrupted,
        "read_errors": errors_found,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if status_message:
        label = "Capture failed" if status == "failed" else "Capture incomplete"
        print(f"{label}: {status_message}.", file=sys.stderr)
    return {"complete": 0, "failed": 1, "incomplete": 2}[status]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body-port", default="COM4", help="ESP32-S3 body serial port")
    parser.add_argument("--base-port", default="COM8", help="C61 base serial port")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output", type=Path, help="new output directory; default is under tmp/magnetic-state-capture")
    args = parser.parse_args(argv)
    if args.baud <= 0:
        parser.error("--baud must be greater than zero")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return capture(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
