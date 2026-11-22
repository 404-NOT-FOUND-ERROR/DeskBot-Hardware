"""Verify the offline first loop using explicit runtimes and isolated stores."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/client-4b1a'))
from apply import apply, BASE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-root', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/client-expression-loop-4b1a-20261116')
    args = parser.parse_args()
    client, node, output = args.client_root.resolve(), args.node.resolve(), args.output.resolve()
    if not node.is_file():
        parser.error('Node runtime not found')
    import jsonschema, referencing  # Validate the existing host dependencies before testing.
    output.mkdir(parents=True, exist_ok=True)
    apply(client)
    version = subprocess.check_output([str(node),'--version'], text=True).strip()
    if int(version.lstrip('v').split('.')[0]) < 24:
        parser.error('Node 24+ is required')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT),str(ROOT/'tests'))),
               DESKBOT_CLIENT_CHECKOUT=str(client), DESKBOT_NODE=str(node))
    suites = [
        ('adapter', [sys.executable,'-m','unittest','tests.test_client_presentation','-q'], ROOT),
        ('host-regression', [sys.executable,'-m','unittest','tests.test_studio_control_link',
            'tests.test_studio_device_roles_e3f9d.DeviceRoleHostTests','tests.test_shell_switch_adapter',
            'tests.test_shell_switch_service','tests.test_shell_workbench','-q'], ROOT),
        ('client', [str(node),'--test','--test-reporter=tap','apps/deskbot-service/recovery-4b1a.test.mjs',
            *['apps/deskbot-service/test/'+name+'.test.mjs' for name in ('chat-orchestrator',
              'output-router','output-router-http','llm-http-error','persistence-restart')]], client),
    ]
    results = []
    for name, command, cwd in suites:
        run = subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
        text = run.stdout + run.stderr
        (output/(name+'.txt')).write_text(text,encoding='utf-8')
        count = re.search(r'Ran (\d+) tests',text) if name!='client' else re.search(r'# tests (\d+)',text)
        row = dict(suite=name,exit_code=run.returncode,tests=int(count[1]) if count else None,
                   skipped=bool(re.search(r'(skipped=[1-9]|# skipped [1-9])',text)))
        results.append(row);print(json.dumps(row),flush=True)
        if run.returncode:
            print(text,flush=True)
    # Reconstruct only the four original source files from the recorded commit,
    # apply the patch, then compare the exact resulting bytes with the test tree.
    patch=ROOT/'integrations/client-4b1a/client-4b1a.patch'
    files=['apps/deskbot-service/src/'+name+'.mjs' for name in
           ('chat-orchestrator','state-engine','output-router','app')]
    with tempfile.TemporaryDirectory(prefix='deskbot-4b1a-patch-') as directory:
        folder=Path(directory)
        for name in files:
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(subprocess.check_output(['git','-C',str(client),'show',BASE+':'+name]))
        subprocess.run(['git','apply','--check',str(patch)],cwd=folder,check=True)
        subprocess.run(['git','apply',str(patch)],cwd=folder,check=True)
        for name in files:
            if (folder/name).read_bytes() != (client/name).read_bytes():
                raise ValueError('client_patch_result_mismatch: '+name)
    inputs = [*files,'apps/deskbot-service/src/chat-turn-recovery.mjs']
    report = dict(schema='deskbot.client-expression-loop-4b1a/v1',at=datetime.now(timezone.utc).isoformat(),
        status='passed' if all(r['exit_code']==0 and not r['skipped'] for r in results) else 'failed',
        client_baseline=BASE,node=version,python=sys.version.split()[0],suites=results,
        tests=sum(r['tests'] or 0 for r in results),hardware_accessed=False,
        source_sha256={name:hashlib.sha256((client/name).read_bytes()).hexdigest() for name in inputs},
        host_source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'protocol/client_presentation.py','protocol/client_presentation_ledger.py',
            'protocol/client_presentation_http.py','protocol/presentation_coordinator.py',
            'protocol/studio_control_bridge.py','protocol/studio_control_link.py',
            'scripts/serve-deskbot-shell-workbench.py','tests/test_client_presentation.py',
            'tests/client_presentation_fixture.py')},
        patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),patch_reproduced=True,
        next_step='4b1b: existing USB and visible screen acceptance')
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],tests=report['tests'],output=str(output))),flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
