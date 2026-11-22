"""Developing-client compatibility through real HTTP and production ControlLink."""
from copy import deepcopy
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from protocol.client_presentation import ClientPresentationService, ClientHTTPError
from protocol.client_presentation_ledger import PresentationLedger
from tests.client_handoff_fixture import IsolatedClient, RecordingHTTP, ROOT
from tests.client_presentation_fixture import MemoryPresentationOwner, DEVICE

START = 1794790800.0
WIRE_EXAMPLES = []


class HandoffHTTPTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get('DESKBOT_HANDOFF_CLIENT') or not os.environ.get('DESKBOT_HANDOFF_NODE'):
            self.fail('Run scripts/check-client-handoff.py with explicit --client and --node paths')
        temporary = tempfile.TemporaryDirectory(prefix='handoff-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.client = IsolatedClient(self.directory)
        self.addCleanup(self.client.close)
        self.http = RecordingHTTP(self.client.http)
        self.owner, self.now = MemoryPresentationOwner(), START
        self.ledger = PresentationLedger(self.directory / 'ledger.sqlite')
        self.addCleanup(lambda: self.ledger.close())
        self.service = self.make_service()
        self.addCleanup(lambda: self.service.close())

    def make_service(self):
        return ClientPresentationService(self.owner, self.http, self.ledger, device_id=DEVICE,
                                         client_stage='client-handoff-v1', wall=lambda: self.now)

    def chat(self, key='turn-1', message='今天做饭成功了，我很开心，想和你分享！'):
        return self.service.chat(key, 'correlation-' + key, message)

    def tick(self):
        state = self.service.tick()
        self.assertIsNone(state['error'], state['error'])
        return self.ledger.all('command')

    def stored(self, row):
        return self.client.http.request('GET', '/api/outbox/' + row['command']['command_id'])['command']

    def test_neutral_happy_identity_exact_receipts_and_other_consumer_queue(self):
        for key, message, semantic in [('greet', '你好，和你打个招呼。', 'neutral'),
                                       ('share', '今天做饭成功了，我很开心，想和你分享！', 'happy')]:
            self.assertTrue(self.chat(key, message)['accepted'])
            rows = self.tick()
            row = rows[-1]
            self.assertEqual(row['phase'], 'acknowledged')
            self.assertEqual(row['command']['payload']['expression'], semantic)
            stored = self.stored(row)
            self.assertEqual(stored['status'], 'completed')
            ack = stored['acknowledgment']
            self.assertEqual(ack['payload']['completion_scope'], 'player_dispatched')
            self.assertEqual(ack['payload']['role_revision'], row['command']['role_revision'])
            self.assertEqual(ack['correlation_id'], 'correlation-' + key)
            self.assertEqual(ack['payload']['receipt']['request_id'], row['usb']['request_id'])
            self.assertTrue(ack['payload']['receipt']['correlated'])
        self.assertEqual({row['command']['character_id'] for row in rows}, {'shaping-001'})
        self.assertEqual(len(self.owner.sent), 2)
        remaining = self.client.http.request('GET', '/api/outbox?target=vocat&device_id=' + DEVICE)['commands']
        self.assertEqual([command['type'] for command in remaining], ['speak', 'speak'])
        world = self.client.http.request('GET', '/api/world/state')
        self.assertEqual(world['world']['interaction']['user_turn_count'], 2)
        WIRE_EXAMPLES.extend(deepcopy(self.http.calls))

    def test_retries_keep_one_turn_and_changed_input_conflicts(self):
        first = self.chat()
        self.chat()
        body = self.ledger.get('input', 'turn-1')['body']
        duplicate = self.client.http.request('POST', '/api/chat', body)
        self.assertFalse(first['turn']['duplicate'])
        self.assertTrue(duplicate['turn']['duplicate'])
        self.assertEqual({key: value for key, value in first['turn'].items() if key != 'duplicate'},
                         {key: value for key, value in duplicate['turn'].items() if key != 'duplicate'})
        with self.assertRaises(ClientHTTPError) as caught:
            self.client.http.request('POST', '/api/chat', dict(body, message='改成另一段输入'))
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaisesRegex(ValueError, 'event_id_conflict'):
            self.chat(message='改成另一段输入')
        self.tick(); self.tick()
        self.assertEqual(len(self.owner.sent), 1)
        world = self.client.http.request('GET', '/api/world/state')
        self.assertEqual(world['world']['interaction']['user_turn_count'], 1)

    def test_accepted_waits_for_correlated_dispatched(self):
        self.owner.auto_receipt = False
        self.chat()
        row = self.tick()[0]
        self.assertEqual(row['phase'], 'prepared')
        self.owner.receipt('accepted')
        self.assertEqual(self.tick()[0]['phase'], 'prepared')
        self.assertNotEqual(self.stored(row)['status'], 'completed')
        self.owner.receipt('dispatched')
        row = self.tick()[0]
        self.assertEqual(row['phase'], 'acknowledged')
        self.assertEqual(self.stored(row)['status'], 'completed')

    def test_busy_then_home_delivers_original_command(self):
        self.chat(); self.owner.busy()
        row = self.tick()[0]
        self.assertEqual(row['phase'], 'claimed')
        self.assertFalse(self.owner.sent)
        self.owner.busy(False)
        completed = self.tick()[0]
        self.assertEqual(completed['command'], row['command'])
        self.assertEqual(self.stored(completed)['status'], 'completed')
        self.assertEqual(len(self.owner.sent), 1)

    def test_generation_change_ends_old_intent(self):
        self.chat(); self.owner.busy(); self.tick()
        with self.owner.lock:
            self.owner.presentation.advance()
        row = self.tick()[0]
        self.assertEqual(self.stored(row)['acknowledgment']['error']['code'], 'superseded')
        self.assertFalse(self.owner.sent)

    def test_busy_command_expires_without_submission(self):
        self.chat(); self.owner.busy(); self.tick()
        self.now += 31
        row = self.tick()[0]
        self.assertEqual(self.stored(row)['acknowledgment']['error']['code'], 'expired')
        self.assertFalse(self.owner.sent)

    def test_committed_ack_loss_and_ledger_reopen_keep_one_submission(self):
        self.chat(); self.http.drop_ack = True
        self.assertEqual(self.service.tick()['error'], 'fixture_ack_reply_lost')
        self.assertEqual(self.ledger.all('command')[0]['phase'], 'terminal')
        self.service.close(); self.ledger.close(); self.owner.restart()
        self.ledger = PresentationLedger(self.directory / 'ledger.sqlite')
        self.service = self.make_service()
        row = self.tick()[0]
        self.assertEqual(row['phase'], 'acknowledged')
        self.assertEqual(self.stored(row)['status'], 'completed')
        self.assertEqual(len(self.owner.sent), 1)


class HandoffToolTests(unittest.TestCase):
    def child(self, script):
        client = IsolatedClient.__new__(IsolatedClient)
        client.stderr = tempfile.TemporaryFile(mode='w+', encoding='utf-8')
        client.process = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=client.stderr, text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.addCleanup(lambda: client.close(require_success=False))
        return client

    def test_clean_shutdown_is_idempotent(self):
        client = self.child('import sys; sys.stdin.readline()')
        process = client.process
        client.close(); client.close()
        self.assertEqual(process.returncode, 0)

    def test_nonzero_shutdown_is_reported(self):
        client = self.child('import sys; sys.stdin.readline(); sys.exit(7)')
        process = client.process
        with self.assertRaisesRegex(RuntimeError, 'client_shutdown_failed: exit=7'):
            client.close()
        self.assertEqual(process.returncode, 7)

    def test_shutdown_timeout_is_reaped_and_reported(self):
        client = self.child('import sys, threading; sys.stdin.readline(); threading.Event().wait(60)')
        process = client.process
        with self.assertRaisesRegex(RuntimeError, 'forced_shutdown'):
            client.close(timeout=0.1)
        self.assertIsNotNone(process.poll())

    def test_scratch_cleanup_failure_preserves_completed_results(self):
        spec = importlib.util.spec_from_file_location('handoff_check_fixture', ROOT / 'scripts/check-client-handoff.py')
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        with tempfile.TemporaryDirectory(prefix='handoff-locked-') as directory:
            base = Path(directory)
            client = base / 'client'
            source = client / 'apps/deskbot-service/src'
            source.mkdir(parents=True)
            for name in ('app', 'persistence', 'runtime-profile'):
                (source / (name + '.mjs')).write_text('// fixture', encoding='utf-8')
            scratch = base / 'scratch'
            scratch.mkdir()

            class LockedScratch:
                name = str(scratch)

                def cleanup(self):
                    raise PermissionError('fixture_cleanup_locked')

            arguments = ['check-client-handoff.py', '--client', str(client), '--node', sys.executable,
                         '--output', str(base / 'report')]
            before_env, before_temp = dict(os.environ), tempfile.tempdir
            with patch.object(sys, 'argv', arguments), \
                    patch.object(checker.subprocess, 'check_output', side_effect=['v24.22.1', 'fixture-head', '']), \
                    patch.object(checker.tempfile, 'TemporaryDirectory', return_value=LockedScratch()), \
                    patch.object(checker.unittest.defaultTestLoader, 'loadTestsFromName',
                                 side_effect=lambda _: unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(checker.main(), 1)
            report = json.loads((base / 'report/report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['tests_run'], 2)
            self.assertEqual(report['passed'], 2)
            self.assertEqual(report['cleanup_error'], 'fixture_cleanup_locked')
            self.assertEqual(dict(os.environ), before_env)
            self.assertEqual(tempfile.tempdir, before_temp)

    def test_missing_checkout_writes_failed_report(self):
        with tempfile.TemporaryDirectory(prefix='handoff-invalid-') as directory:
            output = Path(directory) / 'report'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/check-client-handoff.py'),
                '--client', str(Path(directory) / 'absent'), '--node', sys.executable,
                '--output', str(output)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['tests_run'], 0)

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix='handoff-existing-') as directory:
            sentinel = Path(directory) / 'report.json'
            sentinel.write_text('original', encoding='utf-8')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/check-client-handoff.py'),
                '--client', directory, '--node', sys.executable, '--output', directory],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'original')


if __name__ == '__main__':
    unittest.main()
