#!/usr/bin/env python3
"""Prepare App-only continuous mapping for the migrated upper-assets-8m body."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_continuous_mmap_baseline import load_migrated_baseline
from protocol.storage_continuous_mmap_experiment import APP_BYTES, APP_START, sha, write_new


def audit_build(path: Path) -> dict:
    # Build auditing imports the locked IDF/YAML helpers only when requested.
    spec = importlib.util.spec_from_file_location("continuous_upper_build_audit",
                                                ROOT / "scripts/audit-expression-e1-43b-build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit(path)


def prepare(build_report: Path) -> dict:
    build_report = build_report.resolve()
    verified = audit_build(build_report)
    baseline = load_migrated_baseline()
    build = json.loads(build_report.read_bytes())
    on = build["rounds"]["on"]
    payload = on["artifacts"]["xiaozhi.bin"]
    candidate = ROOT / on["evidence_directory"] / "xiaozhi.bin"
    raw = candidate.read_bytes()
    if candidate.is_symlink() or len(raw) != payload["bytes"] or sha(raw) != payload["sha256"]:
        raise ValueError("audited diagnostic App differs")
    span = (max(payload["bytes"], baseline["original_app"]["bytes"]) + 4095) // 4096 * 4096
    if span > APP_BYTES or payload["bytes"] > APP_BYTES - 1024 * 1024:
        raise ValueError("diagnostic App exceeds partition or 1 MiB margin")
    return {
        "schema": "deskbot.continuous_mmap.upper_preparation.v1", "stage": "E1.43b-2",
        "result": "offline_preparation_passed_live_backup_required", "port": "COM4", "chip": "esp32s3",
        "build_verification": verified, "baseline": baseline,
        "candidate_app": {"path": candidate.relative_to(ROOT).as_posix(), **payload},
        "original_app": baseline["original_app"],
        "write_range": {"offset": APP_START, "payload_bytes": payload["bytes"],
                        "erase_bytes": span, "end_exclusive": APP_START + span},
        "probe_read_range": {"offset": 0x1000000, "bytes": 0x800000, "flash_written": False},
        "mapping": {"layout": "upper-assets-8m", "allowed_page_costs": [0, 128],
                    "shared_mapping": "IDF reuses the active assets mapping", "page_reserve": 16},
        "capture": {"quiet_wait_seconds": 120, "hold_seconds": 180,
                    "sample_interval_seconds": 10, "maximum_log_bytes": 2097152},
        "before_write": ["fresh_COM4_identity", "same_run_32MiB_backup", "migrated_static_ranges_match",
                         "exact_App_erase_span_saved", "durable_restore_inputs_and_intent"],
        "restore": {"range": "same_App_erase_span", "payload": "same_run_exact_saved_bytes",
                    "verify": "App_byte_readback_then_all_static_ranges_then_normal_boot",
                    "nvs": "record_runtime_changes_separately"},
        "fresh_live_backup_ready": False, "device_accessed": False, "device_written": False,
        "cpu_verified": False, "physical_interaction_accepted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.relative_to(ROOT).parts[0] not in {"tmp", "artifacts"} or output.exists():
        parser.error("output must be a new file inside repository tmp or artifacts")
    result = prepare(args.build_report)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_new(output, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(result["result"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
