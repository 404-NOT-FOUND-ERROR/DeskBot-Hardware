#!/usr/bin/env python3
"""Prepare the exact App-only E1.43b experiment ranges from audited files; offline."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("continuous_preparation_audit", ROOT / "scripts/audit-expression-e1-43b-build.py")
AUDIT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AUDIT)
BASELINE_REPORT = ROOT / "artifacts/expression-capacity-read-mmap-e1-42-20261031/build-report.json"
ACCEPTANCE = ROOT / "artifacts/expression-upper-snapshot-e1-43a7-20261103/acceptance-20261103.json"


def prepare(build_report: Path) -> dict:
    verified = AUDIT.audit(build_report)
    report = json.loads(build_report.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
    acceptance = json.loads(ACCEPTANCE.read_text(encoding="utf-8"))
    if (acceptance["stage"] != "E1.43a-7" or acceptance["result"] != "restored_baseline_interaction_accepted"
            or acceptance["baseline"] != {"app": "E1.42", "bootloader": "E1.40", "assets": "E1.18",
                                           "partition_layout": "unchanged_legacy"}):
        raise ValueError("restored A7 interaction acceptance required")
    live_path = ROOT / acceptance["sealed_experiment_report"]
    if AUDIT.BUILD.BASE.sha(live_path) != acceptance["sealed_experiment_report_sha256"]:
        raise ValueError("sealed A7 report digest changed")
    live = json.loads(live_path.read_text(encoding="utf-8"))
    on = report["rounds"]["on"]
    payload = on["artifacts"]["xiaozhi.bin"]
    candidate = ROOT / on["evidence_directory"] / "xiaozhi.bin"
    old = baseline["artifacts"]["xiaozhi.bin"]
    span = (max(payload["bytes"], old["bytes"]) + 4095) // 4096 * 4096
    if span > 0x5DC000 or payload["bytes"] > 0x5DC000 - 1024 * 1024:
        raise ValueError("App does not fit the reviewed partition and margin")
    return {"schema": "deskbot.continuous_mmap.deployment_preparation.v1", "stage": "E1.43b",
            "result": "offline_preparation_passed_live_backup_required", "port": "COM4", "chip": "esp32s3",
            "build_verification": verified,
            "baseline_acceptance": {"path": ACCEPTANCE.relative_to(ROOT).as_posix(),
                                     "sha256": AUDIT.BUILD.BASE.sha(ACCEPTANCE)},
            "candidate_app": {"path": candidate.relative_to(ROOT).as_posix(), **payload},
            "original_app": old,
            "write_range": {"offset": 0x10000, "payload_bytes": payload["bytes"],
                            "erase_bytes": span, "end_exclusive": 0x10000 + span},
            "probe_read_range": {"offset": 0x1000000, "bytes": 0x800000, "flash_written": False},
            "before_write": ["fresh_COM4_chip_bus_capacity_and_security_identity",
                             "same_run_32MiB_backup_and_digest", "accepted_E142_App_E140_bootloader_legacy_table",
                             "accepted_live_E118_assets_and_models", "exact_current_App_erase_span_saved",
                             "durable_recovery_inputs_and_intent_before_first_write"],
            "protected_static_sha256": {name: row["sha256"] for name, row in live["restored_ranges"].items()
                                         if name not in {"app", "nvs", "gap_after_app"}},
            "capture": {"quiet_wait_seconds": 120, "hold_seconds": 180,
                        "sample_interval_seconds": 10, "maximum_log_bytes": 2097152},
            "restore": {"range": "same_App_erase_span", "payload": "same_run_exact_saved_bytes",
                        "verify": "byte_for_byte_App_and_all_static_regions_then_normal_boot",
                        "nvs": "observe_runtime_changes_separately"},
            "fresh_live_backup_ready": False, "device_accessed": False, "device_written": False,
            "cpu_verified": False, "physical_interaction_accepted": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.relative_to(ROOT).parts[0] not in {"tmp", "artifacts"} or output.exists():
        parser.error("output must be a new file inside repository tmp or artifacts")
    result = prepare(args.build_report.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["result"])
