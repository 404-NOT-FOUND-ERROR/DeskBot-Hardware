#!/usr/bin/env python3
"""Prepare an E1.43b plan from a fresh, validated E3c-3a COM4 snapshot."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol import storage_continuous_mmap_e3c3a_baseline as baseline
from protocol.storage_continuous_mmap_experiment import (
    APP_START, FLASH_BYTES, Journal, make_plan, persist_plan, sha, write_new,
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load local module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD_AUDIT = _load_module(
    "expression_e143b_e3c3a_build_audit",
    ROOT / "scripts/audit-expression-e1-43b-e3c3a-build.py",
)
RUNNER = _load_module(
    "expression_e143b_e3c3a_runner",
    ROOT / "scripts/run-expression-continuous-mmap-e1-43b.py",
)

RUN_LABEL = re.compile(r"e143b-e3c3a-[0-9]{8}-[a-z0-9][a-z0-9-]{0,23}")


class ReadOnlyAppDevice(RUNNER.AppDevice):
    """COM4 transport for preparation; deliberately exposes no write path."""

    def __init__(self, folder: Path, expected_identity: str):
        if re.fullmatch(r"[0-9a-f]{64}", expected_identity) is None:
            raise ValueError("migrated COM4 identity is required")
        self.expected_identity = expected_identity
        super().__init__(folder)

    def identity(self) -> str:
        identity = super().identity()
        if identity != self.expected_identity:
            raise ValueError("COM4 identity changed during read-only preparation")
        return identity

    def _invoke(self, label, args, *, timeout=180, log_path=None):
        commands = {"write_flash", "erase_flash", "erase_region", "erase_all"}
        unsafe_options = {"--erase-all", "--force", "--encrypt", "--encrypt-files"}
        if any(argument in commands or argument in unsafe_options for argument in args):
            raise PermissionError("E1.43b preparation is read-only")
        return super()._invoke(label, args, timeout=timeout, log_path=log_path)

    def write_app(self, _payload: bytes) -> None:
        raise PermissionError("E1.43b preparation is read-only")

    def write_flash(self, _offset: int, _payload: bytes) -> None:
        raise PermissionError("E1.43b preparation is read-only")


def _safe_run_folder(folder: Path, run_label: str) -> Path:
    if RUN_LABEL.fullmatch(run_label) is None:
        raise ValueError("run label must be e143b-e3c3a-YYYYMMDD-name")
    root = ROOT.resolve(strict=True)
    tmp_root = root / "tmp"
    if tmp_root.is_symlink() or not tmp_root.is_dir():
        raise ValueError("repository tmp directory is unavailable or unsafe")
    target = Path(folder)
    if target.is_symlink():
        raise ValueError("run directory must not be a symlink")
    resolved = target.resolve()
    if resolved != tmp_root / run_label or resolved.parent != tmp_root.resolve():
        raise ValueError("run directory must be the named direct child of repository tmp")
    if target.exists():
        raise FileExistsError("retain existing run; choose a new run label")
    return resolved


def _load_build_inputs(report_path: Path) -> dict:
    report_path = Path(report_path)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    audit = BUILD_AUDIT.audit_report(report_path)
    report_path = report_path.resolve(strict=True)
    raw_report = report_path.read_bytes()
    report = json.loads(raw_report)
    round_on = report["rounds"]["on"]
    candidate_path = ROOT / round_on["evidence_directory"] / "xiaozhi.bin"
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise ValueError("audited E3c-3a probe App is unavailable")
    candidate = candidate_path.read_bytes()
    artifact = round_on["artifacts"]["xiaozhi.bin"]
    if len(candidate) != artifact["bytes"] or sha(candidate) != artifact["sha256"]:
        raise ValueError("candidate App changed after independent build audit")
    return {
        "report_path": report_path,
        "report_sha256": sha(raw_report),
        "candidate": candidate,
        "candidate_path": candidate_path,
        "candidate_sha256": artifact["sha256"],
        "audit": audit,
    }


def persist_validated_snapshot(folder: Path, run_label: str, snapshot: bytes,
                               identity_sha256: str, candidate: bytes,
                               build_facts: dict, baseline_facts: dict,
                               *, normal_boot_observed: bool) -> dict:
    """Save the same-run snapshot and exact App rollback after validation."""
    if len(snapshot) != FLASH_BYTES:
        raise ValueError("complete 32 MiB snapshot required")
    if baseline_facts.get("identity_sha256") != identity_sha256:
        raise ValueError("validated baseline identity differs")
    if normal_boot_observed is not True:
        raise ValueError("normal boot must be observed before preparing the plan")
    original = baseline_facts.get("original_app")
    if (not isinstance(original, dict) or type(original.get("bytes")) is not int
            or original["bytes"] <= 0
            or sha(snapshot[APP_START:APP_START + original["bytes"]]) != original.get("sha256")):
        raise ValueError("validated current App does not match the saved snapshot")
    if (not candidate or sha(candidate) != build_facts.get("candidate_sha256")
            or type(build_facts.get("report_sha256")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", build_facts["report_sha256"])):
        raise ValueError("candidate or audited build report digest differs")

    snapshot_path = folder / "snapshot-32m.bin"
    if snapshot_path.is_symlink() or not snapshot_path.is_file():
        raise ValueError("same-run snapshot is missing or unsafe")
    if snapshot_path.stat().st_size != len(snapshot) or sha(snapshot_path.read_bytes()) != sha(snapshot):
        raise ValueError("saved same-run snapshot differs")
    plan = make_plan(
        snapshot, candidate, original_app_bytes=original["bytes"],
        identity_sha256=identity_sha256, run_label=run_label,
    )
    persist_plan(folder, plan)
    saved_plan = json.loads((folder / "plan.json").read_bytes())
    if saved_plan.get("plan_id") != plan.plan_id or sha((folder / "rollback-app.bin").read_bytes()) != plan.metadata["rollback_sha256"]:
        raise ValueError("persisted rollback plan failed its local verification")
    receipt = {
        "schema": "deskbot.continuous_mmap.live_preflight.e3c3a.v1",
        "edition": "E1.43b-2",
        "result": "snapshot_validated_rollback_prepared",
        "run_label": run_label,
        "plan_id": plan.plan_id,
        "target": "body_COM4",
        "device": {"chip": "esp32s3", "flash_bytes": FLASH_BYTES,
                    "psram_bytes": 16 * 1024 * 1024,
                    "identity_sha256": identity_sha256},
        "baseline": {
            "migration_report": baseline_facts.get("migration_reference"),
            "migration_plan_id": baseline_facts.get("migration_plan_id"),
            "build_report": baseline_facts.get("build_reference"),
            "layout": baseline_facts.get("layout"),
            "app_bytes": original["bytes"],
            "app_sha256": original["sha256"],
            "nvs_sha256": baseline_facts.get("nvs", {}).get("sha256"),
        },
        "build": {
            "report": build_facts["audit"]["report"],
            "report_sha256": build_facts["report_sha256"],
            "audit_result": build_facts["audit"]["result"],
            "candidate_app": {"path": build_facts["candidate_path"].relative_to(ROOT).as_posix(),
                               "bytes": len(candidate), "sha256": sha(candidate)},
        },
        "snapshot": {"file": "snapshot-32m.bin", "bytes": len(snapshot),
                     "sha256": sha(snapshot)},
        "rollback": {"file": "rollback-app.bin", "offset": APP_START,
                     "bytes": len(plan.rollback), "sha256": sha(plan.rollback)},
        "candidate_write": {"offset": APP_START, "payload_bytes": len(candidate),
                            "erase_bytes": plan.metadata["erase_bytes"],
                            "end_exclusive": APP_START + plan.metadata["erase_bytes"]},
        "normal_boot_observed": True,
        "device_written": False,
        "flash_written": False,
        "nvs_written": False,
        "assets_written": False,
        "bootloader_written": False,
        "partition_table_written": False,
        "c61_accessed": False,
    }
    write_new(folder / "preflight.json", (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return receipt


def prepare_live(folder: Path, run_label: str, report_path: Path,
                 *, device_factory=None) -> dict:
    folder = _safe_run_folder(folder, run_label)
    build = _load_build_inputs(report_path)
    migration_reference = baseline.migrated.load_migrated_baseline()
    folder.mkdir()
    expected_identity = migration_reference["identity_sha256"]
    device = (device_factory(folder, expected_identity) if device_factory
              else ReadOnlyAppDevice(folder, expected_identity))
    device_touched = False
    boot_attempted = False
    snapshot = None
    observed_identity = None
    validation = None
    try:
        device_touched = True
        observed_identity = device.identity()
        if observed_identity != expected_identity:
            raise ValueError("COM4 identity differs from the migrated body")
        snapshot = device.read_full()
        if device.identity() != expected_identity:
            raise ValueError("COM4 identity changed during the full Flash read")
        if len(snapshot) != FLASH_BYTES:
            raise ValueError("COM4 did not return a complete 32 MiB snapshot")
        write_new(folder / "snapshot-32m.bin", snapshot)
        validation_error = None
        try:
            validation = baseline.validate_e3c3a_snapshot(snapshot, observed_identity)
        except BaseException as error:
            validation_error = error
        boot_attempted = True
        boot_ok = device.boot_original()
        observation = {
            "schema": "deskbot.continuous_mmap.device_observation.e3c3a.v1",
            "run_label": run_label,
            "target": "body_COM4",
            "identity_sha256": observed_identity,
            "snapshot_bytes": len(snapshot),
            "snapshot_sha256": sha(snapshot),
            "baseline_validated": validation_error is None,
            "normal_boot_observed": bool(boot_ok),
            "device_written": False,
        }
        if validation_error is not None:
            observation["baseline_error"] = type(validation_error).__name__ + ": " + str(validation_error)
        write_new(folder / "device-observation.json", (json.dumps(observation, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if not boot_ok:
            raise ValueError("original App normal boot was not observed after the read")
        if validation_error is not None:
            raise validation_error
        return persist_validated_snapshot(
            folder, run_label, snapshot, observed_identity, build["candidate"],
            {"report_sha256": build["report_sha256"], "candidate_sha256": build["candidate_sha256"],
             "candidate_path": build["candidate_path"], "audit": build["audit"]},
            validation, normal_boot_observed=True,
        )
    except BaseException:
        if device_touched and not boot_attempted:
            try:
                boot_ok = device.boot_original()
                record = {"schema": "deskbot.continuous_mmap.device_observation.e3c3a.v1",
                          "run_label": run_label, "target": "body_COM4",
                          "identity_sha256": observed_identity,
                          "snapshot_bytes": len(snapshot) if snapshot is not None else 0,
                          "snapshot_sha256": sha(snapshot) if snapshot is not None else None,
                          "normal_boot_observed": bool(boot_ok), "device_written": False}
                write_new(folder / "device-observation.json",
                          (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            except BaseException:
                pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    args = parser.parse_args()
    if RUN_LABEL.fullmatch(args.run_label) is None:
        parser.error("run label must be e143b-e3c3a-YYYYMMDD-name")
    folder = ROOT / "tmp" / args.run_label
    result = prepare_live(folder, args.run_label, args.build_report)
    print(json.dumps({"result": result["result"], "run_label": result["run_label"],
                      "plan_id": result["plan_id"], "snapshot_sha256": result["snapshot"]["sha256"],
                      "rollback_bytes": result["rollback"]["bytes"], "device_written": False},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
