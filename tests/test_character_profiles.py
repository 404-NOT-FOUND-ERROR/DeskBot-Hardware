"""Character unit tests and the production HTTP/persistence integration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from protocol import character_profiles as cp
from protocol.role_activation import RoleStorageError
from protocol.studio_control_link import initial_snapshot

ROOT = Path(__file__).resolve().parents[1]
CATALOG = dict(catalog_id='a' * 64, roles=[dict(id='round', name='圆团'), dict(id='old', name='原有角色')])
GRAPH = dict(sha256='b' * 64, roles=['round', 'old'])


def profile(ident='character-test'):
    return dict(schema=cp.SCHEMA, role_id=ident, display_name='小圆', persona='好奇，回应简短。',
        voice=dict(provider='xiaozhi', voice_id='voice-test', speaking_style='轻快'),
        expression_role_id='round', expression_graph_sha256=GRAPH['sha256'],
        preview_catalog_id=CATALOG['catalog_id'], profile_revision=0)


def service_module():
    spec = importlib.util.spec_from_file_location('character_test_server', ROOT / 'scripts/serve-deskbot-shell-workbench.py')
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='characters-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'profiles.json'
        self.store = cp.CharacterProfiles(self.path, catalog=CATALOG, graph=GRAPH)
        self.addCleanup(self.store.close)
        self.legacy = json.loads((ROOT / 'protocol/deskbot-role-profile-v1.example.json').read_bytes())

    def test_create_edit_and_reopen_are_full_characters_without_shell(self):
        row = self.store.save(profile(), 0)['profile']; row['persona'] = '温和。'
        saved = self.store.save(row, 1)['profile']
        self.assertEqual(saved['profile_revision'], 2)
        self.assertNotIn('shell_id', saved)
        self.assertNotEqual(saved['role_id'], saved['expression_role_id'])
        self.store.close()
        restored = cp.CharacterProfiles(self.path, catalog=CATALOG, graph=GRAPH)
        self.addCleanup(restored.close)
        self.assertEqual(restored.snapshot()['profiles'], [saved])

    def test_configuration_and_activation_have_separate_progress(self):
        self.store.save(profile(), 0)
        readiness = self.store.snapshot()['readiness']['character-test']
        self.assertTrue(readiness['voice_configured'])
        self.assertEqual(readiness['voice_service'], 'pending')
        self.assertEqual(readiness['shell_binding'], 'not_configured')

    def test_blank_persona_voice_is_a_valid_draft(self):
        row = profile(); row['persona'] = ''; row['voice'].update(provider='unconfigured', voice_id='', speaking_style='')
        self.store.save(row, 0)
        readiness = self.store.snapshot()['readiness'][row['role_id']]
        self.assertFalse(readiness['voice_configured']); self.assertFalse(readiness['persona_configured'])

    def test_stale_revision_does_not_overwrite(self):
        self.store.save(profile(), 0)
        before = self.path.read_bytes()
        with self.assertRaises(cp.CharacterConflict): self.store.save(profile(), 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_writers_produce_one_revision(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(self.store.save, profile(), 0) for _ in range(2)]
        self.assertEqual(sum(f.exception() is None for f in pending), 1)
        self.assertEqual(sum(isinstance(f.exception(), cp.CharacterConflict) for f in pending), 1)

    def test_lost_reply_retry_reports_conflict_and_saved_result(self):
        result = self.store.save(profile(), 0)
        with self.assertRaises(cp.CharacterConflict): self.store.save(profile(), 0)
        self.assertEqual(self.store.snapshot()['profiles'], [result['profile']])

    def test_failure_keeps_saved_state_and_retry_works(self):
        row = self.store.save(profile(), 0)['profile']; original = self.path.read_bytes(); writer = self.store.writer
        self.store.writer = lambda *_: (_ for _ in ()).throw(OSError('full'))
        row['display_name'] = '新名'
        with self.assertRaises(RoleStorageError): self.store.save(row, 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.store.snapshot()['profiles'][0]['display_name'], '小圆')
        self.store.writer = writer
        self.assertEqual(self.store.save(row, 1)['profile']['display_name'], '新名')

    def test_graph_and_preview_versions_are_checked_separately(self):
        for key in ('expression_graph_sha256', 'preview_catalog_id'):
            row = profile(); row[key] = 'c' * 64
            with self.subTest(key=key), self.assertRaises(cp.CharacterConflict): self.store.save(row, 0)
        row = profile(); row['expression_role_id'] = 'absent'
        with self.assertRaises(cp.CharacterConflict): self.store.save(row, 0)

    def test_changed_graph_reopens_old_draft_and_requires_relink(self):
        saved = self.store.save(profile(), 0)['profile']; self.store.close()
        changed = cp.CharacterProfiles(self.path, catalog=CATALOG, graph=dict(GRAPH, sha256='d' * 64))
        self.addCleanup(changed.close)
        self.assertEqual(changed.snapshot()['readiness'][saved['role_id']]['expression_reference'], 'stale')
        with self.assertRaises(cp.CharacterConflict): changed.save(saved, 1)
        saved['expression_graph_sha256'] = 'd' * 64
        self.assertEqual(changed.save(saved, 1)['profile']['profile_revision'], 2)

    def test_v1_migration_preserves_source_and_does_not_confirm_binding(self):
        result = self.store.migrate(self.legacy, 'old'); row = result['profile']
        self.assertTrue(row['role_id'].startswith('character-'))
        self.assertEqual(row['expression_role_id'], 'old'); self.assertEqual(row['profile_revision'], 1)
        self.assertEqual(row['legacy']['profile'], self.legacy)
        self.assertEqual(row['voice']['provider'], 'unconfigured')
        self.assertNotIn('binding_state', row)
        self.assertEqual(result['state']['readiness'][row['role_id']]['shell_binding'], 'not_configured')
        row['persona'] = '继续编辑'
        self.assertEqual(self.store.save(row, 1)['profile']['legacy']['profile'], self.legacy)

    def test_migration_requires_valid_explicit_expression_and_source(self):
        with self.assertRaises((ValueError, TypeError)): self.store.migrate(self.legacy, None)
        with self.assertRaises(cp.CharacterConflict): self.store.migrate(self.legacy, 'unknown')
        bad = deepcopy(self.legacy); bad['voice_token'] = 'not-accepted'
        with self.assertRaises(ValueError): self.store.migrate(bad, 'round')

    def test_legacy_cannot_be_replaced_after_import(self):
        row = self.store.migrate(self.legacy, 'round')['profile']; del row['legacy']
        with self.assertRaises(ValueError): self.store.save(row, 1)

    def test_import_retry_is_idempotent_and_keeps_later_edits(self):
        first = self.store.migrate(self.legacy, 'round')['profile']
        first['persona'] = '导入后编辑的性格'
        saved = self.store.save(first, 1)['profile']
        self.assertEqual(self.store.migrate(self.legacy, 'round')['profile'], saved)
        self.assertEqual(len(self.store.snapshot()['profiles']), 1)

    def test_library_count_limit_and_real_atomic_failure(self):
        for i in range(cp.MAX_PROFILES): self.store.save(profile('character-' + str(i)), 0)
        with self.assertRaises(ValueError): self.store.save(profile('character-over-limit'), 0)
        from unittest.mock import patch
        before = self.path.read_bytes()
        row = self.store.snapshot()['profiles'][0]; row['display_name'] = '另一名字'
        with patch('protocol.role_activation.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(RoleStorageError): self.store.save(row, 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob('.role-*.tmp')))

    def test_invalid_input_boundaries(self):
        invalid = [('display_name', ''), ('display_name', '  '), ('display_name', 'x' * 81),
            ('persona', 'x' * 2001), ('persona', '\x00'), ('role_id', '../a'),
            ('expression_role_id', []), ('schema', 'v1'), ('profile_revision', True),
            ('profile_revision', -1), ('profile_revision', 2**53), ('expression_graph_sha256', 'f')]
        for key, value in invalid:
            row = profile(); row[key] = value
            with self.subTest(key=key, value=str(value)[:10]), self.assertRaises(ValueError): cp.validate(row)
        for key, value in [('provider', 'unsupported'), ('provider', []), ('voice_id', 'v' * 97),
                           ('voice_id', 'bad\nvoice'), ('speaking_style', 'x' * 501)]:
            row = profile(); row['voice'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): cp.validate(row)
        for extra in ('shell_id', 'magnet_profile_id', 'token', 'binding_state'):
            row = profile(); row[extra] = 'bad'
            with self.assertRaises(ValueError): cp.validate(row)

    def test_revision_consistency_and_upper_bound(self):
        with self.assertRaises(ValueError): self.store.save(profile(), True)
        row = profile(); row['profile_revision'] = 1
        with self.assertRaises(ValueError): self.store.save(row, 0)
        with self.assertRaises(ValueError): self.store.save(profile(), cp.MAX_REVISION)

    def test_single_process_owner_and_closed_store(self):
        with self.assertRaises(RoleStorageError): cp.CharacterProfiles(self.path, catalog=CATALOG, graph=GRAPH)
        self.store.close()
        with self.assertRaises(RoleStorageError): self.store.save(profile(), 0)

    def test_corrupt_store_retained_and_owner_released(self):
        self.store.close(); self.path.write_text('{bad', encoding='utf-8')
        for _ in range(2):
            with self.assertRaises(ValueError): cp.CharacterProfiles(self.path, catalog=CATALOG, graph=GRAPH)
        self.assertEqual(self.path.read_text(), '{bad')

    def test_schema_required_fields_match_runtime(self):
        schema = json.loads((ROOT / 'protocol/deskbot-role-profile-v2.schema.json').read_bytes())
        self.assertEqual(set(schema['required']), cp.FIELDS)
        cp.validate(profile())

    def test_json_schema_resolves_migration_source_locally(self):
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        schemas = [json.loads((ROOT / f'protocol/deskbot-role-profile-v{version}.schema.json').read_bytes()) for version in (1, 2)]
        registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
        validator = Draft202012Validator(schemas[1], registry=registry)
        validator.validate(profile())
        validator.validate(self.store.migrate(self.legacy, 'round')['profile'])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.service = service_module()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='characters-http-'); self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.bridge = type('MemoryBridge', (), {'control': lambda _: initial_snapshot(), 'close': lambda _: None,
                                               'lock': threading.RLock()})()
        self.server = self.service.StudioServer(0, bridge=self.bridge, state_path=folder / 'active.json',
            profiles_path=folder / 'characters.json', shell_root=folder / 'shells', package_output=folder / 'packages')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'; self.opener = build_opener(ProxyHandler({}))
        self.row = profile(); snap = self.server.characters.snapshot()['expression_catalog']
        self.row.update(expression_role_id=snap['roles'][0]['id'], expression_graph_sha256=snap['graph_sha256'], preview_catalog_id=snap['catalog_id'])

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def request(self, action, value=None, headers=None, raw=None):
        if value is not None: raw = json.dumps(value).encode('utf-8')
        if raw is not None and headers is None: headers = {'Content-Type': 'application/json', 'X-DeskBot-Studio': 'characters-v1'}
        try:
            with self.opener.open(Request(self.origin + '/api/characters/' + action, data=raw, headers=headers or {}), timeout=10) as r:
                return r.status, json.load(r)
        except HTTPError as error: return error.code, json.load(error)

    def test_save_read_conflict_and_old_modules_still_work(self):
        code, result = self.request('save', dict(profile=self.row, expected_revision=0)); self.assertEqual(code, 200, result)
        self.assertEqual(self.request('state')[1]['profiles'], [result['profile']])
        code, conflict = self.request('save', dict(profile=self.row, expected_revision=0)); self.assertEqual(code, 409)
        self.assertEqual(conflict['state']['profiles'], [result['profile']])
        for route in ('/api/shells/state', '/api/roles/state'):
            with self.opener.open(self.origin + route) as r: self.assertEqual(r.status, 200)

    def test_bad_request_headers_origin_size_and_duplicate_fields(self):
        payload = dict(profile=self.row, expected_revision=0)
        self.assertEqual(self.request('save', payload, headers={'Content-Type': 'application/json'})[0], 415)
        self.assertEqual(self.request('save', payload, headers={'Content-Type': 'application/json', 'X-DeskBot-Studio': 'characters-v1', 'Origin': 'http://other.test'})[0], 403)
        self.assertEqual(self.request('save', raw=b'{"profile":{},"profile":{}}')[0], 422)
        for _ in range(8): self.assertEqual(self.request('save', raw=b' ' * 65537)[0], 413)
        self.assertEqual(self.request('save', {})[0], 422)

    def test_storage_failure_is_retryable_and_does_not_write(self):
        writer = self.server.characters.writer
        self.server.characters.writer = lambda *_: (_ for _ in ()).throw(OSError('full'))
        self.assertEqual(self.request('save', dict(profile=self.row, expected_revision=0))[0], 503)
        self.assertEqual(self.request('state')[1]['profiles'], [])
        self.server.characters.writer = writer
        self.assertEqual(self.request('save', dict(profile=self.row, expected_revision=0))[0], 200)

    def test_production_migration_saves_explicit_expression(self):
        legacy = json.loads((ROOT / 'protocol/deskbot-role-profile-v1.example.json').read_bytes())
        code, result = self.request('migrate', dict(legacy_profile=legacy, expression_role_id=self.row['expression_role_id']))
        self.assertEqual(code, 200, result); self.assertEqual(result['profile']['legacy']['profile'], legacy)


if __name__ == '__main__': unittest.main()
