#!/usr/bin/env python3
"""Check, execute or recover the E3c-3b App-only mapping experiment."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol import storage_continuous_mmap_e3c3b_baseline as baseline
from protocol.storage_continuous_mmap_experiment import (
    APP_START, Journal, execute, load_plan, recover, sha, write_new,
)


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("reviewed App transport is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


previous = _module("e143b_e3c3b_previous_runner", ROOT / "scripts/run-expression-continuous-mmap-e3c3a.py")
AUDIT = _module("e143b_e3c3b_preparation_audit", ROOT / "scripts/audit-expression-e1-43b-e3c3b-deployment.py")
ARCHIVE = ROOT / baseline.ARCHIVE
RUN_LABEL = AUDIT.RUN_LABEL


class E3c3bAppDevice(previous.E3c3aAppDevice):
    def _require_plan_identity(self):
        identity = self._fresh_identity()
        if identity.digest != self.plan.metadata["identity_sha256"]:
            raise ValueError("fresh COM4 identity differs from this E3c-3b plan")
        return identity

    def boot_original(self) -> bool:
        # Startup markers alone cannot distinguish two otherwise similar Apps.
        AUDIT.preparation.validate_boot_log(self._capture_serial("normal-boot", 45))
        return True


def _run_folder(label: str) -> Path:
    if RUN_LABEL.fullmatch(label) is None:
        raise ValueError("run label must be e143b-e3c3b-YYYYMMDD-name")
    folder = baseline.safe_path(ROOT / "tmp" / label, ROOT / "tmp")
    if folder.parent != (ROOT / "tmp").resolve() or not folder.is_dir():
        raise ValueError("preparation must be a named direct child of repository tmp")
    return folder


def _require_current_rollback(plan) -> None:
    # Recovery needs only the persisted original bytes, never a build or candidate.
    if (len(plan.rollback) != 0x45A000 - APP_START
            or sha(plan.rollback[:baseline.APP_BYTES_EXPECTED]) != baseline.APP_SHA256_EXPECTED):
        raise ValueError("recovery bytes differ from accepted E3c-3b")


def _load_audited_plan(folder: Path, audit_path: Path):
    path = baseline.safe_path(audit_path, ARCHIVE)
    if path.parent != ARCHIVE.resolve() or not path.is_file():
        raise ValueError("preparation audit must be a direct file in the E3c-3b archive")
    saved = path.read_bytes()
    audited = AUDIT.audit_preparation(folder)
    if json.loads(saved) != audited or audited.get("result") != "passed":
        raise ValueError("saved audit differs from a fresh E3c-3b preparation audit")
    plan = load_plan(folder, execution=True)
    _require_current_rollback(plan)
    if plan.plan_id != audited.get("plan_id") or plan.metadata["run_label"] != folder.name:
        raise ValueError("App plan differs from the audited E3c-3b run")
    return plan, audited, sha(saved)


def _save(folder: Path, name: str, value: dict) -> None:
    write_new(folder / name, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def check_prepared(label: str, audit_path: Path) -> dict:
    plan, _, digest = _load_audited_plan(_run_folder(label), audit_path)
    return {"schema": "deskbot.continuous_mmap.e3c3b_execution_check.v1", "result": "ready",
            "run_label": label, "plan_id": plan.plan_id, "audit_sha256": digest,
            "candidate_bytes": plan.metadata["candidate_bytes"], "rollback_bytes": len(plan.rollback),
            "device_opened": False, "device_written": False}


def execute_prepared(label: str, audit_path: Path, confirm_plan: str, *, device_factory=None) -> dict:
    folder = _run_folder(label)
    plan, audit, digest = _load_audited_plan(folder, audit_path)
    if confirm_plan != plan.plan_id:
        raise PermissionError("execute requires this exact plan ID")
    if any((folder / name).exists() for name in ("recovery.json", "execute-result.json", "execute-failure.json")):
        raise FileExistsError("execution already started; use recover")
    device = device_factory(folder, plan) if device_factory else E3c3bAppDevice(folder, plan, write_enabled=True)
    journal = Journal(folder, plan)
    try:
        if device.identity() != plan.metadata["identity_sha256"]:
            raise ValueError("COM4 identity differs from the audited plan")
        if device.boot_original() is not True:
            raise ValueError("original E3c-3b boot was not observed")
        snapshot = device.read_full()
        result = execute(plan, device, journal, initial_snapshot=snapshot)
        result.update(audit_sha256=digest, execution_plan_id=audit["plan_id"], physical_interaction_accepted=False)
        _save(folder, "execute-result.json", result)
        return result
    except BaseException as error:
        try:
            state = journal.load()
        except BaseException as journal_error:
            state = {"phase": "journal_unreadable", "error": str(journal_error)}
        cleanup = None
        if state is None or state["phase"] == "prepared":
            try:
                cleanup = {"normal_boot_observed": device.boot_original() is True}
            except BaseException as boot_error:
                cleanup = {"normal_boot_observed": False, "error": str(boot_error)}
        _save(folder, "execute-failure.json", {"schema": "deskbot.continuous_mmap.e3c3b_execution_failure.v1",
              "run_label": label, "plan_id": plan.plan_id, "error": type(error).__name__ + ": " + str(error),
              "recovery_phase": state.get("phase") if isinstance(state, dict) else None, "boot_cleanup": cleanup})
        raise


def recover_prepared(label: str, *, device_factory=None) -> dict:
    folder = _run_folder(label)
    plan = load_plan(folder)
    _require_current_rollback(plan)
    if plan.metadata["run_label"] != label:
        raise ValueError("App recovery plan belongs to a different run")
    journal = Journal(folder, plan)
    state = journal.load()
    if state is None or state["phase"] in {"prepared", "restored"}:
        raise ValueError("there is no pending App recovery")
    device = device_factory(folder, plan) if device_factory else E3c3bAppDevice(folder, plan, write_enabled=True)
    index = 1
    while (folder / f"recovery-result-{index:03d}.json").exists():
        index += 1
    try:
        result = recover(plan, device, journal)
        result["recovery_result"] = "restored" if result.get("phase") == "restored" else "needs_recovery"
    except BaseException as error:
        _save(folder, f"recovery-result-{index:03d}.json", {
            "schema": "deskbot.continuous_mmap.e3c3b_recovery_failure.v1", "run_label": label,
            "plan_id": plan.plan_id, "phase": "needs_recovery", "error": type(error).__name__ + ": " + str(error)})
        raise
    _save(folder, f"recovery-result-{index:03d}.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("check", "execute", "recover"))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--audit-report", type=Path)
    parser.add_argument("--confirm-plan")
    args = parser.parse_args()
    if args.phase != "recover" and args.audit_report is None:
        parser.error("check and execute require --audit-report")
    if args.phase == "execute" and args.confirm_plan is None:
        parser.error("execute requires --confirm-plan")
    audit_path = args.audit_report
    if audit_path is not None and not audit_path.is_absolute():
        audit_path = ROOT / audit_path
    try:
        if args.phase == "check":
            result = check_prepared(args.run_label, audit_path)
        elif args.phase == "execute":
            result = execute_prepared(args.run_label, audit_path, args.confirm_plan)
        else:
            result = recover_prepared(args.run_label)
    except (OSError, ValueError, PermissionError) as error:
        print(json.dumps({"phase": args.phase, "result": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"phase": args.phase, "run_label": args.run_label, "plan_id": result.get("plan_id"),
                      "result": result.get("result", result.get("phase")),
                      "probe_passed": result.get("probe_passed"), "restored": result.get("restored")}, ensure_ascii=False))
    return 0 if args.phase != "execute" or result.get("probe_passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
