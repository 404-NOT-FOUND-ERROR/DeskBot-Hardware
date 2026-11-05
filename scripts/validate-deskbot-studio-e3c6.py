#!/usr/bin/env python3
"""Validate host state, one-owner IPC, HTTP and the production C++ endpoint."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "artifacts/deskbot-studio-e3c6-20261105/offline-validation.json"
TESTS = ("test_studio_control_link.py", "test_studio_control_native.py", "test_deskbot_studio_e3c.py")
INPUTS = (
    "protocol/studio_control_link.py", "protocol/studio_control_bridge.py",
    "protocol/deskbot-studio-control-link-v1.schema.json", "protocol/studio_display_query.py",
    "protocol/studio_runtime_query.py", "protocol/studio_usb_session.py", "protocol/expression_control.py",
    "scripts/build-deskbot-studio-e3c.py", "scripts/serve-deskbot-studio-e3c.py",
    "scripts/deskbot-studio-e3c.js", "scripts/deskbot-studio-e3c.css",
    "tools/observe-studio-body-e3c.py", "scripts/validate-deskbot-studio-e3c6.py",
    "tests/deskbot-studio-e3c-browser.cjs", "tests/cpp/studio_expression_control_test.cc",
    "tests/test_studio_expression_control.py",
    "firmware/body/SOURCE_MANIFEST.sha256",
    "firmware/body/main/boards/esp_vocat/studio_usb_session.h",
    "firmware/body/main/boards/esp_vocat/studio_expression_control.h",
    "firmware/body/main/boards/esp_vocat/studio_expression_adapter.cc",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    suite = unittest.TestSuite()
    for name in TESTS:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful() or result.testsRun != 45 or result.skipped:
        return 1
    report = dict(edition="E3c-6", result="host_link_and_production_endpoint_passed",
        tests_run=result.testsRun, failures=0, errors=0, skipped=0,
        native_production_endpoint_cases=4, native_adapter="host_owner_and_player_shims",
        serial_owners=1, explicit_enable=True, automatic_expression_retry=False,
        min_send_interval_ms=400, display_query_interval_ms=1000, state_valid_ms=2000,
        receipt_history_max=24, command_history_max=64, ipc_command_max_bytes=512,
        hardware_accessed=False, device_written=False, firmware_changed=False,
        hardware_acceptance="pending", screen_playback_confirmed=False,
        source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                       for name in (*INPUTS, *("tests/"+name for name in TESTS))})
    raw = (json.dumps(report, ensure_ascii=False, indent=2)+"\n").encode()
    if args.check:
        if OUTPUT.read_bytes() != raw:
            raise ValueError("saved E3c-6 validation differs")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(raw)
    print(f"E3c-6: {result.testsRun} tests passed; no hardware access")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
