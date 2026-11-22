"""Real full-role package, loop boundaries, legacy aliases and native UI ownership."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from protocol import studio_resource_package as p
from protocol.role_activation import load_roles
from protocol.studio_role_package import Library
from tests.test_studio_role_player_e3f9c import RolePlayerTests

ROOT = Path(__file__).resolve().parents[1]


class AirboatPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = Library()
        cls.report, cls.files = cls.library.assemble(cls.library.initial)
        cls.members = cls.library.parser.parse_package_bytes(
            cls.files['assets-8m.bin'][:cls.report['package_bytes']], modern=False)['members']
        cls.graph = json.loads(cls.files['deskbot-roles.json'])

    def test_all_29_resident_without_grok_or_old_face_assets(self):
        self.assertEqual(self.report['resident_expressions'], 29)
        self.assertEqual(self.report['role_ids'], ['airboat-face-v1'])
        self.assertLess(self.report['package_bytes'], 8*1024*1024)
        self.assertGreater(self.report['partition_free_bytes'], 512*1024)
        self.assertNotIn('grok', self.files['deskbot-roles.json'].decode())
        self.assertNotIn('Happy.eaf', self.members)
        self.assertEqual(len(self.files['assets-8m.bin']), 8*1024*1024)

    def test_all_clips_seams_and_mirror(self):
        manifest = json.loads((ROOT/'artifacts/airboat-role-20261121/manifest.json').read_bytes())
        home = set()
        for row in manifest['expressions'].values():
            a,b,c = [row['clips'][phase] for phase in ('entry','loop','exit')]
            self.assertEqual(a['last_rgba_sha256'], b['first_rgba_sha256'])
            self.assertEqual(b['first_rgba_sha256'], b['last_rgba_sha256'])
            self.assertEqual(b['last_rgba_sha256'], c['first_rgba_sha256'])
            home.update((a['first_rgba_sha256'], c['last_rgba_sha256']))
        self.assertEqual(len(home), 1)
        self.assertTrue(all(not clip['mirror'] for expression in self.graph['roles'][0]['expressions'].values()
                            for clip in expression['resources'].values()))

    def test_complete_package_integrity_and_legacy_timing(self):
        expressions = self.graph['roles'][0]['expressions']
        index = json.loads(self.members['index.json'])
        for row in index['emoji_collection']:
            self.assertIn(row['file'], self.members)
            self.assertEqual(row['eaf']['fps'], 20)
            self.assertTrue(row['eaf']['loop'])
        for expression in expressions.values():
            for clip in expression['resources'].values():
                self.assertEqual(p.sha(self.members[clip['resource_id']]), clip['sha256'])

    def test_shared_fonts_icons_and_models_preserved(self):
        index=json.loads(self.members['index.json'])
        names=[index['srmodels'],index['text_font']]
        names += [r['file'] for key in ('emoji_collection','icon_collection') for r in index[key]]
        self.assertTrue(all(n in self.members for n in names))
        for name,raw in self.library.before.items():
            if not name.endswith('.eaf') and name!='index.json':self.assertEqual(self.members[name],raw)

    def test_mirror_covers_extra_expressions(self):
        draft = deepcopy(self.library.initial)
        new = draft['roles'][1]
        extra = self.library.catalog['expressions']['airboat-eat']['resources']
        new['mirror_overrides'][extra['entry']] = True
        with self.assertRaisesRegex(ValueError, '完整角色'):
            self.library.assemble(draft)

    def test_original_role_can_be_packaged_for_return(self):
        draft = deepcopy(self.library.initial)
        draft['selected_role_id'] = 'xiaozhi-e145'
        report, files = self.library.assemble(draft)
        members = self.library.parser.parse_package_bytes(files['assets-8m.bin'][:report['package_bytes']], modern=False)['members']
        self.assertTrue(all(members[n] == raw for n,raw in self.library.before.items()))
        self.assertEqual(report['role_ids'], ['xiaozhi-e145'])

    def test_runtime_and_package_use_same_assets(self):
        catalog, _ = load_roles()
        self.assertEqual(catalog['default_role_id'], 'airboat-face-v1')
        for semantic, expression in catalog['roles'][1]['expressions'].items():
            self.assertEqual(expression['clips']['loop']['sha256'],
                self.graph['roles'][0]['expressions'][semantic]['resources']['loop']['sha256'])


class AirboatNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the production loader/player + the accepted GFX timer harness.
        RolePlayerTests.setUpClass.__func__(cls)
        library = Library()
        report, files = library.assemble(library.initial)
        cls.members = library.parser.parse_package_bytes(files['assets-8m.bin'][:report['package_bytes']], modern=False)['members']
        cls.graph = json.loads(files['deskbot-roles.json'])

    compile = RolePlayerTests.compile if hasattr(RolePlayerTests, 'compile') else None
    run_player = RolePlayerTests.run_player

    def test_all_29_load_and_play(self):
        for semantic, expression in self.graph['roles'][0]['expressions'].items():
            with self.subTest(semantic=semantic):
                rows = self.run_player(semantic+',finish,finish')
                self.assertTrue(all(row['loaded'] for row in rows))
                self.assertFalse(rows[0]['mirror'])
                self.assertEqual(rows[-1]['phase'], 3)
                self.assertEqual(rows[-1]['source'], expression['resources']['loop']['resource_id'])
                self.assertEqual(rows[-1]['json_live_bytes'], 0)

    def test_latest_target_and_menu_listen_sleep_resume(self):
        rows = self.run_player('eat,tick,menu-on,tick,listen-on,menu-off,tick,listen-off,tick,sleep-on,tick,sleep-off,tick,finish,happy,sad,finish,finish')
        self.assertFalse(rows[6]['playing'])
        self.assertTrue(rows[8]['playing'])
        self.assertFalse(rows[10]['playing'])
        self.assertTrue(rows[12]['playing'])
        self.assertEqual(rows[-1]['source'], self.graph['roles'][0]['expressions']['sad']['resources']['entry']['resource_id'])

    def test_old_six_state_package_remains_loadable(self):
        report = json.loads((ROOT/'artifacts/studio-role-package-e3f9b/default-package-report.json').read_bytes())
        raw = (ROOT/'tmp/deskbot-role-packages-e3f9b'/report['draft_sha256']/'assets-8m.bin').read_bytes()
        parser,_ = p.load_legacy_tools()
        old = parser.parse_package_bytes(p.extract_package(raw,len(raw)),modern=False)['members']
        self.assertTrue(self.run_player('neutral,finish', members=old)[-1]['loaded'])

    def test_escaped_role_names_remain_loadable(self):
        for name in ('小团 "开心"', r'A\B', '小团🐸'):
            graph=deepcopy(self.graph);graph['roles'][0]['name']=name
            for escaped in (False,True):
                raw=json.dumps(graph,ensure_ascii=escaped).encode('utf-8')
                self.assertTrue(self.run_player('neutral',members={**self.members,'deskbot-roles.json':raw})[0]['loaded'])


if __name__ == '__main__':
    unittest.main()
