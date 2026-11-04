"""COM4 transport for separately authorized E1.43c hardware plans."""
from __future__ import annotations

import subprocess
from pathlib import Path

from protocol.storage_upper_experiment_esptool import (
    BOOT_CAPTURE_SECONDS, CAPTURE_SCRIPT, CAPTURE_SCRIPT_SHA256,
    DEVICE_PORT, ESPYTHON, EsptoolDevice, Runner, _NORMAL_BOOT_MARKERS,
    _MAC, _PANIC_MARKERS, _write_exclusive, sha,
)
from protocol.storage_upper_migration_preflight import UPPER_BYTES, UPPER_START
from protocol.storage_upper_migration_transaction import (
    CANDIDATE_FILES, FLASH_BYTES, RECOVERY_FILES, TABLE_BYTES, TABLE_START,
    Journal, load_plan,
)

ASSET_ERRORS = (
    "No assets partition found", "Failed to mmap assets partition",
    "The calculated checksum", "index.json file is not found",
    "index.json file is not valid", "Failed to load srmodels.bin",
    "The srmodels file",
    "Failed to initialize emote", "Emote display is not initialized",
    "Failed to get asset data by name",
)


class UpperMigrationEsptoolDevice(EsptoolDevice):
    """Bounded COM4 transport; simulation plans cannot enable hardware access."""

    simulation_only = False
    hardware_migration = True

    def __init__(self, run_dir: Path, *, python: Path = ESPYTHON,
                 runner: Runner | None = None, write_enabled: bool = False):
        if not python.is_file():
            raise FileNotFoundError("pinned IDF Python is missing: " + str(python))
        if not isinstance(run_dir, Path) or run_dir.is_symlink():
            raise ValueError("run directory must be a non-symlink path")
        run_dir = run_dir.resolve()
        if run_dir.parent != (Path(__file__).resolve().parents[1] / "tmp").resolve():
            raise ValueError("all adapter evidence must stay under the repository tmp directory")
        if not CAPTURE_SCRIPT.is_file() or sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256:
            raise ValueError("the fixed USB boot-capture script differs from its audited SHA-256")
        if type(write_enabled) is not bool:
            raise ValueError("write_enabled must be a boolean")

        self.port = DEVICE_PORT
        self.run_dir = run_dir
        self.python = python
        self.runner = runner or Runner()
        self.plan = load_plan(run_dir)
        if self.plan.metadata["run_label"] != run_dir.name:
            raise ValueError("persisted run label differs from its directory")
        self._identity = None
        self._reserved_logs = set()
        self._sequence = self._last_evidence_sequence()
        # The hardware entry persists the user's direct-migration instruction.
        self._write_armed = write_enabled

    def _require_plan_identity(self):
        identity = self._fresh_identity()
        if identity.digest != self.plan.metadata["identity_sha256"]:
            raise ValueError("fresh COM4 identity differs from the persisted plan")
        return identity

    def identity(self) -> str:
        return self._require_plan_identity().digest

    def _invoke(self, label, args, *, timeout=180, log_path=None):
        tail = tuple(args)
        output = super()._invoke(label, tail, timeout=timeout, log_path=log_path)
        if tail and tail[0] in {"read_flash", "write_flash"}:
            if self._identity is None or {m.lower() for m in _MAC.findall(output)} != {self._identity.mac}:
                raise ValueError("every transfer must identify the freshly confirmed COM4 device")
        return output

    def read_flash(self, offset: int, size: int) -> bytes:
        if type(offset) is not int or type(size) is not int:
            raise ValueError("read geometry must use integers")
        plan = self.plan
        exact = {(TABLE_START, TABLE_BYTES), (UPPER_START, UPPER_BYTES)}
        exact.update((row["offset"], row["bytes"]) for row in plan.metadata["static_ranges"])
        if (offset, size) not in exact:
            raise ValueError("read must match a planned range or use the complete 32 MiB snapshot")
        self._require_plan_identity()
        raw = self._capture("migration-read", offset, size)
        self._require_plan_identity()
        return raw

    def read_full(self) -> bytes:
        self._require_plan_identity()
        raw = self._capture("migration-full-backup", 0, FLASH_BYTES)
        self._require_plan_identity()
        return raw

    def write_flash(self, offset: int, raw: bytes) -> None:
        self._write_authorized()
        if not isinstance(raw, bytes):
            raise TypeError("migration payload must be immutable bytes")
        persisted = load_plan(self.run_dir)
        state = Journal(self.run_dir, persisted).load()
        allowed = {
            "upper_write_intent": (UPPER_START, CANDIDATE_FILES["upper"]),
            "table_write_intent": (TABLE_START, CANDIDATE_FILES["table"]),
            "restore_table_intent": (TABLE_START, RECOVERY_FILES["table"]),
            "restore_upper_intent": (UPPER_START, RECOVERY_FILES["upper"]),
        }
        if persisted.plan_id != self.plan.plan_id or state is None:
            raise ValueError("write requires the matching persisted recovery plan")
        target = allowed.get(state["phase"])
        expected_size = {TABLE_START: TABLE_BYTES, UPPER_START: UPPER_BYTES}.get(offset)
        if target is None or target[0] != offset or len(raw) != expected_size:
            raise ValueError("write range or journal intent is not allowed")
        source_plan = load_plan(self.run_dir, execution=state["phase"] in {
            "upper_write_intent", "table_write_intent"})
        expected = (source_plan.candidate_upper if state["phase"] == "upper_write_intent"
                    else source_plan.candidate_table if state["phase"] == "table_write_intent"
                    else source_plan.original_table if state["phase"] == "restore_table_intent"
                    else source_plan.original_upper)
        if raw != expected:
            raise ValueError("write payload differs from the exact journal-bound bytes")

        label = "upper-assets" if offset == UPPER_START else "partition-table"
        payload = self._new_path("payloads", label + ".bin")
        _write_exclusive(payload, raw)
        log = self._reserve_log("write-" + label)
        self._require_plan_identity()
        if (load_plan(self.run_dir).plan_id != persisted.plan_id
                or Journal(self.run_dir, persisted).load() != state):
            raise ValueError("persisted write intent changed during identity confirmation")
        output = self._invoke("write-" + label, (
            "write_flash", "--verify", "--no-progress", "--flash_mode", "keep",
            "--flash_freq", "keep", "--flash_size", "keep", hex(offset), str(payload),
        ), timeout=180, log_path=log)
        if "Hash of data verified" not in output:
            raise ValueError("esptool did not confirm payload verification")

    def snapshot(self, plan):
        raise ValueError("E1.43c uses one complete same-run Flash readback")

    def capture_probe(self, timeout_seconds: int) -> str:
        raise ValueError("E1.43c verifies the resource table and normal boot")

    def boot(self, target: str) -> bool:
        if target not in {"candidate", "original"}:
            raise ValueError("unknown boot target")
        try:
            log = self._capture_serial("migration-" + target + "-boot", BOOT_CAPTURE_SECONDS)
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
        # The active Emote strategy does not emit LvglStrategy's partition-size
        # or checksum-timing logs. Boot evidence does not verify resource mounts.
        return (
            all(marker in log for marker in _NORMAL_BOOT_MARKERS)
            and log.count("Calling app_main()") == 1
            and not any(marker in log for marker in _PANIC_MARKERS + ASSET_ERRORS)
        )

    def boot_original(self) -> bool:
        return self.boot("original")


__all__ = ["UpperMigrationEsptoolDevice"]
