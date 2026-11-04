"""Offline-testable transaction rules for the E1.43a single-page experiment."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from protocol.storage_upper_pattern import PAGE_BYTES, PAGE_START, SECTOR_BYTES, validate_pattern

APP_OFFSET = 0x10000
APP_PARTITION_BYTES = 0x5DC000
FLASH_BYTES = 32 * 1024 * 1024
PROTECTED_NAMES = frozenset({
    "bootloader_reserved", "partition_table", "nvs", "otadata", "phy_init",
    "gap_after_app", "assets", "model", "gap_after_model", "upper_outside_test_page",
})
STATIC_PROTECTED_NAMES = PROTECTED_NAMES - {"nvs"}
JOURNAL_SCHEMA = "deskbot.upper_pattern.experiment_journal.v3"
BOUND_JOURNAL_SCHEMA = "deskbot.upper_pattern.experiment_journal.v4"
RECOVERY_PHASES = frozenset({
    "prepared", "restore_required", "pattern_write_started", "pattern_written",
    "app_write_started", "app_written", "probe_started", "probe_finished",
    "restore_started", "restore_verified",
})
JOURNAL_PHASES = RECOVERY_PHASES | {"complete"}
JOURNAL_FIELDS = frozenset({
    "schema", "plan_id", "phase", "sequence", "static_protected_sha256",
    "nvs_observation", "error", "probe_passed",
})
BOUND_JOURNAL_FIELDS = JOURNAL_FIELDS | {"snapshot_manifest_sha256"}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def align_up(value: int, alignment: int) -> int:
    if type(value) is not int or type(alignment) is not int or value <= 0 or alignment <= 0:
        raise ValueError("positive integer geometry required")
    return (value + alignment - 1) // alignment * alignment


def _validate_hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(name + " must be a lowercase SHA-256")
    return value


@dataclass(frozen=True)
class DeviceSnapshot:
    identity_sha256: str
    chip: str
    physical_flash_bytes: int
    psram_bytes: int
    secure_boot: bool
    flash_encryption: bool
    app_erase_bytes: bytes
    upper_page: bytes
    protected_sha256: dict[str, str]
    runtime_low_page: bytes | None = None
    runtime_boundary_lower: bytes | None = None


@dataclass(frozen=True)
class ExperimentPlan:
    expected_identity_sha256: str
    rollback_app: bytes
    rollback_page: bytes
    candidate_app: bytes
    pattern: bytes
    expected_static_protected_sha256: dict[str, str]

    @property
    def app_erase_bytes(self) -> int:
        return align_up(max(len(self.rollback_app), len(self.candidate_app)), SECTOR_BYTES)

    @property
    def plan_id(self) -> str:
        material = {
            "schema": JOURNAL_SCHEMA,
            "identity_sha256": self.expected_identity_sha256,
            "rollback_app_sha256": sha(self.rollback_app),
            "rollback_page_sha256": sha(self.rollback_page),
            "candidate_app_sha256": sha(self.candidate_app),
            "pattern_sha256": sha(self.pattern),
            "static_protected_sha256": {
                name: self.expected_static_protected_sha256[name]
                for name in sorted(self.expected_static_protected_sha256)
            },
        }
        return sha(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii"))

    def validate(self) -> None:
        _validate_hash(self.expected_identity_sha256, "expected device identity")
        if (not isinstance(self.expected_static_protected_sha256, dict)
                or self.expected_static_protected_sha256.keys() != STATIC_PROTECTED_NAMES):
            raise ValueError("the exact static protected-range set is required")
        for name, digest in self.expected_static_protected_sha256.items():
            _validate_hash(digest, "protected range " + name)
        if (not isinstance(self.rollback_app, bytes) or not isinstance(self.candidate_app, bytes)
                or not self.rollback_app or not self.candidate_app):
            raise ValueError("App rollback and candidate bytes are required")
        if (len(self.rollback_app) % SECTOR_BYTES or len(self.rollback_app) != self.app_erase_bytes
                or self.app_erase_bytes > APP_PARTITION_BYTES):
            raise ValueError("App rollback must cover the aligned erase-span union")
        if not isinstance(self.rollback_page, bytes) or len(self.rollback_page) != PAGE_BYTES:
            raise ValueError("exact 64 KiB page rollback is required")
        validate_pattern(self.pattern)
        if len(self.candidate_app) > APP_PARTITION_BYTES:
            raise ValueError("diagnostic App exceeds the accepted App partition")


class Device(Protocol):
    def identity_matches(self, expected_sha256: str) -> bool: ...
    def snapshot(self, plan: ExperimentPlan) -> DeviceSnapshot: ...
    def write_flash(self, offset: int, data: bytes) -> None: ...
    def read_flash(self, offset: int, size: int) -> bytes: ...
    def capture_probe(self, timeout_seconds: int) -> str: ...
    def boot_original(self) -> bool: ...


class Journal(Protocol):
    def load(self) -> dict[str, Any] | None: ...
    def save(self, state: dict[str, Any]) -> None: ...


class MemoryJournal:
    """Small deterministic journal used by offline tests."""

    def __init__(self) -> None:
        self.state: dict[str, Any] | None = None

    def load(self) -> dict[str, Any] | None:
        return None if self.state is None else copy.deepcopy(self.state)

    def save(self, state: dict[str, Any]) -> None:
        _validate_journal_state(state)
        self.state = copy.deepcopy(state)


def _validate_nvs_observation(value: Any) -> None:
    if (not isinstance(value, dict)
            or value.keys() != {"before_sha256", "latest_sha256", "changed"}
            or type(value.get("changed")) is not bool):
        raise ValueError("invalid NVS observation")
    _validate_hash(value["before_sha256"], "initial NVS observation")
    _validate_hash(value["latest_sha256"], "latest NVS observation")
    if value["changed"] is not (value["before_sha256"] != value["latest_sha256"]):
        raise ValueError("NVS observation change flag is inconsistent")


def _validate_journal_state(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("invalid experiment recovery journal")
    bound = value.get("schema") == BOUND_JOURNAL_SCHEMA
    if (value.keys() != (BOUND_JOURNAL_FIELDS if bound else JOURNAL_FIELDS)
            or value.get("schema") not in (JOURNAL_SCHEMA, BOUND_JOURNAL_SCHEMA)
            or value.get("phase") not in JOURNAL_PHASES
            or not isinstance(value.get("plan_id"), str)
            or re.fullmatch(r"[0-9a-f]{64}", value["plan_id"]) is None
            or type(value.get("sequence")) is not int or value["sequence"] < 1):
        raise ValueError("invalid experiment recovery journal")
    if bound:
        _validate_hash(value["snapshot_manifest_sha256"], "snapshot manifest binding")
    protected = value.get("static_protected_sha256")
    if not isinstance(protected, dict) or protected.keys() != STATIC_PROTECTED_NAMES:
        raise ValueError("invalid static protected-range journal summary")
    for name, digest in protected.items():
        _validate_hash(digest, "protected range " + name)
    _validate_nvs_observation(value.get("nvs_observation"))
    if (value.get("error") is not None
            and (not isinstance(value["error"], str) or len(value["error"]) > 512)):
        raise ValueError("invalid journal error")
    if value.get("probe_passed") is not None and type(value["probe_passed"]) is not bool:
        raise ValueError("invalid probe result")


class AtomicJournal:
    """Persist recovery intent before writes and retain it until verified boot."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        if self.path.is_symlink():
            raise ValueError("journal path must not be a symlink")
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        if len(raw) > 16 * 1024:
            raise ValueError("journal exceeds its size limit")
        value = json.loads(raw.decode("utf-8"))
        _validate_journal_state(value)
        return value

    def save(self, state: dict[str, Any]) -> None:
        _validate_journal_state(state)
        if self.path.is_symlink():
            raise ValueError("journal path must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".pending")
        if temporary.is_symlink():
            raise ValueError("journal temporary path must not be a symlink")
        payload = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > 16 * 1024:
            raise ValueError("journal exceeds its size limit")
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)


@dataclass(frozen=True)
class ExperimentResult:
    probe_passed: bool
    restored: bool
    phase: str
    error: str | None
    journal_finalized: bool = True


class RecoveryRequired(RuntimeError):
    pass


def validate_snapshot(plan: ExperimentPlan, snapshot: DeviceSnapshot) -> dict[str, str]:
    if not isinstance(snapshot, DeviceSnapshot):
        raise ValueError("device preflight snapshot is missing")
    if (_validate_hash(snapshot.identity_sha256, "observed device identity") != plan.expected_identity_sha256
            or snapshot.chip != "esp32s3" or snapshot.physical_flash_bytes != FLASH_BYTES
            or snapshot.psram_bytes != 16 * 1024 * 1024 or snapshot.secure_boot is not False
            or snapshot.flash_encryption is not False):
        raise ValueError("device identity/security/capacity mismatch")
    if snapshot.app_erase_bytes != plan.rollback_app or snapshot.upper_page != plan.rollback_page:
        raise ValueError("fresh App/page bytes differ from the exact rollback inputs")
    values = snapshot.protected_sha256
    if not isinstance(values, dict) or values.keys() != PROTECTED_NAMES:
        raise ValueError("all protected ranges including live NVS must be fingerprinted")
    for name, digest in values.items():
        _validate_hash(digest, "protected range " + name)
    if any(values[name] != digest for name, digest in plan.expected_static_protected_sha256.items()):
        raise ValueError("a static protected range differs from its reviewed baseline")
    return dict(values)


def _record(journal: Journal, plan: ExperimentPlan, phase: str, protected: dict[str, str],
            *, error: str | None = None, probe_passed: bool | None = None,
            latest_nvs_sha256: str | None = None,
            snapshot_manifest_sha256: str | None = None) -> None:
    if not isinstance(protected, dict) or protected.keys() != PROTECTED_NAMES:
        raise ValueError("complete initial protected-range snapshot is required")
    for name, digest in protected.items():
        _validate_hash(digest, "protected range " + name)
    old = journal.load()
    if snapshot_manifest_sha256 is not None:
        _validate_hash(snapshot_manifest_sha256, "snapshot manifest binding")
    if old is not None:
        _validate_journal_state(old)
        if old["plan_id"] != plan.plan_id:
            raise RecoveryRequired("journal belongs to another experiment plan")
        binding = old.get("snapshot_manifest_sha256")
        if snapshot_manifest_sha256 is not None and snapshot_manifest_sha256 != binding:
            raise RecoveryRequired("snapshot manifest binding cannot change within a journal")
    else:
        binding = snapshot_manifest_sha256
    sequence = 1 if old is None else old["sequence"] + 1
    if sequence > (1 << 63) - 1:
        raise ValueError("journal sequence limit reached")
    before_nvs = protected["nvs"]
    previous_nvs = None if old is None else old["nvs_observation"]
    latest_nvs = latest_nvs_sha256 or (
        previous_nvs["latest_sha256"] if previous_nvs is not None else before_nvs
    )
    _validate_hash(latest_nvs, "latest NVS observation")
    previous_probe = None if old is None else old["probe_passed"]
    state = {"schema": BOUND_JOURNAL_SCHEMA if binding is not None else JOURNAL_SCHEMA,
                  "plan_id": plan.plan_id, "phase": phase,
                  "sequence": sequence,
                  "static_protected_sha256": {name: protected[name] for name in STATIC_PROTECTED_NAMES},
                  "nvs_observation": {"before_sha256": before_nvs, "latest_sha256": latest_nvs,
                                      "changed": before_nvs != latest_nvs},
                  "error": error, "probe_passed": previous_probe if probe_passed is None else probe_passed}
    if binding is not None:
        state["snapshot_manifest_sha256"] = binding
    journal.save(state)


def _assert_identity(device: Device, plan: ExperimentPlan) -> None:
    if device.identity_matches(plan.expected_identity_sha256) is not True:
        raise ValueError("refuse Flash operation: target identity is not freshly confirmed")


def restore(plan: ExperimentPlan, device: Device, journal: Journal, protected: dict[str, str]) -> bool:
    try:
        _record(journal, plan, "restore_started", protected)
    except Exception:
        pass
    for offset, data in ((PAGE_START, plan.rollback_page), (APP_OFFSET, plan.rollback_app)):
        _assert_identity(device, plan)
        device.write_flash(offset, data)
        actual = device.read_flash(offset, len(data))
        if actual != data:
            raise RecoveryRequired("restored Flash range failed byte-for-byte readback")
    snapshot = device.snapshot(plan)
    observed = validate_snapshot(plan, snapshot)
    if any(observed[name] != protected[name] for name in STATIC_PROTECTED_NAMES):
        raise RecoveryRequired("a static protected range changed during the experiment or restore")
    try:
        _record(journal, plan, "restore_verified", protected, latest_nvs_sha256=observed["nvs"])
    except Exception:
        pass
    _assert_identity(device, plan)
    if device.boot_original() is not True:
        raise RecoveryRequired("original App bytes restored, but normal boot was not confirmed")
    try:
        _record(journal, plan, "complete", protected, latest_nvs_sha256=observed["nvs"])
    except Exception:
        return False
    return True


def run_experiment(plan: ExperimentPlan, device: Device, journal: Journal,
                   probe_validator=None, *, probe_validator_factory=None,
                   timeout_seconds: int = 120,
                   initial_snapshot: DeviceSnapshot | None = None,
                   snapshot_manifest_sha256: str | None = None) -> ExperimentResult:
    plan.validate()
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
        raise ValueError("probe wait must be bounded to 1..120 seconds")
    if (callable(probe_validator) == callable(probe_validator_factory)
            or (probe_validator is not None and not callable(probe_validator))
            or (probe_validator_factory is not None and not callable(probe_validator_factory))):
        raise ValueError("provide exactly one probe validator or snapshot-bound validator factory")
    if snapshot_manifest_sha256 is not None:
        _validate_hash(snapshot_manifest_sha256, "snapshot manifest binding")
        if initial_snapshot is None or not callable(probe_validator_factory):
            raise ValueError("bound journal requires the saved initial snapshot and validator factory")
    existing = journal.load()
    if existing is not None:
        raise RecoveryRequired("a journal already exists; recover it or use a new run directory")
    _assert_identity(device, plan)
    # The entrypoint may persist this exact write-time snapshot before handing
    # it to the controller. Legacy callers still acquire their snapshot here.
    snapshot = device.snapshot(plan) if initial_snapshot is None else initial_snapshot
    protected = validate_snapshot(plan, snapshot)
    validator = (probe_validator_factory(snapshot) if probe_validator_factory is not None
                 else probe_validator)
    if not callable(validator):
        raise ValueError("snapshot-bound probe validator factory must return a callable")
    _record(journal, plan, "prepared", protected, snapshot_manifest_sha256=snapshot_manifest_sha256)
    # Do not allow the first Flash write unless the durable journal says recovery is required.
    _record(journal, plan, "restore_required", protected)

    try:
        _assert_identity(device, plan)
        _record(journal, plan, "pattern_write_started", protected)
        device.write_flash(PAGE_START, plan.pattern)
        if device.read_flash(PAGE_START, PAGE_BYTES) != plan.pattern:
            raise ValueError("test pattern failed immediate full-page readback")
        _record(journal, plan, "pattern_written", protected)

        _assert_identity(device, plan)
        _record(journal, plan, "app_write_started", protected)
        device.write_flash(APP_OFFSET, plan.candidate_app)
        if device.read_flash(APP_OFFSET, len(plan.candidate_app)) != plan.candidate_app:
            raise ValueError("diagnostic App failed immediate payload readback")
        _record(journal, plan, "app_written", protected)

        _assert_identity(device, plan)
        _record(journal, plan, "probe_started", protected)
        log = device.capture_probe(timeout_seconds)
        probe_passed = validator(log) is True
        _record(journal, plan, "probe_finished", protected, probe_passed=probe_passed)
        journal_finalized = restore(plan, device, journal, protected)
        phase = "probe_passed_and_restored" if probe_passed else "probe_failed_but_restored"
        if not journal_finalized:
            phase = "restored_but_journal_pending"
        error_text = None if journal_finalized else "Flash was restored and booted, but the journal could not be finalized."
        return ExperimentResult(probe_passed, True, phase, error_text, journal_finalized)
    except BaseException as error:
        try:
            _assert_identity(device, plan)
            journal_finalized = restore(plan, device, journal, protected)
        except BaseException as restore_error:
            journal_error = None
            try:
                _record(journal, plan, "restore_required", protected,
                        error=type(restore_error).__name__ + ": " + str(restore_error)[:300])
            except Exception as record_error:
                journal_error = "; recovery journal update failed: " + str(record_error)[:180]
            message = "automatic restore did not complete; inspect the retained journal and retry recovery"
            if journal_error:
                message += journal_error
            raise RecoveryRequired(message) from restore_error
        if not isinstance(error, Exception):
            raise
        message = type(error).__name__ + ": " + str(error)[:300]
        phase = "operation_failed_but_restored"
        if not journal_finalized:
            phase = "restored_but_journal_pending"
            message += "; Flash was restored and booted, but the journal could not be finalized"
        return ExperimentResult(False, True, phase, message, journal_finalized)


def recover(plan: ExperimentPlan, device: Device, journal: Journal) -> None:
    plan.validate()
    state = journal.load()
    if state is None:
        raise ValueError("no recovery journal exists")
    _validate_journal_state(state)
    if state.get("plan_id") != plan.plan_id:
        raise RecoveryRequired("journal belongs to another experiment plan")
    if state.get("phase") == "complete":
        try:
            _assert_identity(device, plan)
            observed = validate_snapshot(plan, device.snapshot(plan))
            if any(observed[name] != state["static_protected_sha256"][name]
                   for name in STATIC_PROTECTED_NAMES):
                raise ValueError("static protected ranges differ from the completed journal")
            if (device.read_flash(PAGE_START, PAGE_BYTES) != plan.rollback_page
                    or device.read_flash(APP_OFFSET, len(plan.rollback_app)) != plan.rollback_app):
                raise ValueError("App or test page differs from its exact rollback bytes")
        except Exception as error:
            raise RecoveryRequired(
                "completed journal does not match the current verified rollback state; "
                "stop and inspect the device before writing"
            ) from error
        _assert_identity(device, plan)
        if device.boot_original() is not True:
            raise RecoveryRequired("completed rollback bytes verified, but normal boot was not confirmed")
        return
    if state.get("phase") not in RECOVERY_PHASES:
        raise RecoveryRequired("journal phase does not authorize recovery")
    static = state["static_protected_sha256"]
    if static != plan.expected_static_protected_sha256:
        raise RecoveryRequired("journal static protected summaries differ from this plan")
    if state["phase"] == "prepared":
        _assert_identity(device, plan)
        observed = validate_snapshot(plan, device.snapshot(plan))
        if any(observed[name] != static[name] for name in STATIC_PROTECTED_NAMES):
            raise RecoveryRequired("prepared snapshot no longer matches its static protection baseline")
        if device.boot_original() is not True:
            raise RecoveryRequired("prepared state verified, but normal boot was not confirmed")
        _record(journal, plan, "complete", observed, latest_nvs_sha256=observed["nvs"])
        return
    protected = dict(static)
    protected["nvs"] = state["nvs_observation"]["before_sha256"]
    _assert_identity(device, plan)
    if not restore(plan, device, journal, protected):
        raise RecoveryRequired("Flash was restored and booted, but the journal still needs finalization")
