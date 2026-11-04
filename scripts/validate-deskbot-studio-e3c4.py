#!/usr/bin/env python3
"""Run USB state query, snapshot, session and receiver/recovery tests offline."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "artifacts/deskbot-studio-e3c4-20261105/offline-validation.json"
TESTS = ("test_studio_display_query.py", "test_studio_runtime_query.py", "test_studio_runtime_snapshot.py", "test_studio_usb_session.py", "test_studio_expression_receiver.py",
         "test_studio_request_framer_cpp.py", "test_studio_app_deployment.py", "test_studio_recovery.py")
INPUTS = (
    "protocol/studio_display_query.py", "protocol/deskbot-studio-display-v1.schema.json",
    "firmware/body/main/boards/esp_vocat/studio_display_readiness.h",
    "firmware/body/main/display/emote_display.cc", "tests/cpp/studio_display_query_test.cc",
    "scripts/validate-deskbot-studio-e3c4.py",
    "firmware/body/main/boards/esp_vocat/studio_runtime_query.h",
    "protocol/studio_runtime_query.py", "protocol/deskbot-studio-state-v1.schema.json",
    "tests/cpp/studio_runtime_query_test.cc",
    "firmware/body/main/application.cc", "firmware/body/main/boards/esp_vocat/ui_bridge.cc",
    "firmware/body/main/Kconfig.projbuild", "firmware/body/main/device_state.h",
    "firmware/body/main/boards/esp_vocat/studio_runtime_snapshot.h",
    "firmware/body/main/boards/esp_vocat/studio_runtime_state.h",
    "firmware/body/main/boards/esp_vocat/studio_runtime_state.cc",
    "tests/cpp/studio_runtime_snapshot_test.cc", "scripts/validate-deskbot-studio-e3c3.py",
    "firmware/body/main/boards/esp_vocat/studio_usb_session.h",
    "firmware/body/main/boards/esp_vocat/studio_usb_diagnostic.cc",
    "protocol/studio_expression_receiver.py", "protocol/studio_app_deployment.py",
    "scripts/deploy-deskbot-studio-e3b.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    suite = unittest.TestSuite()
    for name in TESTS:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=name))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful() or result.testsRun != 212 or result.skipped:
        return 1
    inputs = (*INPUTS, *("tests/" + name for name in TESTS))
    report = {
        "edition": "E3c-4", "result": "native_display_query_and_regression_passed",
        "tests_run": result.testsRun, "display_tests": 34, "query_tests": 46, "snapshot_tests": 21, "regression_tests": 111,
        "failures": 0, "errors": 0, "skipped": 0,
        "native_cpp17_core_and_production_adapter_run": True,
        "native_adapter_critical_section": "host_mutex_shim",
        "concurrency_case": {"application_transitions": 60001, "page_transitions": 20000, "reads": 80000},
        "client_state_valid_ms": 2000, "client_pending_requests": 1, "limits": {"store_max_bytes": 80, "snapshot_max_bytes": 24, "writers_per_channel": 4},
        "owner_original_logic_preserved": True, "sleep_state": "unknown", "controls_available": False,
        "snapshot_transport_integrated": True, "display_query_integrated": True, "preview_available": False, "hardware_accessed": False, "device_written": False,
        "hardware_acceptance": "pending",
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in inputs},
    }
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.check:
        if OUTPUT.read_bytes() != raw:
            raise ValueError("saved snapshot validation differs")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(raw)
    print(f"E3c-4: {result.testsRun} tests passed; no device access")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
