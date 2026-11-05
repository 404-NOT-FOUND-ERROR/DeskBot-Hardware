#!/usr/bin/env python3
"""Run USB-drain, host, native endpoint and deployment tests together."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('e3c8_test_inputs', ROOT / 'scripts/validate-deskbot-studio-e3c5.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
TESTS = (*previous.TESTS, 'test_studio_usb_transport.py', 'test_studio_e3c_deployment.py',
    'test_studio_e3c8_deployment.py', 'test_studio_control_link.py', 'test_studio_control_native.py', 'test_deskbot_studio_e3c.py')
OUTPUT = ROOT / 'artifacts/deskbot-studio-e3c8-20261105/focused-validation.json'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    suite = unittest.TestSuite()
    for name in TESTS:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=name))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful() or result.testsRun != 336 or result.skipped:
        raise SystemExit(1)
    inputs = set(previous.INPUTS) | {'tests/'+name for name in TESTS} | {
        'protocol/studio_e3c8_deployment.py', 'protocol/studio_control_link.py', 'protocol/studio_control_bridge.py',
        'tools/observe-studio-body-e3c.py', 'scripts/deploy-deskbot-studio-e3c.py', 'scripts/deploy-deskbot-studio-e3c8.py',
        'tools/verify-studio-control-e3c.py', 'scripts/audit-deskbot-studio-e3c-live.py',
        'scripts/audit-deskbot-studio-e3c8.py', 'scripts/build-deskbot-studio-e3c8-firmware.py',
        'scripts/build-deskbot-studio-e3c7-firmware.py',
        'scripts/validate-deskbot-studio-e3c8.py'}
    report = {'edition': 'E3c-8', 'result': 'passed', 'tests_run': result.testsRun,
        'transport_tests': 9, 'new_deployment_tests': 7, 'failures': 0, 'errors': 0, 'skipped': 0,
        'write_timeout_seconds': 5, 'state_valid_ms': 2000, 'hardware_accessed': False,
        'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(inputs)}}
    raw = (json.dumps(report, ensure_ascii=False, indent=2)+'\n').encode()
    if args.check:
        if OUTPUT.read_bytes() != raw:
            raise ValueError('saved E3c-8 validation differs')
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(raw)
    print('E3c-8: 336 focused tests passed')
