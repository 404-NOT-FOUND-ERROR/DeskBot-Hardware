"""Reproduce installation synchronization with an explicit developing client and Node."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fingerprints(client):
    hardware = ['docs/client-installation-sync.md', 'integrations/client-handoff/README.md',
        'integrations/client-handoff/contract.json', 'integrations/client-installation/README.md', 'integrations/client-installation/receiver.mjs',
        'integrations/client-installation/server.mjs', 'protocol/client_installation_sync.py',
        'scripts/check-client-installation.py', 'scripts/sync-client-installation.py',
        'tests/client-installation.test.mjs', 'tests/test_client_installation_sync.py']
    hardware += [p.relative_to(ROOT).as_posix() for p in (ROOT/'protocol').glob('*.py')]
    return dict(hardware={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(set(hardware))},
                client={p.relative_to(client).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted((client/'apps/deskbot-service/src').rglob('*.mjs'))})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('client', 'node', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    client, node, output = (p.resolve() for p in (args.client, args.node, args.output))
    if output.exists() or output == client or client in output.parents:
        parser.error('请选择客户端目录以外的新归档目录')
    output.mkdir(parents=True)
    report = dict(artifact='client-installation-sync', status='failed',
        at=datetime.now(timezone.utc).isoformat(), scope='installation_facts',
        client_persona_voice_adoption='pending', real_device_test='not_requested',
        client_checkout=str(client), node=str(node), python=sys.executable)
    try:
        for name in ('app', 'persistence', 'device-registry', 'world-definition'):
            if not (client/f'apps/deskbot-service/src/{name}.mjs').is_file():
                raise ValueError('client_module_missing: ' + name)
        version = subprocess.check_output([str(node), '--version'], text=True, timeout=10).strip()
        if int(version.removeprefix('v').split('.')[0]) < 24:
            raise ValueError('Node 24+ required')
        report['node_version'] = version
        report['client_commit'] = subprocess.check_output(['git', '-C', str(client), 'rev-parse', 'HEAD'], text=True, timeout=10).strip()
        report['client_worktree'] = subprocess.check_output(['git', '-C', str(client), 'status', '--short', '--', 'apps/deskbot-service/src'],
                                                          text=True, timeout=10).splitlines()
        before = fingerprints(client)
        save(output/'source-sha256.json', before)
        os.environ.update(DESKBOT_HANDOFF_CLIENT=str(client), DESKBOT_HANDOFF_NODE=str(node), PYTHONIOENCODING='utf-8')
        result = subprocess.run([str(node), '--test', '--test-reporter=tap', str(ROOT/'tests/client-installation.test.mjs')],
                                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
        (output/'node-tests.tap').write_text(result.stdout + result.stderr, encoding='utf-8')
        report['node_exit_code'] = result.returncode
        import re
        counts = dict(re.findall(r'^# (tests|pass|fail|skipped) (\d+)$', result.stdout, re.M))
        report['node_tests'] = {key: int(value) for key, value in counts.items()}
        stream = io.StringIO()
        from tests import test_client_installation_sync as module
        suite = unittest.defaultTestLoader.loadTestsFromNames(['tests.test_client_installation_sync',
            'tests.test_shell_installation', 'tests.test_studio_shared_owner'])
        validation = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        (output/'python-tests.txt').write_text(stream.getvalue(), encoding='utf-8')
        report.update(python_tests=validation.testsRun, python_failures=len(validation.failures),
                      python_errors=len(validation.errors), python_skipped=len(validation.skipped))
        save(output/'wire-examples.json', module.WIRE_EXAMPLES)
        report['sources_unchanged_during_check'] = before == fingerprints(client)
        if (result.returncode or not validation.wasSuccessful() or validation.skipped
                or report['node_tests'].get('skipped', 1) or not report['sources_unchanged_during_check']):
            raise ValueError('installation_sync_check_failed')
        report['status'] = 'passed'
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report['error'] = str(error)
    save(output/'report.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
