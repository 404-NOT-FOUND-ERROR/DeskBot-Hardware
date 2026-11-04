#!/usr/bin/env python3
"""Read an explicitly selected USB console; emit only bounded state snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.body_console_observation import Observation


def ports(list_ports):
    result = []
    for port in list_ports.comports():
        if (port.vid, port.pid) != (0x303A, 0x1001) or not port.serial_number:
            continue
        key = hashlib.sha256(f"{port.vid}:{port.pid}:{port.serial_number}".encode()).hexdigest()
        result.append({"port": port.device, "usb_id": "303A:1001", "key": key})
    return sorted(result, key=lambda row: row["port"])


def open_console(serial_module, port):
    device = serial_module.Serial(port=None, baudrate=115200, timeout=0.1, rtscts=False, dsrdtr=False)
    device.dtr = False
    device.rts = False
    device.port = port
    device.open()
    return device


def watch(serial_module, list_ports, port, key, emit, *, stop=lambda: False):
    observation = Observation()
    device, next_try, last_emit, last_check = None, 0.0, -1.0, -1.0
    try:
        while not stop():
            now = time.monotonic()
            if now - last_check >= 1:
                last_check = now
                selected = next((row for row in ports(list_ports) if row["port"] == port), None)
                if selected is None or selected["key"] != key:
                    if device is not None:
                        device.close()
                        device = None
                    observation.link("missing" if selected is None else "mismatch", port,
                                     "usb_not_present" if selected is None else "usb_identity_changed")
                    next_try = now + 1
            if device is None and now >= next_try:
                try:
                    device = open_console(serial_module, port)
                    observation.link("connected", port, "passive_console")
                except serial_module.SerialException:
                    observation.link("busy", port, "port_unavailable")
                    next_try = now + 2
            if device is not None:
                try:
                    raw = device.read(4096)
                    observation.feed(raw)
                except (serial_module.SerialException, OSError):
                    device.close()
                    device = None
                    observation.link("missing", port, "usb_read_interrupted")
                    next_try = now + 1
            else:
                time.sleep(0.1)
            if now - last_emit >= 1:
                emit(observation.snapshot())
                last_emit = now
    finally:
        if device is not None:
            device.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--port")
    parser.add_argument("--key")
    args = parser.parse_args()
    import serial
    from serial.tools import list_ports
    emit = lambda value: print(json.dumps(value, ensure_ascii=True, separators=(",", ":")), flush=True)
    if args.list:
        emit({"schema": "deskbot.console_ports/v1", "ports": ports(list_ports)})
        return
    if not args.port or not re.fullmatch(r"COM[1-9][0-9]{0,3}", args.port) or not args.key or not re.fullmatch(r"[a-f0-9]{64}", args.key):
        parser.error("an enumerated --port and --key are required")
    watch(serial, list_ports, args.port, args.key, emit)


if __name__ == "__main__":
    main()
