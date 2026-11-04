#!/usr/bin/env python3
"""Validate CPU records against the same run's saved write-time snapshot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_upper_experiment import sha
from protocol.storage_upper_pattern import build_pattern, manifest_for
from protocol.storage_upper_pattern_runtime import extract_records, validate_capture
from protocol.storage_upper_snapshot import load_snapshot


def validate(log: str, run_dir: Path, *, identity_sha256: str, plan_id: str,
             expected_manifest_sha256: str) -> dict:
    reference = load_snapshot(run_dir, identity_sha256=identity_sha256, plan_id=plan_id,
                              expected_manifest_sha256=expected_manifest_sha256)
    pattern = build_pattern()
    records = extract_records(log)
    checked = validate_capture(records, manifest_for(pattern), reference.low_alias_sha256,
                               reference.boundary_hex(pattern), reference.low_protected_sha256)
    observations = checked.get("observations", {})
    if "nvs_containing_low_page_matches_historical_reference" in observations:
        observations = {"nvs_containing_low_page_matches_write_snapshot":
                        observations["nvs_containing_low_page_matches_historical_reference"]}
    return {"schema": "deskbot.upper_pattern.runtime_validation.e1_43a7/v1",
            "result": "probe_aborted" if "aborted" in records else (
                "runtime_probe_passed" if checked["probe_passed"] else "runtime_probe_failed"),
            "binding": {"identity_sha256": identity_sha256, "plan_id": plan_id,
                        "run_label": reference.run_label, "snapshot_manifest_sha256": reference.manifest_sha256,
                        "source_log_sha256": sha(log.encode("utf-8")),
                        "association": "host_collected_same_run", "log_identity_authenticated": False},
            "reference": {"low_alias_sha256": reference.low_alias_sha256,
                          "low_protected_sha256": reference.low_protected_sha256,
                          "boundary_hex": reference.boundary_hex(pattern)},
            "checks": checked["checks"], "observations": observations, "raw_records": records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--identity-sha256", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--snapshot-manifest-sha256", required=True)
    args = parser.parse_args()
    log_path = args.log.resolve()
    if args.log.is_symlink() or log_path.parent != (args.run_dir / "captures").resolve():
        parser.error("log must be a direct capture from this run")
    result = validate(log_path.read_text(encoding="utf-8-sig"), args.run_dir,
                      identity_sha256=args.identity_sha256, plan_id=args.plan_id,
                      expected_manifest_sha256=args.snapshot_manifest_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["result"] == "runtime_probe_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
