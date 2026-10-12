"""Capture an ESP32-S3 body boot log through USB-Serial/JTAG.

This diagnostic only toggles the normal USB reset control and reads the
console. It never sends application UART frames or writes flash.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import time

import serial


def stamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--no-reset", action="store_true",
                        help="Listen to the live console without toggling reset lines.")
    parser.add_argument("--usb-reset", action="store_true",
                        help="Use esptool's Windows-compatible USB-Serial/JTAG hard reset.")
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be greater than zero")
    if args.no_reset and args.usb_reset:
        parser.error("--no-reset and --usb-reset are mutually exclusive")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = 0
    started = time.monotonic()
    device = serial.Serial(
        port=None,
        baudrate=115200,
        timeout=0.05,
        rtscts=False,
        dsrdtr=False,
    )
    # Set idle control-line states before opening, so --no-reset also avoids
    # pyserial's default asserted DTR/RTS state at initial port open.
    device.dtr = False
    device.rts = False
    device.port = args.port
    with device, output.open("w", encoding="utf-8", newline="") as log:
        if not args.no_reset:
            if args.usb_reset:
                from esptool.reset import HardReset
                HardReset(device, uses_usb=True)()
            else:
                device.rts = True
                time.sleep(0.20)
                device.rts = False
                time.sleep(0.05)
        deadline = started + args.duration
        while time.monotonic() < deadline:
            data = device.readline()
            if not data:
                continue
            line = data.decode("utf-8", errors="replace").rstrip("\r\n")
            log.write(f"{stamp()}\t{line}\n")
            log.flush()
            print(line, flush=True)
            lines += 1

    print(f"Capture complete: {output.resolve()} (lines={lines})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
