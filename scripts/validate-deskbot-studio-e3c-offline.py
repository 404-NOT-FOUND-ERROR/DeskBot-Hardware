#!/usr/bin/env python3
"""Test the offline E3c reference and retain a deterministic public result."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "artifacts/deskbot-studio-e3c-20261104/offline-validation.json"
INPUTS = (
    "protocol/expression_control.py", "protocol/studio_expression_receiver.py",
    "protocol/deskbot-expression-control-v1.schema.json",
    "scripts/deskbot-studio-e2c-core.js", "scripts/validate-deskbot-studio-e3c-offline.py",
    "artifacts/deskbot-studio-e2c-20261104/expression-catalog.json",
    "tests/test_studio_expression_receiver.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Rerun tests and compare the retained report")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_studio_expression_receiver.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.testsRun == 0 or result.skipped:
        return 1
    report = {
        "edition": "E3c-offline", "result": "offline_reference_passed",
        "tests_run": result.testsRun, "failures": 0, "errors": 0, "skipped": 0,
        "hardware_accessed": False, "device_written": False, "firmware_changed": False,
        "e3b_hardware_acceptance": "pending", "e3c_device_controls": False,
        "scope": {"max_frame_bytes": 2048, "max_json_depth": 8, "pending_slots": 1,
                  "history_entries": 24, "dedup_decisions": 64, "semantic": "neutral",
                  "resource": "winking.eaf", "receipts_source": "simulator",
                  "ttl_origin": "complete_valid_request_received",
                  "full_dedup_window": "reject_new_ids_until_reboot"},
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS},
    }
    data = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != data:
            print("E3c retained report differs; rerun the validation entry.", file=sys.stderr)
            return 1
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(data)
    print(f"E3c offline reference: {result.testsRun} passed; report {OUTPUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
