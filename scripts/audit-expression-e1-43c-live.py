#!/usr/bin/env python3
"""Audit saved migration readbacks and boot logs without opening a device."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import storage_upper_migration_transaction as tx
from protocol.storage_upper_experiment_esptool import _NORMAL_BOOT_MARKERS, _PANIC_MARKERS
from protocol.storage_upper_migration_esptool import ASSET_ERRORS
from protocol.storage_n32r16 import PACKAGE_BYTES


def audit(label: str) -> dict:
    if re.fullmatch(r"e143c-live-[a-z0-9][a-z0-9-]{0,39}", label) is None:
        raise ValueError("hardware migration run label required")
    folder = ROOT / "tmp" / label
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("run directory must stay inside repository tmp")
    plan = tx.load_plan(folder, execution=True)
    state = tx.Journal(folder, plan).load()
    result = json.loads((folder / "execute-result.json").read_bytes())
    if (plan.metadata["mode"] != tx.LIVE_MODE or state["phase"] != "awaiting_interaction"
            or result["result"] != "candidate_committed" or result["device_written"] is not True
            or result["plan_id"] != plan.plan_id):
        raise ValueError("completed hardware migration and boot result required")
    pattern = re.compile(r"([0-9]+)-migration-full-backup-([0-9a-f]{8})-([0-9a-f]{8})\.bin")
    files = sorted((p for p in (folder / "captures").iterdir() if pattern.fullmatch(p.name)),
                   key=lambda p: int(pattern.fullmatch(p.name)[1]))
    current = []
    for path in files:
        match = pattern.fullmatch(path.name)
        offset, size = int(match[2], 16), int(match[3], 16)
        if offset == 0:
            current = []
        # Truncated transfer attempts may precede the completed same block.
        if current and current[-1][0] == offset:
            current.pop()
        current.append((offset, size, path))
    chunks, expected_offset, references = [], 0, []
    for offset, size, path in current:
        raw = path.read_bytes()
        if path.is_symlink() or offset != expected_offset or len(raw) != size:
            raise ValueError("last complete readback has a gap or short block")
        expected_offset += size
        chunks.append(raw)
        references.append({"path": path.relative_to(ROOT).as_posix(), "sha256": tx.sha(raw), "bytes": size})
    raw = b"".join(chunks)
    if len(raw) != tx.FLASH_BYTES:
        raise ValueError("final complete 32 MiB readback required")
    nvs = tx.static_check(plan, raw)
    if (raw[tx.TABLE_START:tx.TABLE_START + tx.TABLE_BYTES] != plan.candidate_table
            or raw[tx.UPPER_START:tx.UPPER_START + tx.UPPER_BYTES] != plan.candidate_upper):
        raise ValueError("candidate table or resource readback differs")
    logs = sorted((folder / "captures").glob("*-migration-candidate-boot.log"))
    if len(logs) != 1:
        raise ValueError("one completed candidate boot log required")
    boot = logs[0].read_text(encoding="utf-8")
    if (not all(m in boot for m in _NORMAL_BOOT_MARKERS) or boot.count("Calling app_main()") != 1
            or any(m in boot for m in _PANIC_MARKERS + ASSET_ERRORS)):
        raise ValueError("candidate boot evidence failed")
    output = ROOT / "artifacts/expression-upper-migration-live-e1-43c3-20261103"
    review = json.loads((output / "execution-review.json").read_bytes())
    for relative, info in review["files"].items():
        if tx.sha((ROOT / relative).read_bytes()) != info["sha256"]:
            raise ValueError("execution input changed after sealing: " + relative)
    writes = sorted((folder / "logs").glob("*-write-*.log"))
    if len(writes) != 2 or any("Hash of data verified" not in p.read_text(encoding="utf-8") for p in writes):
        raise ValueError("exactly two verified resource/table writes required")
    host = json.loads((output / "host-tests.json").read_bytes())
    audit_tests = json.loads((output / "saved-audit-tests.json").read_bytes())
    full = json.loads((output / "full-suite-final.json").read_bytes())
    return {
        "schema": "deskbot.upper_migration.live_audit.v1", "stage": "E1.43c-3", "date": "2026-11-03",
        "result": "migration_readback_and_boot_passed_interaction_pending", "run_label": label,
        "mode": tx.LIVE_MODE, "plan_id": plan.plan_id, "device_written": True,
        "authorization": tx.DIRECT_AUTHORIZATION, "continuous_mapping_interaction_status": "pending",
        "physical_interaction_accepted": False,
        "write_ranges": plan.metadata["write_ranges"], "write_logs": [p.relative_to(ROOT).as_posix() for p in writes],
        "snapshot_sha256": plan.metadata["snapshot_sha256"], "final_readback_sha256": tx.sha(raw),
        "final_readback_blocks": references,
        "protected_static_ranges": [{**row, "verified": True} for row in plan.metadata["static_ranges"]],
        "nvs": nvs, "candidate_table_sha256": tx.sha(plan.candidate_table),
        "candidate_upper_sha256": tx.sha(plan.candidate_upper),
        "resources": {"members": 29, "package_bytes": PACKAGE_BYTES, "partition_bytes": tx.UPPER_BYTES,
                      "free_bytes": tx.UPPER_BYTES - PACKAGE_BYTES, "surprised": "shocked.eaf",
                      "package_unchanged": True},
        "boot": {"path": logs[0].relative_to(ROOT).as_posix(), "sha256": tx.sha(logs[0].read_bytes()),
                 "normal_markers": {m: m in boot for m in _NORMAL_BOOT_MARKERS}, "app_main_count": 1,
                 "panic_or_asset_model_error_markers": [], "resource_mount_proven_by_boot_log": False},
        "host_tests": {"tests": sum(row["tests"] for row in host) + audit_tests["tests"],
                       "all_passed": all(row["exit_code"] == 0 for row in host) and audit_tests["exit_code"] == 0,
                       "reports": ["host-tests.json", "saved-audit-tests.json"]},
        "full_offline_suite": full,
        "next": ["screen/menu/touch/conversation/sleep acceptance", "single new expression"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    args = parser.parse_args()
    report = audit(args.run_label)
    path = ROOT / "artifacts/expression-upper-migration-live-e1-43c3-20261103/report.json"
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    tx.write_new(path, raw)
    tx.write_new(ROOT / "tmp" / args.run_label / "post-migration-audit.json", raw)
    print(json.dumps({k: report[k] for k in ("stage", "result", "device_written", "physical_interaction_accepted")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
