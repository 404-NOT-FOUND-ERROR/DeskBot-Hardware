"""Serial handoff, asynchronous tasks and the production workbench HTTP routes."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from protocol import body_network_workbench as workbench
from protocol.studio_control_link import initial_snapshot
from tests.test_character_profiles import service_module

ROOT = Path(__file__).resolve().parents[1]
DEVICE, ELF = 'device-' + 'a' * 32, 'c' * 64


def profiles(folder):
    for channel, port in (('touch', 4318), ('expression', 4319)):
        target = folder / channel
        target.mkdir(parents=True)
        (target / 'receiver.json').write_text(json.dumps(dict(schema='deskbot.expression-wifi-config.v1',
            device_id=DEVICE, firmware_sha256=ELF, host='10.0.0.9', port=port, cert_file='server.pem')))
    build = folder / 'expression/build'
    build.mkdir()
    (build / 'report.json').write_text(json.dumps(dict(edition='E3f-9d.7-network-settings', network_settings=True, app_elf_sha256=ELF)))
    return folder / 'expression'


def settings(revision=0, host='10.0.0.9'):
    return dict(schema='deskbot.body_network/v1', message_type='network', request_id='network-read',
        target_id=DEVICE, boot_id='boot-' + 'b' * 32, session_id='session-' + 'b' * 32 + '-1',
        sequence=2, at_ms=1000, lease_ms=30000, result='state', storage='saved' if revision else 'defaults',
        host=host, touch_port=4318, expression_port=4319, revision=revision,
        source='saved' if revision else 'defaults', applied_revision=revision)


def result(row=None):
    return subprocess.CompletedProcess([], 0, json.dumps(dict(schema='deskbot.body_network_maintenance/v1',
        settings=row or settings(), device_id=DEVICE, firmware_sha256=ELF)), '')


class Bridge:
    def __init__(self):
        self.lock, self.operation = threading.RLock(), threading.Lock()
        self.python = Path('verified-python.exe')
        self.connection = dict(status='connected', port='COM4', reason='fixture')
        self.trace, self.listed, self.pending = [], 0, False
        self.restore_started, self.restore_gate = threading.Event(), None

    def available(self): return True
    def ports(self):
        self.listed += 1
        return dict(available=True, ports=[dict(port='COM4', key='d' * 64, usb_id='303A:1001')])
    def control(self): return dict(initial_snapshot(), pending={} if not self.pending else {'request': 'busy'})
    def snapshot(self): return dict(connection=deepcopy(self.connection))
    def _disconnect(self):
        self.trace.append('disconnect')
        self.connection = dict(status='disabled', port=None)
    def _connect(self, port, key):
        self.trace.append('connect')
        self.restore_started.set()
        if self.restore_gate: self.restore_gate.wait(5)
        self.connection = dict(status='connected', port=port)
    def connect(self, port, key):
        with self.operation: self._connect(port, key)
        return self.snapshot()
    def disconnect(self):
        with self.operation: self._disconnect()
        return self.snapshot()
    def close(self): pass


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='network-ui-test-')
        self.addCleanup(self.temp.cleanup)
        self.profile = profiles(Path(self.temp.name))
        self.bridge = Bridge()
        self.calls = []
        self.reply = result()
        def runner(command, **kwargs):
            self.bridge.trace.append('maintenance')
            self.calls.append(command)
            return self.reply
        self.service = workbench.NetworkWorkbench(self.bridge, self.profile, runner=runner,
            probe=lambda _: dict(touch='ready', expression='ready', xiaozhi='ready'))
        self.addCleanup(self.service.close)

    def payload(self, action='get', **changes):
        row = dict(request_id='network-' + '1' * 32, action=action, port='COM4')
        if action != 'get': row['expected_revision'] = 0
        if action == 'set': row.update(host='10.0.0.9', touch_port=4318, expression_port=4319)
        return row | changes

    def finish(self):
        self.service.thread.join(5)
        self.assertFalse(self.service.thread.is_alive())
        return self.service.snapshot()

    def test_passive_reads_do_not_enumerate_or_open_usb(self):
        for _ in range(20): self.service.snapshot()
        self.assertEqual((self.bridge.trace, self.bridge.listed), ([], 0))

    def test_recommended_address_tracks_the_current_matching_receivers(self):
        self.assertEqual(self.service.snapshot()['recommended']['host'], '10.0.0.9')
        for channel in ('touch', 'expression'):
            path = self.profile.parent / channel / 'receiver.json'
            row = json.loads(path.read_bytes()) | {'host': '10.0.0.12'}
            path.write_text(json.dumps(row))
            recommended = self.service.snapshot()['recommended']
            if channel == 'touch':
                self.assertIsNone(recommended)
            else:
                self.assertEqual(recommended, dict(host='10.0.0.12', touch_port=4318, expression_port=4319))
        self.assertEqual(self.bridge.trace, [])

    def test_read_hands_back_same_port_and_duplicate_request_does_not_rerun(self):
        value = self.payload()
        self.service.start(value)
        snapshot = self.finish()
        self.assertEqual(self.bridge.trace, ['disconnect', 'maintenance', 'connect'])
        self.assertEqual(snapshot['task']['status'], 'completed')
        self.assertTrue(snapshot['task']['observation_restored'])
        self.assertEqual(snapshot['settings']['revision'], 0)
        self.service.start(value)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaisesRegex(ValueError, 'network_request_changed'):
            self.service.start(value | dict(port='COM5'))

    def test_terminal_status_waits_for_observation_restore(self):
        self.bridge.restore_gate = threading.Event()
        self.service.start(self.payload())
        self.assertTrue(self.bridge.restore_started.wait(3))
        self.assertTrue(self.service.busy())
        with self.assertRaises(workbench.Busy): self.service.start(self.payload(request_id='network-' + '2' * 32))
        self.bridge.restore_gate.set()
        self.assertEqual(self.finish()['task']['status'], 'completed')

    def test_save_forwards_page_revision_and_preserves_exact_address(self):
        self.reply = result(settings(5, '10.0.0.7'))
        self.service.start(self.payload('set', expected_revision=4, host='10.0.0.7'))
        self.assertEqual(self.finish()['settings']['revision'], 5)
        cmd = self.calls[0]
        self.assertEqual(cmd[cmd.index('--expected-revision') + 1], '4')
        self.assertEqual(cmd[cmd.index('--host') + 1], '10.0.0.7')

    def test_conflict_or_malformed_output_retains_last_settings_and_restores_observer(self):
        self.service.settings = settings(2)
        for index, reply in enumerate([subprocess.CompletedProcess([], 1, '', 'ValueError: body_network_conflict'),
                                      subprocess.CompletedProcess([], 0, '[]', ''),
                                      subprocess.CompletedProcess([], 0, 'private traceback/path', '')]):
            self.reply = reply
            self.service.start(self.payload(request_id='network-' + str(index + 1) * 32))
            snapshot = self.finish()
            self.assertEqual(snapshot['task']['status'], 'failed')
            self.assertTrue(snapshot['task']['observation_restored'])
            self.assertEqual(snapshot['settings']['revision'], 2)
            self.assertNotIn('private', snapshot['task']['error'])
        self.assertEqual(len(self.calls), 3)

    def test_failure_timeout_and_restore_error_release_ownership(self):
        def fail(*_, **__): raise subprocess.TimeoutExpired('fixture', 110)
        def restore(*_): raise OSError('removed')
        self.service.runner, self.bridge._connect = fail, restore
        self.service.start(self.payload())
        task = self.finish()['task']
        self.assertEqual(task['error'], 'network_settings_failed')
        self.assertFalse(task['observation_restored'])
        self.assertTrue(self.bridge.operation.acquire(False)); self.bridge.operation.release()

    def test_other_usb_owner_or_command_rejected_before_disconnecting(self):
        self.bridge.connection['port'] = 'COM5'
        with self.assertRaisesRegex(workbench.Busy, 'other_usb_port_connected'): self.service.start(self.payload())
        self.bridge.connection['port'] = 'COM4'; self.bridge.pending = True
        with self.assertRaisesRegex(workbench.Busy, 'usb_command_in_progress'): self.service.start(self.payload())
        self.bridge.pending = False; self.bridge.operation.acquire()
        try:
            with self.assertRaisesRegex(workbench.Busy, 'usb_connection_busy'): self.service.start(self.payload())
        finally: self.bridge.operation.release()
        self.assertEqual(self.bridge.trace, [])

    def test_non_head_ports_and_invalid_fields_do_not_run_maintenance(self):
        for value in (self.payload(port='COM8'), self.payload(port='COM13'), self.payload(request_id=5),
                      self.payload('set', expected_revision=True), self.payload('set', host='127.0.0.1'),
                      self.payload('set', expression_port=4318), self.payload(extra=True), [], None):
            with self.subTest(value=value), self.assertRaises(ValueError): self.service.start(value)
        self.assertEqual(self.calls, [])

    def test_failed_probe_stays_available_for_settings(self):
        self.service.probe = lambda _: (_ for _ in ()).throw(OSError('fixture'))
        self.service._probe()
        self.assertEqual(set(self.service.services.values()), {'unavailable'})
        self.assertEqual(self.service.snapshot()['recommended']['host'], '10.0.0.9')

    def test_background_usb_command_cannot_cross_maintenance_handoff(self):
        from protocol.studio_control_bridge import ControlBridge
        owner = ControlBridge()
        owner.network_maintenance = True
        value = dict(operation='disable', command_id='command-test', connection_id='connection-' + 'a' * 32,
            target_id=DEVICE, boot_id='boot-' + 'b' * 32, session_id='session-' + 'b' * 32 + '-1')
        with self.assertRaisesRegex(ValueError, '连接设置正在处理'):
            owner._command(value)


class NetworkHTTPTests(unittest.TestCase):
    payload = WorkbenchTests.payload
    finish = WorkbenchTests.finish

    def setUp(self):
        WorkbenchTests.setUp(self)
        folder = Path(self.temp.name)
        self.server = service_module().StudioServer(0, bridge=self.bridge, state_path=folder / 'state.json',
            profiles_path=folder / 'profiles.json', shell_root=folder / 'shells', package_output=folder / 'packages')
        self.server.body_network.close(); self.server.body_network = self.service
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True); self.worker.start()
        self.addCleanup(self.stop_server)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = build_opener(ProxyHandler({}))

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(3)

    def request(self, value=None, *, raw=None, headers=None, path='/api/body/network'):
        if value is not None: raw = json.dumps(value).encode()
        if raw is not None and headers is None: headers = {'Content-Type': 'application/json', 'X-DeskBot-Studio': 'body-network-v1'}
        try:
            with self.opener.open(Request(self.origin + path, data=raw, headers=headers or {}), timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_real_http_async_read_and_existing_routes(self):
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.bridge.listed, 0)
        self.assertEqual(self.request(self.payload())[0], 202)
        self.finish()
        self.assertEqual(self.request()[1]['task']['status'], 'completed')
        self.assertEqual(self.request(path='/api/characters/state')[0], 200)

    def test_http_headers_origin_duplicates_and_busy_gate(self):
        self.assertEqual(self.request(raw=b'{}', headers={'Content-Type': 'application/json'})[0], 415)
        headers = {'Content-Type': 'application/json', 'X-DeskBot-Studio': 'body-network-v1', 'Origin': 'https://other.invalid'}
        self.assertEqual(self.request(self.payload(), headers=headers)[0], 403)
        self.assertEqual(self.request(raw=b'{"action":"get","action":"set"}')[0], 422)
        self.assertEqual(self.request(raw=b'[]')[0], 422)
        self.service.task = dict(status='running')
        for path in ('/api/body/connect', '/api/body/disconnect', '/api/studio/command'):
            self.assertEqual(self.request(raw=b'{}', path=path)[0], 409)

    def test_slow_disconnect_body_finishes_before_network_can_start(self):
        entered, runner_entered = threading.Event(), threading.Event()
        parent = self.server.RequestHandlerClass
        class Signalled(parent):
            def allowed(handler):
                if handler.path == '/api/body/disconnect': entered.set()
                return super().allowed()
        self.server.RequestHandlerClass = Signalled
        runner = self.service.runner
        def run(*args, **kwargs):
            runner_entered.set()
            return runner(*args, **kwargs)
        self.service.runner = run
        stream = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=4)
        self.addCleanup(stream.close)
        stream.sendall((f'POST /api/body/disconnect HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'
            'Content-Type: application/json\r\nX-DeskBot-Studio: e3a\r\nContent-Length: 2\r\nConnection: close\r\n\r\n').encode())
        self.assertTrue(entered.wait(2))
        start = threading.Thread(target=lambda: self.service.start(self.payload()), daemon=True)
        start.start()
        self.assertFalse(runner_entered.wait(.15))
        stream.sendall(b'{}')
        response = stream.recv(8192)
        self.assertIn(b'200', response.split(b'\r\n')[0])
        start.join(3)
        self.assertFalse(start.is_alive())
        self.assertEqual(self.finish()['task']['status'], 'completed')
        self.assertEqual(self.bridge.trace, ['disconnect', 'disconnect', 'maintenance'])


if __name__ == '__main__': unittest.main()
