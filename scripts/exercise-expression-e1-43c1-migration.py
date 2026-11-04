#!/usr/bin/env python3
"""Simulate upper-resource migration or recover a saved simulation. No hardware mode."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_migration import safe_relative_path  # noqa: E402
from protocol.storage_upper_migration_preflight import validate_preflight_report  # noqa: E402
from protocol.storage_upper_migration_simulator import FileFlash, SimulatedPowerLoss, synthetic_snapshot  # noqa: E402
from protocol.storage_upper_migration_transaction import (  # noqa: E402
    Journal, execute, make_plan, persist_plan, recover, sha, write_new,
)


def folder_for(label: str) -> Path:
    if re.fullmatch(r"e143c-sim-[a-z0-9][a-z0-9-]{0,39}", label) is None:
        raise ValueError("simulation run label must begin e143c-sim-")
    folder = ROOT / "tmp" / label
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("simulation run must be a direct child of repository tmp")
    return folder


def verified_inputs() -> tuple[bytes, bytes, bytes, bytes]:
    report = json.loads((ROOT / "artifacts/expression-upper-migration-e1-43c-20261103/preflight.json").read_bytes())
    validate_preflight_report(report)
    for relative, info in report["inputs"].items():
        raw = safe_relative_path(ROOT, relative).read_bytes()
        if len(raw) != info["bytes"] or sha(raw) != info["sha256"]:
            raise ValueError("retained offline preflight input changed: " + relative)
    old = ROOT / "tmp/e135b-migration-20261029/old"
    upper = ROOT / "tmp/e135b-migration-20261029/candidate/assets.bin"
    table = ROOT / "tmp/e136-n32r16-layout-20261030/tables/02-upper-assets-8m.bin"
    return (old.joinpath("partition-table.bin").read_bytes(), old.joinpath("assets.bin").read_bytes(),
            upper.read_bytes(), table.read_bytes())


def run_new(folder: Path, interrupt_at: str | None) -> dict:
    if folder.exists():
        raise FileExistsError("simulation directory already exists; use recover-simulation or a new label")
    old_table, old_assets, upper, table = verified_inputs()
    raw = synthetic_snapshot(old_table, old_assets)
    plan = make_plan(raw, upper, table, identity_sha256=sha(b"deskbot-synthetic-flash-device"), run_label=folder.name)
    persist_plan(folder, plan)
    device = FileFlash.create(folder, raw, interrupt_at=interrupt_at)
    try:
        outcome = execute(folder, device, initial_snapshot=raw)
    except SimulatedPowerLoss as error:
        return {"result": "simulated_power_loss", "error": str(error), "run_label": folder.name,
                "mode": "offline_simulation", "device_written": False,
                "recovery_phase": Journal(folder, plan).load()["phase"], "plan_id": plan.plan_id,
                "simulated_writes": device.writes, "snapshot_sha256": plan.metadata["snapshot_sha256"]}
    outcome["simulated_writes"] = device.writes
    outcome["run_label"] = folder.name
    outcome["full_readback_sha256"] = sha(device.read_full())
    outcome["snapshot_sha256"] = device.plan.metadata["snapshot_sha256"]
    outcome["full_image_equal"] = outcome["full_readback_sha256"] == outcome["snapshot_sha256"]
    return outcome


def run_recovery(folder: Path) -> dict:
    # This path deliberately does not load preflight, source, candidate or build inputs.
    device = FileFlash(folder)
    outcome = recover(folder, device)
    outcome["simulated_writes"] = device.writes
    outcome["run_label"] = folder.name
    outcome["full_readback_sha256"] = sha(device.read_full())
    outcome["snapshot_sha256"] = device.plan.metadata["snapshot_sha256"]
    outcome["full_image_equal"] = outcome["full_readback_sha256"] == outcome["snapshot_sha256"]
    return outcome


def save_result(folder: Path, result: dict) -> Path:
    # Inspect direct result files only; a restarted simulation never overwrites evidence.
    numbers = [int(match.group(1)) for p in folder.iterdir() if p.is_file()
               and (match := re.fullmatch(r"result-([0-9]+)\.json", p.name))]
    sequence = max(numbers, default=0) + 1
    path = folder / f"result-{sequence:03d}.json"
    write_new(path, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("simulate", "recover-simulation"))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--interrupt-at", choices=("upper_write_intent", "table_write_intent"))
    args = parser.parse_args()
    folder = folder_for(args.run_label)
    if args.phase == "recover-simulation" and args.interrupt_at is not None:
        parser.error("recovery does not create a new interruption")
    outcome = run_new(folder, args.interrupt_at) if args.phase == "simulate" else run_recovery(folder)
    path = save_result(folder, outcome)
    print(json.dumps({"result": outcome["result"], "mode": "offline_simulation", "device_written": False,
                      "report": path.relative_to(ROOT).as_posix()}, ensure_ascii=False, indent=2))
    if outcome["result"] == "simulated_power_loss":
        return 3
    return 0 if outcome["result"] in {"candidate_committed_simulation", "restored_simulation"} else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print("offline migration exercise failed: " + str(error), file=sys.stderr)
        raise SystemExit(2)
