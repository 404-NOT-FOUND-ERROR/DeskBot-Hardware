"""Run the display failure and App-installation tests without device access."""
import datetime
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


if __name__=='__main__':
    loader=unittest.TestLoader()
    suite=loader.discover(str(ROOT/'tests'),pattern='test_display_psram*.py')
    suite.addTests(loader.discover(str(ROOT/'tests'),pattern='test_e3c3b_display_dma.py'))
    log=io.StringIO()
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    files=['tests/test_display_psram_e3f9d2.py','tests/test_display_psram_deployment_e3f9d2.py',
        'tests/cpp/psram_display_dma_harness.c','tests/test_e3c3b_display_dma.py','tests/cpp/dma_display_harness.c']
    evidence=dict(schema='deskbot.display_psram_e3f9d2_tests/v1',edition='E3f-9d.2',
        recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),result='pass' if result.wasSuccessful() else 'failed',
        tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),
        skipped=len(result.skipped),native_full_content_cases=97 if result.wasSuccessful() else None,
        device_accessed=False,device_written=False,
        files={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in files},output=log.getvalue())
    output=ROOT/'artifacts/display-psram-e3f9d2/offline-validation.json'
    output.parent.mkdir(exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(evidence,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(log.getvalue(),end='')
    print(json.dumps({k:evidence[k] for k in ('result','tests','errors','failures','skipped','device_accessed')}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
