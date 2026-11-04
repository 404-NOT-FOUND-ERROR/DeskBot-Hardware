"""Offline baseline checks for E1.43b after the E1.43c-3 resource migration."""
from __future__ import annotations

import json
from pathlib import Path

from protocol import storage_upper_migration_transaction as migration
from protocol.storage_upper_migration_preflight import IMAGE_SHA256, LIVE_MODEL_SHA256

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path("artifacts/expression-upper-migration-live-e1-43c3-20261103/report.json")
REPORT_SHA256 = "c682f7ac38119cf8657035b362818a8375449490aac3a0a4f4a8e6b555be1ffc"
APP_BYTES = 4481776
APP_SHA256 = "14bc81889621bc8cd9f9c34077ade062231a964fb0e9efcdbbccdfdf296e2f1d"


def accepted_snapshot(raw: bytes, baseline: dict) -> None:
    """Compare immutable regions; NVS values may change during normal use."""
    if len(raw) != migration.FLASH_BYTES:
        raise ValueError("complete 32 MiB snapshot required")
    for row in baseline["protected_ranges"]:
        offset, size = row["offset"], row["bytes"]
        if migration.sha(raw[offset:offset + size]) != row["sha256"]:
            raise ValueError("migrated baseline differs: " + row["name"])
    app = baseline["original_app"]
    if migration.sha(raw[0x10000:0x10000 + app["bytes"]]) != app["sha256"]:
        raise ValueError("migrated App is not E1.42")


def load_migrated_baseline() -> dict:
    """Recheck saved report, migration plan, 32 MiB blocks and candidate bytes."""
    path = ROOT / REPORT
    report_raw = path.read_bytes()
    if path.is_symlink() or migration.sha(report_raw) != REPORT_SHA256:
        raise ValueError("sealed E1.43c-3 report differs")
    report = json.loads(report_raw)
    if (report.get("schema") != "deskbot.upper_migration.live_audit.v1"
            or report.get("stage") != "E1.43c-3"
            or report.get("result") != "migration_readback_and_boot_passed_interaction_pending"
            or report.get("mode") != migration.LIVE_MODE
            or report.get("run_label") != "e143c-live-20261103-direct1"):
        raise ValueError("completed E1.43c-3 migration evidence required")
    folder = ROOT / "tmp" / report["run_label"]
    plan = migration.load_plan(folder, execution=True)
    state = migration.Journal(folder, plan).load()
    result = json.loads((folder / "execute-result.json").read_bytes())
    if (state is None or state["phase"] != "awaiting_interaction"
            or plan.metadata["mode"] != migration.LIVE_MODE
            or plan.plan_id != report["plan_id"] or result["plan_id"] != plan.plan_id
            or result["result"] != "candidate_committed"):
        raise ValueError("migration plan or completion state differs")
    blocks = report["final_readback_blocks"]
    if len(blocks) != 32:
        raise ValueError("32 saved readback blocks required")
    chunks = []
    for index, row in enumerate(blocks):
        path = ROOT / row["path"]
        expected_suffix = f"-migration-full-backup-{index * 0x100000:08x}-00100000.bin"
        if (path.is_symlink() or path.resolve().parent != (folder / "captures").resolve()
                or not path.name.endswith(expected_suffix) or row["bytes"] != 0x100000):
            raise ValueError("saved readback block path or geometry differs")
        raw = path.read_bytes()
        if len(raw) != row["bytes"] or migration.sha(raw) != row["sha256"]:
            raise ValueError("saved readback block differs: " + str(index))
        chunks.append(raw)
    raw = b"".join(chunks)
    if migration.sha(raw) != report["final_readback_sha256"]:
        raise ValueError("saved final 32 MiB readback digest differs")
    migration.static_check(plan, raw)
    if (raw[0x8000:0x9000] != plan.candidate_table
            or raw[0x1000000:0x1800000] != plan.candidate_upper
            or migration.sha(plan.candidate_table) != migration.TABLE_SECTOR_SHA256
            or migration.sha(plan.candidate_upper) != IMAGE_SHA256
            or migration.sha(raw[0xBC8000:0xC90000]) != LIVE_MODEL_SHA256):
        raise ValueError("migrated table, assets or model differs")
    protected = [dict(row) for row in plan.metadata["static_ranges"]]
    protected += [
        {"name": "partition_table", "offset": 0x8000, "bytes": 0x1000,
         "sha256": migration.TABLE_SECTOR_SHA256},
        {"name": "upper_assets", "offset": 0x1000000, "bytes": 0x800000,
         "sha256": IMAGE_SHA256},
    ]
    baseline = {
        "layout": "upper-assets-8m", "identity_sha256": plan.metadata["identity_sha256"],
        "report": {"path": REPORT.as_posix(), "sha256": REPORT_SHA256},
        "migration_plan_id": plan.plan_id,
        "saved_readback_sha256": report["final_readback_sha256"],
        "original_app": {"bytes": APP_BYTES, "sha256": APP_SHA256},
        "protected_ranges": sorted(protected, key=lambda row: row["offset"]),
        "interaction_status": "current_version_reported_normal_continuous_mapping_deferred",
    }
    accepted_snapshot(raw, baseline)
    return baseline
