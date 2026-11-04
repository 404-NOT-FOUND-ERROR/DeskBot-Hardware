"""Durable upper-resource migration with separate simulation and hardware plans."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Protocol
import uuid

from protocol.storage_n32r16 import TABLE_SHA256
from protocol.storage_upper_migration_preflight import (
    IMAGE_SHA256, LEGACY_TABLE_SECTOR_SHA256, UPPER_BYTES, UPPER_START,
)

FLASH_BYTES = 32 * 1024 * 1024
TABLE_START, TABLE_BYTES = 0x8000, 4096
NVS_START, NVS_END = 0x9000, 0xD000
MODE = "offline_simulation"
LIVE_MODE = "hardware_migration"
DIRECT_AUTHORIZATION = "user_requested_direct_migration"
TABLE_SECTOR_SHA256 = "407663cb70bc52d7452ee6afea441cc7c0655e616205cefbf21c110ecd8c6689"
STATIC_GEOMETRY = (
    ("bootloader", 0, 0x8000), ("otadata", 0xD000, 0x2000),
    ("phy", 0xF000, 0x1000), ("app", 0x10000, 0x5DC000),
    ("legacy_assets", 0x5EC000, 0x5DC000), ("model", 0xBC8000, 0xC8000),
    ("lower_tail", 0xC90000, 0x370000), ("upper_tail", 0x1800000, 0x800000),
)
PHASES = {
    "prepared", "upper_write_intent", "upper_verified", "table_write_intent",
    "candidate_verified", "awaiting_interaction", "restore_table_intent",
    "restore_table_verified", "restore_upper_intent", "restore_verified", "restored",
    "needs_recovery",
}
RECOVERY_FILES = {"table": "original-table.bin", "upper": "original-upper.bin"}
CANDIDATE_FILES = {"table": "candidate-table.bin", "upper": "candidate-upper.bin"}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def valid_sha(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def write_new(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ValueError("file must not be a symlink")
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def read_file(folder: Path, name: str, maximum: int) -> bytes:
    path = folder / name
    if folder.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError("missing or unsafe persisted input: " + name)
    if path.stat().st_size > maximum:
        raise ValueError("persisted input exceeds size limit: " + name)
    return path.read_bytes()


@contextmanager
def operation_lock(folder: Path):
    """OS releases the lock on process exit, including an interrupted run."""
    path = folder / "operation.lock"
    if folder.is_symlink() or path.is_symlink():
        raise ValueError("unsafe operation lock")
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class MigrationPlan:
    metadata: dict
    original_table: bytes
    original_upper: bytes
    candidate_table: bytes | None = None
    candidate_upper: bytes | None = None

    @property
    def plan_id(self) -> str:
        return sha(canonical(self.metadata))

    def validate(self) -> None:
        m = self.metadata
        keys = {"schema", "mode", "run_label", "identity_sha256", "snapshot_sha256", "snapshot_bytes",
                "original_table_sha256", "original_upper_sha256", "candidate_table_sha256",
                "candidate_upper_sha256", "nvs_sha256", "static_ranges", "write_ranges"}
        if isinstance(m, dict) and m.get("mode") == LIVE_MODE:
            keys.add("authorization")
        label_pattern = (r"e143c-live-[a-z0-9][a-z0-9-]{0,39}"
                         if isinstance(m, dict) and m.get("mode") == LIVE_MODE
                         else r"e143c-sim-[a-z0-9][a-z0-9-]{0,39}")
        if (not isinstance(m, dict) or set(m) != keys
                or m["schema"] != "deskbot.upper_migration.plan.v1" or m["mode"] not in {MODE, LIVE_MODE}
                or (m["mode"] == LIVE_MODE and m.get("authorization") != DIRECT_AUTHORIZATION)
                or not isinstance(m["run_label"], str)
                or re.fullmatch(label_pattern, m["run_label"]) is None
                or type(m["snapshot_bytes"]) is not int or m["snapshot_bytes"] != FLASH_BYTES):
            raise ValueError("invalid migration mode, authorization or plan")
        for key in (k for k in keys if k.endswith("sha256")):
            if not valid_sha(m[key]):
                raise ValueError("invalid plan hash: " + key)
        if m["write_ranges"] != [
            {"name": "upper", "offset": UPPER_START, "bytes": UPPER_BYTES},
            {"name": "table", "offset": TABLE_START, "bytes": TABLE_BYTES},
        ]:
            raise ValueError("only the upper resource and table sector may be written")
        for raw, size, key in ((self.original_table, TABLE_BYTES, "original_table_sha256"),
                              (self.original_upper, UPPER_BYTES, "original_upper_sha256")):
            if not isinstance(raw, bytes) or len(raw) != size or sha(raw) != m[key]:
                raise ValueError("exact original recovery bytes are required")
        if sha(self.original_table) != LEGACY_TABLE_SECTOR_SHA256:
            raise ValueError("migration starts from the accepted legacy table")
        expected_table = m["candidate_table_sha256"]
        if m["candidate_upper_sha256"] != IMAGE_SHA256 or expected_table != TABLE_SECTOR_SHA256:
            raise ValueError("v1 migrates the unchanged accepted E1.18 resource image")
        rows = m["static_ranges"]
        if not isinstance(rows, list) or len(rows) != len(STATIC_GEOMETRY):
            raise ValueError("complete static coverage required")
        for row, (name, offset, size) in zip(rows, STATIC_GEOMETRY):
            if (not isinstance(row, dict) or set(row) != {"name", "offset", "bytes", "sha256"}
                    or (row["name"], row["offset"], row["bytes"]) != (name, offset, size)
                    or not valid_sha(row["sha256"])):
                raise ValueError("static coverage changed")
        if (self.candidate_table is None) != (self.candidate_upper is None):
            raise ValueError("candidate table and resources must be supplied together")
        if self.candidate_table is not None:
            if (len(self.candidate_table) != TABLE_BYTES
                    or sha(self.candidate_table[:3072]) != TABLE_SHA256["upper-assets-8m"]
                    or self.candidate_table[3072:] != b"\xff" * 1024
                    or sha(self.candidate_table) != expected_table
                    or len(self.candidate_upper) != UPPER_BYTES
                    or sha(self.candidate_upper) != IMAGE_SHA256):
                raise ValueError("candidate bytes differ from the accepted upper layout")


def make_plan(snapshot: bytes, upper: bytes, table_payload: bytes, *, identity_sha256: str,
              run_label: str, mode: str = MODE, authorization: str | None = None) -> MigrationPlan:
    if not isinstance(snapshot, bytes) or len(snapshot) != FLASH_BYTES:
        raise ValueError("complete 32 MiB same-execution snapshot required")
    if len(table_payload) != 3072:
        raise ValueError("retained E1.36 table payload required")
    table = table_payload + b"\xff" * 1024
    original_table = snapshot[TABLE_START:TABLE_START + TABLE_BYTES]
    original_upper = snapshot[UPPER_START:UPPER_START + UPPER_BYTES]
    plan = MigrationPlan({
        "schema": "deskbot.upper_migration.plan.v1", "mode": mode, "run_label": run_label,
        "identity_sha256": identity_sha256, "snapshot_sha256": sha(snapshot), "snapshot_bytes": FLASH_BYTES,
        "original_table_sha256": sha(original_table), "original_upper_sha256": sha(original_upper),
        "candidate_table_sha256": sha(table), "candidate_upper_sha256": sha(upper),
        "nvs_sha256": sha(snapshot[NVS_START:NVS_END]),
        "static_ranges": [{"name": name, "offset": offset, "bytes": size,
                           "sha256": sha(snapshot[offset:offset + size])}
                          for name, offset, size in STATIC_GEOMETRY],
        "write_ranges": [{"name": "upper", "offset": UPPER_START, "bytes": UPPER_BYTES},
                         {"name": "table", "offset": TABLE_START, "bytes": TABLE_BYTES}],
    }, original_table, original_upper, table, upper)
    if authorization is not None:
        plan.metadata["authorization"] = authorization
    plan.validate()
    return plan


def persist_plan(folder: Path, plan: MigrationPlan) -> None:
    plan.validate()
    if plan.candidate_upper is None:
        raise ValueError("execution requires candidate bytes")
    folder.mkdir(parents=False, exist_ok=False)
    for name, raw in ((RECOVERY_FILES["table"], plan.original_table),
                      (RECOVERY_FILES["upper"], plan.original_upper),
                      (CANDIDATE_FILES["table"], plan.candidate_table),
                      (CANDIDATE_FILES["upper"], plan.candidate_upper)):
        write_new(folder / name, raw)
    write_new(folder / "plan.json", canonical({"plan_id": plan.plan_id, "metadata": plan.metadata}) + b"\n")


def load_plan(folder: Path, *, execution: bool = False) -> MigrationPlan:
    value = json.loads(read_file(folder, "plan.json", 16384))
    if not isinstance(value, dict) or set(value) != {"plan_id", "metadata"}:
        raise ValueError("invalid persisted plan")
    plan = MigrationPlan(value["metadata"], read_file(folder, RECOVERY_FILES["table"], TABLE_BYTES),
                         read_file(folder, RECOVERY_FILES["upper"], UPPER_BYTES),
                         read_file(folder, CANDIDATE_FILES["table"], TABLE_BYTES) if execution else None,
                         read_file(folder, CANDIDATE_FILES["upper"], UPPER_BYTES) if execution else None)
    plan.validate()
    if plan.plan_id != value["plan_id"]:
        raise ValueError("persisted plan identity changed")
    return plan


class Journal:
    def __init__(self, folder: Path, plan: MigrationPlan):
        self.folder, self.plan = folder, plan
        self.path = folder / "recovery.json"

    def load(self) -> dict | None:
        if not self.path.exists() and not self.path.is_symlink():
            return None
        value = json.loads(read_file(self.folder, "recovery.json", 32768))
        fields = {"schema", "mode", "plan_id", "sequence", "phase", "write_intent", "details", "state_sha256"}
        if (not isinstance(value, dict) or set(value) != fields
                or value["schema"] != "deskbot.upper_migration.journal.v1"
                or value["mode"] != self.plan.metadata["mode"] or value["plan_id"] != self.plan.plan_id
                or type(value["sequence"]) is not int or value["sequence"] < 1
                or value["phase"] not in PHASES or type(value["write_intent"]) is not bool
                or value["write_intent"] is not (value["phase"] != "prepared")
                or not isinstance(value["details"], dict)
                or value["state_sha256"] != sha(canonical({k: v for k, v in value.items() if k != "state_sha256"}))):
            raise ValueError("invalid or mismatched recovery journal")
        return value

    def save(self, phase: str, **details) -> None:
        if phase not in PHASES:
            raise ValueError("unknown journal phase")
        old = self.load()
        if old is not None and phase == "prepared":
            raise ValueError("a recorded run cannot be prepared again")
        value = {"schema": "deskbot.upper_migration.journal.v1", "mode": self.plan.metadata["mode"],
                 "plan_id": self.plan.plan_id, "sequence": old["sequence"] + 1 if old else 1,
                 "phase": phase, "write_intent": phase != "prepared", "details": details}
        value["state_sha256"] = sha(canonical(value))
        temporary = self.folder / (".journal-" + uuid.uuid4().hex + ".tmp")
        try:
            write_new(temporary, canonical(value) + b"\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class Device(Protocol):
    simulation_only: bool
    def identity(self) -> str: ...
    def read_flash(self, offset: int, size: int) -> bytes: ...
    def write_flash(self, offset: int, raw: bytes) -> None: ...
    def read_full(self) -> bytes: ...
    def boot(self, target: str) -> bool: ...


class RecoveryRequired(RuntimeError):
    pass


def check_device(plan: MigrationPlan, device: Device) -> None:
    if plan.metadata["mode"] == MODE and getattr(device, "simulation_only", None) is not True:
        raise ValueError("this stage accepts simulation adapters only")
    if plan.metadata["mode"] == LIVE_MODE and (
            getattr(device, "simulation_only", None) is not False
            or getattr(device, "hardware_migration", None) is not True):
        raise ValueError("hardware plans require the explicit hardware migration adapter")
    if device.identity() != plan.metadata["identity_sha256"]:
        raise ValueError("device identity differs from the persisted plan")


def static_check(plan: MigrationPlan, raw: bytes) -> dict:
    if len(raw) != FLASH_BYTES:
        raise ValueError("complete 32 MiB readback required")
    for row in plan.metadata["static_ranges"]:
        if sha(raw[row["offset"]:row["offset"] + row["bytes"]]) != row["sha256"]:
            raise ValueError("static protected range changed: " + row["name"])
    latest = sha(raw[NVS_START:NVS_END])
    return {"before_sha256": plan.metadata["nvs_sha256"], "after_sha256": latest,
            "changed": latest != plan.metadata["nvs_sha256"]}


def check_ranges(plan: MigrationPlan, device: Device, table: bytes, upper: bytes) -> None:
    check_device(plan, device)
    if device.read_flash(TABLE_START, TABLE_BYTES) != table:
        raise ValueError("partition-table sector readback differs")
    check_device(plan, device)
    if device.read_flash(UPPER_START, UPPER_BYTES) != upper:
        raise ValueError("upper resource readback differs")


def result(plan: MigrationPlan, phase: str, **details) -> dict:
    live = plan.metadata["mode"] == LIVE_MODE
    return {"schema": "deskbot.upper_migration.result.v1", "mode": plan.metadata["mode"], "plan_id": plan.plan_id,
            "result": phase.removesuffix("_simulation") if live else phase,
            "device_written": live and phase not in {"failed_before_write", "no_persisted_write_intent"},
            "physical_interaction_accepted": False, **details}


def record_failure(journal: Journal, error: Exception) -> None:
    # A failed disk write may prevent this update. The previous durable intent
    # remains the recovery entry point in that case.
    try:
        journal.save("needs_recovery", error=(type(error).__name__ + ": " + str(error))[:512])
    except OSError:
        pass


def _recover(plan: MigrationPlan, device: Device, journal: Journal) -> dict:
    state = journal.load()
    if state is None or not state["write_intent"]:
        return result(plan, "no_persisted_write_intent", restored=False)
    try:
        check_device(plan, device)
        journal.save("restore_table_intent")
        if device.read_flash(TABLE_START, TABLE_BYTES) != plan.original_table:
            check_device(plan, device)
            device.write_flash(TABLE_START, plan.original_table)
        if device.read_flash(TABLE_START, TABLE_BYTES) != plan.original_table:
            raise ValueError("original table recovery readback differs")
        journal.save("restore_table_verified")
        check_device(plan, device)
        journal.save("restore_upper_intent")
        if device.read_flash(UPPER_START, UPPER_BYTES) != plan.original_upper:
            check_device(plan, device)
            device.write_flash(UPPER_START, plan.original_upper)
        check_ranges(plan, device, plan.original_table, plan.original_upper)
        check_device(plan, device)
        nvs = static_check(plan, device.read_full())
        journal.save("restore_verified", nvs=nvs)
        check_device(plan, device)
        if device.boot("original") is not True:
            raise ValueError("original boot was not observed")
        journal.save("restored", nvs=nvs, boot_observed=True)
        return result(plan, "restored_simulation", restored=True, nvs=nvs)
    except Exception as error:
        record_failure(journal, error)
        raise RecoveryRequired("recovery remains pending: " + str(error)) from error


def recover(folder: Path, device: Device) -> dict:
    # Candidate images, the old full snapshot and build tools are not read.
    with operation_lock(folder):
        plan = load_plan(folder)
        return _recover(plan, device, Journal(folder, plan))


def execute(folder: Path, device: Device, *, initial_snapshot: bytes) -> dict:
    with operation_lock(folder):
        plan = load_plan(folder, execution=True)
        journal = Journal(folder, plan)
        if journal.load() is not None:
            raise ValueError("a migration run cannot be replayed; use recovery")
        if len(initial_snapshot) != FLASH_BYTES or sha(initial_snapshot) != plan.metadata["snapshot_sha256"]:
            raise ValueError("same-execution full snapshot differs")
        check_device(plan, device)
        if device.read_full() != initial_snapshot:
            raise ValueError("device changed after the snapshot")
        journal.save("prepared")
        try:
            check_device(plan, device)
            journal.save("upper_write_intent")
            check_device(plan, device)
            device.write_flash(UPPER_START, plan.candidate_upper)
            if device.read_flash(UPPER_START, UPPER_BYTES) != plan.candidate_upper:
                raise ValueError("upper resource readback differs before table commit")
            journal.save("upper_verified")
            check_device(plan, device)
            # Recheck immediately before committing the table, even after a
            # journal or adapter callback between the two operations.
            check_ranges(plan, device, plan.original_table, plan.candidate_upper)
            static_check(plan, device.read_full())
            journal.save("table_write_intent")
            check_device(plan, device)
            device.write_flash(TABLE_START, plan.candidate_table)
            check_ranges(plan, device, plan.candidate_table, plan.candidate_upper)
            nvs = static_check(plan, device.read_full())
            journal.save("candidate_verified", nvs=nvs)
            check_device(plan, device)
            if device.boot("candidate") is not True:
                raise ValueError("candidate boot was not observed")
            journal.save("awaiting_interaction", nvs=nvs, boot_observed=True)
            return result(plan, "candidate_committed_simulation", restored=False, nvs=nvs,
                          interaction_status="pending")
        except Exception as error:
            state = journal.load()
            if state is None or not state["write_intent"]:
                return result(plan, "failed_before_write", restored=False, error=str(error))
            recovery = _recover(plan, device, journal)
            return result(plan, "failed_and_restored_simulation", restored=True,
                          error=type(error).__name__ + ": " + str(error), recovery=recovery)
