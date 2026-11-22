#!/usr/bin/env python3
"""Run host/UI unit regressions and retain the browser evidence separately."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT/'tmp/studio-play-e3f4b'
PUBLIC = ROOT/'artifacts/studio-play-e3f4b'
NAMES = ('test_studio_timing', 'test_studio_control_link', 'test_studio_eat',
         'test_studio_catalog_query', 'test_studio_display_query', 'test_studio_play_workbench')
SOURCES = ('scripts/deskbot-studio-play-readiness.js', 'scripts/deskbot-studio-e3c.js',
    'scripts/deskbot-studio-e3d2.js', 'scripts/build-deskbot-studio-e3c.py',
    'scripts/build-deskbot-studio-e3f4a.py','scripts/build-deskbot-studio-e3f4b.py',
    'scripts/serve-deskbot-studio-e3f4b.py', 'scripts/validate-studio-play-readiness.py',
    'tests/studio-play-readiness.test.cjs','tests/studio-play-browser.cjs','tests/serve_studio_play_fixture.py',
    'tests/test_studio_play_workbench.py','protocol/studio_timing.py','protocol/studio_control_link.py',
    'protocol/studio_control_bridge.py','tools/observe-studio-body-e3c.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    WORK.mkdir(exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    target = PUBLIC/f'validation-{args.attempt}.json'
    log_path = WORK/f'tests-{args.attempt}.log'
    if target.exists() or log_path.exists():
        raise FileExistsError('Use a new attempt number')
    before = {name: sha(ROOT/name) for name in SOURCES}
    package = ModuleType('tests')
    package.__path__ = [str(ROOT/'tests')]
    sys.modules['tests'] = package
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module('tests.'+n)) for n in NAMES)
    node = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    started = time.monotonic()
    with log_path.open('x', encoding='utf-8') as stream, redirect_stdout(stream), redirect_stderr(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        stream.flush()
        js = subprocess.run([str(node),'--test','--test-reporter=spec','tests/studio-play-readiness.test.cjs'],
                            cwd=ROOT, stdout=stream, stderr=stream)
    browser_path = WORK/'browser/validation-2.json'
    browser = json.loads(browser_path.read_bytes())
    unchanged = before == {name: sha(ROOT/name) for name in SOURCES}
    passed = result.wasSuccessful() and not result.skipped and js.returncode == 0 and browser['result']=='passed' and unchanged
    report = dict(result='passed' if passed else 'failed',python_tests=result.testsRun,
        python_failures=len(result.failures),python_errors=len(result.errors),python_skipped=len(result.skipped),
        node_tests=19,node_exit=js.returncode,browser_checks=browser['checks_passed'],
        browser_report=str(browser_path.relative_to(ROOT)).replace('\\','/'),browser_sha256=sha(browser_path),
        source_sha256=before,source_unchanged=unchanged,firmware_changed=False,
        runtime=sys.version,elapsed_seconds=round(time.monotonic()-started,3),hardware_accessed=False,
        device_written=False,log=str(log_path.relative_to(ROOT)).replace('\\','/'),log_sha256=sha(log_path))
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('result','python_tests','node_tests','browser_checks','source_unchanged')}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
