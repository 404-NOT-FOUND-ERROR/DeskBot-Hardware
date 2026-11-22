#!/usr/bin/env python3
"""Validate the reflow increment against frozen source and retain each run."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PUBLIC = ROOT/'artifacts/studio-reflow-e3f4e'
WORK = ROOT/'tmp/studio-reflow-e3f4e'
MODULES = ('test_studio_reflow_e3f4e', 'test_studio_asset_layout_native', 'test_studio_catalog_native',
           'test_studio_display_query', 'test_e3c3b_display_dma')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', action='store_true')
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error('positive attempt required')
    package = ModuleType('tests')
    package.__path__ = [str(ROOT/'tests')]
    sys.modules['tests'] = package
    layout = importlib.import_module('tests.test_studio_asset_layout_native')
    catalog = importlib.import_module('tests.test_studio_catalog_native')
    project = ROOT/('tmp/studio-layout-e3f4c-device/p' if args.control else 'tmp/studio-reflow-e3f4e/p')
    if not project.exists():
        project = ROOT/'firmware/body'
    layout.BOARD = catalog.BOARD = project/'main/boards/esp_vocat'
    modules = MODULES[:1] if args.control else MODULES
    suite = unittest.TestSuite()
    for name in modules:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module('tests.'+name)))
    paths = {project/'main/boards/esp_vocat'/name for name in (
        'studio_asset_layout.cc', 'studio_asset_layout.h', 'studio_catalog_builder.cc', 'studio_catalog_adapter.cc')}
    paths.update(ROOT/'tests'/(name+'.py') for name in modules)
    paths.update((ROOT/'tests/cpp').glob('studio*'))
    paths.update((ROOT/'tests/cpp/layout_stubs').rglob('*.h'))
    paths.update((ROOT/'tests/cpp/catalog_stubs').rglob('*.h'))
    paths.add(Path(__file__))
    paths = sorted(p for p in paths if p.is_file())
    before = {p.relative_to(ROOT).as_posix():sha(p) for p in paths}
    label = ('control' if args.control else 'validation') + '-'+str(args.attempt)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    output, log = PUBLIC/(label+'.json'), WORK/(label+'.log')
    if output.exists() or log.exists():
        raise FileExistsError('run retained; choose a new attempt')
    with log.open('x', encoding='utf-8') as stream, redirect_stderr(stream), redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    unchanged = before == {p.relative_to(ROOT).as_posix():sha(p) for p in paths}
    failures = [case.id() for case,_ in result.failures+result.errors]
    report = dict(schema='deskbot.studio_reflow_validation/v1', mode='control' if args.control else 'candidate',
                  result='passed' if result.wasSuccessful() and unchanged and not result.skipped else 'failed',
                  tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped), failed_cases=failures, source_sha256=before,
                  source_unchanged=unchanged, log=log.relative_to(ROOT).as_posix(), log_sha256=sha(log),
                  hardware_accessed=False, device_written=False)
    raw = (json.dumps(report, ensure_ascii=False, indent=2)+'\n').encode()
    output.write_bytes(raw)
    if report['result']=='passed' and not args.control:
        (PUBLIC/'offline-validation.json').write_bytes(raw)
    print(json.dumps({k:report[k] for k in ('mode','result','tests_run','failures','errors','skipped')}))
    return 0 if report['result']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
