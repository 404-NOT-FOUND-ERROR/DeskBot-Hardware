#!/usr/bin/env python3
"""Check a developing client checkout against the existing hardware adapter."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HARDWARE_FILES = [
    'integrations/client-handoff/README.md', 'integrations/client-handoff/contract.json',
    'integrations/client-handoff/server.mjs', 'scripts/check-client-handoff.py',
    'tests/client_handoff_fixture.py', 'tests/test_client_handoff.py',
    'tests/client_presentation_fixture.py', 'tests/test_client_presentation.py',
    'tests/test_studio_control_link.py', 'tests/test_studio_device_roles_e3f9d.py',
]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def wire_examples(calls):
    """Keep actual wire values; omit the unrelated full world/prompt snapshots."""
    examples = []
    for call in calls:
        response = call['response']
        if call['path'] == '/api/chat':
            turn = response['turn']
            response = {key: response[key] for key in ('accepted', 'duplicate')}
            response['turn'] = {key: turn[key] for key in ('turn_id', 'reply', 'provider', 'duplicate')}
        examples.append(dict(call, response=response,
                             response_excerpt=call['path'] == '/api/chat'))
    return examples


def fingerprints(client):
    client_files = sorted((client / 'apps/deskbot-service/src').rglob('*.mjs'))
    hardware = sorted(set(HARDWARE_FILES + [path.relative_to(ROOT).as_posix()
                                           for path in (ROOT / 'protocol').glob('*.py')]))
    return dict(client={path.relative_to(client).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in client_files},
                hardware={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in hardware})


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append(dict(test=test.id(), status='passed'))

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.records.append(dict(test=test.id(), status='failed'))

    def addError(self, test, error):
        super().addError(test, error)
        self.records.append(dict(test=test.id(), status='error'))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append(dict(test=test.id(), status='skipped', reason=reason))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True, type=Path)
    parser.add_argument('--node', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    client, node, output = (path.resolve() for path in (args.client, args.node, args.output))
    if output.exists():
        print('output_directory_exists: choose a new --output directory', file=sys.stderr)
        return 1
    if client == output or client in output.parents:
        print('output_must_be_outside_client_checkout', file=sys.stderr)
        return 1
    output.mkdir(parents=True)
    report = dict(artifact='client-hardware-handoff-check', status='failed', tests_run=0,
                  started_at=datetime.now(timezone.utc).isoformat(),
                  scope='original client HTTP/SQLite + production adapter/ControlLink in memory; fixed model',
                  client_checkout=str(client), python=sys.executable, node=str(node))
    try:
        for name in ('app', 'persistence', 'runtime-profile'):
            if not (client / ('apps/deskbot-service/src/' + name + '.mjs')).is_file():
                raise ValueError('client_module_missing: ' + name)
        version = subprocess.check_output([str(node), '--version'], text=True, timeout=10).strip()
        if not version.startswith('v') or int(version[1:].split('.')[0]) < 24:
            raise ValueError('node_24_or_newer_required')
        report['node_version'] = version
        report['client_commit'] = subprocess.check_output(
            ['git', '-C', str(client), 'rev-parse', 'HEAD'], text=True, timeout=10).strip()
        report['client_worktree'] = subprocess.check_output(
            ['git', '-C', str(client), 'status', '--short', '--', 'apps/deskbot-service/src'],
            text=True, timeout=10).splitlines()
        before = fingerprints(client)
        write_json(output / 'source-hashes.json', before)
        contract = json.loads((ROOT / 'integrations/client-handoff/contract.json').read_text(encoding='utf-8'))
        from protocol.client_presentation import SEMANTICS
        if set(contract['expression']['semantics']) != SEMANTICS:
            raise ValueError('handoff_semantics_differ_from_adapter')
        for name in contract['references']:
            if not (ROOT / name).is_file():
                raise ValueError('contract_reference_missing: ' + name)
        report['contract_revision'] = contract['revision']
        old_temp, old_env = tempfile.tempdir, dict(os.environ)
        scratch = tempfile.TemporaryDirectory(prefix='scratch-', dir=output)
        try:
            # All scratch databases live with this run and are removed on exit.
            tempfile.tempdir = scratch.name
            os.environ.update(DESKBOT_HANDOFF_CLIENT=str(client), DESKBOT_HANDOFF_NODE=str(node))
            suite = unittest.TestSuite([
                unittest.defaultTestLoader.loadTestsFromName('tests.test_client_presentation.PresentationTests'),
                unittest.defaultTestLoader.loadTestsFromName('tests.test_client_handoff')])
            log = io.StringIO()
            result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=RecordedResult).run(suite)
            # Preserve completed results even if Windows still holds a scratch file.
            report.update(tests_run=result.testsRun, passed=sum(row['status'] == 'passed' for row in result.records),
                          failed=len(result.failures) + len(result.errors), skipped=len(result.skipped), tests=result.records)
            (output / 'tests.log').write_text(log.getvalue(), encoding='utf-8')
        finally:
            try:
                scratch.cleanup()
            except OSError as error:
                report['cleanup_error'] = str(error)
            tempfile.tempdir = old_temp
            os.environ.clear(); os.environ.update(old_env)
        from tests.test_client_handoff import WIRE_EXAMPLES
        write_json(output / 'wire-examples.json', wire_examples(WIRE_EXAMPLES))
        report['sources_unchanged'] = fingerprints(client) == before
        if result.wasSuccessful() and not result.skipped and report['sources_unchanged'] and 'cleanup_error' not in report:
            report['status'] = 'passed'
    except Exception as error:
        report['error'] = str(error)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_json(output / 'report.json', report)
    print(json.dumps(dict(status=report['status'], tests_run=report['tests_run'],
                          passed=report.get('passed', 0), report=str(output / 'report.json')), ensure_ascii=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
