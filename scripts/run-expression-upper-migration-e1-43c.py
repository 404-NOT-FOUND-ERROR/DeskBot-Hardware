#!/usr/bin/env python3
"""Migrate the accepted E1.18 resource image on COM4 or recover the same run."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import storage_upper_migration_transaction as tx
from protocol.storage_upper_experiment_esptool import load_persisted_plan
from protocol.storage_upper_migration_esptool import UpperMigrationEsptoolDevice

CONFIRMATION = "COM4_E1_43C_UPPER_ASSETS_AND_TABLE"


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def folder_for(label: str) -> Path:
    if re.fullmatch(r"e143c-live-[a-z0-9][a-z0-9-]{0,39}", label) is None:
        raise ValueError("hardware run label must begin e143c-live-")
    folder = ROOT / "tmp" / label
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("hardware evidence must stay inside repository tmp")
    return folder


def save_json(path: Path, value: dict) -> None:
    tx.write_new(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def run_new(folder: Path) -> dict:
    if folder.exists():
        raise FileExistsError("retain the previous run; use recovery or a new run label")
    # Reuse the sealed input validation, without loading or writing a B App.
    inputs = load_script("upper_migration_inputs", "exercise-expression-e1-43c1-migration.py")
    _, _, upper, table = inputs.verified_inputs()
    prep = json.loads((ROOT / "artifacts/expression-continuous-mmap-e1-43b-20261103/deployment-preparation.json").read_bytes())
    acceptance = json.loads((ROOT / prep["baseline_acceptance"]["path"]).read_bytes())
    accepted = json.loads((ROOT / acceptance["sealed_experiment_report"]).read_bytes())
    previous_label = accepted.get("run_label", "")
    if re.fullmatch(r"e143a7-[a-z0-9][a-z0-9-]{0,39}", previous_label) is None:
        raise ValueError("sealed acceptance must identify the private A7 device plan")
    previous = load_persisted_plan(ROOT / "tmp" / previous_label)
    if previous.plan_id != accepted["plan_id"]:
        raise ValueError("accepted device plan differs from sealed report")
    transport = load_script("upper_migration_backup", "run-expression-continuous-mmap-e1-43b.py")
    backup_folder = folder.with_name(folder.name.replace("e143c-live-", "e143c-backup-", 1))
    backup_folder.mkdir(exist_ok=False)
    reader = transport.AppDevice(backup_folder)
    confirmed, plan, device = False, None, None
    try:
        identity = reader.identity()
        if identity != previous.expected_identity_sha256:
            raise ValueError("COM4 is not the previously accepted N32R16 body")
        confirmed = True
        print("COM4 identity confirmed; reading the complete 32 MiB backup.", flush=True)
        snapshot = reader.read_full()
        transport.accepted_snapshot(snapshot, prep)
        tx.write_new(backup_folder / "snapshot-32m.bin", snapshot)
        plan = tx.make_plan(snapshot, upper, table, identity_sha256=identity,
                            run_label=folder.name, mode=tx.LIVE_MODE,
                            authorization=tx.DIRECT_AUTHORIZATION)
        tx.persist_plan(folder, plan)
        save_json(folder / "live-preflight.json", {
            "schema": "deskbot.upper_migration.live_preflight.v1", "plan_id": plan.plan_id,
            "authorization": tx.DIRECT_AUTHORIZATION, "user_instruction": "直接迁移吧",
            "continuous_mapping_interaction_status": "pending",
            "snapshot": (backup_folder / "snapshot-32m.bin").relative_to(ROOT).as_posix(),
            "snapshot_sha256": tx.sha(snapshot), "snapshot_bytes": len(snapshot),
            "baseline_matches_accepted_A7": True, "write_ranges": plan.metadata["write_ranges"],
            "device_written": False,
        })
        device = UpperMigrationEsptoolDevice(folder, write_enabled=True)
        print("Backup and recovery plan saved; migrating resources, then the partition table.", flush=True)
        outcome = tx.execute(folder, device, initial_snapshot=snapshot)
        save_json(folder / "execute-result.json", outcome)
        return outcome
    except BaseException:
        if confirmed:
            state = tx.Journal(folder, plan).load() if plan is not None else None
            if state is not None and state["write_intent"] and state["phase"] not in {"restored", "awaiting_interaction"}:
                if device is None:
                    device = UpperMigrationEsptoolDevice(folder, write_enabled=True)
                tx.recover(folder, device)
            elif state is None or state["phase"] == "prepared":
                reader.boot_original()
        raise


def run_recovery(folder: Path) -> dict:
    plan = tx.load_plan(folder)
    if plan.metadata["mode"] != tx.LIVE_MODE:
        raise ValueError("recovery requires a hardware migration plan")
    device = UpperMigrationEsptoolDevice(folder, write_enabled=True)
    outcome = tx.recover(folder, device)
    numbers = [int(m[1]) for p in folder.iterdir()
               if (m := re.fullmatch(r"recovery-result-([0-9]+)\.json", p.name))]
    save_json(folder / f"recovery-result-{max(numbers, default=0)+1:03d}.json", outcome)
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("execute", "recover"))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--confirm", required=True, choices=(CONFIRMATION,))
    args = parser.parse_args()
    folder = folder_for(args.run_label)
    outcome = run_new(folder) if args.phase == "execute" else run_recovery(folder)
    print(json.dumps({"result": outcome["result"], "run_label": folder.name,
                      "device_written": outcome["device_written"],
                      "physical_interaction_accepted": False}, ensure_ascii=False), flush=True)
    return 0 if outcome["result"] in {"candidate_committed", "restored"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
