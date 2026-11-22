"""Field checklist persistence and progression through real capture evaluation."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from protocol.shell_capture_readiness import build_field_guide, validate_setup
from protocol import shell_workbench as subject
import test_shell_workbench as previous
from test_shell_workbench import GRAPH, CONTEXT, raw_window


class FieldGuideTests(unittest.TestCase):
    setUp = previous.ShellTests.setUp
    create = previous.ShellTests.create
    window = previous.ShellTests.window

    def guide(self):
        return self.store.state(self.doc['id'])['field_guide']

    def prepare(self, **changes):
        values = dict(layout_fixed=True, frame_confirmed=True, unknown_reference='未登记蓝色外壳')
        values.update(changes)
        self.doc = self.store.prepare(self.doc['id'], self.doc['revision'], **values)['session']

    def capture_all(self, mismatch=False):
        self.create()
        self.prepare()
        for task in list(self.doc['tasks']):
            xyz = (500, 0, 0) if task['scenario'] in ('removed', 'unknown_layout') else (100, 20, 30)
            if mismatch and task['scenario'] == 'remount' and task['number'] == 1:
                xyz = (900, 0, 0)
            self.window(task, raw_window(self.offset + 100, xyz=xyz))
        self.doc = self.store.evaluate(self.doc['id'], self.doc['revision'])['session']

    def bind(self):
        self.doc = self.store.bind(self.doc['id'], self.doc['revision'],
            shell_id=self.doc['shells'][0]['shell_id'], role_id=GRAPH['roles'][0])['session']

    def test_old_project_defaults_to_unconfirmed_without_modifying_saved_bytes(self):
        self.create()
        path = self.folder / self.doc['id'] / 'session.json'
        old = deepcopy(self.doc); old.pop('field_setup')
        path.write_text(json.dumps(old), encoding='utf-8')
        before = path.read_bytes()
        guide = self.guide()
        self.assertEqual(guide['stage'], 'preparation')
        self.assertFalse(guide['prepared'])
        self.assertEqual(guide['revision'], 0)
        self.assertEqual(path.read_bytes(), before)

    def test_preparation_survives_service_reopen_and_preserves_capture_evidence(self):
        self.create(); self.window(self.doc['tasks'][0]); self.prepare()
        expected = self.store.state(self.doc['id'])
        self.store.close()
        self.store = subject.SessionStore(self.folder, project_root=self.store.project_root, graph=GRAPH)
        self.assertEqual(self.store.state(self.doc['id']), expected)
        self.assertTrue(expected['field_guide']['prepared'])
        self.assertIsNotNone(expected['session']['tasks'][0]['recording'])

    def test_registration_is_done_for_each_shell_before_remounts(self):
        self.create(('A', 'B')); self.prepare()
        first = self.guide()['next_task']
        self.assertEqual(first['scenario'], 'registration')
        self.window(self.doc['tasks'][0])
        second = self.guide()['next_task']
        self.assertIn('B', second['label'])
        self.assertEqual(second['scenario'], 'registration')
        self.window(next(t for t in self.doc['tasks'] if t['id'] == second['task_id']))
        self.assertEqual(self.guide()['stage'], 'remount')

    def test_suggested_mounts_are_unique_and_compatible_with_import_limits(self):
        self.create(('A', 'B')); self.prepare()
        tasks = self.guide()['tasks']
        self.assertEqual(len({t['suggested_mount_id'] for t in tasks}), 42)
        for task in tasks:
            self.assertEqual(subject.identifier(task['suggested_mount_id']), task['suggested_mount_id'])
            self.assertEqual(len(task['instructions']), 3)
        self.assertIn('完整取下', next(t for t in tasks if t['scenario'] == 'remount')['instructions'][0])
        self.assertIn('未登记蓝色外壳', tasks[-1]['instructions'][0])

    def test_unknown_reference_missing_is_a_visible_preparation_item(self):
        self.create(); self.prepare(unknown_reference='')
        self.assertFalse(self.guide()['prepared'])
        self.assertEqual([r['id'] for r in self.guide()['checks'] if not r['done']], ['unknown_reference'])

    def test_recorded_unknown_reference_cannot_relabel_existing_evidence(self):
        self.capture_all(); self.bind()
        before = self.store.state(self.doc['id'])
        with self.assertRaisesRegex(ValueError, '名称已固定'):
            self.prepare(unknown_reference='另一种未知布局')
        self.assertEqual(self.store.state(self.doc['id']), before)
        self.prepare(frame_confirmed=False)
        self.assertEqual(self.guide()['setup']['unknown_reference'], '未登记蓝色外壳')

    def test_reference_can_be_corrected_before_unknown_data_is_recorded(self):
        self.create(); self.prepare(); self.window(self.doc['tasks'][0])
        self.prepare(unknown_reference='未登记绿色外壳')
        self.assertEqual(self.guide()['setup']['unknown_reference'], '未登记绿色外壳')
        task = next(t for t in self.doc['tasks'] if t['scenario'] == 'unknown_layout')
        self.window(task, raw_window(self.offset + 100, xyz=(500, 0, 0)))
        with self.assertRaisesRegex(ValueError, '名称已固定'):
            self.prepare(unknown_reference='未登记蓝色外壳')

    def test_old_unlabelled_unknown_data_is_not_reassigned_by_new_preparation(self):
        self.create()
        task = next(t for t in self.doc['tasks'] if t['scenario'] == 'unknown_layout')
        self.window(task, raw_window(100, xyz=(500, 0, 0)))
        path = self.folder / self.doc['id'] / 'session.json'
        old = deepcopy(self.doc); old.pop('field_setup')
        path.write_text(json.dumps(old), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '名称已固定'):
            self.prepare()
        self.assertFalse(self.guide()['prepared'])

    def test_registered_shell_name_cannot_also_name_the_unknown_reference(self):
        self.create(('Alpha',))
        with self.assertRaisesRegex(ValueError, '不同的名称'):
            self.prepare(unknown_reference='alpha')

    def test_preparation_rejects_bad_types_and_preserves_saved_project(self):
        self.create()
        before = self.store.state(self.doc['id'])
        for changes in [dict(layout_fixed=1), dict(frame_confirmed='yes'),
                        dict(unknown_reference='x' * 81), dict(unknown_reference=' A '),
                        dict(unknown_reference='A\nB'), dict(unknown_reference=None)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(**changes)
        self.assertEqual(self.store.state(self.doc['id']), before)

    def test_setup_schema_is_explicit(self):
        for invalid in [None, [], {}, dict(schema='other', layout_fixed=True,
                frame_confirmed=True, unknown_reference='X')]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_setup(invalid)

    def test_stale_preparation_and_write_failure_leave_prior_revision(self):
        self.create(); self.prepare()
        before = self.store.state(self.doc['id'])
        with self.assertRaises(subject.SessionConflict):
            self.store.prepare(self.doc['id'], 0, layout_fixed=False,
                               frame_confirmed=False, unknown_reference='')
        with patch.object(self.store, 'writer', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.prepare(layout_fixed=False)
        self.assertEqual(self.store.state(self.doc['id']), before)

    def test_completed_files_do_not_pass_failed_recognition(self):
        self.capture_all(mismatch=True); self.bind()
        guide = self.guide()
        self.assertTrue(guide['completion']['capture_complete'])
        self.assertFalse(guide['completion']['evaluation_matches'])
        self.assertEqual(guide['stage'], 'review_samples')
        self.assertFalse(guide['completion']['ready_for_confirmation'])

    def test_actual_complete_replay_prepares_binding_confirmation_not_acceptance(self):
        self.capture_all()
        self.assertEqual(self.guide()['stage'], 'binding')
        self.bind()
        guide = self.guide()
        self.assertTrue(guide['completion']['ready_for_confirmation'])
        self.assertEqual(guide['stage'], 'ready_for_confirmation')
        self.assertEqual(guide['completion']['manual_acceptance'], 'pending')
        self.assertEqual(self.doc['assignments'][0]['binding_state'], 'draft')
        self.assertFalse(guide['device_written'])

    def test_saved_assignment_for_another_graph_needs_new_selection(self):
        self.capture_all(); self.bind()
        guide = build_field_guide(self.doc, dict(GRAPH, sha256='b' * 64))
        self.assertEqual(guide['stage'], 'binding')
        self.assertFalse(guide['completion']['bindings_current'])

    def test_partial_assessment_remains_in_capture_stage(self):
        self.create(); self.prepare(); self.window(self.doc['tasks'][0])
        self.doc = self.store.evaluate(self.doc['id'], self.doc['revision'])['session']
        self.bind()
        guide = self.guide()
        self.assertTrue(guide['completion']['bindings_current'])
        self.assertFalse(guide['completion']['evaluation_matches'])
        self.assertEqual(guide['stage'], 'remount')

    def test_removing_setup_confirmation_preserves_data_and_reopens_preparation(self):
        self.capture_all(); self.bind(); self.prepare(frame_confirmed=False)
        guide = self.guide()
        self.assertTrue(guide['completion']['evaluation_matches'])
        self.assertEqual(guide['stage'], 'preparation')
        self.assertFalse(guide['completion']['ready_for_confirmation'])

    def test_historical_examples_cannot_become_formal_by_saving_preparation(self):
        self.doc = self.store.example()['session']
        guide = self.guide()
        self.assertFalse(guide['formal']); self.assertEqual(guide['tasks'], [])
        self.assertIsNone(guide['completion'])
        with self.assertRaises(ValueError):
            self.prepare()


class FieldGuideHTTPTests(unittest.TestCase):
    setUpClass = classmethod(previous.ShellHTTPTests.setUpClass.__func__)
    setUp = previous.ShellHTTPTests.setUp
    stop = previous.ShellHTTPTests.stop
    request = previous.ShellHTTPTests.request

    def test_prepare_api_persists_guidance_and_rejects_stale_changes(self):
        _, state = self.request('/api/shells/create', dict(name='正式采样', device_id='device-test',
                                                         context=CONTEXT, shell_names=['A']))
        ident = state['session']['id']
        change = dict(session_id=ident, revision=0, layout_fixed=True,
                      frame_confirmed=True, unknown_reference='未登记外壳')
        code, prepared = self.request('/api/shells/prepare', change)
        self.assertEqual(code, 200, prepared)
        self.assertTrue(prepared['field_guide']['prepared'])
        self.assertEqual(self.request('/api/shells/state?session_id=' + ident)[1], prepared)
        self.assertEqual(self.request('/api/shells/prepare', change)[0], 409)
        change['revision'] = 1; change['layout_fixed'] = 'yes'
        self.assertEqual(self.request('/api/shells/prepare', change)[0], 422)
        self.assertEqual(self.request('/api/shells/state?session_id=' + ident)[1], prepared)

    def test_prepare_requires_same_origin_and_complete_fields(self):
        self.assertEqual(self.request('/api/shells/prepare', {})[0], 422)
        headers = {'Content-Type': 'application/json', 'X-DeskBot-Studio': 'shells-v1',
                   'Origin': 'http://elsewhere.test'}
        self.assertEqual(self.request('/api/shells/prepare', {}, headers=headers)[0], 403)

    def test_http_unknown_sampling_locks_the_reference_label(self):
        _, response = self.request('/api/shells/create', dict(name='正式采样', device_id='device-test',
                                                            context=CONTEXT, shell_names=['A']))
        ident = response['session']['id']
        change = dict(session_id=ident, revision=0, layout_fixed=True,
                      frame_confirmed=True, unknown_reference='对照蓝')
        _, response = self.request('/api/shells/prepare', change)
        task = next(t for t in response['session']['tasks'] if t['scenario'] == 'unknown_layout')
        code, response = self.request('/api/shells/import', dict(session_id=ident, revision=1,
            task_id=task['id'], capture_id='capture-one', mount_id='unknown-one',
            log_text=raw_window(0, xyz=(500, 0, 0))))
        self.assertEqual(code, 200, response)
        self.assertTrue(response['field_guide']['reference_locked'])
        change.update(revision=2, unknown_reference='对照绿')
        self.assertEqual(self.request('/api/shells/prepare', change)[0], 422)
        self.assertEqual(self.request('/api/shells/state?session_id=' + ident)[1], response)


if __name__ == '__main__':
    unittest.main()
