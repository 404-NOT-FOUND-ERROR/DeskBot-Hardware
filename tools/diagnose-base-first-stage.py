#!/usr/bin/env python3
"""Read-only first-stage diagnostics for the ESP-VoCat C61 base.

This tool deliberately has no flash, erase, or application-protocol write
operation.  The esptool identity probe necessarily exchanges ROM bootloader
sync bytes; it never sends a flash/erase command.  It can:

* list serial ports and their USB descriptors;
* run the read-only esptool ``chip_id`` probe on one explicitly selected port;
* delegate diagnostic log capture to ``capture-base-diagnostics.py``; and
* listen for the C61 boot log and classify the homing result.

The base and the ESP-VoCat body are separate MCUs.  A port is not identified
as the C61 merely because it is an Espressif USB port; ``identify`` must be
used on the selected candidate.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable, Sequence

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - depends on the selected Python env
    serial = None  # type: ignore[assignment]
    SerialException = Exception  # type: ignore[assignment,misc]
    list_ports = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BAUD = 115200
EXPECTED_CHIP = "ESP32-C61"

UART_LOG_RE = re.compile(
    r"\[diag\]\s+uart_tx\s+name=(?P<name>\S+)\s+"
    r"len=(?P<length>\d+)\s+hex=(?P<hex>[0-9A-Fa-f]+)"
)
HOMING_PATTERNS = {
    "task_started": re.compile(r"Base calibration task started", re.I),
    "limit_pressed": re.compile(r"Limit switch pressed, moving to home position", re.I),
    "completed": re.compile(r"Base calibration completed(?:\s|$)", re.I),
    "timeout": re.compile(r"Calibration timeout!", re.I),
    "fallback_completed": re.compile(
        r"Base calibration completed \(timeout fallback mode\)", re.I
    ),
}


class DiagnosticError(RuntimeError):
    """A user-actionable diagnostic failure."""


def require_serial() -> None:
    if serial is None or list_ports is None:
        raise DiagnosticError(
            "pyserial is not available. Run this script with the ESP-IDF Python "
            "environment (for example idf5.5_py3.11_env)."
        )


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def default_output(prefix: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return ROOT / "logs" / "base-first-stage" / f"{prefix}-{stamp}"


def resolve_path_arg(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    except (TypeError, ValueError):
        return None


def hex_or_dash(value: Any) -> str:
    parsed = safe_int(value)
    return "-" if parsed is None else f"{parsed:04X}"


@dataclasses.dataclass(frozen=True)
class PortRecord:
    device: str
    description: str
    hwid: str
    vid: int | None
    pid: int | None
    serial_number: str
    location: str
    manufacturer: str
    role_hint: str

    def as_json(self) -> dict[str, Any]:
        result = dataclasses.asdict(self)
        result["vid"] = None if self.vid is None else f"0x{self.vid:04X}"
        result["pid"] = None if self.pid is None else f"0x{self.pid:04X}"
        return result


def role_hint(vid: int | None, pid: int | None, hwid: str, description: str) -> str:
    upper_hwid = (hwid or "").upper()
    upper_description = (description or "").upper()
    if "BTHENUM" in upper_hwid or "BLUETOOTH" in upper_description:
        return "bluetooth (ignore for C61 probe)"
    if vid == 0x303A and pid == 0x1001:
        return "Espressif USB-Serial/JTAG (probe chip)"
    if (vid, pid) in {
        (0x10C4, 0xEA60),  # CP210x
        (0x1A86, 0x7523),  # CH340/CH341
        (0x0403, 0x6001),  # FTDI FT232
        (0x0403, 0x6010),  # FTDI dual channel
    }:
        return "USB-UART bridge (probe chip)"
    return "candidate (probe chip)"


def enumerate_ports() -> list[PortRecord]:
    require_serial()
    records: list[PortRecord] = []
    for info in sorted(list_ports.comports(), key=lambda item: item.device):
        vid = safe_int(getattr(info, "vid", None))
        pid = safe_int(getattr(info, "pid", None))
        description = str(getattr(info, "description", "") or "")
        hwid = str(getattr(info, "hwid", "") or "")
        records.append(
            PortRecord(
                device=str(info.device),
                description=description,
                hwid=hwid,
                vid=vid,
                pid=pid,
                serial_number=str(getattr(info, "serial_number", "") or ""),
                location=str(getattr(info, "location", "") or ""),
                manufacturer=str(getattr(info, "manufacturer", "") or ""),
                role_hint=role_hint(vid, pid, hwid, description),
            )
        )
    return records


def print_ports(records: Sequence[PortRecord], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([record.as_json() for record in records], ensure_ascii=True, indent=2))
        return
    if not records:
        print("No serial ports were detected.")
        print("Connect the C61 base, wait for Windows to enumerate it, then run list again.")
        return
    print("Detected serial ports (only an explicitly selected --port is probed):")
    print("PORT   VID:PID   ROLE                                     DESCRIPTION")
    print("-----  --------  ---------------------------------------  ------------------------------")
    for record in records:
        vid_pid = f"{hex_or_dash(record.vid)}:{hex_or_dash(record.pid)}"
        print(
            f"{record.device:<6} {vid_pid:<9} {record.role_hint:<39} "
            f"{record.description[:70]}"
        )
        if record.hwid:
            print(f"       HWID: {record.hwid}")
        if record.serial_number or record.location or record.manufacturer:
            details = ", ".join(
                item
                for item in (
                    f"serial={record.serial_number}" if record.serial_number else "",
                    f"location={record.location}" if record.location else "",
                    f"manufacturer={record.manufacturer}" if record.manufacturer else "",
                )
                if item
            )
            print(f"       {details}")
    print("A role hint is not proof of the chip model; use identify --port COMx.")


def find_port_record(port: str) -> PortRecord | None:
    wanted = port.upper()
    for record in enumerate_ports():
        if record.device.upper() == wanted:
            return record
    return None


def resolve_esptool(python_exe: str | None, esptool_exe: str | None) -> list[str]:
    if esptool_exe:
        path = Path(esptool_exe)
        if not path.exists():
            raise DiagnosticError(f"esptool executable was not found: {path}")
        return [str(path)]

    selected_python = Path(python_exe).resolve() if python_exe else Path(sys.executable)
    if not selected_python.exists():
        raise DiagnosticError(f"Python executable was not found: {selected_python}")

    # Prefer the module in the selected interpreter.  This is the same Python
    # environment that provides pyserial in the ESP-IDF setup.
    try:
        probe = subprocess.run(
            [str(selected_python), "-c", "import esptool"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise DiagnosticError(
            f"The selected Python did not respond while checking esptool: {selected_python}"
        ) from exc
    if probe.returncode == 0:
        return [str(selected_python), "-m", "esptool"]

    adjacent = selected_python.with_name("esptool.exe")
    if adjacent.exists():
        return [str(adjacent)]
    on_path = shutil.which("esptool") or shutil.which("esptool.py")
    if on_path:
        return [on_path]
    raise DiagnosticError(
        "esptool was not found. Run with the ESP-IDF Python environment or pass "
        "--python with your ESP-IDF Python executable."
    )


def parse_chip_output(output: str) -> dict[str, str | None]:
    chip: str | None = None
    chip_id: str | None = None
    mac: str | None = None
    # esptool emits either "Detecting chip type... ESP32-C61" or
    # "Chip is ESP32-C61 (revision ...)" depending on the connection path.
    for line in output.splitlines():
        upper = line.upper()
        if "DETECTING CHIP TYPE" in upper or "CHIP IS " in upper:
            match = re.search(r"\bESP32[-A-Z0-9]+\b", upper)
            if match:
                chip = match.group(0)
        match = re.search(r"CHIP ID:\s*(0X[0-9A-F]+)", upper)
        if match:
            chip_id = match.group(1)
        match = re.search(r"^\s*MAC(?:\s+BASE)?\s*:\s*([0-9A-F:]{17})", upper)
        if match:
            mac = match.group(1).lower()
    return {"chip": chip, "chip_id": chip_id, "mac": mac}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def cmd_list(args: argparse.Namespace) -> int:
    records = enumerate_ports()
    print_ports(records, as_json=args.json)
    return 0 if records else 1


def cmd_identify(args: argparse.Namespace) -> int:
    record = find_port_record(args.port)
    if record is None:
        print(f"Warning: {args.port} is not currently listed by pyserial.")
        print("The probe will still be attempted; check the cable and port spelling if it fails.")
    elif "bluetooth" in record.role_hint and not args.allow_bluetooth:
        raise DiagnosticError(
            f"{args.port} looks like a Bluetooth virtual port. Refusing to probe it; "
            "choose the C61 USB/UART port or pass --allow-bluetooth explicitly."
        )

    if args.before != "no_reset":
        print(
            f"The selected port {args.port} may be reset by esptool before the read-only probe."
        )
    if args.after in {"hard_reset", "soft_reset", "watchdog_reset"}:
        print(
            "After the probe the chip will be reset back to its application; a C61 base "
            "may start its homing motion, so keep the mechanism clear."
        )
    print("No flash/erase/write_flash command will be run; esptool only exchanges ROM sync bytes.")

    tool = resolve_esptool(args.python, args.esptool)
    command = tool + [
        "--chip",
        "auto",
        "--port",
        args.port,
        "--before",
        args.before,
        "--after",
        args.after,
        "--connect-attempts",
        str(args.connect_attempts),
        "chip_id",
    ]
    display = " ".join(command)
    print(f"Running: {display}")
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        parts = []
        for part in (exc.stdout or "", exc.stderr or ""):
            if part:
                parts.append(part.decode("utf-8", errors="replace") if isinstance(part, bytes) else part)
        output = "\n".join(parts)
        print(output, end="" if output.endswith("\n") else "\n")
        raise DiagnosticError(
            f"esptool timed out after {args.timeout}s. No write was attempted."
        ) from exc

    output = (result.stdout or "") + (result.stderr or "")
    print(output, end="" if output.endswith("\n") else "\n")
    parsed = parse_chip_output(output)
    print(
        "Probe summary: chip={chip}, chip_id={chip_id}, mac={mac}".format(
            chip=parsed["chip"] or "unknown",
            chip_id=parsed["chip_id"] or "not reported",
            mac=parsed["mac"] or "not reported",
        )
    )

    if args.output:
        output_path = resolve_path_arg(args.output)
        write_text(
            output_path,
            json.dumps(
                {
                    "timestamp": now_iso(),
                    "port": args.port,
                    "before": args.before,
                    "after": args.after,
                    "returncode": result.returncode,
                    **parsed,
                    "raw_output": output,
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
        )
        print(f"Probe report: {output_path.resolve()}")

    if result.returncode != 0:
        print(
            "Probe failed. If the board is running application firmware, put only the "
            "selected C61 into download mode and retry with --before default_reset."
        )
        return 1
    if parsed["chip"] and EXPECTED_CHIP not in parsed["chip"]:
        print(
            f"The selected port is {parsed['chip']}, not {EXPECTED_CHIP}; it is likely the body."
        )
        return 2
    if not parsed["chip"]:
        print("The probe completed but did not report a recognizable chip model.")
        return 3
    print(f"C61 identity confirmed: {parsed['chip']} on {args.port}.")
    return 0


def delegate_capture(args: argparse.Namespace) -> int:
    collector = Path(__file__).with_name("capture-base-diagnostics.py")
    if not collector.exists():
        raise DiagnosticError(f"The existing collector was not found: {collector}")
    command = [
        sys.executable,
        str(collector),
        "--port",
        args.port,
        "--baud",
        str(args.baud),
    ]
    if args.duration is not None:
        command.extend(["--duration", str(args.duration)])
    if args.output:
        command.extend(["--output", str(resolve_path_arg(args.output))])
    print("Starting read-only console capture; this process never calls serial.write().")
    print("Close every other serial monitor for this port before continuing.")
    return subprocess.run(command, cwd=str(ROOT)).returncode


def decode_frame(hex_text: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", hex_text)
    try:
        frame = bytes.fromhex(compact)
    except ValueError:
        return {"frame": compact.upper(), "valid": False, "reason": "non_hex"}
    result: dict[str, Any] = {
        "frame": compact.upper(),
        "length": len(frame),
        "valid": False,
        "command": None,
        "value": None,
        "checksum": None,
        "checksum_expected": None,
    }
    if len(frame) < 5 or frame[:2] != b"\xAA\x55":
        result["reason"] = "bad_header_or_short"
        return result
    payload_length = int.from_bytes(frame[2:4], "big")
    expected_total = payload_length + 5
    result["payload_length"] = payload_length
    if len(frame) != expected_total or payload_length < 1:
        result["reason"] = "length_mismatch"
        return result
    result["command"] = f"0x{frame[4]:02X}"
    if payload_length >= 3:
        result["value"] = f"0x{int.from_bytes(frame[5:7], 'big'):04X}"
    expected_checksum = sum(frame[4 : 4 + payload_length]) & 0xFF
    result["checksum"] = f"0x{frame[-1]:02X}"
    result["checksum_expected"] = f"0x{expected_checksum:02X}"
    result["valid"] = frame[-1] == expected_checksum
    if not result["valid"]:
        result["reason"] = "checksum_mismatch"
    return result


def evaluate_homing(lines: Iterable[str | tuple[str, str]]) -> dict[str, Any]:
    flags = {name: False for name in HOMING_PATTERNS}
    first_seen: dict[str, str] = {}
    event_order: list[str] = []
    uart_frames: list[dict[str, Any]] = []
    for item in lines:
        if isinstance(item, tuple):
            when, line = item
        else:
            when, line = now_iso(), item
        for name, pattern in HOMING_PATTERNS.items():
            if not flags[name] and pattern.search(line):
                flags[name] = True
                first_seen[name] = when
                event_order.append(name)
        uart = UART_LOG_RE.search(line)
        if uart:
            decoded = decode_frame(uart.group("hex"))
            declared_length = int(uart.group("length"))
            uart_frames.append(
                {
                    "timestamp": when,
                    "name": uart.group("name"),
                    "declared_length": declared_length,
                    "declared_length_matches": decoded.get("length") == declared_length,
                    **decoded,
                }
            )

    normal_order = [
        name
        for name in event_order
        if name in {"task_started", "limit_pressed", "completed"}
    ]
    normal_sequence = normal_order[:3] == ["task_started", "limit_pressed", "completed"]
    if normal_sequence and not flags["fallback_completed"] and not flags["timeout"]:
        status = "PASS"
        exit_code = 0
        note = "Normal homing reached the limit switch and completed."
    elif flags["fallback_completed"] or flags["timeout"]:
        status = "WARN"
        exit_code = 2
        note = "The firmware used timeout fallback; the limit switch was not verified."
    elif flags["completed"]:
        status = "INCONCLUSIVE"
        exit_code = 2
        note = "Completion was logged, but the normal limit-switch message was absent."
    else:
        status = "FAIL"
        exit_code = 1
        note = "No complete homing sequence was observed in the listening window."
    return {
        "status": status,
        "exit_code": exit_code,
        "note": note,
        "flags": flags,
        "first_seen": first_seen,
        "event_order": event_order,
        "normal_sequence": normal_sequence,
        "uart_frames": uart_frames,
    }


def open_read_only_serial(port: str, baud: int):
    require_serial()
    # No write call is made anywhere in this module.  Explicitly disable
    # software/hardware flow control; the base protocol is 8N1 without RTS/CTS.
    return serial.Serial(
        port=port,
        baudrate=baud,
        timeout=0.5,
        rtscts=False,
        dsrdtr=False,
    )


def serial_port_is_present(port: str) -> bool:
    """Return whether Windows currently enumerates the requested port."""
    wanted = port.upper()
    return any(str(info.device).upper() == wanted for info in list_ports.comports())


def cmd_homing(args: argparse.Namespace) -> int:
    require_serial()
    output = resolve_path_arg(args.output) if args.output else default_output("homing")
    output.mkdir(parents=True, exist_ok=True)
    session_path = output / "session.log"
    summary_path = output / "homing-summary.json"
    frames_path = output / "uart-frames.csv"
    started = time.monotonic()
    lines: list[tuple[str, str]] = []
    interrupted = False
    print(f"Listening on {args.port} at {args.baud} baud for {args.duration:g}s.")
    print("This listener never sends UART data.")
    if args.wait_for_port:
        print(
            "Hot-plug mode is enabled: connect or reconnect the C61 Type-C cable "
            "after this message; the listener will wait for COM enumeration."
        )
    else:
        print("Start the listener first, then power-cycle or press reset on the C61 base.")
    print(f"Logs will be saved under: {output.resolve()}")

    try:
        with session_path.open("w", encoding="utf-8", newline="") as session:
            wait_notice_shown = False
            while time.monotonic() - started < args.duration:
                remaining = args.duration - (time.monotonic() - started)
                if remaining <= 0:
                    break

                if not serial_port_is_present(args.port):
                    if not args.wait_for_port:
                        raise DiagnosticError(
                            f"Could not open or read {args.port}: port is not currently enumerated."
                        )
                    if not wait_notice_shown:
                        print(
                            f"Waiting for {args.port} to appear; connect the C61 Type-C data cable.",
                            flush=True,
                        )
                        wait_notice_shown = True
                    time.sleep(min(args.poll_interval, remaining))
                    continue

                device = None
                disconnected = False
                try:
                    device = open_read_only_serial(args.port, args.baud)
                    wait_notice_shown = False
                    print(f"Connected to {args.port}; collecting boot/application logs.", flush=True)
                    while time.monotonic() - started < args.duration:
                        try:
                            data = device.readline()
                        except (SerialException, OSError, ValueError) as exc:
                            if not args.wait_for_port:
                                raise DiagnosticError(
                                    f"Could not open or read {args.port}: {exc}"
                                ) from exc
                            print(
                                f"{args.port} disconnected ({exc}); waiting to reconnect.",
                                flush=True,
                            )
                            disconnected = True
                            break
                        if not data:
                            continue
                        line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                        when = now_iso()
                        lines.append((when, line))
                        session.write(f"{when}\t{line}\n")
                        session.flush()
                        print(line, flush=True)
                except (SerialException, OSError, ValueError) as exc:
                    if not args.wait_for_port:
                        raise DiagnosticError(f"Could not open or read {args.port}: {exc}") from exc
                    print(
                        f"Could not keep {args.port} open ({exc}); waiting to reconnect.",
                        flush=True,
                    )
                    disconnected = True
                finally:
                    if device is not None:
                        try:
                            device.close()
                        except (SerialException, OSError, ValueError):
                            pass

                if disconnected and time.monotonic() - started < args.duration:
                    wait_notice_shown = False
                    time.sleep(min(args.poll_interval, args.duration - (time.monotonic() - started)))
    except KeyboardInterrupt:
        interrupted = True
        print("\nListener stopped by user; classifying the data captured so far.")

    result = evaluate_homing(lines)
    summary = {
        "timestamp": now_iso(),
        "port": args.port,
        "baud": args.baud,
        "duration_seconds": args.duration,
        "interrupted": interrupted,
        "session": str(session_path.resolve()),
        **{key: value for key, value in result.items() if key != "uart_frames"},
    }
    write_text(summary_path, json.dumps(summary, ensure_ascii=True, indent=2) + "\n")
    with frames_path.open("w", encoding="utf-8", newline="") as frames_file:
        fields = [
            "timestamp",
            "name",
            "declared_length",
            "declared_length_matches",
            "frame",
            "length",
            "payload_length",
            "command",
            "value",
            "checksum",
            "checksum_expected",
            "valid",
            "reason",
        ]
        writer = csv.DictWriter(frames_file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["uart_frames"])
    print(f"Homing result: {result['status']} - {result['note']}")
    print(f"Summary: {summary_path.resolve()}")
    return int(result["exit_code"])


def add_common_serial_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", required=True, help="Explicit serial port, for example COM8")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only first-stage diagnostics for the ESP-VoCat C61 base."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List serial ports and USB descriptors")
    list_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    list_parser.set_defaults(handler=cmd_list)

    identify = subparsers.add_parser(
        "identify", help="Read chip_id (and MAC when esptool reports it) from one port"
    )
    identify.add_argument("--port", required=True, help="One explicitly selected port, e.g. COM8")
    identify.add_argument(
        "--before",
        choices=("default_reset", "usb_reset", "no_reset", "no_reset_no_sync"),
        default="default_reset",
        help="esptool pre-connection behavior (default: default_reset)",
    )
    identify.add_argument(
        "--after",
        choices=("hard_reset", "soft_reset", "no_reset", "no_reset_stub", "watchdog_reset"),
        default="hard_reset",
    )
    identify.add_argument("--connect-attempts", type=int, default=2)
    identify.add_argument("--timeout", type=float, default=30.0)
    identify.add_argument("--python", help="Python executable containing esptool")
    identify.add_argument("--esptool", help="Explicit esptool executable path")
    identify.add_argument("--output", help="Optional JSON probe report path")
    identify.add_argument(
        "--allow-bluetooth",
        action="store_true",
        help="Allow probing a port that pyserial identifies as Bluetooth",
    )
    identify.set_defaults(handler=cmd_identify)

    capture = subparsers.add_parser(
        "capture", help="Run the existing diagnostic console/UART-frame collector"
    )
    add_common_serial_options(capture)
    capture.add_argument("--duration", type=float)
    capture.add_argument("--output", help="Output directory for session.log/samples.csv/events.csv")
    capture.set_defaults(handler=delegate_capture)

    homing = subparsers.add_parser(
        "homing", help="Listen for and classify one C61 startup homing sequence"
    )
    add_common_serial_options(homing)
    homing.add_argument("--duration", type=float, default=15.0)
    homing.add_argument(
        "--wait-for-port",
        action="store_true",
        help="Wait for a hot-plugged port and reconnect after Type-C disconnects",
    )
    homing.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        help="Seconds between hot-plug port checks (default: 0.25)",
    )
    homing.add_argument("--output", help="Output directory for homing logs and summary")
    homing.set_defaults(handler=cmd_homing)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "baud", DEFAULT_BAUD) <= 0:
        parser.error("--baud must be greater than zero")
    if getattr(args, "duration", None) is not None and args.duration <= 0:
        parser.error("--duration must be greater than zero")
    if getattr(args, "timeout", 1.0) <= 0:
        parser.error("--timeout must be greater than zero")
    if getattr(args, "connect_attempts", 1) <= 0:
        parser.error("--connect-attempts must be greater than zero")
    if getattr(args, "poll_interval", 0.25) <= 0:
        parser.error("--poll-interval must be greater than zero")
    try:
        return int(args.handler(args))
    except DiagnosticError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
