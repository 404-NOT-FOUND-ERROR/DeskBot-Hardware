"""E1.45 resource-only transaction with same-round, candidate-independent recovery."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat

from protocol.studio_app_deployment import Journal as BaseJournal, canonical, write_new, _unique
from protocol.studio_resource_package import TABLE_SHA, BASE_IMAGE_SHA, sha
from protocol.storage_continuous_mmap_e3c3b_baseline import APP_BYTES_EXPECTED, APP_SHA256_EXPECTED
from protocol.studio_e3d2b_deployment import APP_SIZE as E3D2B_APP_BYTES, APP_SHA as E3D2B_APP_SHA

START, SIZE, FLASH = 0x1000000, 0x800000, 0x2000000
NVS_START, NVS_END = 0x9000, 0xD000
CANDIDATE_SHA = "e5ac78a187866cff05a16e5fd223c934a45d6cb4bf13dfcd1766cc93db805748"
SCHEMA = "deskbot.resource_installation_plan/v1"
E3D2B_SCHEMA = "deskbot.resource_installation_plan.e3d2b/v1"
E3F4C_SCHEMA = "deskbot.resource_installation_plan.e3f4c/v1"
# Pin the accepted App here: importing its deployment module would create an
# eat-deployment -> resource-transaction -> layout-deployment import cycle.
E3F4C_APP_BYTES = 4521744
E3F4C_APP_SHA = "36a6d8518281a57b1f15389b8b54b41fb6cf09d5f772e761f91cd3c6a6ec233d"
BASELINES = {"E3c-3b": (APP_BYTES_EXPECTED, APP_SHA256_EXPECTED),
             "E3d-2b": (E3D2B_APP_BYTES, E3D2B_APP_SHA),
             "E3f-4c": (E3F4C_APP_BYTES, E3F4C_APP_SHA)}
SCHEMAS = {"E3c-3b": SCHEMA, "E3d-2b": E3D2B_SCHEMA, "E3f-4c": E3F4C_SCHEMA}
RECOVERABLE = {"write_intent", "candidate_verified", "installed", "restore_intent", "needs_recovery", "restored"}


class ResourcePreparationRecoveryError(ValueError):
    def __init__(self, stage, preparation_error, *, boot_result=None, boot_error=None):
        self.stage = stage
        self.preparation_error = preparation_error
        self.boot_result = boot_result
        self.boot_error = boot_error
        super().__init__(stage + "_original_app_boot_unconfirmed")


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _baseline_from_schema(schema):
    for baseline, expected in SCHEMAS.items():
        if schema == expected:
            return baseline
    raise ValueError("unsupported_resource_baseline")


def plan_baseline(plan):
    return _baseline_from_schema(plan.metadata.get("schema"))


def validate_snapshot(raw, *, baseline="E3c-3b"):
    if not isinstance(baseline, str) or baseline not in BASELINES:
        raise ValueError("unsupported_resource_baseline")
    if type(raw) is not bytes or len(raw) != FLASH:
        raise ValueError("complete_32m_snapshot_required")
    if sha(raw[0x8000:0x8C00]) != TABLE_SHA or raw[0x8C00:0x9000] != b"\xff" * 1024:
        raise ValueError("partition_table_changed")
    app_bytes, app_sha = BASELINES[baseline]
    if sha(raw[0x10000:0x10000 + app_bytes]) != app_sha:
        raise ValueError("accepted_" + baseline.lower().replace("-", "") + "_app_required")
    if sha(raw[START:START + SIZE]) != BASE_IMAGE_SHA:
        raise ValueError("accepted_e118_resources_required")


@dataclass(frozen=True)
class RecoveryPlan:
    metadata: dict
    snapshot: bytes

    @property
    def id(self):
        return sha(canonical(self.metadata))

    @property
    def rollback(self):
        return self.snapshot[START:START + SIZE]

    def validate(self):
        m = self.metadata
        baseline = _baseline_from_schema(m.get("schema") if type(m) is dict else None)
        keys = {"schema", "mode", "offset", "bytes", "identity_sha256", "snapshot_sha256", "rollback_sha256", "candidate_sha256"}
        if baseline != "E3c-3b":
            keys.add("baseline")
        if (type(m) is not dict or set(m) != keys
                or m["mode"] not in ("offline", "live") or type(m["offset"]) is not int
                or type(m["bytes"]) is not int or (m["offset"], m["bytes"]) != (START, SIZE)
                or (baseline != "E3c-3b" and m["baseline"] != baseline)
                or any(not _hash(m[k]) for k in keys if k.endswith("_sha256"))):
            raise ValueError("invalid_resource_plan")
        validate_snapshot(self.snapshot, baseline=baseline)
        if (sha(self.snapshot) != m["snapshot_sha256"] or sha(self.rollback) != m["rollback_sha256"]
                or m["candidate_sha256"] != CANDIDATE_SHA):
            raise ValueError("resource_plan_digest_changed")


@dataclass(frozen=True)
class Plan(RecoveryPlan):
    image: bytes

    def validate(self):
        super().validate()
        if type(self.image) is not bytes or len(self.image) != SIZE or sha(self.image) != CANDIDATE_SHA:
            raise ValueError("candidate_resource_changed")


def prepare(snapshot, image, identity, *, mode="offline", baseline="E3c-3b"):
    if not isinstance(baseline, str) or baseline not in BASELINES:
        raise ValueError("unsupported_resource_baseline")
    metadata = dict(schema=SCHEMAS[baseline],
                    mode=mode, offset=START, bytes=SIZE, identity_sha256=identity,
                    snapshot_sha256=sha(snapshot), rollback_sha256=sha(snapshot[START:START + SIZE]),
                    candidate_sha256=sha(image))
    if baseline != "E3c-3b":
        metadata["baseline"] = baseline
    plan = Plan(metadata, snapshot, image)
    plan.validate()
    return plan


def _safe(folder, name):
    path = Path(folder) / name
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("redirected_resource_plan_path")
    return path


class Journal(BaseJournal):
    def __init__(self, folder, plan):
        self.path, self.plan = _safe(folder, "resource-state.json"), plan

    def load(self):
        _safe(self.path.parent, self.path.name)
        value = super().load()
        if value and value["phase"] not in RECOVERABLE:
            raise ValueError("invalid_resource_journal_phase")
        return value

    def save(self, phase, **details):
        _safe(self.path.parent, self.path.name)
        if phase not in RECOVERABLE:
            raise ValueError("invalid_resource_journal_phase")
        return super().save(phase, **details)


def persist(folder, plan):
    plan.validate()
    for name, raw in (("snapshot-32m.bin", plan.snapshot), ("rollback-assets-8m.bin", plan.rollback),
                      ("candidate-assets-8m.bin", plan.image),
                      ("plan.json", canonical(dict(plan_id=plan.id, metadata=plan.metadata)))):
        write_new(_safe(folder, name), raw)


def load(folder, *, recovery=False):
    saved = json.loads(_safe(folder, "plan.json").read_bytes(), object_pairs_hook=_unique)
    if type(saved) is not dict or set(saved) != {"metadata", "plan_id"}:
        raise ValueError("invalid_saved_resource_plan")
    original = RecoveryPlan(saved["metadata"], _safe(folder, "snapshot-32m.bin").read_bytes())
    original.validate()
    if original.id != saved["plan_id"] or _safe(folder, "rollback-assets-8m.bin").read_bytes() != original.rollback:
        raise ValueError("saved_resource_backup_changed")
    if recovery:
        return original
    plan = Plan(original.metadata, original.snapshot, _safe(folder, "candidate-assets-8m.bin").read_bytes())
    plan.validate()
    return plan


def check(plan):
    plan.validate()
    return dict(result="prepared", mode=plan.metadata["mode"], plan_id=plan.id,
                offset=START, bytes=SIZE, rollback_sha256=sha(plan.rollback),
                candidate_sha256=plan.metadata["candidate_sha256"], device_accessed=False,
                hardware_acceptance="pending")


def compare_outside(plan, raw, *, allow_runtime_nvs=False):
    if type(raw) is not bytes or len(raw) != FLASH:
        raise ValueError("full_flash_readback_required")
    a, b = raw, plan.snapshot
    same = (a[:NVS_START] == b[:NVS_START] and a[NVS_END:START] == b[NVS_END:START]
            and a[START + SIZE:] == b[START + SIZE:])
    if not same or not allow_runtime_nvs and a[NVS_START:NVS_END] != b[NVS_START:NVS_END]:
        raise ValueError("outside_resource_bytes_changed")


def _identity(plan, device):
    if device.identity() != plan.metadata["identity_sha256"]:
        raise ValueError("resource_device_identity_changed")


def _boot_after_prewrite_failure(device, error, stage):
    try:
        result = device.boot()
    except BaseException as boot_error:
        raise ResourcePreparationRecoveryError(stage, error, boot_error=boot_error) from error
    if not result:
        raise ResourcePreparationRecoveryError(stage, error, boot_result=False) from error


def recover(plan, device, journal):
    plan.validate()
    if plan.metadata["mode"] == "offline" and not getattr(device, "simulation_only", False):
        raise ValueError("offline_resource_plan_is_simulation_only")
    state = journal.load()
    if not state or state["phase"] not in RECOVERABLE:
        raise ValueError("resource_recovery_state_required")
    try:
        _identity(plan, device)
        before = device.read_full()
        compare_outside(plan, before, allow_runtime_nvs=True)
        # The App may have updated NVS after boot. Preserve those current bytes.
        journal.save("restore_intent", current_nvs_sha256=sha(before[NVS_START:NVS_END]))
        device.write_assets(plan.rollback)
        after = device.read_full()
        expected = before[:START] + plan.rollback + before[START + SIZE:]
        if after != expected:
            raise ValueError("resource_restore_readback_changed")
        if not device.boot():
            raise ValueError("original_boot_incomplete")
        journal.save("restored", readback_sha256=sha(after))
        return dict(result="original_resources_restored", readback_sha256=sha(after))
    except BaseException as error:
        journal.save("needs_recovery", error_type=type(error).__name__)
        raise


def install(plan, device, journal):
    if not isinstance(plan, Plan):
        raise ValueError("candidate_plan_required")
    plan.validate()
    if plan.metadata["mode"] == "offline" and not getattr(device, "simulation_only", False):
        raise ValueError("offline_resource_plan_is_simulation_only")
    if journal.load() is not None:
        raise ValueError("existing_transaction_use_recovery")
    try:
        _identity(plan, device)
        # No normal App runs between this capture and the resource write.
        if device.read_full() != plan.snapshot:
            raise ValueError("snapshot_changed_prepare_again")
    except BaseException as error:
        _boot_after_prewrite_failure(device, error, "resource_precheck_failed")
        raise
    # Persist intent before any write. If persistence fails, Flash is unchanged.
    try:
        journal.save("write_intent")
    except BaseException as error:
        _boot_after_prewrite_failure(device, error, "write_intent_save_failed")
        raise
    try:
        device.write_assets(plan.image)
        after = device.read_full()
        expected = plan.snapshot[:START] + plan.image + plan.snapshot[START + SIZE:]
        if after != expected:
            raise ValueError("candidate_readback_changed")
        journal.save("candidate_verified", readback_sha256=sha(after))
        if not device.boot():
            raise ValueError("candidate_boot_incomplete")
        journal.save("installed", readback_sha256=sha(after), hardware_acceptance="pending")
        return dict(result="candidate_resources_installed", readback_sha256=sha(after), hardware_acceptance="pending")
    except BaseException:
        recover(plan, device, journal)
        raise
