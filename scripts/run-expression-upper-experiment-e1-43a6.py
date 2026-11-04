#!/usr/bin/env python3
"""Run E1.43a-7 with same-run references; retain E1.43a-6 recovery."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_upper_experiment import (  # noqa: E402
    AtomicJournal,
    ExperimentPlan,
    RecoveryRequired,
    run_experiment,
    validate_snapshot,
    recover,
)
from protocol.storage_upper_experiment_esptool import (  # noqa: E402
    DEVICE_PORT,
    EsptoolDevice,
    PROTECTED_RANGES,
    load_persisted_plan,
    parse_identity,
    persist_plan_inputs,
    sha,
)
from protocol.storage_upper_pattern import (  # noqa: E402
    PAGE_BYTES,
    PAGE_START,
    build_pattern,
    validate_manifest,
)
from protocol.storage_upper_snapshot import load_snapshot, persist_snapshot

BACKUP_ROOT = ROOT / "tmp/e143a-single-page-20261102"
CAPTURE_DIR = BACKUP_ROOT / "capture"
BACKUP_REPORT = ROOT / "artifacts/expression-upper-backup-e1-43a3-20261102/report.json"
BUILD_REPORT = ROOT / "artifacts/expression-upper-snapshot-e1-43a7-20261103/final-build-report.json"
PATTERN_MANIFEST = ROOT / "artifacts/expression-upper-pattern-e1-43a-20261102/pattern-manifest.json"
PATTERN_PATH = ROOT / "tmp/e143a-upper-pattern-20261102/initial/upper-pattern-64k.bin"
CANDIDATE_PATH = ROOT / "tmp/e143a7-probe-20261103/evidence/final/on/xiaozhi.bin"
ROLLBACK_PATHS = {
    "rollback_app": BACKUP_ROOT / "rollback/app_original_sectors.bin",
    "rollback_page": BACKUP_ROOT / "rollback/upper_original_page.bin",
}
EXPECTED = {
    "full_capture": (32 * 1024 * 1024, "ac2639babfb26f7469f9c4cc0e0a509da4cd130ac76288cc8d1c647d4ab32c1e"),
    "rollback_app": (4489216, "8a6a323569538c48683006c5baa400054dec990eff5ce25f6c27def826b6d633"),
    "rollback_page": (PAGE_BYTES, "71189f7fb6aed638640078fba3a35fda6c39c8962e74dcc75935aac948da9063"),
    "candidate_app": (4486768, "7ff00081eb9a89fcc37a5f029c34a15d0c256f51521a4104c77ce6f6815fdced"),
    "pattern": (PAGE_BYTES, "63cd7bb2d9acd763bec3d74f3561ac68572f3f45fdfc8c4fc6513d7f6048df22"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("required reviewed JSON is missing or unsafe: " + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def verify_file(path: Path, key: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("required reviewed binary is missing or unsafe: " + path.name)
    raw = path.read_bytes()
    expected_size, expected_sha = EXPECTED[key]
    require(len(raw) == expected_size and sha(raw) == expected_sha,
            key + " differs from the audited bytes")
    return raw


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_candidate(report: dict) -> bytes:
    require(report.get("edition") == "E1.43a-7"
            and report.get("result") == "isolated_default_off_and_diagnostic_builds_passed",
            "E1.43a-7 isolated build audit is required; the old A6 candidate is not eligible")
    source = ROOT / "firmware/body"
    inputs = report.get("source_sha256", {})
    required = {"main/Kconfig.projbuild", "main/boards/esp_vocat/dev_tools.cc",
                "main/boards/esp_vocat/upper_pattern_probe.h"}
    require(isinstance(inputs, dict) and required <= inputs.keys(), "candidate source evidence is incomplete")
    manifest = source / "SOURCE_MANIFEST.sha256"
    require(manifest.is_file() and not manifest.is_symlink(), "candidate source manifest is missing")
    inventory = {}
    for row in manifest.read_text(encoding="utf-8").splitlines():
        digest, name = row.split("  ", 1)
        require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None and name not in inventory,
                "candidate source manifest has an invalid or duplicate entry")
        inventory[name] = digest
    require(inputs == inventory, "candidate source evidence must match the complete manifest inventory")
    for name, digest in inputs.items():
        path = source / name
        require(path.resolve().is_relative_to(source.resolve()) and not path.is_symlink()
                and path.is_file() and file_sha256(path) == digest, "candidate source differs: " + name)
    require(report.get("source_manifest_sha256") == file_sha256(manifest),
            "candidate source manifest differs")
    header = (source / "main/boards/esp_vocat/upper_pattern_probe.h").read_bytes()
    require(b"kExpectedLowProtectedSha" not in header, "probe still embeds the historical reference")
    rounds = report.get("rounds", {})
    protected = {"bootloader/bootloader.bin", "partition_table/partition-table.bin", "expression_assets.bin",
                 "srmodels/srmodels.bin", "ota_data_initial.bin"}
    for label in ("off", "on"):
        row = rounds.get(label, {})
        require(row.get("protected_match_e1_42") == {key: True for key in protected}
                and row.get("new_task_symbol_present") is (label == "on")
                and row.get("old_diagnostic_task_present") is False
                and type(row.get("app_headroom_bytes")) is int and row["app_headroom_bytes"] >= 1024 * 1024,
                "candidate isolation/protected-output audit failed: " + label)
    require(rounds["off"].get("disabled_image_comparison", {}).get(
                "only_elf_identity_checksum_digest_changed") is True, "default-off baseline comparison is missing")
    require(CANDIDATE_PATH.parent == ROOT / rounds["on"].get("evidence_directory", ""),
            "candidate is outside the audited A7 evidence directory")
    require(CANDIDATE_PATH.is_file() and not CANDIDATE_PATH.is_symlink(), "A7 candidate binary is missing")
    raw = CANDIDATE_PATH.read_bytes()
    meta = rounds["on"].get("artifacts", {}).get("xiaozhi.bin", {})
    require(meta.get("bytes") == len(raw) and meta.get("sha256") == sha(raw)
            and sha(raw) != EXPECTED["candidate_app"][1], "candidate is old or differs from the A7 build")
    return raw


def build_plan() -> ExperimentPlan:
    """Rebuild the plan only from the audited backup, pattern, and candidate."""
    backup_report = read_json(BACKUP_REPORT)
    build_report = read_json(BUILD_REPORT)
    index = read_json(CAPTURE_DIR / "capture-index.json")
    require(backup_report.get("result") == "fresh_backup_and_exact_rollback_passed_single_page_test_pending",
            "E1.43a-3 backup audit is not in its reviewed state")
    require(backup_report.get("safety", {}).get("device_written") is False,
            "historical backup report has an unexpected write claim")
    candidate = verify_candidate(build_report)
    require(index.get("capture_complete") is True and index.get("flash_written") is False
            and index.get("port") == DEVICE_PORT and index.get("physical_flash_bytes") == EXPECTED["full_capture"][0],
            "historical complete capture metadata differs")

    full_path = CAPTURE_DIR / index["full_image"]["path"]
    full = verify_file(full_path, "full_capture")
    require(index["full_image"].get("sha256") == EXPECTED["full_capture"][1],
            "full capture manifest digest differs")
    app = verify_file(ROLLBACK_PATHS["rollback_app"], "rollback_app")
    page = verify_file(ROLLBACK_PATHS["rollback_page"], "rollback_page")
    pattern = verify_file(PATTERN_PATH, "pattern")
    manifest = read_json(PATTERN_MANIFEST)
    validate_manifest(manifest, build_pattern())
    require(pattern == build_pattern() and manifest["sha256"] == EXPECTED["pattern"][1],
            "pattern file and locked manifest differ")

    audit_path = ROOT / "scripts/audit-expression-e1-43a3-backup.py"
    audit_spec = importlib.util.spec_from_file_location("deskbot_e143a6_backup_audit", audit_path)
    if audit_spec is None or audit_spec.loader is None:
        raise RuntimeError("cannot load the reviewed backup audit")
    audit_module = importlib.util.module_from_spec(audit_spec)
    audit_spec.loader.exec_module(audit_module)
    current_backup_report = (json.dumps(audit_module.make_report(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    require(current_backup_report == BACKUP_REPORT.read_bytes(),
            "retained E1.43a-3 audit is stale against its private recovery evidence")

    flash_log = (BACKUP_ROOT / "flash-id.log").read_text(encoding="utf-8")
    security_log = (BACKUP_ROOT / "security-info.log").read_text(encoding="utf-8")
    identity = parse_identity(flash_log, security_log)
    expected_static = {}
    for name, (offset, size) in PROTECTED_RANGES.items():
        if name == "nvs":
            continue
        segment = full[offset:offset + size]
        require(len(segment) == size, "reviewed protected range is outside the full capture")
        expected_static[name] = sha(segment)

    plan = ExperimentPlan(
        expected_identity_sha256=identity.digest,
        rollback_app=app,
        rollback_page=page,
        candidate_app=candidate,
        pattern=pattern,
        expected_static_protected_sha256=expected_static,
    )
    plan.validate()
    return plan


def run_dir_for(label: str, *, recovery: bool = False) -> Path:
    prefix = "e143a[67]" if recovery else "e143a7"
    if re.fullmatch(prefix + r"-[a-z0-9][a-z0-9-]{0,39}", label) is None:
        raise ValueError("use e143a7- for a new experiment; e143a6- is recovery-only")
    root = (ROOT / "tmp").resolve()
    path = root / label
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("run directory must be a direct child of repository tmp")
    return path


def require_current_run(run_dir: Path) -> None:
    require(run_dir == run_dir_for(run_dir.name), "a new experiment requires a direct E1.43a-7 run directory")


def write_json_new(path: Path, value: dict) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("refuse to overwrite experiment evidence: " + path.name)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(run_dir: Path) -> ExperimentPlan:
    require_current_run(run_dir)
    if run_dir.exists() or run_dir.is_symlink():
        raise FileExistsError("run directory already exists; retain it and choose a new run-label")
    run_dir.mkdir(parents=False)
    plan = build_plan()
    persist_plan_inputs(run_dir, plan)
    return plan


def preflight(run_dir: Path) -> int:
    plan = prepare(run_dir)
    device = EsptoolDevice(port=DEVICE_PORT, run_dir=run_dir, plan=plan, write_enabled=False)
    try:
        snapshot = device.snapshot(plan)
        protected = validate_snapshot(plan, snapshot)
    except BaseException:
        if device.identity_matches(plan.expected_identity_sha256):
            device.boot_original()
        raise
    boot_ok = device.boot_original()
    record = {
        "schema": "deskbot.upper_pattern.live_preflight.e1_43a7/v1",
        "run_label": run_dir.name,
        "plan_id": plan.plan_id,
        "result": "fresh_snapshot_and_original_boot_passed" if boot_ok else "original_boot_failed",
        "snapshot": {
            "identity_sha256": snapshot.identity_sha256,
            "chip": snapshot.chip,
            "physical_flash_bytes": snapshot.physical_flash_bytes,
            "psram_bytes": snapshot.psram_bytes,
            "secure_boot": snapshot.secure_boot,
            "flash_encryption": snapshot.flash_encryption,
            "app_erase_sha256": sha(snapshot.app_erase_bytes),
            "app_erase_bytes": len(snapshot.app_erase_bytes),
            "upper_page_sha256": sha(snapshot.upper_page),
            "upper_page_bytes": len(snapshot.upper_page),
            "protected_sha256": protected,
        },
        "normal_boot_observed": boot_ok,
        "safety": {"flash_written": False, "partition_table_changed": False,
                   "nvs_targeted": False, "c61_accessed": False},
    }
    write_json_new(run_dir / "preflight.json", record)
    print(json.dumps({"result": record["result"], "run_label": run_dir.name,
                      "plan_id": plan.plan_id, "flash_written": False}))
    return 0 if boot_ok else 2


def load_validator():
    path = ROOT / "scripts/validate-expression-e1-43a7-probe.py"
    spec = importlib.util.spec_from_file_location("deskbot_e143a7_validator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the reviewed CPU result validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute(run_dir: Path) -> int:
    require_current_run(run_dir)
    plan = load_persisted_plan(run_dir)
    require(build_plan().plan_id == plan.plan_id,
            "persisted experiment plan differs from the freshly rechecked audited inputs")
    preflight_record = read_json(run_dir / "preflight.json")
    require(preflight_record.get("schema") == "deskbot.upper_pattern.live_preflight.e1_43a7/v1"
            and preflight_record.get("result") == "fresh_snapshot_and_original_boot_passed"
            and preflight_record.get("plan_id") == plan.plan_id
            and preflight_record.get("run_label") == run_dir.name,
            "fresh read-only preflight is missing, failed, or belongs to a different plan")
    result_path = run_dir / "result.json"
    if result_path.exists() or result_path.is_symlink():
        raise FileExistsError("result.json already exists; do not replay this experiment")

    validator = load_validator()
    checked: list[dict] = []
    journal = AtomicJournal(run_dir / "recovery.json")
    require(journal.load() is None, "a journal already exists; use recovery instead of execute")
    device = EsptoolDevice(port=DEVICE_PORT, run_dir=run_dir, plan=plan, write_enabled=True)
    try:
        snapshot = device.snapshot(plan)
        reference = persist_snapshot(run_dir, plan, snapshot)

        def factory(controller_snapshot):
            require(controller_snapshot is snapshot, "controller and validator must share the write-time snapshot")
            load_snapshot(run_dir, identity_sha256=plan.expected_identity_sha256, plan_id=plan.plan_id,
                          expected_manifest_sha256=reference.manifest_sha256)

            def validate(log: str) -> bool:
                result = validator.validate(log, run_dir, identity_sha256=plan.expected_identity_sha256,
                                            plan_id=plan.plan_id,
                                            expected_manifest_sha256=reference.manifest_sha256)
                checked.append(result)
                return result.get("result") == "runtime_probe_passed"

            return validate

        experiment = run_experiment(plan, device, journal, probe_validator_factory=factory,
                                    initial_snapshot=snapshot,
                                    snapshot_manifest_sha256=reference.manifest_sha256, timeout_seconds=120)
    except BaseException:
        # The controller restores once its first-write intent is durable. Before
        # that point no Flash write is possible, but reads may leave ROM active.
        state = journal.load()
        if state is None or state["phase"] == "prepared":
            if device.identity_matches(plan.expected_identity_sha256):
                device.boot_original()
        raise
    state = journal.load()
    record = {
        "schema": "deskbot.upper_pattern.live_result.e1_43a7/v1",
        "run_label": run_dir.name,
        "plan_id": plan.plan_id,
        "snapshot_manifest_sha256": reference.manifest_sha256,
        "result": experiment.phase,
        "probe_passed": experiment.probe_passed,
        "restored": experiment.restored,
        "journal_finalized": experiment.journal_finalized,
        "error": experiment.error,
        "journal": state,
        "runtime_validation": checked[-1] if checked else None,
        "safety": {"flash_ranges": ["0x01000000..0x01010000",
                                    f"0x00010000..0x{0x10000 + plan.app_erase_bytes:08x}"],
                   "bootloader_written": False, "partition_table_written": False,
                   "assets_written": False, "model_written": False,
                   "nvs_targeted": False, "c61_accessed": False},
    }
    write_json_new(result_path, record)
    print(json.dumps({"result": experiment.phase, "probe_passed": experiment.probe_passed,
                      "restored": experiment.restored, "journal_finalized": experiment.journal_finalized,
                      "run_label": run_dir.name}))
    return 0 if experiment.probe_passed and experiment.restored and experiment.journal_finalized else 2


def recover_run(run_dir: Path) -> int:
    require(run_dir == run_dir_for(run_dir.name, recovery=True), "invalid recovery run directory")
    plan = load_persisted_plan(run_dir)
    device = EsptoolDevice(port=DEVICE_PORT, run_dir=run_dir, plan=plan, write_enabled=True)
    journal = AtomicJournal(run_dir / "recovery.json")
    recover(plan, device, journal)
    state = journal.load()
    write_json_new(run_dir / "recovery-result.json", {
        "schema": "deskbot.upper_pattern.recovery_result.e1_43a7/v1" if run_dir.name.startswith("e143a7-")
                  else "deskbot.upper_pattern.recovery_result.e1_43a6/v1",
        "run_label": run_dir.name,
        "plan_id": plan.plan_id,
        "result": "rollback_verified_and_original_boot_observed",
        "journal": state,
    })
    print(json.dumps({"result": "rollback_verified_and_original_boot_observed",
                      "run_label": run_dir.name, "journal_phase": state["phase"]}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "execute", "recover"))
    parser.add_argument("--run-label", required=True)
    args = parser.parse_args()
    run_dir = run_dir_for(args.run_label, recovery=args.phase == "recover")
    if args.phase == "preflight":
        return preflight(run_dir)
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise ValueError("existing run directory is required for execute/recover")
    if args.phase == "execute":
        return execute(run_dir)
    return recover_run(run_dir)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryRequired as error:
        print("RECOVERY REQUIRED: " + str(error), file=sys.stderr)
        raise SystemExit(3)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("E1.43a-7 stopped: " + str(error), file=sys.stderr)
        raise SystemExit(2)
