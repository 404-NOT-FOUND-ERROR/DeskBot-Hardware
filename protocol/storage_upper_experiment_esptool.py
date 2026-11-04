"""Bounded COM4/esptool adapter for the E1.43a single-page transaction."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Sequence

from protocol.storage_upper_experiment import (
    APP_OFFSET,
    APP_PARTITION_BYTES,
    FLASH_BYTES,
    AtomicJournal,
    DeviceSnapshot,
    ExperimentPlan,
    PAGE_BYTES,
    PAGE_START,
    PROTECTED_NAMES,
    RECOVERY_PHASES,
    Device,
    align_up,
)


ROOT = Path(__file__).resolve().parents[1]
ESPYTHON = Path(r"D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe")
CAPTURE_SCRIPT = ROOT / "tools" / "capture-body-boot.py"
CAPTURE_SCRIPT_SHA256 = "be6b5b7e2132cdb38cd54e42263b854d424f556ad3f5f8272b1dad18050669f8"
CHIP = "esp32s3"
DEVICE_PORT = "COM4"
MAX_TIMEOUT_SECONDS = 180
READ_CHUNK_BYTES = 1024 * 1024
READ_TRANSFER_ATTEMPTS = 3
SERIAL_LOG_LIMIT = 2 * 1024 * 1024
BOOT_CAPTURE_SECONDS = 45
WRITE_CONFIRMATION_PREFIX = "WRITE COM4 E1.43A5"
APP_ERASE_END = 0x00458000
APP_ROLLBACK_BYTES = APP_ERASE_END - APP_OFFSET
PLAN_SCHEMA = "deskbot.upper_pattern.persisted_plan.v1"
PLAN_FILES = {
    "rollback_app": "rollback-app.bin",
    "rollback_page": "rollback-page.bin",
    "candidate_app": "candidate-app.bin",
    "pattern": "pattern.bin",
}

PROTECTED_RANGES: dict[str, tuple[int, int]] = {
    "bootloader_reserved": (0x00000000, 0x00008000),
    "partition_table": (0x00008000, 0x00001000),
    "nvs": (0x00009000, 0x00004000),
    "otadata": (0x0000D000, 0x00002000),
    "phy_init": (0x0000F000, 0x00001000),
    "gap_after_app": (0x00458000, 0x00194000),
    "assets": (0x005EC000, 0x005DC000),
    "model": (0x00BC8000, 0x000C8000),
    "gap_after_model": (0x00C90000, 0x00370000),
    "upper_outside_test_page": (0x01010000, 0x00FF0000),
}

_PORT = re.compile(r"^COM[0-9]+$", re.IGNORECASE)
_EVIDENCE_FOLDERS = ("logs", "captures", "payloads")
_EVIDENCE_SEQUENCE = re.compile(r"^([0-9]+)-")
_MAC = re.compile(r"\bMAC:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b", re.IGNORECASE)
_REVISION = re.compile(r"revision\s+v([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_PSRAM = re.compile(r"Embedded\s+PSRAM\s+([0-9]+)MB", re.IGNORECASE)
_FLASH = re.compile(r"Detected\s+flash\s+size:\s*([0-9]+)MB", re.IGNORECASE)
_PANIC_MARKERS = ("Guru Meditation", "assert failed", "abort() was called")
_NORMAL_BOOT_MARKERS = (
    "Calling app_main()",
    "LCD panel create success",
    "Touch sensor initialized successfully",
    "Found 16MB PSRAM device",
    "detected chip: mxic (opi)",
    "flash io: opi_str",
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_identity(*, mac: str, revision: str, flash_bytes: int,
                        psram_bytes: int, secure_boot: bool,
                        flash_encryption: bool) -> str:
    return ";".join((
        "chip=esp32s3",
        "revision=" + revision,
        "mac=" + mac.lower(),
        "flash_bytes=" + str(flash_bytes),
        "psram_bytes=" + str(psram_bytes),
        "secure_boot=" + str(int(secure_boot)),
        "flash_encryption=" + str(int(flash_encryption)),
    ))


@dataclass(frozen=True)
class Identity:
    mac: str
    revision: str
    flash_bytes: int
    psram_bytes: int
    secure_boot: bool
    flash_encryption: bool

    @property
    def digest(self) -> str:
        return sha(_canonical_identity(
            mac=self.mac,
            revision=self.revision,
            flash_bytes=self.flash_bytes,
            psram_bytes=self.psram_bytes,
            secure_boot=self.secure_boot,
            flash_encryption=self.flash_encryption,
        ).encode("ascii"))


def parse_identity(flash_log: str, security_log: str, *, expected_port: str = DEVICE_PORT) -> Identity:
    """Parse paired, single-device esptool identity captures; never persist MAC."""
    if not isinstance(expected_port, str) or expected_port.upper() != DEVICE_PORT:
        raise ValueError("E1.43a-5 is locked to the reviewed body port COM4")
    expected_tool = "esptool.py v4.12.0"
    for label, log in (("flash", flash_log), ("security", security_log)):
        if expected_tool not in log or log.count("Serial port " + DEVICE_PORT) != 1:
            raise ValueError(label + " capture tool or port does not match the pinned target")
    flash_macs = {value.lower() for value in _MAC.findall(flash_log)}
    security_macs = {value.lower() for value in _MAC.findall(security_log)}
    if len(flash_macs) != 1 or flash_macs != security_macs:
        raise ValueError("paired flash/security captures must identify the same single MAC")
    revisions = _REVISION.findall(flash_log)
    psram = _PSRAM.findall(flash_log)
    flash = _FLASH.findall(flash_log)
    if len(revisions) != 1 or len(psram) != 1 or len(flash) != 1:
        raise ValueError("flash identity is incomplete or ambiguous")
    if "Chip is ESP32-S3" not in flash_log or "Flash type set in eFuse: octal" not in flash_log:
        raise ValueError("unexpected chip or Flash bus identity")
    secure = re.findall(r"Secure Boot:\s*(Enabled|Disabled)", security_log, re.IGNORECASE)
    encrypted = re.findall(r"Flash Encryption:\s*(Enabled|Disabled)", security_log, re.IGNORECASE)
    if len(secure) != 1 or len(encrypted) != 1:
        raise ValueError("security identity is incomplete or ambiguous")
    return Identity(
        mac=next(iter(flash_macs)),
        revision="v" + revisions[0],
        flash_bytes=int(flash[0]) * 1024 * 1024,
        psram_bytes=int(psram[0]) * 1024 * 1024,
        secure_boot=secure[0].lower() == "enabled",
        flash_encryption=encrypted[0].lower() == "enabled",
    )


class CommandTimedOut(TimeoutError):
    def __init__(self, output: str):
        super().__init__("host command timed out")
        self.output = output


class FlashReadTransferError(OSError):
    """A logged, truncated read packet; no Flash write was attempted."""


def _decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _run_directory(run_dir: Path) -> Path:
    if not isinstance(run_dir, Path) or not run_dir.is_absolute() or run_dir.is_symlink():
        raise ValueError("run_dir must be a non-symlink absolute directory")
    resolved = run_dir.resolve()
    try:
        resolved.relative_to((ROOT / "tmp").resolve())
    except ValueError as error:
        raise ValueError("all adapter artifacts must remain under the repository tmp directory") from error
    return resolved


def _write_exclusive(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def persist_plan_inputs(run_dir: Path, plan: ExperimentPlan) -> Path:
    """Persist every byte needed to restore an interrupted transaction before writing Flash."""
    run_dir = _run_directory(run_dir)
    plan.validate()
    directory = run_dir / "plan"
    if directory.is_symlink():
        raise ValueError("persisted plan directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        loaded = load_persisted_plan(run_dir)
        if loaded.plan_id != plan.plan_id:
            raise ValueError("persisted recovery inputs belong to a different experiment plan")
        return manifest_path
    if any(path.exists() or path.is_symlink() for path in directory.iterdir()):
        raise FileExistsError("incomplete persisted-plan directory; preserve and inspect it before continuing")
    payloads = {
        "rollback_app": plan.rollback_app,
        "rollback_page": plan.rollback_page,
        "candidate_app": plan.candidate_app,
        "pattern": plan.pattern,
    }
    file_facts = {}
    for key, filename in PLAN_FILES.items():
        raw = payloads[key]
        _write_exclusive(directory / filename, raw)
        file_facts[key] = {"file": filename, "bytes": len(raw), "sha256": sha(raw)}
    manifest = {
        "schema": PLAN_SCHEMA,
        "plan_id": plan.plan_id,
        "expected_identity_sha256": plan.expected_identity_sha256,
        "expected_static_protected_sha256": {
            name: plan.expected_static_protected_sha256[name]
            for name in sorted(plan.expected_static_protected_sha256)
        },
        "files": file_facts,
    }
    content = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    _write_exclusive(manifest_path, content)
    return manifest_path


def load_persisted_plan(run_dir: Path) -> ExperimentPlan:
    """Rebuild a plan only from its complete, hash-checked local recovery bundle."""
    run_dir = _run_directory(run_dir)
    directory = run_dir / "plan"
    manifest_path = directory / "manifest.json"
    if directory.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("persisted recovery plan is missing or unsafe")
    raw_manifest = manifest_path.read_bytes()
    if len(raw_manifest) > 16 * 1024:
        raise ValueError("persisted recovery manifest exceeds its size limit")
    manifest = json.loads(raw_manifest.decode("utf-8"))
    expected_keys = {"schema", "plan_id", "expected_identity_sha256",
                     "expected_static_protected_sha256", "files"}
    if not isinstance(manifest, dict) or set(manifest) != expected_keys or manifest["schema"] != PLAN_SCHEMA:
        raise ValueError("persisted recovery manifest has an unsupported shape")
    if not isinstance(manifest["files"], dict) or set(manifest["files"]) != set(PLAN_FILES):
        raise ValueError("persisted recovery manifest does not contain the exact payload set")
    payloads = {}
    for key, filename in PLAN_FILES.items():
        facts = manifest["files"][key]
        if (not isinstance(facts, dict) or set(facts) != {"file", "bytes", "sha256"}
                or facts["file"] != filename or type(facts["bytes"]) is not int
                or not isinstance(facts["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", facts["sha256"]) is None):
            raise ValueError("persisted recovery payload metadata is invalid")
        expected_size = APP_ROLLBACK_BYTES if key == "rollback_app" else PAGE_BYTES
        if (key == "candidate_app" and not 1 <= facts["bytes"] <= APP_ROLLBACK_BYTES
                or key != "candidate_app" and facts["bytes"] != expected_size):
            raise ValueError("persisted recovery payload metadata has an unreviewed byte count")
        path = directory / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError("persisted recovery payload is missing or unsafe")
        if path.stat().st_size != facts["bytes"]:
            raise ValueError("persisted recovery payload failed its byte/hash check")
        payload = path.read_bytes()
        if len(payload) != facts["bytes"] or sha(payload) != facts["sha256"]:
            raise ValueError("persisted recovery payload failed its byte/hash check")
        payloads[key] = payload
    plan = ExperimentPlan(
        expected_identity_sha256=manifest["expected_identity_sha256"],
        rollback_app=payloads["rollback_app"],
        rollback_page=payloads["rollback_page"],
        candidate_app=payloads["candidate_app"],
        pattern=payloads["pattern"],
        expected_static_protected_sha256=manifest["expected_static_protected_sha256"],
    )
    plan.validate()
    if manifest["plan_id"] != plan.plan_id:
        raise ValueError("persisted recovery plan id does not match its inputs")
    return plan


class Runner:
    """Subprocess boundary with no shell and retained timeout output."""

    def run(self, argv: Sequence[str], *, timeout: int) -> tuple[int, str]:
        try:
            completed = subprocess.run(
                list(argv), capture_output=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = _decode_output(error.stdout) + _decode_output(error.stderr)
            raise CommandTimedOut(output) from error
        output = _decode_output(completed.stdout) + _decode_output(completed.stderr)
        return completed.returncode, output


class EsptoolDevice(Device):
    """COM4-only implementation; all device commands and reset paths are fixed."""

    def __init__(self, *, port: str, run_dir: Path, plan: ExperimentPlan,
                 python: Path = ESPYTHON, runner: Runner | None = None,
                 write_enabled: bool = False) -> None:
        if not isinstance(port, str) or port.upper() != DEVICE_PORT or _PORT.fullmatch(port) is None:
            raise ValueError("E1.43a-5 is locked to the reviewed body port COM4")
        if not python.is_file():
            raise FileNotFoundError("pinned IDF Python is missing: " + str(python))
        run_dir = _run_directory(run_dir)
        if not CAPTURE_SCRIPT.is_file() or sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256:
            raise ValueError("the fixed USB boot-capture script differs from its audited SHA-256")
        if type(write_enabled) is not bool:
            raise ValueError("write_enabled must be an explicit boolean")
        plan.validate()
        if len(plan.rollback_app) != APP_ROLLBACK_BYTES:
            raise ValueError("adapter plan must contain the reviewed complete App erase-union rollback")
        self.port = DEVICE_PORT
        self.run_dir = run_dir
        self.plan = plan
        self.python = python
        self.runner = runner or Runner()
        self._write_armed = False
        self._sequence = self._last_evidence_sequence()
        self._reserved_logs: set[Path] = set()
        self._identity: Identity | None = None
        if write_enabled:
            confirmation = f"{WRITE_CONFIRMATION_PREFIX} {self.plan.plan_id}"
            try:
                entered = input(
                    "Flash writes affect only this reviewed COM4 E1.43a-5 plan. "
                    f"Type exactly '{confirmation}' to arm writes: "
                )
            except (EOFError, OSError) as error:
                raise PermissionError("interactive operator confirmation is required for Flash writes") from error
            if entered != confirmation:
                raise PermissionError("operator confirmation did not match the exact experiment plan")
            self._write_armed = True

    @property
    def write_enabled(self) -> bool:
        return self._write_armed

    @property
    def recovery_journal_path(self) -> Path:
        return self.run_dir / "recovery.json"

    def _base(self) -> list[str]:
        return [str(self.python), "-m", "esptool", "--chip", CHIP,
                "--port", DEVICE_PORT, "--baud", "460800",
                "--before", "usb_reset", "--after", "no_reset"]

    def _evidence_directory(self, folder: str) -> Path:
        if folder not in _EVIDENCE_FOLDERS:
            raise ValueError("unsupported adapter evidence directory")
        directory = self.run_dir / folder
        if directory.is_symlink() or directory.resolve().parent != self.run_dir:
            raise ValueError("adapter evidence directory must stay inside this run")
        return directory

    def _last_evidence_sequence(self) -> int:
        sequence = 0
        for folder in _EVIDENCE_FOLDERS:
            directory = self._evidence_directory(folder)
            if not directory.exists():
                continue
            # Inspect only immediate evidence files; never recurse into build outputs.
            for path in directory.iterdir():
                match = _EVIDENCE_SEQUENCE.match(path.name)
                if match and (path.is_file() or path.is_symlink()):
                    sequence = max(sequence, int(match[1]))
        return sequence

    def _new_path(self, folder: str, name: str) -> Path:
        directory = self._evidence_directory(folder)
        directory.mkdir(parents=True, exist_ok=True)
        self._sequence += 1
        path = directory / f"{self._sequence:03d}-{name}"
        if path.exists() or path.is_symlink():
            raise FileExistsError("refuse to overwrite adapter evidence: " + str(path))
        return path

    def _reserve_log(self, label: str) -> Path:
        path = self._new_path("logs", label + ".log")
        _write_exclusive(path, b"")
        self._reserved_logs.add(path)
        return path

    def _run_logged(self, label: str, argv: Sequence[str], *, timeout: int,
                    log_path: Path | None = None) -> tuple[int, str]:
        path = log_path if log_path is not None else self._reserve_log(label)
        if (path not in self._reserved_logs or path.parent != self.run_dir / "logs" or path.is_symlink()
                or not path.is_file() or not path.name.endswith("-" + label + ".log")):
            raise ValueError("reserved command log is missing or outside this run")
        self._reserved_logs.remove(path)
        with path.open("r+", encoding="utf-8", newline="") as stream:
            if os.fstat(stream.fileno()).st_size != 0:
                raise FileExistsError("refuse to replace non-empty adapter evidence: " + str(path))
            # The empty file was created and flushed before any device command.
            stream.flush()
            os.fsync(stream.fileno())
            try:
                code, output = self.runner.run(argv, timeout=timeout)
            except CommandTimedOut as error:
                stream.write(error.output)
                stream.flush()
                os.fsync(stream.fileno())
                raise
            except subprocess.TimeoutExpired as error:
                output = _decode_output(error.stdout) + _decode_output(error.stderr)
                stream.write(output)
                stream.flush()
                os.fsync(stream.fileno())
                raise CommandTimedOut(output) from error
            except BaseException as error:
                stream.write(_decode_output(getattr(error, "output", None)))
                stream.flush()
                os.fsync(stream.fileno())
                raise
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        return code, output

    def _invoke(self, label: str, args: Iterable[str], *, timeout: int = 180,
                log_path: Path | None = None) -> str:
        if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
            raise ValueError("esptool timeout must be bounded")
        tail = list(args)
        if any(not isinstance(item, str) or not item for item in tail):
            raise ValueError("esptool arguments must be non-empty strings")
        argv = self._base() + tail
        if any(option in argv for option in ("--erase-all", "--force", "--encrypt", "--encrypt-files")):
            raise ValueError("unsafe esptool option is forbidden")
        code, output = self._run_logged(label, argv, timeout=timeout, log_path=log_path)
        if output.count("Serial port " + DEVICE_PORT) != 1:
            raise ValueError("esptool result does not identify the selected COM4 port")
        observed_macs = {value.lower() for value in _MAC.findall(output)}
        if observed_macs and self._identity is not None and observed_macs != {self._identity.mac}:
            raise ValueError("esptool result does not match the freshly confirmed device identity")
        if code != 0:
            if (tail[0] == "read_flash" and re.search(
                    r"A fatal error occurred: Corrupt data, expected 0x[0-9a-f]+ bytes "
                    r"but received 0x[0-9a-f]+ bytes", output, re.IGNORECASE)):
                raise FlashReadTransferError(
                    f"esptool {label} returned a truncated read packet; evidence retained"
                )
            raise OSError(f"esptool {label} failed with exit code {code}")
        return output

    def _fresh_identity(self) -> Identity:
        flash = self._invoke("flash-id", ("flash_id",), timeout=30)
        security = self._invoke("security-info", ("get_security_info",), timeout=30)
        identity = parse_identity(flash, security)
        if identity.flash_bytes != FLASH_BYTES or identity.psram_bytes != 16 * 1024 * 1024:
            raise ValueError("device capacity differs from the reviewed ESP32-S3 N32R16")
        if identity.secure_boot or identity.flash_encryption:
            raise ValueError("secure boot or Flash Encryption is enabled")
        self._identity = identity
        return identity

    def _require_plan_identity(self) -> Identity:
        identity = self._fresh_identity()
        if identity.digest != self.plan.expected_identity_sha256:
            raise ValueError("fresh COM4 identity differs from the reviewed experiment plan")
        return identity

    def identity_matches(self, expected_sha256: str) -> bool:
        try:
            return self._fresh_identity().digest == expected_sha256
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def _capture(self, label: str, offset: int, size: int) -> bytes:
        if type(offset) is not int or type(size) is not int or offset < 0 or size <= 0:
            raise ValueError("Flash range must use positive integer geometry")
        padded_size = align_up(size, 0x1000)
        if offset % 0x1000 or offset + padded_size > FLASH_BYTES:
            raise ValueError("Flash range must start on a sector and fit within 32 MiB")
        chunks: list[bytes] = []
        completed = 0
        while completed < padded_size:
            chunk_size = min(READ_CHUNK_BYTES, padded_size - completed)
            for attempt in range(READ_TRANSFER_ATTEMPTS):
                path = self._new_path(
                    "captures",
                    f"{label}-{offset + completed:08x}-{chunk_size:08x}.bin",
                )
                _write_exclusive(path, b"")
                try:
                    self._invoke(label, ("read_flash", "--no-progress", "--flash_size", "keep",
                                         hex(offset + completed), hex(chunk_size), str(path)))
                    break
                except FlashReadTransferError:
                    if attempt + 1 == READ_TRANSFER_ATTEMPTS:
                        raise
                    # Retry only the observed read-packet truncation, retaining
                    # its partial file/log. Identity drift and all write errors
                    # still stop immediately; no existing evidence is reused.
                    self._require_plan_identity()
            if not path.is_file() or path.is_symlink():
                raise FileNotFoundError("esptool did not produce the expected Flash capture")
            raw = path.read_bytes()
            if len(raw) != chunk_size:
                raise ValueError(f"{label} chunk length differs from the requested sector-aligned range")
            chunks.append(raw)
            completed += chunk_size
        return b"".join(chunks)[:size]

    def read_flash(self, offset: int, size: int) -> bytes:
        allowed = (
            (PAGE_START, PAGE_BYTES),
            (APP_OFFSET, len(self.plan.rollback_app)),
            (APP_OFFSET, len(self.plan.candidate_app)),
        )
        if (offset, size) not in allowed:
            raise ValueError("controller read outside the exact App/test-page ranges")
        self._require_plan_identity()
        return self._capture("readback", offset, size)

    def snapshot(self, plan: ExperimentPlan) -> DeviceSnapshot:
        if plan.plan_id != self.plan.plan_id:
            raise ValueError("snapshot plan differs from adapter plan")
        identity = self._require_plan_identity()
        app = self._capture("snapshot-app", APP_OFFSET, len(plan.rollback_app))
        page = self._capture("snapshot-page", PAGE_START, PAGE_BYTES)
        protected: dict[str, str] = {}
        low_ranges: dict[str, bytes] = {}
        boundary_lower = None
        for name in sorted(PROTECTED_NAMES):
            offset, size = PROTECTED_RANGES[name]
            raw = self._capture("snapshot-" + name, offset, size)
            protected[name] = sha(raw)
            if name in {"bootloader_reserved", "partition_table", "nvs", "otadata", "phy_init"}:
                low_ranges[name] = raw
            elif name == "gap_after_model":
                boundary_lower = raw[-16:]
        low_page = b"".join(low_ranges[name] for name in (
            "bootloader_reserved", "partition_table", "nvs", "otadata", "phy_init"))
        if len(low_page) != 0x10000 or boundary_lower is None or len(boundary_lower) != 16:
            raise ValueError("write-time snapshot cannot form the probe's low page and boundary reference")
        return DeviceSnapshot(
            identity_sha256=identity.digest,
            chip=CHIP,
            physical_flash_bytes=identity.flash_bytes,
            psram_bytes=identity.psram_bytes,
            secure_boot=identity.secure_boot,
            flash_encryption=identity.flash_encryption,
            app_erase_bytes=app,
            upper_page=page,
            protected_sha256=protected,
            runtime_low_page=low_page,
            runtime_boundary_lower=boundary_lower,
        )

    def _write_authorized(self) -> None:
        if not self._write_armed:
            raise PermissionError("Flash writes are disabled until an operator confirms this exact plan")

    def _require_recovery_intent(self) -> None:
        persisted = load_persisted_plan(self.run_dir)
        if persisted.plan_id != self.plan.plan_id:
            raise PermissionError("persisted recovery inputs do not match this experiment plan")
        state = AtomicJournal(self.recovery_journal_path).load()
        if (state is None or state.get("plan_id") != self.plan.plan_id
                or state.get("phase") not in RECOVERY_PHASES - {"prepared"}
                or state.get("static_protected_sha256") != self.plan.expected_static_protected_sha256
                or state.get("sequence", 0) < 2):
            raise PermissionError(
                "Flash write blocked: matching durable restore_required recovery intent is missing"
            )

    def write_flash(self, offset: int, data: bytes) -> None:
        self._write_authorized()
        if not isinstance(data, bytes):
            raise TypeError("Flash payload must be immutable reviewed bytes")
        if offset == PAGE_START:
            if len(data) != PAGE_BYTES or data not in (self.plan.pattern, self.plan.rollback_page):
                raise ValueError("test-page writes must be the exact reviewed pattern or rollback page")
            label = "write-page"
        elif offset == APP_OFFSET:
            if data not in (self.plan.candidate_app, self.plan.rollback_app):
                raise ValueError("App writes must be the exact candidate or rollback image")
            if len(data) > APP_PARTITION_BYTES:
                raise ValueError("App payload exceeds the reviewed partition")
            label = "write-app"
        else:
            raise ValueError("writes are limited to the test page and App")
        persist_plan_inputs(self.run_dir, self.plan)
        self._require_recovery_intent()
        path = self._new_path("payloads", label + ".bin")
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        log_path = self._reserve_log(label)
        self._require_plan_identity()
        self._invoke(label, ("write_flash", "--verify", "--flash_mode", "keep",
                             "--flash_size", "keep", hex(offset), str(path)),
                     log_path=log_path)

    def _capture_serial(self, label: str, duration_seconds: int) -> str:
        if type(duration_seconds) is not int or not 1 <= duration_seconds <= 120:
            raise ValueError("serial capture duration must be bounded to 1..120 seconds")
        if sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256:
            raise ValueError("the audited USB capture script changed during the run")
        log_path = self._new_path("captures", label + ".log")
        _write_exclusive(log_path, b"")
        command_log_path = self._reserve_log(label + "-capture")
        argv = [str(self.python), str(CAPTURE_SCRIPT), "--port", DEVICE_PORT,
                "--duration", str(duration_seconds), "--output", str(log_path), "--usb-reset"]
        self._require_plan_identity()
        code, output = self._run_logged(
            label + "-capture", argv, timeout=duration_seconds + 45,
            log_path=command_log_path,
        )
        if code != 0:
            raise OSError(f"{label} serial capture failed with exit code {code}")
        if not log_path.is_file() or log_path.is_symlink() or log_path.stat().st_size > SERIAL_LOG_LIMIT:
            raise ValueError(f"{label} serial evidence is missing or exceeds its size limit")
        return log_path.read_text(encoding="utf-8", errors="replace")

    def capture_probe(self, timeout_seconds: int) -> str:
        return self._capture_serial("upper-probe", timeout_seconds)

    def boot_original(self) -> bool:
        try:
            log = self._capture_serial("normal-boot", BOOT_CAPTURE_SECONDS)
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
        return (all(marker in log for marker in _NORMAL_BOOT_MARKERS)
                and not any(marker in log for marker in _PANIC_MARKERS))


__all__ = [
    "APP_ERASE_END", "APP_ROLLBACK_BYTES", "CAPTURE_SCRIPT_SHA256", "DEVICE_PORT",
    "ESPYTHON", "EsptoolDevice", "Identity", "PROTECTED_RANGES", "Runner",
    "load_persisted_plan", "parse_identity", "persist_plan_inputs", "sha",
]
