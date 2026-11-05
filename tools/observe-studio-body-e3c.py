#!/usr/bin/env python3
"""One console owner: passive logs, explicit session queries and neutral requests."""
import argparse
import importlib.util
import json
from pathlib import Path
from queue import Queue, Empty, Full
import re
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.body_console_observation import ANSI
from protocol.studio_identity_observation import IdentityObservation
from protocol.studio_control_link import ControlLink, validate_command
from protocol.studio_usb_session import _unique

spec = importlib.util.spec_from_file_location("studio_e3c_console", ROOT / "tools/observe-studio-body-e3a.py")
console = importlib.util.module_from_spec(spec)
spec.loader.exec_module(console)
REPLY = re.compile(rb"^I \([0-9]{1,15}\) DeskBotSession: (\{.*\})\r?$")
WRITE_TIMEOUT_SECONDS = 5


class Observation(IdentityObservation):
    def __init__(self, control, **kwargs):
        self.control = control
        super().__init__(**kwargs)

    def _line(self, raw):
        match = REPLY.fullmatch(ANSI.sub(b"", raw))
        if match:
            try:
                value = json.loads(match[1].decode("ascii"), object_pairs_hook=_unique)
                self.control.accept(value, int(self.clock()*1000))
            except (ValueError, UnicodeError, RecursionError):
                self.control.rejected += 1
            return
        super()._line(raw)


def read_commands(stream, commands):
    while True:
        line = stream.readline(513)
        if not line:
            return
        if len(line) > 512 or not line.endswith("\n"):
            # End the IPC stream on an overlong command, rather than grow it.
            return
        try:
            row = validate_command(json.loads(line, object_pairs_hook=_unique))
            commands.put_nowait(row)
        except (ValueError, TypeError, RecursionError, Full):
            continue


def write_frame(device, wire):
    # Bounded one-frame write. A partial frame is never automatically replayed.
    if not wire or len(wire) > 2048 or device.write(wire) != len(wire):
        raise OSError("usb_write_incomplete")


def watch(serial_module, list_ports, port, key, emit, commands, *, stop=lambda: False, clock=time.monotonic):
    control = ControlLink()
    observation = Observation(control, clock=clock)
    device = None
    next_try = last_emit = last_check = 0
    try:
        while not stop():
            now = clock()
            if now-last_check >= 1 or last_check == 0:
                last_check = now
                selected = next((r for r in console.ports(list_ports) if r["port"] == port), None)
                if selected is None or selected["key"] != key:
                    if device is not None:
                        device.close()
                        device = None
                        control.disconnect(int(now*1000))
                    observation.link("missing" if selected is None else "mismatch", port,
                                     "usb_not_present" if selected is None else "usb_identity_changed")
                    next_try = now+1
            if device is None and now >= next_try:
                try:
                    device = console.open_console(serial_module, port)
                    device.write_timeout = WRITE_TIMEOUT_SECONDS
                    observation.link("connected", port, "single_console_owner")
                except (serial_module.SerialException, OSError):
                    observation.link("busy", port, "port_unavailable")
                    next_try = now+2
            if device is not None:
                try:
                    observation.feed(device.read(4096))
                    current = observation.snapshot()
                    hello = current["identity"]["hello"] if current["identity"]["status"] == "confirmed" else None
                    control.observe(hello, int(clock()*1000))
                    try:
                        control.command(commands.get_nowait(), int(clock()*1000))
                    except Empty:
                        pass
                    wire = control.tick(int(clock()*1000))
                    if wire:
                        write_frame(device, wire)
                except (serial_module.SerialException, OSError):
                    device.close()
                    device = None
                    control.disconnect(int(clock()*1000))
                    observation.link("missing", port, "usb_io_interrupted")
                    next_try = clock()+1
            else:
                control.observe(None, int(clock()*1000))
                time.sleep(0.05)
            if clock()-last_emit >= 0.25:
                emit(dict(schema="deskbot.studio_transport/v1", observation=observation.snapshot(),
                          control=control.snapshot(int(clock()*1000))))
                last_emit = clock()
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
    emit = lambda v: print(json.dumps(v, ensure_ascii=True, separators=(",", ":")), flush=True)
    if args.list:
        emit(dict(schema="deskbot.console_ports/v1", ports=console.ports(list_ports)))
        return
    if not args.port or not re.fullmatch(r"COM[1-9][0-9]{0,3}", args.port) or not args.key or not re.fullmatch(r"[a-f0-9]{64}", args.key):
        parser.error("an enumerated --port and --key are required")
    commands = Queue(maxsize=1)
    threading.Thread(target=read_commands, args=(sys.stdin, commands), daemon=True).start()
    watch(serial, list_ports, args.port, args.key, emit, commands)


if __name__ == "__main__":
    main()
