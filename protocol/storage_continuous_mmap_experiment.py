"""App-only E1.43b transaction and persisted recovery, independent of hardware IO."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Protocol
import uuid

from protocol.storage_continuous_mmap import START, SIZE, extract_records, reference_for, validate_capture

FLASH_BYTES = 32 * 1024 * 1024
APP_START = 0x10000
APP_BYTES = 0x5DC000
NVS_START, NVS_END = 0x9000, 0xD000
PHASES = {"prepared", "restore_required", "candidate_verified", "captured", "restoring", "restored", "needs_recovery"}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class AppPlan:
    metadata: dict
    rollback: bytes
    candidate: bytes | None = None

    @property
    def plan_id(self) -> str:
        return sha(canonical(self.metadata))

    def validate(self) -> None:
        m = self.metadata
        if m.get("schema") != "deskbot.continuous_mmap.app_plan.v1":
            raise ValueError("invalid App plan schema")
        if not re.fullmatch(r"e143b-[a-z0-9][a-z0-9-]{0,39}", m.get("run_label", "")):
            raise ValueError("invalid App run label")
        for key in ("identity_sha256", "snapshot_sha256", "rollback_sha256", "candidate_sha256", "padded_candidate_sha256", "nvs_sha256"):
            if re.fullmatch(r"[0-9a-f]{64}", m.get(key, "")) is None:
                raise ValueError("invalid plan digest: " + key)
        span, length = m.get("erase_bytes"), m.get("candidate_bytes")
        if (type(span) is not int or type(length) is not int or not 0 < length <= span <= APP_BYTES
                or span % 4096 or length > APP_BYTES - 1024 * 1024):
            raise ValueError("invalid App erase span or margin")
        if len(self.rollback) != span or sha(self.rollback) != m["rollback_sha256"]:
            raise ValueError("rollback App differs from persisted plan")
        expected = [("prefix", 0, NVS_START), ("ota_phy", NVS_END, APP_START - NVS_END),
                    ("after_app", APP_START + span, FLASH_BYTES - APP_START - span)]
        rows = m.get("static_ranges")
        if not isinstance(rows, list) or len(rows) != len(expected):
            raise ValueError("complete static coverage required")
        for row, (name, offset, size) in zip(rows, expected):
            if (set(row) != {"name", "offset", "bytes", "sha256"}
                    or (row["name"], row["offset"], row["bytes"]) != (name, offset, size)
                    or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None):
                raise ValueError("static range geometry changed")
        reference = m.get("reference", {})
        if (reference.get("identity_sha256") != m["identity_sha256"]
                or reference.get("run_label") != m["run_label"]
                or reference.get("source_sha256") != m["snapshot_sha256"]
                or reference.get("start") != START or reference.get("bytes") != SIZE):
            raise ValueError("content reference belongs to another run")
        if self.candidate is not None:
            if (len(self.candidate) != span or sha(self.candidate) != m["padded_candidate_sha256"]
                    or sha(self.candidate[:length]) != m["candidate_sha256"]
                    or self.candidate[length:] != b"\xff" * (span - length)):
                raise ValueError("candidate App differs from persisted plan")


def make_plan(snapshot: bytes, candidate: bytes, *, original_app_bytes: int,
              identity_sha256: str, run_label: str) -> AppPlan:
    if len(snapshot) != FLASH_BYTES or not candidate or not 0 < original_app_bytes <= APP_BYTES:
        raise ValueError("complete snapshot and App images required")
    span = (max(original_app_bytes, len(candidate)) + 4095) // 4096 * 4096
    if span > APP_BYTES:
        raise ValueError("App erase union exceeds partition")
    rollback = snapshot[APP_START:APP_START + span]
    padded = candidate + b"\xff" * (span - len(candidate))
    geometry = [("prefix", 0, NVS_START), ("ota_phy", NVS_END, APP_START - NVS_END),
                ("after_app", APP_START + span, FLASH_BYTES - APP_START - span)]
    snapshot_sha = sha(snapshot)
    plan = AppPlan({"schema": "deskbot.continuous_mmap.app_plan.v1", "run_label": run_label,
        "identity_sha256": identity_sha256, "snapshot_sha256": snapshot_sha,
        "erase_bytes": span, "rollback_sha256": sha(rollback), "candidate_bytes": len(candidate),
        "candidate_sha256": sha(candidate), "padded_candidate_sha256": sha(padded),
        "nvs_sha256": sha(snapshot[NVS_START:NVS_END]),
        "static_ranges": [{"name": name, "offset": offset, "bytes": size,
                           "sha256": sha(snapshot[offset:offset + size])} for name, offset, size in geometry],
        "reference": reference_for(snapshot[START:START + SIZE], identity_sha256=identity_sha256,
                                   run_label=run_label, source_sha256=snapshot_sha)}, rollback, padded)
    plan.validate()
    return plan


def persist_plan(folder: Path, plan: AppPlan) -> None:
    plan.validate()
    if plan.candidate is None:
        raise ValueError("execution candidate required")
    write_new(folder / "rollback-app.bin", plan.rollback)
    write_new(folder / "candidate-app-padded.bin", plan.candidate)
    write_new(folder / "plan.json", canonical({"plan_id": plan.plan_id, "metadata": plan.metadata}) + b"\n")


def load_plan(folder: Path, *, execution: bool = False) -> AppPlan:
    def read(name: str) -> bytes:
        path = folder / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing or unsafe recovery input: " + name)
        return path.read_bytes()
    saved = json.loads(read("plan.json"))
    plan = AppPlan(saved["metadata"], read("rollback-app.bin"),
                   read("candidate-app-padded.bin") if execution else None)
    plan.validate()
    if plan.plan_id != saved.get("plan_id"):
        raise ValueError("persisted plan digest differs")
    return plan


class Journal:
    def __init__(self, folder: Path, plan: AppPlan):
        self.path, self.plan = folder / "recovery.json", plan

    def load(self) -> dict | None:
        if self.path.is_symlink():
            raise ValueError("unsafe recovery journal")
        if not self.path.exists():
            return None
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if (state.get("schema") != "deskbot.continuous_mmap.recovery.v1"
                or state.get("plan_id") != self.plan.plan_id or state.get("phase") not in PHASES
                or type(state.get("sequence")) is not int or state["sequence"] < 1):
            raise ValueError("journal belongs to another plan or has invalid state")
        return state

    def save(self, phase: str, **details) -> None:
        if phase not in PHASES:
            raise ValueError("invalid recovery phase")
        old = self.load()
        state = {"schema": "deskbot.continuous_mmap.recovery.v1", "plan_id": self.plan.plan_id,
                 "sequence": (old["sequence"] if old else 0) + 1, "phase": phase, "details": details}
        temporary = self.path.with_name("recovery-" + uuid.uuid4().hex + ".tmp")
        try:
            write_new(temporary, canonical(state) + b"\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class Device(Protocol):
    def identity(self) -> str: ...
    def write_app(self, raw: bytes) -> None: ...
    def read_app(self, size: int) -> bytes: ...
    def read_full(self) -> bytes: ...
    def capture(self) -> str: ...
    def boot_original(self) -> bool: ...


def require_identity(plan: AppPlan, device: Device) -> None:
    if device.identity() != plan.metadata["identity_sha256"]:
        raise ValueError("fresh device identity differs from this plan")


def static_check(plan: AppPlan, snapshot: bytes) -> None:
    if len(snapshot) != FLASH_BYTES:
        raise ValueError("complete 32 MiB readback required")
    for row in plan.metadata["static_ranges"]:
        if sha(snapshot[row["offset"]:row["offset"] + row["bytes"]]) != row["sha256"]:
            raise ValueError("protected static range changed: " + row["name"])


def recover(plan: AppPlan, device: Device, journal: Journal) -> dict:
    plan.validate()
    saved = load_plan(journal.path.parent)
    if saved.plan_id != plan.plan_id:
        raise ValueError("durable rollback inputs differ")
    state = journal.load()
    if state is None or state["phase"] == "prepared":
        raise ValueError("no persisted first-write intent; recovery write is not needed")
    if state["phase"] == "restored":
        return state
    try:
        require_identity(plan, device)
        journal.save("restoring")
        device.write_app(plan.rollback)
        if device.read_app(len(plan.rollback)) != plan.rollback:
            raise ValueError("restored App readback differs")
        full = device.read_full()
        static_check(plan, full)
        nvs = {"before_sha256": plan.metadata["nvs_sha256"], "after_sha256": sha(full[NVS_START:NVS_END])}
        nvs["changed"] = nvs["before_sha256"] != nvs["after_sha256"]
        if not device.boot_original():
            raise ValueError("original App boot markers were not observed")
        journal.save("restored", nvs=nvs, full_readback_sha256=sha(full), normal_boot_observed=True)
        return journal.load()
    except BaseException as error:
        journal.save("needs_recovery", error=type(error).__name__ + ": " + str(error))
        raise


def execute(plan: AppPlan, device: Device, journal: Journal, *, initial_snapshot: bytes) -> dict:
    plan.validate()
    if plan.candidate is None or journal.load() is not None:
        raise ValueError("new execution requires candidate and a fresh journal")
    if (load_plan(journal.path.parent, execution=True).plan_id != plan.plan_id
            or sha(initial_snapshot) != plan.metadata["snapshot_sha256"]):
        raise ValueError("durable inputs or same-run snapshot differ")
    static_check(plan, initial_snapshot)
    if initial_snapshot[APP_START:APP_START + len(plan.rollback)] != plan.rollback:
        raise ValueError("same-run App rollback differs")
    require_identity(plan, device)
    journal.save("prepared")
    checked, error, unchanged_boot = None, None, None
    try:
        journal.save("restore_required")
        device.write_app(plan.candidate)
        if device.read_app(len(plan.candidate)) != plan.candidate:
            raise ValueError("candidate App readback differs")
        journal.save("candidate_verified")
        log = device.capture()
        if len(log.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("runtime log exceeds two MiB")
        if any(marker in log for marker in ("Guru Meditation", "assert failed", "abort() was called")):
            raise ValueError("runtime log contains a crash")
        checked = validate_capture(extract_records(log), plan.metadata["reference"])
        journal.save("captured", probe_passed=checked.get("probe_passed") is True)
    except BaseException as caught:
        error = type(caught).__name__ + ": " + str(caught)
    finally:
        state = journal.load()
        if state is not None and state["phase"] != "prepared":
            recover(plan, device, journal)
        elif state is not None:
            unchanged_boot = device.boot_original()
    return {"schema": "deskbot.continuous_mmap.live_result.v1", "plan_id": plan.plan_id,
            "probe_passed": error is None and checked is not None and checked.get("probe_passed") is True,
            "runtime_validation": checked, "error": error, "restored": journal.load()["phase"] == "restored",
            "unchanged_original_boot_observed": unchanged_boot,
            "recovery": journal.load(), "physical_interaction_accepted": False}
