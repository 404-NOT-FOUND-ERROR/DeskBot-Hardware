#!/usr/bin/env python3
"""Independently verify E3c-3b read-only backup and App recovery evidence."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol import storage_continuous_mmap_e3c3b_baseline as baseline
from protocol.storage_continuous_mmap_experiment import APP_START, FLASH_BYTES, load_plan, make_plan, sha, static_check, write_new


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("reviewed preparation audit helpers are unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


previous = _module("e143b_previous_transfer_audit", ROOT / "scripts/audit-expression-e1-43b-e3c3a-deployment.py")
preparation = _module("e143b_e3c3b_preparation", ROOT / "scripts/prepare-expression-e1-43b-e3c3b-deployment.py")
RUN_LABEL = preparation.RUN_LABEL


def _regular_entries(directory: Path) -> list[Path]:
    directory = baseline.safe_path(directory, ROOT / "tmp")
    if not directory.is_dir():
        raise ValueError("evidence directory is missing")
    entries = list(directory.iterdir())
    for path in entries:
        baseline.safe_path(path, directory)
        if not path.is_file():
            raise ValueError("evidence directory must contain only regular files")
    return entries


def audit_preparation(folder: Path) -> dict:
    folder = baseline.safe_path(folder, ROOT / "tmp")
    if folder.parent != (ROOT / "tmp").resolve() or RUN_LABEL.fullmatch(folder.name) is None:
        raise ValueError("E3c-3b preparation must be a named direct child of tmp")
    entries = _regular_entries(folder / "captures")
    _regular_entries(folder / "logs")
    allowed = {"captures", "logs", "payloads", "snapshot-32m.bin", "device-observation.json",
               "rollback-app.bin", "candidate-app-padded.bin", "plan.json", "preflight.json"}
    for path in folder.iterdir():
        baseline.safe_path(path, folder)
        if path.name not in allowed or (path.name not in {"captures", "logs", "payloads"} and not path.is_file()):
            raise ValueError("unexpected preparation evidence")
    def local(name: str) -> bytes:
        path = baseline.safe_path(folder / name, folder)
        if not path.is_file():
            raise ValueError("preparation evidence must be a regular file")
        return path.read_bytes()
    receipt = json.loads(local("preflight.json"))
    observation = json.loads(local("device-observation.json"))
    snapshot = local("snapshot-32m.bin")
    if (receipt.get("schema") != "deskbot.continuous_mmap.live_preflight.e3c3b.v1"
            or receipt.get("edition") != "E1.43b-2" or receipt.get("baseline") != "E3c-3b"
            or receipt.get("result") != "snapshot_validated_rollback_prepared"
            or receipt.get("run_label") != folder.name or receipt.get("target") != "body_COM4"
            or receipt.get("normal_boot_observed") is not True
            or receipt.get("device_accessed") is not True):
        raise ValueError("E3c-3b preparation receipt differs")
    for key in ("device_written", "flash_written", "nvs_written", "assets_written",
                "bootloader_written", "partition_table_written", "c61_accessed"):
        if receipt.get(key) is not False:
            raise ValueError("read-only preparation contains a write result")
    if len(snapshot) != FLASH_BYTES or receipt.get("snapshot") != {
            "file": "snapshot-32m.bin", "bytes": FLASH_BYTES, "sha256": sha(snapshot)}:
        raise ValueError("saved snapshot differs from receipt")
    expected_observation = {
        "schema": "deskbot.continuous_mmap.device_observation.e3c3b.v1", "run_label": folder.name,
        "target": "body_COM4", "identity_sha256": receipt.get("identity_sha256"),
        "snapshot_bytes": FLASH_BYTES, "snapshot_sha256": sha(snapshot),
        "baseline_validated": True, "normal_boot_observed": True, "device_written": False}
    if observation != expected_observation:
        raise ValueError("device observation differs from preparation")
    chunks = previous._check_snapshot_chunks(folder, snapshot)
    transfers = previous._check_read_transfer_logs(folder, receipt["identity_sha256"])
    bound = previous._bind_read_transfers_to_captures(transfers, chunks)
    build = baseline.load_e3c3b_probe_inputs()
    facts = baseline.validate_e3c3b_snapshot(snapshot, receipt["identity_sha256"])
    if (receipt.get("baseline_reference") != facts or receipt.get("build") != {
            "report": build["report"], "audit": build["audit"], "candidate_path": build["candidate_path"]}):
        raise ValueError("baseline or probe build binding differs")
    plan = load_plan(folder, execution=True)
    expected = make_plan(snapshot, build["candidate"], original_app_bytes=facts["original_app"]["bytes"],
                         identity_sha256=receipt["identity_sha256"], run_label=folder.name)
    if plan != expected or receipt.get("plan_id") != plan.plan_id:
        raise ValueError("saved App recovery plan differs from independent reconstruction")
    static_check(plan, snapshot)
    if (receipt.get("rollback") != {"file": "rollback-app.bin", "offset": APP_START,
                                   "bytes": len(plan.rollback), "sha256": sha(plan.rollback)}
            or receipt.get("candidate_write") != {"offset": APP_START, "payload_bytes": len(build["candidate"]),
               "erase_bytes": plan.metadata["erase_bytes"], "end_exclusive": APP_START + plan.metadata["erase_bytes"]}):
        raise ValueError("App erase union or rollback receipt differs")
    boot_logs = [path for path in entries if re.fullmatch(r"[0-9]{3}-normal-boot\.log", path.name)]
    if len(boot_logs) != 1 or {path.name for path in entries} != {
            row["capture_file"] for row in chunks} | {boot_logs[0].name}:
        raise ValueError("unexpected boot or snapshot capture evidence")
    boot_facts = preparation.validate_boot_log(boot_logs[0].read_text(encoding="utf-8"))
    command_logs = list((folder / "logs").glob("[0-9][0-9][0-9]-normal-boot-capture.log"))
    if len(command_logs) != 1:
        raise ValueError("one boot capture command log is required")
    match = re.search(r"Capture complete: (.+) \(lines=([0-9]+)\)", command_logs[0].read_text(encoding="utf-8"))
    if (match is None or Path(match.group(1)).resolve() != boot_logs[0].resolve() or int(match.group(2)) <= 0
            or sha(previous.CAPTURE_SCRIPT.read_bytes()) != previous.CAPTURE_SCRIPT_SHA256):
        raise ValueError("boot command log or capture implementation differs")
    return {"schema": "deskbot.continuous_mmap.live_preflight_audit.e3c3b.v1", "result": "passed",
            "baseline": "E3c-3b", "run_label": folder.name, "plan_id": plan.plan_id,
            "identity_sha256": receipt["identity_sha256"], "snapshot_sha256": sha(snapshot),
            "snapshot_bytes": len(snapshot), "snapshot_chunks": chunks, "com4_read_transfers": bound,
            "build_report": build["report"], "build_audit": build["audit"],
            "candidate_app_sha256": build["candidate_sha256"], "rollback_app_sha256": sha(plan.rollback),
            "app_erase_start": APP_START, "app_erase_end_exclusive": APP_START + len(plan.rollback),
            "normal_boot": boot_facts, "nvs_sha256": facts["nvs"]["sha256"],
            "write_logs": 0, "write_command_payload_files": 0, "device_written": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    archive = ROOT / baseline.ARCHIVE
    output = baseline.safe_path(output, archive, exists=False)
    if output.parent != archive.resolve() or output.exists():
        raise ValueError("audit output must be a new direct child of the E3c-3b archive")
    result = audit_preparation(ROOT / "tmp" / args.run_label)
    write_new(output, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({key: result[key] for key in ("result", "run_label", "plan_id", "device_written")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
