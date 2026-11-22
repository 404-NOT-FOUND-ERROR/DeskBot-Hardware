"""Production installation/ControlLink -> original client HTTP/SQLite, in isolation."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from protocol.client_installation_sync import InstallationHTTP, prepare, sync_once
from tests import test_shell_installation as fixtures

ROOT = Path(__file__).resolve().parents[1]
CHARACTER = 'shaping-001'
SOURCE = 'installation-integration'
WIRE_EXAMPLES = []


class ClientProcess:
    def __init__(self, folder):
        self.folder, self.process = folder, None
        self.log = (folder/'client.log').open('a+', encoding='utf-8')
        self.start()

    def start(self, resume=False):
        self.process = subprocess.Popen([os.environ['DESKBOT_HANDOFF_NODE'],
            str(ROOT/'integrations/client-installation/server.mjs'),
            '--client-root', os.environ['DESKBOT_HANDOFF_CLIENT'], '--database', str(self.folder/'client.sqlite'),
            '--source-id', SOURCE, *(['--resume'] if resume else [])],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(self.process.stdout.readline()), daemon=True).start()
        try:
            line = ready.get(timeout=20)
            if not line:
                raise RuntimeError('isolated_client_start_failed')
            self.http = InstallationHTTP(json.loads(line)['url'])
        except Exception:
            self.close()
            raise

    def stop(self):
        if self.process:
            try:
                self.process.communicate('stop\n', timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.communicate(timeout=5)
                raise RuntimeError('isolated_client_shutdown_failed') from None
            finally:
                self.process = None

    def restart(self):
        self.stop()
        self.start(resume=True)

    def close(self):
        self.stop()
        self.log.close()


class InstallationHTTPFixture(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.calls.append((self.command, self.path))
        data = json.dumps(self.server.installations.state(), ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class ClientInstallationSyncTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get('DESKBOT_HANDOFF_CLIENT') or not os.environ.get('DESKBOT_HANDOFF_NODE'):
            self.fail('Use scripts/check-client-installation.py with explicit --client and --node')
        self.f = fixtures.InstallationTests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.f.service.character_id = CHARACTER
        self.workbench_server = ThreadingHTTPServer(('127.0.0.1', 0), InstallationHTTPFixture)
        self.workbench_server.installations = self.f.service
        self.workbench_server.calls = []
        self.thread = threading.Thread(target=self.workbench_server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_workbench)
        self.workbench = InstallationHTTP(f'http://127.0.0.1:{self.workbench_server.server_port}')
        temporary = tempfile.TemporaryDirectory(prefix='installation-integration-')
        self.addCleanup(temporary.cleanup)
        self.client = ClientProcess(Path(temporary.name))
        self.addCleanup(self.client.close)
        self.client.http.request('POST', '/api/devices/hello', dict(device_id=fixtures.DEVICE, character_id=CHARACTER,
            hardware='esp32-s3', firmware='E3f-9d.3-memory', capabilities={'display.expression': True}))

    def close_workbench(self):
        self.workbench_server.shutdown()
        self.thread.join(timeout=5)
        self.workbench_server.server_close()

    def confirm(self, shell, ident, run=True):
        request = self.f.request(shell, ident)
        request['character_id'] = CHARACTER
        self.f.service.confirm(**request)
        if run:
            self.f.run_to(self.f.clock.ms + 4500)

    def sync(self, client=None):
        return sync_once(self.workbench, client or self.client.http, source_id=SOURCE,
                         character_id=CHARACTER, device_id=fixtures.DEVICE)

    def current(self):
        query = urlencode(dict(character_id=CHARACTER, device_id=fixtures.DEVICE))
        return self.client.http.request('GET', '/api/hardware/installation?' + query)

    def test_real_installation_a_b_a_receipts_and_original_life_unchanged(self):
        world = self.client.http.request('GET', '/api/world/state')
        activations = 0
        for shell, ident in [('a', 'a-first'), ('b', 'b-first'), ('a', 'a-return')]:
            self.confirm(shell, ident)
            activations += 1
            result = self.sync()
            self.assertEqual(result['status'], 'synchronized')
            self.assertEqual(self.current()['current']['installation_id'], ident)
            self.assertEqual(len(self.f.owner.activations), activations)
            self.assertEqual(self.sync()['result']['duplicate'], True)
        self.assertEqual(self.client.http.request('GET', '/api/world/state'), world)
        self.assertTrue(all(method == 'GET' and path == '/api/shell-installation/state'
                            for method, path in self.workbench_server.calls))
        state = self.f.service.state()
        WIRE_EXAMPLES.append(dict(request=prepare(state, source_id=SOURCE, character_id=CHARACTER,
                                                  device_id=fixtures.DEVICE), result=result, client_state=self.current()))

    def test_waiting_and_newest_pending_confirmation_send_nothing(self):
        self.f.owner.lab.device.page = 'other'
        self.f.run_to(4000)
        self.confirm('b', 'waiting-b', run=False)
        self.assertEqual(self.sync()['status'], 'waiting')
        self.f.run_to(4200)
        self.confirm('a', 'latest-a', run=False)
        self.f.run_to(4400)
        self.assertEqual(self.sync()['status'], 'waiting')
        self.assertIsNone(self.current()['current'])
        self.assertEqual(self.f.row('waiting-b')['phase'], 'superseded')
        self.f.owner.lab.device.page = 'home'
        self.f.run_to(9000)
        self.assertEqual(self.sync()['status'], 'synchronized')
        self.assertEqual(self.current()['current']['installation_id'], 'latest-a')

    def test_committed_reply_lost_then_client_reopen_reuses_receipt(self):
        self.confirm('b', 'lost-response')
        http = self.client.http
        class LostReturn:
            receipt = None
            def request(inner, method, route, value=None):
                result = http.request(method, route, value)
                inner.receipt = result['receipt']
                raise OSError('fixture_response_lost')
        lost = LostReturn()
        with self.assertRaises(OSError):
            self.sync(lost)
        self.client.restart()
        result = self.sync()
        self.assertTrue(result['result']['duplicate'])
        self.assertEqual(result['result']['receipt'], lost.receipt)
        self.assertEqual(self.current()['current']['installation_id'], 'lost-response')

    def test_mismatched_current_device_and_wrong_identity_are_not_submitted(self):
        self.confirm('a', 'a')
        state = self.f.service.state()
        state['device']['actual'] = 'old'
        self.assertIsNone(prepare(state, source_id=SOURCE, character_id=CHARACTER, device_id=fixtures.DEVICE))
        with self.assertRaises(ValueError):
            prepare(self.f.service.state(), source_id=SOURCE, character_id='other', device_id=fixtures.DEVICE)
        self.assertIsNone(self.current()['current'])

    def test_frozen_profile_hash_and_old_revision_conflicts_preserve_new_current(self):
        self.confirm('a', 'original-a')
        old = prepare(self.f.service.state(), source_id=SOURCE, character_id=CHARACTER, device_id=fixtures.DEVICE)
        self.sync()
        self.confirm('b', 'new-b')
        self.sync()
        changed = deepcopy(old)
        changed['installation']['profile']['persona'] = 'changed'
        with self.assertRaises(OSError):
            self.client.http.request('POST', '/api/hardware/installations', changed)
        old['source_revision'] += 1000
        repeated = self.client.http.request('POST', '/api/hardware/installations', old)
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(repeated['current_installation_id'], 'new-b')

    def test_new_query_session_does_not_change_saved_installation_identity(self):
        self.confirm('b', 'session-b')
        first = self.sync()['result']['receipt']
        self.f.owner.lab.reconnect(self.f.clock.ms)
        self.f.run_to(self.f.clock.ms + 4000)
        result = self.sync()
        self.assertTrue(result['result']['duplicate'])
        self.assertEqual(result['result']['receipt'], first)

    def test_python_migrated_profile_with_unicode_boundaries_reaches_original_client(self):
        legacy = dict(schema='deskbot.role_profile.v1', shell_id='legacy-shell', magnet_profile_id='legacy-magnet',
            role_id='legacy-role', display_name='🐸' * 80, voice_id='voice-a', catchphrases=['想吃🍲'],
            expression_map={'happy': 'smile'}, action_map={'greeting': 'cat_nuzzle'}, asset_version='assets-1',
            profile_revision=1, binding_state='confirmed', binding_version='deskbot.expression_bindings/v1')
        profile = self.f.characters.migrate(legacy, 'round')['profile']
        profile['persona'] = '🍲' * 2000
        profile = self.f.characters.save(profile, profile['profile_revision'])['profile']
        self.f.registry.confirm('a', profile['role_id'], profile['profile_revision'], None, None,
                                self.f.registry.state()['revision'])
        self.confirm('a', 'unicode-migration')
        self.assertEqual(self.sync()['status'], 'synchronized')
        stored = self.current()['current']['installation']['profile']
        self.assertEqual(stored, profile)
        self.assertEqual(stored['legacy']['profile'], legacy)

    def test_sync_command_reports_waiting_then_original_client_receipt(self):
        command = [sys.executable, str(ROOT/'scripts/sync-client-installation.py'),
            '--workbench', self.workbench.url, '--client', self.client.http.url,
            '--source-id', SOURCE, '--character-id', CHARACTER, '--device-id', fixtures.DEVICE]
        waiting = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(waiting.returncode, 2, waiting.stderr)
        self.assertEqual(json.loads(waiting.stdout)['status'], 'waiting')
        self.confirm('a', 'from-command')
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'synchronized')
        self.assertEqual(self.current()['current']['installation_id'], 'from-command')


class LocalURLTests(unittest.TestCase):
    def response_fixture(self, raw, length):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(length))
                self.end_headers();self.wfile.write(raw);self.close_connection=True
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close():
            server.shutdown();server.server_close();thread.join(3)
        self.addCleanup(close)
        return InstallationHTTP(f'http://127.0.0.1:{server.server_port}')

    def test_truncated_http_is_a_recoverable_transport_error(self):
        http=self.response_fixture(b'{',100)
        with self.assertRaises(OSError): http.request('GET','/state')

    def test_nonobject_http_json_is_rejected(self):
        http=self.response_fixture(b'[]',2)
        with self.assertRaises(ValueError): http.request('GET','/state')

    def test_only_explicit_loopback_origin_is_supported(self):
        for url in ['https://127.0.0.1:1234', 'http://example.com:1234', 'http://127.0.0.1',
                    'http://127.0.0.1:1234/path', 'http://user@127.0.0.1:1234', 'http://localhost:1234']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                InstallationHTTP(url)
