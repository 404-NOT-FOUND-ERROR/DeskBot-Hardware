"""Capture-task persistence, M2 integration and installed-role rehearsal."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from protocol import shell_workbench as subject
from protocol.shell_profile_archive import restore_archive
from protocol.shell_role_assignment import activation_preview, resolve_assignment
from protocol.studio_control_link import initial_snapshot

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = dict(sensor_id='sensor-test', frame_id='fixed-test-frame', sensor_kind=1, units='transport_counts')
GRAPH = dict(sha256='a' * 64, roles=['grok-round-personal-v1', 'xiaozhi-e145'],
             names={'grok-round-personal-v1': '圆团', 'xiaozhi-e145': '原有角色'}, source='test')


def raw_window(offset=0, xyz=(100, 20, 30), kind=1, count=22, step=.5):
    start = datetime(2026, 11, 13, tzinfo=timezone.utc) + timedelta(seconds=offset)
    payload = bytes([kind]) + b''.join(v.to_bytes(2, 'big', signed=True) for v in xyz)
    return ''.join(f'{(start + timedelta(seconds=i * step)).isoformat()}\tHandle: cmd=06, {payload.hex(" ")}\n'
                   for i in range(count))


def service_module():
    spec = importlib.util.spec_from_file_location('test_shell_service', ROOT / 'scripts/serve-deskbot-shell-workbench.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='shell-unit-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = subject.SessionStore(self.folder, project_root=ROOT, graph=GRAPH)
        self.addCleanup(lambda: self.store.close())
        self.offset = 0

    def create(self, names=('A',)):
        self.doc = self.store.create(name='正式采集', device_id='device-test', context=CONTEXT,
                                     shell_names=list(names))['session']
        return self.doc

    def window(self, task, raw=None, mount=None):
        self.offset += 100
        raw = raw_window(self.offset) if raw is None else raw
        state = self.store.import_window(self.doc['id'], self.doc['revision'], task_id=task['id'],
            capture_id='batch-test', mount_id=mount or 'mount-' + str(self.offset), log_text=raw)
        self.doc = state['session']
        return self.doc

    def trained(self):
        self.create()
        self.window(self.doc['tasks'][0])
        self.doc = self.store.evaluate(self.doc['id'], self.doc['revision'])['session']
        return self.doc

    def test_project_creates_separate_shell_magnetic_ids_and_independent_tasks(self):
        doc = self.create(('A', 'B'))
        self.assertEqual(len(doc['tasks']), 42)
        self.assertEqual(len({t['id'] for t in doc['tasks']}), 42)
        self.assertNotEqual(doc['shells'][0]['shell_id'], doc['shells'][0]['magnet_profile_id'])
        self.assertEqual(doc['manual_acceptance'], 'pending')
        self.assertFalse(self.store.state()['device_written'])

    def test_context_names_and_project_count_limits(self):
        for names in [[], ['A', 'A'], ['A\n'], [str(i) for i in range(8)]]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.create(names)
        with self.assertRaises(ValueError):
            self.store.create(name='A', device_id='device', context=dict(CONTEXT, sensor_kind=True), shell_names=['A'])
        self.create()
        with patch.object(subject, 'MAX_SESSIONS', 1), self.assertRaises(ValueError):
            self.create()

    def test_close_and_new_process_owner_restore_saved_project(self):
        self.create(); self.window(self.doc['tasks'][0])
        expected = deepcopy(self.doc)
        self.store.close()
        self.store = subject.SessionStore(self.folder, project_root=ROOT, graph=GRAPH)
        self.assertEqual(self.store.state(expected['id'])['session'], expected)
        with self.assertRaises(Exception):
            subject.SessionStore(self.folder, project_root=ROOT, graph=GRAPH)

    def test_registration_required_before_assessment_or_binding(self):
        self.create()
        with self.assertRaises(ValueError):
            self.store.evaluate(self.doc['id'], 0)
        with self.assertRaises(ValueError):
            self.store.bind(self.doc['id'], 0, shell_id=self.doc['shells'][0]['shell_id'], role_id=GRAPH['roles'][0])

    def test_import_uses_actual_decoder_and_rejects_sensor_gaps_short_windows(self):
        self.create(); task = self.doc['tasks'][0]
        for raw in ['no timestamp', raw_window(kind=2), raw_window(count=10), raw_window(step=3)]:
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                self.window(task, raw)
        with patch.object(subject, 'MAX_LOG_BYTES', 10), self.assertRaises(ValueError):
            self.window(task)

    def test_duplicate_mount_samples_and_overlapping_windows_are_rejected(self):
        self.create()
        self.window(self.doc['tasks'][0], raw_window(0), mount='same-mount')
        task = self.doc['tasks'][1]
        for raw, mount in [(raw_window(100), 'same-mount'), (raw_window(0), 'new-mount'),
                           (raw_window(2, xyz=(101, 20, 30)), 'overlap')]:
            with self.subTest(mount=mount), self.assertRaises(ValueError):
                self.window(task, raw, mount=mount)

    def test_revision_bool_and_old_revision_cannot_replace_project(self):
        self.create(); self.window(self.doc['tasks'][0])
        path = self.folder / self.doc['id'] / 'session.json'
        before = path.read_bytes()
        for revision in [False, 0, -1]:
            with self.subTest(revision=revision), self.assertRaises(subject.SessionConflict):
                self.store.evaluate(self.doc['id'], revision)
        self.assertEqual(path.read_bytes(), before)

    def test_failed_atomic_write_keeps_previous_project_and_raw_log(self):
        self.create()
        path = self.folder / self.doc['id'] / 'session.json'
        before = path.read_bytes()
        with patch.object(self.store, 'writer', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.window(self.doc['tasks'][0])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(list(path.parent.glob('logs/*.log'))), 1)

    def test_empty_evaluation_coverage_is_not_a_pass(self):
        self.trained()
        report = self.doc['assessment']['report']
        self.assertFalse(report['all_evaluated_mounts_matched'])
        self.assertFalse(report['coverage_complete'])
        self.assertEqual([r['independent_mounts'] for r in report['coverage']], [0, 0, 0])

    def test_field_data_end_to_end_archive_binding_replay_and_removal(self):
        self.create()
        for task in list(self.doc['tasks'])[:2]:
            self.window(task)
        removed = next(t for t in self.doc['tasks'] if t['scenario'] == 'removed')
        self.window(removed, raw_window(1000, xyz=(500, 0, 0)))
        self.doc = self.store.evaluate(self.doc['id'], self.doc['revision'])['session']
        self.assertTrue(self.doc['assessment']['report']['all_evaluated_mounts_matched'])
        self.doc = self.store.bind(self.doc['id'], self.doc['revision'],
            shell_id=self.doc['shells'][0]['shell_id'], role_id=GRAPH['roles'][0])['session']
        self.doc = self.store.rehearse(self.doc['id'], self.doc['revision'])['session']
        first, removed = self.doc['rehearsal']['rows']
        self.assertEqual(first['resolution']['role_id'], GRAPH['roles'][0])
        self.assertEqual(first['resolution']['operation'], 'role.activate.0.' + 'a' * 64)
        self.assertIsNone(removed['resolution']['operation'])
        self.assertFalse(self.doc['rehearsal']['device_activated'])
        self.assertEqual(self.doc['rehearsal']['initial_status'], 'missing')

    def test_binding_unbind_and_unknown_roles(self):
        self.trained(); shell = self.doc['shells'][0]['shell_id']
        with self.assertRaises(ValueError):
            self.store.bind(self.doc['id'], self.doc['revision'], shell_id=shell, role_id='invented-role')
        for role in [GRAPH['roles'][0], GRAPH['roles'][1], None]:
            self.doc = self.store.bind(self.doc['id'], self.doc['revision'], shell_id=shell, role_id=role)['session']
            self.assertEqual(len(self.doc['assignments']), int(role is not None))

    def test_new_evidence_invalidates_assessment_before_rebinding(self):
        self.trained()
        self.window(self.doc['tasks'][1])
        self.assertIsNone(self.doc['assessment'])
        with self.assertRaises(ValueError):
            self.store.rehearse(self.doc['id'], self.doc['revision'])

    def test_repeat_evaluation_preserves_archive_version_and_binding(self):
        self.trained()
        self.doc = self.store.bind(self.doc['id'], self.doc['revision'],
            shell_id=self.doc['shells'][0]['shell_id'], role_id=GRAPH['roles'][0])['session']
        first = self.doc['assessment']['archive']['payload_sha256']
        self.doc = self.store.evaluate(self.doc['id'], self.doc['revision'])['session']
        self.assertEqual(self.doc['assessment']['archive']['payload_sha256'], first)
        self.assertEqual(self.doc['assignments'][0]['archive_sha256'], first)

    def test_non_object_project_file_has_clear_format_error(self):
        self.create()
        (self.folder / self.doc['id'] / 'session.json').write_text('[]', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '格式'):
            self.store.state(self.doc['id'])

    def test_historical_real_logs_remain_history_with_truthful_coverage(self):
        doc = self.store.example()['session']
        self.assertEqual(doc['source'], 'historical_rehearsal')
        self.assertEqual([r['independent_mounts'] for r in doc['assessment']['report']['coverage']], [1, 0, 1, 0])
        self.assertFalse(doc['assessment']['report']['coverage_complete'])
        self.assertEqual(doc['assessment']['archive']['qualification'], 'offline_replay')
        with self.assertRaises(ValueError):
            self.store.import_window(doc['id'], doc['revision'], task_id=doc['tasks'][1]['id'],
                capture_id='x', mount_id='x', log_text=raw_window())

    def test_path_escape_and_closed_store(self):
        for ident in ['../outside', '/tmp', 'bad\\name']:
            with self.assertRaises(ValueError):
                self.store.state(ident)
        self.store.close()
        with self.assertRaises(ValueError):
            self.store.state()


class RoleResolutionTests(unittest.TestCase):
    setUp = ShellTests.setUp
    create = ShellTests.create
    window = ShellTests.window
    trained = ShellTests.trained
    def assignment(self):
        self.trained()
        self.doc = self.store.bind(self.doc['id'], self.doc['revision'],
            shell_id=self.doc['shells'][0]['shell_id'], role_id=GRAPH['roles'][0])['session']
        self.archive = self.doc['assessment']['archive']
        self.observation = dict(status='known', profile_id=self.doc['shells'][0]['magnet_profile_id'])

    def test_resolution_retains_current_role_for_unknown_and_changed_versions(self):
        self.assignment()
        for status in ['unknown', 'ambiguous', 'unstable', 'stale', 'warming_up', 'missing']:
            result = resolve_assignment(self.archive, self.doc['assignments'], dict(self.observation, status=status), GRAPH)
            self.assertEqual(result['action'], 'keep_current')
        old = deepcopy(self.doc['assignments']); old[0]['archive_sha256'] = '0' * 64
        self.assertEqual(resolve_assignment(self.archive, old, self.observation, GRAPH)['reason'], 'registration_changed')
        self.assertEqual(resolve_assignment(self.archive, self.doc['assignments'], self.observation,
            dict(GRAPH, sha256='b' * 64))['reason'], 'role_package_changed')

    def test_live_template_requires_same_device_fresh_identity_and_graph(self):
        self.assignment()
        device, boot, session = 'device-' + 'a' * 32, 'boot-' + 'b' * 32, 'session-' + 'b' * 32 + '-1'
        resolution = resolve_assignment(self.archive, self.doc['assignments'], self.observation, GRAPH)
        row = dict(schema='deskbot.studio_roles/v1', message_type='roles', request_id='request-1',
            target_id=device, boot_id=boot, session_id=session, sequence=3,
            at_ms=1000, lease_ms=30000, result='state', reason='none', loaded=True,
            graph_sha256=GRAPH['sha256'], roles=GRAPH['roles'], default_index=1, target_index=1,
            current_index=1, active_index=1, phase='held', blocked=0, sleep='unknown', saved_index=1,
            save_state='saved', activation=dict(request_id='', status='idle'))
        control = dict(enabled=True, target_id=device, boot_id=boot, session_id=session,
            connection_id='connection-test', status='ready', pending=None, host_command=None,
            roles=dict(status='current', reply=row, age_ms=30, pending=None, error=None))
        good = activation_preview(resolution, control, expected_device_id=device)
        self.assertTrue(good['ready']); self.assertFalse(good['device_activated'])
        for key, val in [('target_id', 'another'), ('pending', {}), ('status', 'waiting')]:
            altered = dict(control, **{key: val})
            if key == 'pending': altered['pending'] = {'operation': 'read_roles'}
            self.assertFalse(activation_preview(resolution, altered, expected_device_id=device)['ready'])
        altered = deepcopy(control); altered['roles']['age_ms'] = 3000
        self.assertFalse(activation_preview(resolution, altered, expected_device_id=device)['ready'])


class ShellHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = service_module()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='shell-http-')
        self.addCleanup(self.temp.cleanup)
        self.bridge = type('MemoryBridge', (), {'control': lambda _: initial_snapshot(), 'close': lambda _: None})()
        self.server = self.service.StudioServer(0, bridge=self.bridge,
            state_path=Path(self.temp.name) / 'active.json', shell_root=Path(self.temp.name) / 'shells',
            profiles_path=Path(self.temp.name) / 'characters.json')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = build_opener(ProxyHandler({}))

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def request(self, path, value=None, headers=None, raw=None):
        if value is not None:
            raw = json.dumps(value).encode('utf-8')
        if raw is not None and headers is None:
            headers = {'Content-Type': 'application/json', 'X-DeskBot-Studio': 'shells-v1'}
        try:
            with self.opener.open(Request(self.origin + path, data=raw, headers=headers or {}), timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_production_http_real_history_binding_rehearsal_and_reload(self):
        code, response = self.request('/api/shells/example', {})
        self.assertEqual(code, 200, response)
        doc = response['session']
        code, response = self.request('/api/shells/bind', dict(session_id=doc['id'], revision=doc['revision'],
            shell_id=doc['shells'][0]['shell_id'], role_id=GRAPH['roles'][0]))
        self.assertEqual(code, 200, response); doc = response['session']
        code, response = self.request('/api/shells/rehearse', dict(session_id=doc['id'], revision=doc['revision']))
        self.assertEqual(code, 200, response)
        self.assertEqual(response['session']['rehearsal']['rows'][0]['resolution']['role_id'], GRAPH['roles'][0])
        self.assertEqual(self.request('/api/shells/state?session_id=' + doc['id'])[1]['session'], response['session'])
        self.assertFalse(response['device_written'])

    def test_http_context_boundaries_and_duplicate_fields(self):
        create = dict(name='正式采样', device_id='device-test', context=CONTEXT, shell_names=['A'])
        self.assertEqual(self.request('/api/shells/create', create)[0], 200)
        self.assertEqual(self.request('/api/shells/create', create, headers={'Content-Type': 'application/json'})[0], 415)
        self.assertEqual(self.request('/api/shells/create', create, headers={'Content-Type': 'application/json',
            'X-DeskBot-Studio': 'shells-v1', 'Origin': 'http://other.test'})[0], 403)
        self.assertEqual(self.request('/api/shells/example', raw=b'{"a":1,"a":2}')[0], 422)
        self.assertEqual(self.request('/api/shells/state?session_id=x&session_id=y')[0], 422)

    def test_stale_browser_revision_has_clear_conflict(self):
        _, response = self.request('/api/shells/example', {})
        doc = response['session']
        code, _ = self.request('/api/shells/rehearse', dict(session_id=doc['id'], revision=0))
        self.assertEqual(code, 409)


if __name__ == '__main__':
    unittest.main()
