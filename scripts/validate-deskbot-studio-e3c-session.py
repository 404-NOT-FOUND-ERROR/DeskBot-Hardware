#!/usr/bin/env python3
"""Run native USB session tests without accessing serial devices."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "artifacts/deskbot-studio-e3c-20261104/session-validation.json"
INPUTS = (
    "protocol/studio_usb_session.py", "protocol/deskbot-studio-session-v1.schema.json",
    "firmware/body/main/boards/esp_vocat/studio_request_framer.h",
    "firmware/body/main/boards/esp_vocat/studio_usb_session.h",
    "tests/cpp/studio_usb_session_test.cc", "tests/test_studio_usb_session.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_studio_usb_session.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.testsRun != 28 or result.skipped:
        return 1
    report = {
        "edition": "E3c-1", "result": "native_session_core_passed",
        "tests_run": result.testsRun, "failures": 0, "errors": 0, "skipped": 0,
        "native_cpp17_compiled_and_run": True, "serial_accessed": False, "device_written": False,
        "firmware_source_integrated": True, "hardware_acceptance": "pending",
        "limits": {"max_frame_bytes": 2048, "max_identifier_bytes": 96, "lease_ms": 30000,
                   "reply_interval_ms": 250, "partial_timeout_ms": 2000, "controls_available": False},
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS},
    }
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != raw:
            raise ValueError("saved session report differs")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(raw)
    print(f"E3c-1 native USB session: {result.testsRun} tests passed; no serial access")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
