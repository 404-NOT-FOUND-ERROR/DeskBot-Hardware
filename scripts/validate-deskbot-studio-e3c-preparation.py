#!/usr/bin/env python3
"""Run the receiver, C++ framing and candidate-independent rollback tests."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "artifacts/deskbot-studio-e3c-20261104/preparation-validation-e3c1.json"
TESTS = ("test_studio_expression_receiver.py", "test_studio_request_framer_cpp.py",
         "test_studio_app_deployment.py", "test_studio_recovery.py")
INPUTS = ("protocol/studio_expression_receiver.py", "protocol/expression_control.py",
          "protocol/cpp/studio_request_framer.h", "tests/cpp/studio_request_framer_test.cc",
          "firmware/body/main/boards/esp_vocat/studio_request_framer.h",
          "protocol/studio_app_deployment.py", "scripts/deploy-deskbot-studio-e3b.py",
          "scripts/validate-deskbot-studio-e3c-preparation.py",
          "scripts/test-expression-e1-26-cpp.py",
          "artifacts/deskbot-studio-e2c-20261104/expression-catalog.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Rerun and compare the retained report")
    args = parser.parse_args()
    suite = unittest.TestSuite()
    for name in TESTS:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful() or result.testsRun == 0 or result.skipped:
        return 1
    inputs = (*INPUTS, *("tests/" + name for name in TESTS))
    report = {
        "edition": "E3c-1-preparation", "result": "offline_components_passed",
        "tests_run": result.testsRun, "failures": 0, "errors": 0, "skipped": 0,
        "hardware_accessed": False, "device_written": False,
        "e3b_hardware_acceptance": "pending", "usb_diagnostic_source_integrated": True,
        "expression_device_receiver_integrated": False,
        "scope": {"max_frame_bytes": 2048, "cpp_native_compiled_and_run": True,
                  "cpp_frame_dynamic_allocations": False, "cpp_storage_max_bytes": 2072,
                  "cpp_matches_python_frames": True, "pending_slots": 1,
                  "rollback_requires_candidate": False, "rollback_requires_build_directory": False,
                  "rollback_faults_use_simulated_flash": True},
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in inputs},
    }
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != raw:
            raise ValueError("E3c preparation report differs")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(raw)
    print(f"E3c preparation: {result.testsRun} tests passed; no device access")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
