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
from protocol import studio_timing as timing
from protocol.magnetic_stream import MagneticStream

spec = importlib.util.spec_from_file_location("studio_e3c_console", ROOT / "tools/observe-studio-body-e3a.py")
console = importlib.util.module_from_spec(spec)
spec.loader.exec_module(console)
REPLY = re.compile(rb"^I \([0-9]{1,15}\) DeskBotSession: (\{.*\})\r?$")
WRITE_TIMEOUT_SECONDS = timing.USB_WRITE_TIMEOUT_SECONDS


class Observation(IdentityObservation):
    def __init__(self, control, *, trace=lambda row: None, **kwargs):
        self.control = control
        self.trace = trace
        self.magnetic = MagneticStream()
        super().__init__(**kwargs)

    def _line(self, raw):
        match = REPLY.fullmatch(ANSI.sub(b"", raw))
        if match:
            try:
                value = json.loads(match[1].decode("ascii"), object_pairs_hook=_unique)
                now = int(self.clock()*1000)
                self.control.accept(value, now)
                row = value if isinstance(value, dict) else {}
                self.trace(dict(event="reply", at_ms=now, schema=row.get("schema"),
                    request_id=row.get("request_id"), sequence=row.get("sequence"),
                    rejected_frames=self.control.rejected))
            except (ValueError, UnicodeError, RecursionError):
                self.control.rejected += 1
            return
        super()._line(raw)
        # A single read can contain hello and telemetry. Apply the hello before tagging this line.
        current = self.snapshot()
        hello = current['identity']['hello'] if current['identity']['status'] == 'confirmed' else None
        now = int(self.clock()*1000)
        self.control.observe(hello, now)
        self.magnetic.line(raw, self.control.snapshot(now), now)


def read_commands(stream, commands, *, clock=time.monotonic):
    while True:
        line = stream.readline(513)
        if not line:
            return
        if len(line) > 512 or not line.endswith("\n"):
            # End the IPC stream on an overlong command, rather than grow it.
            return
        try:
            row = validate_command(json.loads(line, object_pairs_hook=_unique))
            # Capture arrival in this reader thread, even while USB write blocks.
            commands.put_nowait((row, int(clock()*1000)))
        except (ValueError, TypeError, RecursionError, Full):
            continue


def write_frame(device, wire):
    # Bounded one-frame write. A partial frame is never automatically replayed.
    if not wire or len(wire) > 2048 or device.write(wire) != len(wire):
        raise OSError("usb_write_incomplete")


def watch(serial_module, list_ports, port, key, emit, commands, *, stop=lambda: False,
          clock=time.monotonic, trace=lambda row: None):
    control = ControlLink()
    observation = Observation(control, clock=clock, trace=trace)
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
                        command, queued_at = commands.get_nowait()
                        control.command(command, int(clock()*1000), queued_at=queued_at)
                    except Empty:
                        pass
                    wire = control.tick(int(clock()*1000))
                    if wire:
                        trace(dict(event="write_start", at_ms=int(clock()*1000), bytes=len(wire),
                                   request_id=json.loads(wire)["request_id"]))
                        write_frame(device, wire)
                        trace(dict(event="write_return", at_ms=int(clock()*1000), bytes=len(wire)))
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
                generated = int(clock()*1000)
                emit(dict(schema="deskbot.studio_transport/v1", generated_at_ms=generated,
                          observation=observation.snapshot(), control=control.snapshot(generated),
                          magnetic=observation.magnetic.snapshot(control.snapshot(generated))))
                last_emit = clock()
    finally:
        if device is not None:
            device.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--port")
    parser.add_argument("--key")
    parser.add_argument("--trace", type=Path)
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
    trace_file = None
    if args.trace:
        path = args.trace.resolve()
        if not path.is_relative_to((ROOT / "tmp").resolve()):
            parser.error("trace must be under the repository tmp directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        trace_file = path.open("a", encoding="utf-8")
    def trace(row):
        if trace_file:
            trace_file.write(json.dumps(row, separators=(",", ":"))+"\n")
            trace_file.flush()
    try:
        watch(serial, list_ports, args.port, args.key, emit, commands, trace=trace)
    finally:
        if trace_file:
            trace_file.close()


if __name__ == "__main__":
    main()
