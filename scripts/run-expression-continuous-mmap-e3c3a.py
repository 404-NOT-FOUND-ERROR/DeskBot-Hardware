#!/usr/bin/env python3
"""Execute or recover the E1.43b probe using an audited E3c-3a preparation."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_continuous_mmap_experiment import (
    APP_START, FLASH_BYTES, Journal, execute, load_plan, recover, sha, write_new,
)
from protocol.storage_upper_experiment_esptool import (
    CAPTURE_SCRIPT, CAPTURE_SCRIPT_SHA256, ESPYTHON, EsptoolDevice, Runner,
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load local module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREPARATION_AUDIT = _load_module(
    "expression_e143b_e3c3a_preparation_audit",
    ROOT / "scripts/audit-expression-e1-43b-e3c3a-deployment.py",
)

RUN_LABEL = re.compile(r"e143b-e3c3a-[0-9]{8}-[a-z0-9][a-z0-9-]{0,23}")
ARCHIVE = ROOT / "artifacts/expression-continuous-mmap-e1-43b2-e3c3a-20261107"
DEFAULT_AUDIT = ARCHIVE / "e143b-e3c3a-live-preflight-audit-20261107-v5.json"


class E3c3aAppDevice(EsptoolDevice):
    """COM4 transport locked to one App plan and its durable recovery journal."""

    def __init__(self, folder: Path, plan, *, write_enabled: bool = False):
        if (not ESPYTHON.is_file() or sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256
                or folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve()):
            raise ValueError("reviewed IDF Python, capture script, or run directory differs")
        self.port = "COM4"
        self.run_dir = folder.resolve()
        self.python = ESPYTHON
        self.runner = Runner()
        self.plan = plan
        self._write_armed = write_enabled is True
        self._sequence = self._last_evidence_sequence()
        self._reserved_logs = set()
        self._identity = None

    def _require_plan_identity(self):
        identity = self._fresh_identity()
        if identity.digest != self.plan.metadata["identity_sha256"]:
            raise ValueError("fresh COM4 identity differs from this E3c-3a plan")
        return identity

    def identity(self) -> str:
        return self._require_plan_identity().digest

    def read_full(self) -> bytes:
        self._require_plan_identity()
        raw = self._capture("full-readback", 0, FLASH_BYTES)
        self._require_plan_identity()
        return raw

    def read_app(self, size: int) -> bytes:
        if size != len(self.plan.rollback):
            raise ValueError("read must cover the exact App erase union")
        self._require_plan_identity()
        raw = self._capture("app-readback", APP_START, size)
        self._require_plan_identity()
        return raw

    def write_app(self, raw: bytes) -> None:
        self._write_authorized()
        persisted = load_plan(self.run_dir)
        state = Journal(self.run_dir, persisted).load()
        if (persisted.plan_id != self.plan.plan_id or state is None
                or state["phase"] not in {"restore_required", "restoring"}):
            raise PermissionError("matching durable App recovery intent is required")
        if not isinstance(raw, bytes):
            raise ValueError("App writes must use bytes from this plan")
        if raw == persisted.rollback:
            pass
        elif state["phase"] == "restore_required":
            persisted = load_plan(self.run_dir, execution=True)
            if persisted.plan_id != self.plan.plan_id or raw != persisted.candidate:
                raise ValueError("App writes must use this plan's candidate or rollback bytes")
        else:
            raise ValueError("App writes must use this plan's candidate or rollback bytes")
        path = self._new_path("payloads", "app.bin")
        write_new(path, raw)
        log_path = self._reserve_log("write-app")
        self._require_plan_identity()
        self._invoke("write-app", ("write_flash", "--verify", "--flash_mode", "keep",
                                   "--flash_size", "keep", hex(APP_START), str(path)),
                     log_path=log_path)

    def read_flash(self, offset, size):
        raise ValueError("use the exact App or complete snapshot read")

    def write_flash(self, offset, data):
        raise ValueError("this E3c-3a run is limited to the App")

    def snapshot(self, plan):
        raise ValueError("use the complete same-run Flash snapshot")

    def capture(self) -> str:
        seconds = 450
        if sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256:
            raise ValueError("the audited USB capture script changed during the run")
        path = self._new_path("captures", "continuous-probe.log")
        write_new(path, b"")
        log_path = self._reserve_log("continuous-probe-capture")
        argv = [str(ESPYTHON), str(CAPTURE_SCRIPT), "--port", "COM4",
                "--duration", str(seconds), "--output", str(path), "--usb-reset"]
        self._require_plan_identity()
        code, _ = self._run_logged("continuous-probe-capture", argv,
                                   timeout=seconds + 45, log_path=log_path)
        if code != 0 or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("continuous probe capture failed or exceeded its size limit")
        return path.read_text(encoding="utf-8", errors="strict")


def _run_folder(run_label: str) -> Path:
    if RUN_LABEL.fullmatch(run_label) is None:
        raise ValueError("run label must be e143b-e3c3a-YYYYMMDD-name")
    tmp_root = ROOT / "tmp"
    if tmp_root.is_symlink() or not tmp_root.is_dir():
        raise ValueError("repository tmp directory is missing or unsafe")
    folder = tmp_root / run_label
    if folder.is_symlink() or folder.resolve().parent != tmp_root.resolve():
        raise ValueError("run directory must be a direct child of repository tmp")
    if not folder.is_dir():
        raise FileNotFoundError("audited E3c-3a preparation directory is missing")
    return folder.resolve()


def _load_audited_plan(folder: Path, audit_path: Path):
    if audit_path.is_symlink() or not audit_path.is_file():
        raise ValueError("E3c-3a v5 preparation audit is missing or unsafe")
    audit_path = audit_path.resolve(strict=True)
    if audit_path.parent != ARCHIVE.resolve(strict=True):
        raise ValueError("preparation audit must be in the E1.43b E3c-3a archive")
    receipt = json.loads((folder / "preflight.json").read_bytes())
    build_report = ROOT / receipt["build"]["report"]
    result = PREPARATION_AUDIT.audit_preparation(folder, build_report)
    saved_audit = json.loads(audit_path.read_bytes())
    if saved_audit != result or result.get("result") != "passed":
        raise ValueError("saved v3 audit differs from a fresh offline audit")
    plan = load_plan(folder, execution=True)
    if (plan.plan_id != result.get("plan_id")
            or plan.metadata.get("run_label") != folder.name):
        raise ValueError("App plan differs from the audited E3c-3a preparation")
    return plan, result, sha(audit_path.read_bytes())


def _write_result(folder: Path, name: str, result: dict) -> Path:
    path = folder / name
    write_new(path, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return path


def check_prepared(run_label: str, audit_path: Path) -> dict:
    folder = _run_folder(run_label)
    plan, audit, audit_sha = _load_audited_plan(folder, audit_path)
    return {
        "schema": "deskbot.continuous_mmap.e3c3a_execution_check.v1",
        "result": "ready",
        "run_label": run_label,
        "plan_id": plan.plan_id,
        "audit_sha256": audit_sha,
        "candidate_bytes": plan.metadata["candidate_bytes"],
        "rollback_bytes": len(plan.rollback),
        "device_opened": False,
        "device_written": False,
    }


def execute_prepared(run_label: str, audit_path: Path, confirm_plan: str,
                     *, device_factory=None) -> dict:
    folder = _run_folder(run_label)
    plan, audit, audit_sha = _load_audited_plan(folder, audit_path)
    if confirm_plan != plan.plan_id:
        raise PermissionError("Flash execution requires --confirm-plan with this exact plan ID")
    if any((folder / name).exists() for name in
           ("recovery.json", "execute-result.json", "execute-failure.json")):
        raise FileExistsError("execution already started; use the recovery phase")
    device = (device_factory(folder, plan) if device_factory
              else E3c3aAppDevice(folder, plan, write_enabled=True))
    journal = Journal(folder, plan)
    try:
        if device.identity() != plan.metadata["identity_sha256"]:
            raise ValueError("COM4 identity differs from the audited plan")
        if not device.boot_original():
            raise ValueError("original E3c-3a App did not boot before the test")
        snapshot = device.read_full()
        result = execute(plan, device, journal, initial_snapshot=snapshot)
        result["audit_sha256"] = audit_sha
        result["execution_plan_id"] = audit["plan_id"]
        result["physical_interaction_accepted"] = False
        _write_result(folder, "execute-result.json", result)
        return result
    except BaseException as error:
        try:
            state = journal.load()
        except BaseException as journal_error:
            state = {"phase": "journal_unreadable", "error": str(journal_error)}
        if state is None or state["phase"] == "prepared":
            try:
                device.boot_original()
            except BaseException:
                pass
        _write_result(folder, "execute-failure.json", {
            "schema": "deskbot.continuous_mmap.e3c3a_execution_failure.v1",
            "run_label": run_label, "plan_id": plan.plan_id,
            "error": type(error).__name__ + ": " + str(error),
            "recovery_phase": state.get("phase") if isinstance(state, dict) else None,
        })
        raise


def recover_prepared(run_label: str, *, device_factory=None) -> dict:
    folder = _run_folder(run_label)
    plan = load_plan(folder)
    journal = Journal(folder, plan)
    if plan.metadata.get("run_label") != folder.name:
        raise ValueError("App plan belongs to a different E3c-3a run")
    state = journal.load()
    if state is None or state["phase"] in {"prepared", "restored"}:
        raise ValueError("there is no pending App recovery for this run")
    device = (device_factory(folder, plan) if device_factory
              else E3c3aAppDevice(folder, plan, write_enabled=True))
    index = 1
    while (folder / f"recovery-result-{index:03d}.json").exists():
        index += 1
    try:
        result = recover(plan, device, journal)
        result["recovery_result"] = "restored" if result.get("phase") == "restored" else "needs_recovery"
    except BaseException as error:
        result = {"schema": "deskbot.continuous_mmap.e3c3a_recovery_failure.v1",
                  "run_label": run_label, "plan_id": plan.plan_id,
                  "phase": "needs_recovery",
                  "error": type(error).__name__ + ": " + str(error)}
        _write_result(folder, f"recovery-result-{index:03d}.json", result)
        raise
    _write_result(folder, f"recovery-result-{index:03d}.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("check", "execute", "recover"))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--audit-report", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--confirm-plan")
    args = parser.parse_args()
    try:
        if args.phase == "check":
            result = check_prepared(args.run_label, args.audit_report)
        elif args.phase == "execute":
            if args.confirm_plan is None:
                parser.error("execute requires --confirm-plan with the audited plan ID")
            result = execute_prepared(args.run_label, args.audit_report,
                                      args.confirm_plan)
        else:
            result = recover_prepared(args.run_label)
    except (OSError, ValueError, PermissionError) as error:
        print(json.dumps({"phase": args.phase, "run_label": args.run_label,
                          "result": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"phase": args.phase, "run_label": args.run_label,
                      "plan_id": result.get("plan_id"),
                      "probe_passed": result.get("probe_passed"),
                      "device_opened": result.get("device_opened"),
                      "device_written": result.get("device_written"),
                      "restored": result.get("restored", result.get("phase") == "restored")},
                     ensure_ascii=False))
    return 0 if args.phase in {"check", "recover"} or result.get("probe_passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
