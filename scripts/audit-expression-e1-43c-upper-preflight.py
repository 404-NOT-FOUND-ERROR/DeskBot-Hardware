#!/usr/bin/env python3
"""Verify retained upper-resource migration inputs offline, without producing BINs."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/expression-upper-migration-e1-43c-20261103"
B = "artifacts/expression-continuous-mmap-e1-43b-20261103"
sys.path.insert(0, str(ROOT))

from protocol.storage_migration import safe_relative_path, sha256_bytes, sha256_file  # noqa: E402
from protocol.storage_n32r16 import MIB, PACKAGE_BYTES, validate_report as validate_e136  # noqa: E402
from protocol.storage_upper_migration_preflight import (  # noqa: E402
    FUTURE_SEQUENCE, LIVE_MODEL_SHA256, PENDING, RECOVERY_SEQUENCE, UPPER_BYTES,
    future_geometry, protected_geometry,
    upper_plan, validate_accepted_baseline, validate_deferred_mapping, validate_members,
    validate_preflight_report, validate_resource_images, validate_upper_layout,
)


def make_report() -> dict:
    inputs = {}

    def read(relative: str, digest: str | None = None) -> bytes:
        path = safe_relative_path(ROOT, relative)
        raw = path.read_bytes()
        actual = sha256_bytes(raw)
        if digest is not None and actual != digest:
            raise ValueError("input digest changed: " + relative)
        inputs[relative] = {"bytes": len(raw), "sha256": actual}
        return raw

    def read_json(relative: str, digest: str | None = None) -> dict:
        value = json.loads(read(relative, digest))
        if not isinstance(value, dict):
            raise ValueError("expected JSON object: " + relative)
        return value

    readiness = read_json(B + "/readiness.json")
    validate_deferred_mapping(readiness)
    for ref in readiness["evidence"].values():
        read(ref["path"], ref["sha256"])
    for relative, digest in readiness["execution_tool_sha256"].items():
        read(relative, digest)
    app = readiness["candidate_app"]
    if len(read(app["path"], app["sha256"])) != app["bytes"]:
        raise ValueError("E1.43b diagnostic App size changed")
    verification = read_json(B + "/build-verification.json")
    build = read_json(verification["build_report"], verification["build_report_sha256"])
    if verification.get("result") != "passed" or len(build["source_sha256"]) != 1062:
        raise ValueError("E1.43b full source verification is incomplete")
    read("firmware/body/SOURCE_MANIFEST.sha256", verification["source_manifest_sha256"])
    for relative, digest in build["source_sha256"].items():
        path = safe_relative_path(ROOT, "firmware/body/" + relative)
        if sha256_file(path) != digest:
            raise ValueError("E1.43b firmware source changed: " + relative)

    checkpoint = read_json(B + "/deferred-checkpoint-20261103.json")
    if (checkpoint.get("status") != "offline_saved_live_deferred"
            or checkpoint.get("readiness_sha256") != inputs[B + "/readiness.json"]["sha256"]
            or checkpoint.get("device_written") is not False
            or checkpoint.get("serial_accessed") is not False
            or checkpoint.get("physical_interaction_accepted") is not False):
        raise ValueError("E1.43b saved checkpoint no longer matches readiness")
    archive = checkpoint["archive"]
    if len(read(archive["path"], archive["sha256"])) != archive["bytes"]:
        raise ValueError("checkpoint archive size changed")

    prep = read_json(B + "/deployment-preparation.json")
    acceptance_ref = prep["baseline_acceptance"]
    acceptance = read_json(acceptance_ref["path"], acceptance_ref["sha256"])
    live = read_json(acceptance["sealed_experiment_report"], acceptance["sealed_experiment_report_sha256"])
    validate_accepted_baseline(live, acceptance)

    e136 = read_json("artifacts/expression-storage-n32r16-e1-36-20261030/report.json")
    validate_e136(e136)
    plan = upper_plan()
    selected = next(row for row in e136["candidates"] if row["id"] == plan["id"])
    validate_upper_layout(plan, read(selected["table_file"], selected["table_sha256"]))
    read("firmware/body/partitions/v2/16m.csv", e136["formal_csv_sha256"])

    # Use E1.35b only for the retained E1.18 bytes/member inventory, never its write plan.
    historical = read_json("artifacts/expression-storage-migration-e1-35b-20261029/report.json")
    original = read_json("tmp/e135b-migration-20261029/manifest.json", historical["manifest_sha256"])
    package = read("tmp/expression-e1-21-package-player-20261026-run2/rollback-e1-18-exact.bin")
    old = read("tmp/e135b-migration-20261029/old/assets.bin")
    image = read("tmp/e135b-migration-20261029/candidate/assets.bin")
    validate_resource_images(package, old, image)
    parser_path = "scripts/audit-expression-resources.py"
    read(parser_path)
    spec = importlib.util.spec_from_file_location("e143c_resource_parser", ROOT / parser_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("resource parser unavailable")
    parser = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = parser
    spec.loader.exec_module(parser)
    members = parser.parse_package_bytes(package, modern=False)["members"]
    resources = validate_members(original["source_inputs"]["asset_members"], members)
    for relative in ("protocol/storage_upper_migration_preflight.py",
                     "protocol/storage_migration.py", "protocol/storage_n32r16.py",
                     "protocol/storage_budget.py", "scripts/audit-expression-e1-43c-upper-preflight.py",
                     "tests/test_storage_upper_migration_preflight.py"):
        read(relative)
    report = {
        "schema": "deskbot.upper_assets.preflight.v1", "stage": "E1.43c", "date": "2026-11-03",
        "result": "offline_preflight_passed_live_pending",
        "serial_accessed": False, "device_written": False, "firmware_changed": False,
        "table_generated": False, "flash_bundle_generated": False,
        "deployment_eligible": False, "continuous_mapping_accepted": False,
        "layout": plan, "resources": resources, "inputs": inputs,
        "firmware_source_files_verified": len(build["source_sha256"]),
        "future_affected_ranges": future_geometry(), "protected_ranges": protected_geometry(),
        "standalone_model": {"offset": 0xBC8000, "bytes": 800 * 1024, "write": False,
             "accepted_live_sha256": LIVE_MODEL_SHA256,
             "source": "accepted_A7_readback_reference_fresh_backup_still_required"},
        "budget": {"partition_bytes": UPPER_BYTES, "package_bytes": PACKAGE_BYTES,
             "free_bytes": UPPER_BYTES - PACKAGE_BYTES, "publish_soft_limit_bytes": 7 * MIB,
             "additional_publish_budget_bytes": 7 * MIB - PACKAGE_BYTES, "reserve_bytes": MIB},
        "pending_before_deployment": PENDING,
        "future_sequence": FUTURE_SEQUENCE,
        "future_recovery": RECOVERY_SEQUENCE,
        "recovery_design_status": "specified_not_implemented_or_fault_tested",
        "historical_inplace_plan_reused": False,
        "historical_rebuild_check": "E1.35b --check rejects later firmware manifest; retained bytes audited directly",
    }
    validate_preflight_report(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify existing report; write no output")
    args = parser.parse_args()
    report = make_report()
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path = OUT / "preflight.json"
    if args.check:
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError("E1.43c preflight report is stale or missing")
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("retained preflight differs; preserve it and use a new stage directory")
        if not path.exists():
            with path.open("xb") as handle:
                handle.write(raw)
    print(json.dumps({"stage": "E1.43c", "result": report["result"],
          "members": report["resources"]["members"], "source_files": report["firmware_source_files_verified"],
          "input_files": len(report["inputs"]), "deployment_eligible": False,
          "device_written": False, "report": path.relative_to(ROOT).as_posix()}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("E1.43c preflight failed: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
