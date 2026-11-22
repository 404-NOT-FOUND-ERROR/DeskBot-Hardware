"""Exercise actual EAF/package bytes, layout aliases, seams, and authoring limits."""
from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from protocol.studio_role_package import Library, PackageStore, GRAPH_FILE
from protocol.studio_resource_package import sha, canonical

ROOT = Path(__file__).resolve().parents[1]


class UnifiedPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = Library()

    def setUp(self):
        self.draft = deepcopy(self.library.initial)

    def assembled(self):
        report, files = self.library.assemble(self.draft)
        package = self.library.parser.parse_package_bytes(files['assets-8m.bin'][:report['package_bytes']], modern=False)['members']
        return report, files, package

    def test_default_two_roles_and_complete_byte_preservation(self):
        report, files, members = self.assembled()
        self.assertEqual((report['added_eaf_files'], report['added_eaf_bytes']), (17, 1244597))
        self.assertLess(report['package_bytes'], 7*1024*1024)
        self.assertTrue(all(members[n] == raw for n, raw in self.library.before.items()))
        self.assertEqual(json.loads(members['index.json']), json.loads(self.library.before['index.json']))
        self.assertEqual(report['package_bytes'], report['base_bytes']+report['added_eaf_bytes']+
                         report['config_bytes']+report['table_growth_bytes'])
        self.assertFalse(report['device_written'])
        self.assertFalse(report['device_compatible'])

    def test_graph_carries_actual_settings_and_layout_digest(self):
        b = self.draft['roles'][0]['bindings']['happy']
        b.update(expression_id='eat.eaf', fps=9, loop=False)
        report, files, members = self.assembled()
        graph = json.loads(members[GRAPH_FILE])
        layout = json.loads(members['deskbot-layout.json'])
        self.assertEqual(graph['layout'], dict(file='deskbot-layout.json', role_id='role-pack',
                                             sha256=sha(members['deskbot-layout.json'])))
        old = next(r for r in graph['roles'] if r['id'] == self.draft['roles'][0]['id'])
        binding = old['expressions']['happy']
        self.assertEqual((binding['fps'], binding['loop'], binding['on_complete']), (9, False, 'hold_last_frame'))
        self.assertEqual(binding['resources']['single']['resource_id'], 'eat.eaf')
        self.assertFalse(binding['resources']['single']['mirror'])
        self.assertEqual(layout['role_id'], graph['package_id'])
        self.assertEqual(report['download_sha256']['deskbot-roles.json'], sha(files['deskbot-roles.json']))

    def test_opposing_layouts_have_distinct_physical_offsets(self):
        self.draft['roles'][0]['mirror_overrides']['winking.eaf'] = False
        report, files, members = self.assembled()
        image = files['assets-8m.bin']
        records = {}
        for i in range(struct.unpack_from('<I', image, 0)[0]):
            at = 12 + i*44
            name = image[at:at+32].split(b'\0')[0].decode()
            records[name] = struct.unpack_from('<II', image, at+32)
        graph = json.loads(members[GRAPH_FILE])
        old = next(r for r in graph['roles'] if r['id'] == self.draft['roles'][0]['id'])
        alias = old['expressions']['neutral']['resources']['single']['resource_id']
        self.assertNotEqual(alias, 'winking.eaf')
        self.assertEqual(members[alias], members['winking.eaf'])
        self.assertNotEqual(records[alias][1], records['winking.eaf'][1])
        self.assertEqual(len(alias), 28)
        self.assertLessEqual(len(alias), 31)

    def test_reusing_composition_changes_graph_without_duplicating_bytes(self):
        self.draft['roles'][1]['bindings']['happy']['expression_id'] = 'grok-sad'
        report, files, members = self.assembled()
        graph = json.loads(members[GRAPH_FILE])
        grok = next(r for r in graph['roles'] if r['id'] == self.draft['roles'][1]['id'])
        self.assertEqual(grok['expressions']['happy'], grok['expressions']['sad'])
        self.assertLess(report['added_eaf_files'], 17)

    def test_shared_clip_has_one_member_for_equal_layout(self):
        report, files, members = self.assembled()
        grok = next(r for r in json.loads(members[GRAPH_FILE])['roles'] if r['id'] == self.draft['roles'][1]['id'])
        refs = [x['resource_id'] for expression in grok['expressions'].values() for x in expression['resources'].values()]
        self.assertEqual((len(refs), len(set(refs))), (18, 17))

    def test_editing_role_does_not_change_package_activation(self):
        before_report, before_files, _ = self.assembled()
        self.draft['selected_role_id'] = self.draft['roles'][1]['id']
        report, files, members = self.assembled()
        self.assertEqual(files['assets-8m.bin'], before_files['assets-8m.bin'])
        self.assertEqual(json.loads(members[GRAPH_FILE])['default_role_id'], 'xiaozhi-e145')

    def test_mirror_must_match_across_phases_and_expression_home(self):
        grok = self.draft['roles'][1]
        items = self.library.catalog['expressions']['grok-happy']['resources']
        grok['mirror_overrides'][items['entry']] = True
        with self.assertRaisesRegex(ValueError, '三段'):
            self.library.validate(self.draft)
        grok['mirror_overrides'] = {name: True for name in items.values()}
        with self.assertRaisesRegex(ValueError, '共享姿态'):
            self.library.validate(self.draft)

    def test_uniform_grok_mirror_passes_and_boundary_hash_mismatch_fails(self):
        grok = self.draft['roles'][1]
        grok['mirror_overrides'] = {name: True for name, asset in self.library.catalog['assets'].items()
                                    if asset['mode'] == 'composable'}
        self.library.validate(self.draft)
        name = next(iter(grok['mirror_overrides']))
        asset = self.library.catalog['assets'][name]
        before = asset['first_rgba_sha256']
        try:
            asset['first_rgba_sha256'] = '0'*64
            with self.assertRaises(ValueError):
                self.library.validate(self.draft)
        finally:
            asset['first_rgba_sha256'] = before

    def test_single_loop_boolean_fps_and_type_limits(self):
        original = deepcopy(self.draft)
        for fps in (0, 61, 1.5, True, None, '20'):
            self.draft = deepcopy(original)
            self.draft['roles'][0]['bindings']['happy']['fps'] = fps
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                self.library.validate(self.draft)
        self.draft = deepcopy(original)
        self.draft['roles'][1]['bindings']['happy']['loop'] = False
        with self.assertRaisesRegex(ValueError, '持续循环'):
            self.library.validate(self.draft)
        for fps in (1, 60):
            self.draft = deepcopy(original)
            self.draft['roles'][0]['bindings']['happy'].update(fps=fps, loop=False)
            self.library.validate(self.draft)

    def test_unknown_duplicate_missing_and_extra_fields(self):
        original = deepcopy(self.draft)
        invalid = []
        value = deepcopy(original); value['roles'][1] = value['roles'][0]; invalid.append(value)
        value = deepcopy(original); value['roles'][0]['bindings']['happy']['expression_id'] = 'grok-happy'; invalid.append(value)
        value = deepcopy(original); del value['roles'][0]['bindings']['happy']; invalid.append(value)
        value = deepcopy(original); value['path'] = '../'; invalid.append(value)
        value = deepcopy(original); value['roles'][0]['name'] = 'x'*41; invalid.append(value)
        value = deepcopy(original); value['roles'][0]['mirror_overrides']['eat.eaf'] = 1; invalid.append(value)
        value = deepcopy(original); value['selected_role_id'] = ['bad']; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.library.validate(value)

    def test_generation_is_deterministic_and_persists_all_downloads(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp', prefix='role-pack-test-') as temp:
            store = PackageStore(self.library, temp)
            report = store.generate(self.draft)
            self.assertEqual(store.generate(self.draft), report)
            key = report['draft_sha256']
            self.assertEqual(key, sha(canonical(self.draft)))
            for name in report['downloads']:
                raw = store.download(key, name)
                self.assertGreater(len(raw), 0)
            for name in report['downloads']:
                file = Path(temp)/key/name
                old = file.read_bytes(); file.write_bytes(old+b'x')
                with self.assertRaises(ValueError):
                    store.download(key, name)
                file.write_bytes(old)
            for key, name in (('../bad', 'assets-8m.bin'), (key, 'checksums.json')):
                with self.assertRaises(ValueError):
                    store.download(key, name)

    def test_atomic_file_commit_can_resume_after_interruption(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp', prefix='role-pack-atomic-') as temp:
            store = PackageStore(self.library, temp)
            with patch('protocol.studio_role_package.os.replace', side_effect=OSError('simulated full disk')):
                with self.assertRaises(OSError):
                    store.generate(self.draft)
            self.assertEqual(list(Path(temp).rglob('*.tmp')), [])
            report = store.generate(self.draft)
            self.assertEqual(len(store.download(report['draft_sha256'], 'assets-8m.bin')), 8388608)

    def test_soft_budget_and_layout_entry_limit(self):
        before = self.library.target['assets']['soft_limit_bytes']
        try:
            self.library.target['assets']['soft_limit_bytes'] = 1
            with self.assertRaisesRegex(ValueError, '7 MiB'):
                self.library.assemble(self.draft)
        finally:
            self.library.target['assets']['soft_limit_bytes'] = before
        layout = deepcopy(self.library.base_layout)
        try:
            sample = layout['assets'][0]
            for i in range(65):
                self.library.base_layout['assets'].append(dict(sample, resource_id=f'variant_{i}.eaf'))
            with self.assertRaisesRegex(ValueError, '64 项'):
                self.library.assemble(self.draft)
        finally:
            self.library.base_layout = layout


if __name__ == '__main__':
    unittest.main()
