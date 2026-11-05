#!/usr/bin/env python3
"""Run production USB transport, deployment and expression regressions offline."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("e3c7_regression_inputs", ROOT / "scripts/validate-deskbot-studio-e3c5.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
TESTS = (*previous.TESTS, "test_studio_usb_transport.py", "test_studio_e3c_deployment.py")
OUTPUT = ROOT / "artifacts/deskbot-studio-e3c7-20261105/focused-validation.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    suite = unittest.TestSuite()
    for name in TESTS:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful() or result.testsRun != 279 or result.skipped:
        return 1
    inputs = (*previous.INPUTS, *("tests/" + name for name in TESTS),
              "protocol/studio_e3c_deployment.py", "scripts/deploy-deskbot-studio-e3c.py",
              "scripts/validate-deskbot-studio-e3c7.py", "tools/verify-studio-control-e3c.py")
    report = {"schema": "deskbot.studio_usb_transport_regression/v1", "edition": "E3c-7", "result": "passed",
              "tests_run": result.testsRun, "transport_tests": 4, "deployment_tests": 21,
              "prior_regression_tests": 254, "failures": 0, "errors": 0, "skipped": 0,
              "hardware_accessed": False, "screen_playback_confirmed": False,
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in inputs}}
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if OUTPUT.read_bytes() != raw:
            raise ValueError("saved E3c-7 regression differs")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(raw)
    print("E3c-7: 279 production transport, deployment and expression tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
