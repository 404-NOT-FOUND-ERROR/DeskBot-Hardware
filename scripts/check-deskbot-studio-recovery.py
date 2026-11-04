#!/usr/bin/env python3
"""Check saved E3b rollback files offline; no device or serial operation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol.storage_continuous_mmap_baseline import APP_BYTES, APP_SHA256

OUTPUT = ROOT / "artifacts/deskbot-studio-e3c-20261104/recovery-readiness.json"
SOURCES = ("protocol/studio_app_deployment.py", "scripts/deploy-deskbot-studio-e3b.py",
           "scripts/check-deskbot-studio-recovery.py", "tests/test_studio_recovery.py")


def inspect(run):
    if re.fullmatch(r"e3b-live-[0-9]{8}-[a-z0-9-]{1,32}", run) is None:
        raise ValueError("invalid saved run label")
    folder = ROOT / "tmp" / run
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("saved recovery must stay inside repository tmp")
    plan = tx.load_recovery(folder)
    state = tx.Journal(folder, plan).load()
    live = json.loads((ROOT / "artifacts/deskbot-studio-e3b-20261104/live-deployment.json").read_bytes())
    if (state is None or state["phase"] not in tx.RECOVERY_PHASES
            or live["run_label"] != run or live["plan_id"] != plan.id
            or live["backup"]["sha256"] != plan.metadata["snapshot_sha256"]
            or live["backup"]["rollback_sha256"] != plan.metadata["rollback_sha256"]):
        raise ValueError("saved recovery does not match the installed release record")
    original = plan.snapshot[tx.START:tx.START + APP_BYTES]
    if plan.metadata["original_bytes"] != APP_BYTES or tx.sha(original) != APP_SHA256:
        raise ValueError("rollback App differs from accepted E1.42")
    return {
        "edition": "E3c-preparation", "result": "saved_recovery_bytes_verified",
        "run_label": run, "recorded_phase": state["phase"],
        "hardware_accessed": False, "device_written": False, "device_current_state_checked": False,
        "rollback_target": "E1.42", "flash_backup_bytes": len(plan.snapshot),
        "original_app_bytes": APP_BYTES, "original_app_sha256": APP_SHA256,
        "rollback_range": {"offset": tx.START, "end_exclusive": tx.START + len(plan.rollback),
                           "bytes": len(plan.rollback), "sha256": tx.sha(plan.rollback)},
        "candidate_file_required": False, "build_directory_required": False,
        "transport_scope": "body_COM4_app_only", "e3b_hardware_acceptance": "pending",
        "source_sha256": {name: tx.sha((ROOT / name).read_bytes()) for name in SOURCES},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="e3b-live-20261104-install2")
    parser.add_argument("--check", action="store_true", help="Compare the retained readiness report")
    args = parser.parse_args()
    report = inspect(args.run)
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != raw:
            raise ValueError("retained recovery readiness differs")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(raw)
    print(f"Saved rollback verified: E1.42, {report['rollback_range']['bytes']} App bytes; device not accessed")


if __name__ == "__main__":
    main()
