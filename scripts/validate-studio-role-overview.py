#!/usr/bin/env python3
"""Record whole-role analysis and production HTTP regressions with memory bridges."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
PUBLIC=ROOT/'artifacts/deskbot-studio-e3f2-20261109'
WORK=ROOT/'tmp/studio-role-overview-e3f2'
SUITES={'test_studio_role_overview.py':13,'test_deskbot_studio_e3f2.py':8,
        'test_studio_role_expression.py':12,'test_deskbot_studio_e3e.py':9}
SOURCES=(
    'protocol/studio_role_overview.py','protocol/studio_role_expression.py',
    'protocol/studio_resource_package.py','protocol/studio_installed_catalog.py',
    'scripts/build-deskbot-studio-e3f2.py','scripts/serve-deskbot-studio-e3f2.py',
    'scripts/start-deskbot-studio-e3f2.ps1',
    'scripts/deskbot-studio-e3f2.js','scripts/deskbot-studio-e3f2.css',
    'scripts/validate-studio-role-overview.py',
    'scripts/build-deskbot-studio-e3e.py','scripts/serve-deskbot-studio-e3e.py',
    'scripts/deskbot-studio-e3e.js','scripts/build-e3e-source-review.py',
    'scripts/run-offline-checks.py','scripts/check-workbenches.py','.github/workflows/host-checks.yml',
    'tests/serve_studio_e3f2_fixture.py','tests/serve_studio_e3e_fixture.py',
    'tests/studio-role-overview-browser.cjs',
    *('tests/'+name for name in SUITES))
PRODUCTS=tuple(f'{prefix}/deskbot-studio-e3f2-20261109/{name}'
               for prefix in ('artifacts','tmp') for name in ('index.html','report.json'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(names):
    return {name:sha(ROOT/name) for name in names}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt',type=int,default=1)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    if args.attempt<1:
        parser.error('attempt must be positive')
    suffix='' if args.attempt==1 else '-'+str(args.attempt)
    report_path=PUBLIC/('offline-validation'+suffix+'.json')
    log_path=WORK/('tests'+suffix+'.log')
    sources,products=hashes(SOURCES),hashes(PRODUCTS)
    if args.check:
        report=json.loads(report_path.read_bytes())
        if (report.get('schema')!='deskbot.role_expression_overview_validation/v1'
                or report.get('result')!='passed' or report.get('tests')!=sum(SUITES.values())
                or report.get('suites')!=SUITES or report.get('source_sha256')!=sources
                or report.get('product_sha256')!=products or report.get('source_unchanged') is not True
                or any(report.get(key)!=0 for key in ('failures','errors','skipped'))
                or report.get('log')!=log_path.relative_to(ROOT).as_posix()
                or report.get('log_sha256')!=sha(log_path)
                or report.get('hardware_accessed') is not False or report.get('device_written') is not False):
            raise ValueError('role_overview_validation_changed')
        print(json.dumps(dict(result='checked',tests=report['tests'],sources=len(sources))))
        return 0
    if report_path.exists() or log_path.exists():
        raise FileExistsError('choose a new overview validation attempt')
    WORK.mkdir(parents=True,exist_ok=True)
    suite=unittest.TestSuite()
    for name,count in SUITES.items():
        spec=importlib.util.spec_from_file_location(Path(name).stem,ROOT/'tests'/name)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tests=unittest.defaultTestLoader.loadTestsFromModule(module)
        if tests.countTestCases()!=count:
            raise ValueError('overview suite count changed: '+name)
        suite.addTests(tests)
    started=time.monotonic()
    with log_path.open('x',encoding='utf-8') as log,redirect_stderr(log),redirect_stdout(log):
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    unchanged=sources==hashes(SOURCES) and products==hashes(PRODUCTS)
    passed=result.wasSuccessful() and result.testsRun==sum(SUITES.values()) and not result.skipped and unchanged
    report=dict(schema='deskbot.role_expression_overview_validation/v1',result='passed' if passed else 'failed',
        tests=result.testsRun,suites=SUITES,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        elapsed_seconds=round(time.monotonic()-started,3),runtime=sys.version,
        source_sha256=sources,product_sha256=products,source_unchanged=unchanged,
        log=log_path.relative_to(ROOT).as_posix(),log_sha256=sha(log_path),
        hardware_accessed=False,device_written=False)
    with report_path.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps({key:report[key] for key in ('result','tests','failures','errors','skipped')}))
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
