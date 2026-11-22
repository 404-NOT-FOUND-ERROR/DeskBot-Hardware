#!/usr/bin/env python3
"""Validate E3f-3 using loopback HTTP and shared in-memory Flash only."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib
import json
from pathlib import Path
import sys
import time
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
package = ModuleType('tests')
package.__path__ = [str(ROOT / 'tests')]
sys.modules['tests'] = package
from protocol import studio_e3d2b_deployment as baseline
from protocol import studio_e3f1_deployment as shy
from protocol import studio_resource_package as packages

PUBLIC = ROOT / 'artifacts/studio-role-flow-e3f3-20261109'
WORK = ROOT / 'tmp/studio-role-flow-e3f3'
EXPECTED = 14
TEST = importlib.import_module('tests.test_studio_role_flow')


def hashes():
    # Include loaded Python, dynamically loaded service ancestors/helpers,
    # native harnesses, third-party native source, and every frozen header.
    paths = set()
    for module in tuple(sys.modules.values()):
        name = getattr(module, '__file__', None)
        if name:
            path = Path(name).resolve()
            if path.is_relative_to(ROOT) and path.suffix == '.py':
                paths.add(path)
    for pattern in ('scripts/serve-deskbot-studio-*.py', 'tests/test_studio_control_link.py',
                    'tests/test_studio_expression_control.py', 'tests/test_studio_catalog_native.py',
                    'scripts/test-expression-e1-26-cpp.py', 'scripts/build-expression-e1-43b-e3c3a.py',
                    'scripts/audit-expression-package-candidate.py', 'scripts/audit-esp32-expression-capacity.py',
                    'protocol/deskbot-studio-*.schema.json', 'tests/cpp/studio_*test.cc',
                    'tests/cpp/studio_role_flow_provider.h', 'tests/cpp/catalog_stubs/**/*',
                    'tests/vendor/studio_catalog/**/*'):
        paths.update(path for path in ROOT.glob(pattern) if path.is_file())
    paths.add(Path(__file__).resolve())
    for path in (baseline.BUILD_REPORT, shy.BUILD_REPORT, shy.TEST_REPORT, shy.RESOURCE_PREPARATION,
                 shy.RESOURCE_READINESS, shy.RESOURCE_CANDIDATE_REPORT, shy.APP_BUILD, shy.APP_ELF,
                 shy.RESOURCE_RUN / 'plan.json', shy.RESOURCE_RUN / 'snapshot-32m.bin'):
        paths.add(ROOT / path)
    paths.update((packages.BASE_IMAGE, packages.CANDIDATE))
    paths.update(ROOT / name for name in (
        'artifacts/deskbot-studio-e2a-20261104/index.html',
        'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json',
        'firmware/releases/esp_vocat_n32r16/target.json',
        'firmware/releases/esp_vocat_n32r16/partitions.csv'))
    paths.update(path for path in (ROOT / 'artifacts').glob('deskbot-studio-*/index.html'))
    for folder, report in ((ROOT / baseline.PROJECT, baseline.BUILD_REPORT),
                           (ROOT / 'tmp/studio-shy-e3f1/p', shy.BUILD_REPORT)):
        manifest = json.loads((ROOT / report).read_bytes())['source_sha256']
        TEST.f.source_check.verify_frozen_sources(folder, manifest)
        paths.update(folder / name for name in manifest)
    return {path.relative_to(ROOT).as_posix(): packages.sha(path.read_bytes()) for path in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error('attempt must be positive')
    suffix = '' if args.attempt == 1 else '-' + str(args.attempt)
    report_path = PUBLIC / ('offline-validation' + suffix + '.json')
    log_path = WORK / ('tests' + suffix + '.log')
    evidence_path = PUBLIC / ('integration-evidence' + suffix + '.json')
    # Eagerly load the package parser before taking the source inventory.
    packages.load_legacy_tools()
    before = hashes()
    if args.check:
        report = json.loads(report_path.read_bytes())
        if (report.get('schema') != 'deskbot.role_expression_flow_validation/v1'
                or report.get('result') != 'passed' or report.get('tests') != EXPECTED
                or report.get('source_sha256') != before or report.get('source_unchanged') is not True
                or any(report.get(key) != 0 for key in ('errors', 'failures', 'skipped'))
                or report.get('log_sha256') != packages.sha(log_path.read_bytes())
                or report.get('evidence_sha256') != packages.sha(evidence_path.read_bytes())
                or report.get('hardware_accessed') is not False or report.get('device_written') is not False):
            raise ValueError('role_flow_validation_changed')
        print(json.dumps(dict(result='checked', tests=EXPECTED, source_files=len(before))))
        return 0
    if any(path.exists() for path in (report_path, log_path, evidence_path)):
        raise FileExistsError('choose a new validation attempt')
    PUBLIC.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromModule(TEST)
    if suite.countTestCases() != EXPECTED:
        raise ValueError('role flow suite count changed')
    started = time.monotonic()
    with log_path.open('x', encoding='utf-8') as log, redirect_stderr(log), redirect_stdout(log):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    unchanged = before == hashes()
    passed = result.wasSuccessful() and result.testsRun == EXPECTED and not result.skipped and unchanged
    evidence = {key: value for key, value in TEST.EVIDENCE.items() if key != 'frozen_source_sha256'}
    evidence.update(hardware_accessed=False, device_written=False, physical_rendering='pending',
        temporary_images='owned TemporaryDirectory files removed after each test')
    evidence_path.write_bytes(packages.json_bytes(evidence))
    report = dict(schema='deskbot.role_expression_flow_validation/v1', result='passed' if passed else 'failed',
        tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        elapsed_seconds=round(time.monotonic()-started, 3), runtime=sys.version,
        source_sha256=before, source_unchanged=unchanged,
        log=log_path.relative_to(ROOT).as_posix(), log_sha256=packages.sha(log_path.read_bytes()),
        evidence=evidence_path.relative_to(ROOT).as_posix(), evidence_sha256=packages.sha(evidence_path.read_bytes()),
        hardware_accessed=False, device_written=False, physical_rendering='pending')
    report_path.write_bytes(packages.json_bytes(report))
    print(json.dumps({key: report[key] for key in ('result', 'tests', 'failures', 'errors', 'skipped', 'source_unchanged')}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
