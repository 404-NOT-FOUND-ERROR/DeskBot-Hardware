#!/usr/bin/env python3
"""Validate a real serial capture against the exact E1.40 readback reference."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.capacity_query import validate_expected_capacity
from protocol.capacity_serial_e1_42_3 import extract_records
from protocol.storage_capacity_probe import parse_probe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.resolve().relative_to(ROOT)
    if args.output.exists():
        raise ValueError("refuse to overwrite existing evidence")
    spec = importlib.util.spec_from_file_location("probe_validator", ROOT / "scripts/validate-expression-e1-42-probe.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    lower, upper, digest = helper.reference_samples(helper.DEFAULT_REFERENCE)
    baseline = json.loads((ROOT / "artifacts/expression-capacity-read-mmap-e1-42-20261031/build-report.json").read_text(encoding="utf-8"))
    if digest != baseline["reference_readback"]["sha256"]:
        raise ValueError("E1.40 reference SHA-256 differs from the accepted build anchor")
    records = extract_records(args.log.read_text(encoding="utf-8-sig"))
    capacity = validate_expected_capacity(records["capacity"])
    probe = parse_probe(records["probe"], expected_lower_sample=lower, expected_upper_sample=upper)
    result = {"schema": "deskbot.expression.capacity_serial.e1_42_3_runtime/v1",
              "result": "runtime_probe_passed" if probe["probe_passed"] else "runtime_probe_failed",
              "transport": "local_usb_secondary_console", "mcp_visibility_verified": False,
              "source_log_sha256": hashlib.sha256(args.log.read_bytes()).hexdigest(),
              "reference_sha256": digest, "capacity": capacity, "probe": probe, "raw_records": records,
              "writes_performed_by_diagnostic": False,
              "claim_limits": "Erased FF samples cannot prove absence of address aliasing or upper assets decoding."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": result["result"], "flash_bytes": capacity["flash_size_bytes"],
                      "mcp_visibility_verified": False}, ensure_ascii=False))
    return 0 if probe["probe_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
