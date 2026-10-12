#!/usr/bin/env python3
"""Capture ESP-VoCat base diagnostic logs without sending serial data."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import time
from pathlib import Path

try:
    import serial
    from serial import SerialException
except ImportError as exc:  # pragma: no cover - environment-dependent message
    raise SystemExit(
        "pyserial is required. Run this script from the ESP-IDF Python environment."
    ) from exc


SAMPLE_RE = re.compile(
    r"\[diag\] sample x=(?P<x>-?\d+) y=(?P<y>-?\d+) z=(?P<z>-?\d+)"
)
HALL_SAMPLE_RE = re.compile(
    r"\[diag\] sample sensor=(?P<sensor>SS49E) gpio=(?P<gpio>\d+) "
    r"raw=(?P<raw_value>-?\d+) voltage_mV=(?P<voltage_mV>-?\d+)"
)
PROBE_RE = re.compile(
    r"\[diag\] probe sensor=(?P<sensor>\S+) i2c_addr=(?P<address>\S+) "
    r".*?match=(?P<match>\S+)"
)
CALIBRATION_CENTER_RE = re.compile(
    r"\[diag\] calibration_centers removed=(?P<removed>-?\d+) "
    r"up=(?P<up>-?\d+) down=(?P<down>-?\d+)"
)
UART_RE = re.compile(
    r"\[diag\] uart_tx name=(?P<name>\S+) len=(?P<length>\d+) "
    r"hex=(?P<hex>[0-9A-Fa-f]+)(?P<truncated>\.\.\.)?"
)


def timestamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def make_output_dir(path: str | None) -> Path:
    if path:
        output = Path(path)
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output = Path("experiments") / "base-diagnostics" / stamp
    output.mkdir(parents=True, exist_ok=True)
    return output


def capture(port: str, baud: int, output: Path, duration: float | None) -> int:
    raw_path = output / "session.log"
    samples_path = output / "samples.csv"
    events_path = output / "events.csv"
    started = time.monotonic()

    with serial.Serial(port=port, baudrate=baud, timeout=0.5) as device, \
            raw_path.open("w", encoding="utf-8", newline="") as raw_file, \
            samples_path.open("w", encoding="utf-8", newline="") as samples_file, \
            events_path.open("w", encoding="utf-8", newline="") as events_file:
        samples_writer = csv.DictWriter(
            samples_file,
            fieldnames=[
                "timestamp",
                "sensor",
                "gpio",
                "x",
                "y",
                "z",
                "raw_value",
                "voltage_mV",
                "raw",
            ],
        )
        samples_writer.writeheader()
        events_writer = csv.DictWriter(
            events_file,
            fieldnames=["timestamp", "kind", "name", "length", "hex", "value"],
        )
        events_writer.writeheader()

        print(f"Capturing {port} at {baud} baud")
        print(f"Output: {output.resolve()}")
        print("Press Ctrl+C to stop. This collector never writes to the device.")

        try:
            while duration is None or time.monotonic() - started < duration:
                data = device.readline()
                if not data:
                    continue
                line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                when = timestamp()
                raw_file.write(f"{when}\t{line}\n")
                raw_file.flush()

                sample = SAMPLE_RE.search(line)
                if sample:
                    samples_writer.writerow(
                        {
                            "timestamp": when,
                            "sensor": "magnetometer",
                            "gpio": "",
                            "x": sample.group("x"),
                            "y": sample.group("y"),
                            "z": sample.group("z"),
                            "raw_value": "",
                            "voltage_mV": "",
                            "raw": line,
                        }
                    )
                    samples_file.flush()
                else:
                    hall_sample = HALL_SAMPLE_RE.search(line)
                    if hall_sample:
                        samples_writer.writerow(
                            {
                                "timestamp": when,
                                "sensor": hall_sample.group("sensor"),
                                "gpio": hall_sample.group("gpio"),
                                "x": "",
                                "y": "",
                                "z": "",
                                "raw_value": hall_sample.group("raw_value"),
                                "voltage_mV": hall_sample.group("voltage_mV"),
                                "raw": line,
                            }
                        )
                        samples_file.flush()

                event_kind = None
                event_value = line
                if PROBE_RE.search(line):
                    event_kind = "probe"
                elif CALIBRATION_CENTER_RE.search(line):
                    event_kind = "calibration_centers"
                uart = UART_RE.search(line)
                if uart:
                    event_kind = "uart_tx"
                elif "[diag]" in line:
                    event_kind = "diagnostic"

                if event_kind:
                    events_writer.writerow(
                        {
                            "timestamp": when,
                            "kind": event_kind,
                            "name": uart.group("name") if uart else "",
                            "length": uart.group("length") if uart else "",
                            "hex": uart.group("hex").upper() if uart else "",
                            "value": event_value,
                        }
                    )
                    events_file.flush()

                print(f"{when} {line}", flush=True)
        except KeyboardInterrupt:
            print("\nCapture stopped by user.")

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture ESP-VoCat base diagnostics without sending serial data."
    )
    parser.add_argument("--port", required=True, help="Base board serial port, e.g. COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--output",
        help="Output directory (default: experiments/base-diagnostics/<timestamp>)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Stop after this many seconds; omit to capture until Ctrl+C",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.duration is not None and args.duration <= 0:
        raise SystemExit("--duration must be greater than zero")

    output = make_output_dir(args.output)
    try:
        return capture(args.port, args.baud, output, args.duration)
    except SerialException as exc:
        raise SystemExit(f"Could not open or read {args.port}: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
