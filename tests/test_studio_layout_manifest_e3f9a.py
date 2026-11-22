"""Real generated package -> native manifest owner -> player -> USB catalog."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest

from protocol import studio_mirror_options as mirrors
from protocol import studio_resource_package as packages
from tests import test_studio_asset_layout_native as native

ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.AssetLayoutNativeTests.setUpClass.__func__(cls)
        cls.player_off = cls.player.with_name('player-off.exe')
        cls.player.rename(cls.player_off)
        spec = importlib.util.spec_from_file_location('manifest_compiler', ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        command, kind = helper.find_compiler()
        flags = ['-UNDEBUG', '-DMBEDTLS_CONFIG_FILE="catalog_mbedtls_config.h"',
                 '-DCONFIG_DESKBOT_ASSET_LAYOUT_ENABLE=1', '-DCONFIG_DESKBOT_ASSET_REFLOW_ENABLE=1',
                 '-DCONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE=1', '-I', str(ROOT/'tests/cpp')]
        for path in (native.LAYOUT_STUBS, native.STUBS, native.BOARD,
                     native.VENDOR/'cjson', native.VENDOR/'mbedtls/include'):
            flags += ['-I', str(path)]
        common = command + (['c++'] if kind == 'zig-c++' else []) + flags + [
            '-include', str(native.STUBS/'sdkconfig.h'), '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pthread',
            str(native.BOARD/'studio_asset_layout.cc'), str(native.BOARD/'studio_asset_layout_manifest.cc'),
            str(ROOT/'tests/cpp/studio_reflow_gfx_mock.cc')]
        objects = [str(cls.folder/'json.o'), str(cls.folder/'sha.o')]
        cls.compile(common + [str(ROOT/'tests/cpp/studio_reflow_test.cc'), *objects, '-o', str(cls.player)])
        cls.compile(common + [str(native.BOARD/'studio_catalog_builder.cc'), str(native.BOARD/'studio_catalog_adapter.cc'),
                             str(ROOT/'tests/cpp/studio_catalog_test.cc'), *objects, '-o', str(cls.directory)])
        parser, _ = packages.load_legacy_tools()
        cls.image = (ROOT/'tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin').read_bytes()
        cls.members = parser.parse_package_bytes(packages.extract_package(cls.image, 0x800000), modern=False)['members']
        cls.catalog = packages.load_catalog()

    def fixture(self, members=None):
        # Player cases need mounted members only; build the package once when
        # testing the catalog, whose package digest is part of its contract.
        files=deepcopy(self.members if members is None else members)
        folder=self.folder/self.id().rsplit('.',1)[-1];folder.mkdir(exist_ok=True)
        for old in folder.iterdir():
            if old.name not in files:old.unlink()
        for name,data in files.items():
            path=folder/name
            if not path.exists() or path.read_bytes()!=data:path.write_bytes(data)
        return folder

    run_player = native.AssetLayoutNativeTests.run_player

    def directory_manifest(self,members=None):
        folder=self.fixture(members);_,packer=packages.load_legacy_tools()
        (folder/'package.bin').write_bytes(packer.build_legacy_package(self.members if members is None else members))
        original=self.fixture
        try:
            self.fixture=lambda _=None:folder
            return native.AssetLayoutNativeTests.directory_manifest(self,members)
        finally:self.fixture=original

    def row(self, name, choice, **kwargs):
        parser, _ = packages.load_legacy_tools()
        dimension = parser.parse_eaf_bytes(self.members[name])['dimensions'][0]
        return dict(resource_id=name, resource_sha256=hashlib.sha256(self.members[name]).hexdigest(),
                    **dimension, layout_id='eye_anim' if choice else 'full_frame', mirror=choice, source='manual') | kwargs

    def with_manifest(self, rows=None, **changes):
        members = deepcopy(self.members)
        value = dict(schema='deskbot.asset_layouts/v1', role_id='default', assets=rows or [self.row('winking.eaf', False)]) | changes
        members['deskbot-layout.json'] = packages.json_bytes(value)
        return members

    def test_single_eye_can_be_centered_and_full_face_can_be_mirrored(self):
        members = self.with_manifest([self.row('winking.eaf', False), self.row('eat.eaf', True)])
        rows = self.run_player('neutral,eat,neutral,eat', members)
        self.assertEqual([r['mirror'] for r in rows], [False, True, False, True])
        self.assertEqual([(r['x'],r['y'],r['width'],r['height']) for r in rows],
                         [(117,100,125,160),(10,10,340,360)] * 2)
        self.assertTrue(all(r['first_image_covered'] and r['covered'] and r['result'] == 0 for r in rows))

    def test_omitted_or_absent_resources_keep_legacy_defaults(self):
        for members in (self.members, self.with_manifest()):
            rows = self.run_player('eat,happy', members)
            self.assertEqual([r['mirror'] for r in rows], [False, True])

    def test_catalog_uses_exact_same_layout_for_aliases(self):
        members = self.with_manifest([self.row('Happy.eaf', False), self.row('eat.eaf', True)])
        result = self.directory_manifest(members)
        for semantic in ('happy','laughing','funny','loving'):
            self.assertEqual(next(r for r in result['entries'] if r['id'] == semantic)['layout_id'], 'full_frame')
        self.assertEqual(next(r for r in result['entries'] if r['id']=='eat')['layout_id'],'eye_anim')

    def test_reset_and_reconfigure_release_previous_registration(self):
        for sequence in ('neutral,configure,neutral,configure,neutral',
                         'neutral,reset,configure,neutral'):
            rows=self.run_player(sequence,self.with_manifest())
            self.assertTrue(all(not r['mirror'] and r['allocations']==1 for r in rows))
        for change in ('remove-manifest', 'corrupt-manifest'):
            rows=self.run_player(f'neutral,{change},configure,neutral',self.with_manifest())
            self.assertEqual([r['mirror'] for r in rows],[False,True])
            self.assertEqual(rows[-1]['allocations'],0)

    def test_invalid_row_rejects_whole_manifest(self):
        mutations = [dict(resource_sha256='0'*64),dict(width=159),dict(height=161),dict(mirror='false'),
                     dict(mirror=0),dict(layout_id='eye_anim'),dict(source='guess'),dict(extra=True),
                     dict(resource_id='../winking.eaf'),dict(resource_id='absent.eaf'),dict(width=0),
                     dict(width=361),dict(width=160.5),dict(width=True),dict(resource_sha256='A'*64)]
        for changes in mutations:
            with self.subTest(changes=changes):
                rows = [self.row('Happy.eaf',False),self.row('winking.eaf',False,**changes)]
                result=self.run_player('happy,neutral,eat',self.with_manifest(rows))
                self.assertEqual([r['mirror'] for r in result],[True,True,False])
                self.assertEqual(result[0]['allocations'],0)

    def test_invalid_top_duplicate_and_bounded_json(self):
        valid=packages.json_bytes(dict(schema='deskbot.asset_layouts/v1',role_id='default',assets=[self.row('winking.eaf',False)]))
        invalid = [b' ',b'{',valid+b'false',valid+b'\0',valid.replace(b'"role_id": "default"',b'"role_id": "a", "role_id": "b"'),
                   valid.replace(b'"mirror": false',b'"mirror": false, "mirror": false'),
                   valid.replace(b'default',b'a\\u0000'),valid.replace(b'default',b'X'),
                   valid.replace(b'asset_layouts/v1',b'asset_layouts/v2'),valid+b' ' * 32769,
                   b'[[[[[[]]]]]]',valid.replace(b'"assets": [',b'"assets": [] , "unknown": [')]
        for raw in invalid:
            with self.subTest(raw=raw[:80]):
                members=self.with_manifest();members[mirrors.FILE]=raw
                self.assertTrue(self.run_player('neutral',members)[0]['mirror'])
        row=self.row('winking.eaf',False)
        for rows in ([row,row],[],[row]*65):
            members=self.with_manifest(assets=rows)
            self.assertTrue(self.run_player('neutral',members)[0]['mirror'])

    def test_listen_remains_system_layout(self):
        status=self.row('listen.eaf',False,layout_id='listen_anim',source='automatic')
        members=self.with_manifest([status,self.row('winking.eaf',False)])
        self.assertFalse(self.run_player('neutral',members)[0]['mirror'])
        directory=self.directory_manifest(members)
        self.assertEqual(next(r for r in directory['entries'] if r['id']=='listen')['layout_id'],'listen_anim')
        for change in (dict(mirror=True),dict(source='manual'),dict(layout_id='full_frame')):
            members=self.with_manifest([status|change,self.row('winking.eaf',False)])
            self.assertTrue(self.run_player('neutral',members)[0]['mirror'])

    def test_geometry_mismatch_reverts_directory_and_player_together(self):
        members=self.with_manifest(); index=json.loads(members['index.json'])
        next(r for r in index['layout'] if r['name']=='eye_anim')['x']=12
        members['index.json']=packages.json_bytes(index)
        self.assertTrue(self.run_player('neutral',members)[0]['mirror'])
        self.assertEqual(next(r for r in self.directory_manifest(members)['entries'] if r['id']=='neutral')['layout_id'],'eye_anim')

    def test_allocation_and_json_failures_release_everything(self):
        members=self.with_manifest()
        for env in ({'LAYOUT_ALLOC_FAIL':'1'}, *({'LAYOUT_JSON_FAIL':str(i)} for i in (1,2,3,5,10,30,100,200))):
            result=subprocess.run([str(self.player),str(self.fixture(members)),'neutral'],capture_output=True,text=True,
                                  timeout=30,env=os.environ|env)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            row=json.loads(result.stdout)
            if 'LAYOUT_ALLOC_FAIL' in env: self.assertTrue(row['mirror']);self.assertEqual(row['allocations'],0)

    def test_failed_play_restores_layout_and_pending_reflow(self):
        members=self.with_manifest([self.row('winking.eaf',False),self.row('eat.eaf',True)])
        for fault in ('mirror','position','align','play','getpos','object','lock'):
            rows=self.run_player(f'neutral,fail:{fault},eat,eat,neutral',members)
            self.assertNotEqual(rows[1]['result'],0)
            self.assertFalse(rows[1]['mirror'])
            self.assertTrue(rows[2]['covered'])
            self.assertFalse(rows[3]['mirror'])

    def test_off_configuration_ignores_new_manifest(self):
        original=self.player;self.player=self.player_off
        try: self.assertTrue(self.run_player('neutral',self.with_manifest())[0]['mirror'])
        finally:self.player=original

    def test_maximum_entries_and_parser_peak(self):
        row=self.row('winking.eaf',False)
        rows=[row|{'resource_id':f'r{i}.eaf'} for i in range(64)]
        members=self.with_manifest(rows)
        for item in rows:members[item['resource_id']]=members['winking.eaf']
        result=self.run_player('neutral,configure,neutral',members)
        self.assertTrue(all(r['allocations']==1 for r in result))
        self.assertLess(result[-1]['json_peak_bytes'],65536)

    def test_changed_later_frame_rejects_dimensions_even_with_matching_digest(self):
        members=self.with_manifest();raw=bytearray(members['winking.eaf'])
        count=int.from_bytes(raw[4:8],'little');offset=int.from_bytes(raw[28:32],'little')
        raw[16+count*8+offset+12:16+count*8+offset+14]=(124).to_bytes(2,'little')
        members['winking.eaf']=bytes(raw)
        value=json.loads(members[mirrors.FILE]);value['assets'][0]['resource_sha256']=packages.sha(bytes(raw))
        members[mirrors.FILE]=packages.json_bytes(value)
        self.assertTrue(self.run_player('neutral',members)[0]['mirror'])

    def test_full_generated_v2_manifest_matches_registered_real_bytes(self):
        draft=deepcopy(self.catalog['initial']);draft['schema']=mirrors.SCHEMA
        for role in draft['roles']:role['mirror_overrides']={}
        role=next(r for r in draft['roles'] if r['id']==draft['selected_role_id'])
        role['bindings']['embarrassed']='shy_candidate.eaf'
        role['mirror_overrides']={'winking.eaf':False,'eat.eaf':True}
        report,_,image=packages.assemble(draft,self.catalog,packages.load_target(),packages.BASE_IMAGE.read_bytes(),
                                       {'shy_candidate.eaf':self.members['shy_candidate.eaf']})
        parser,_=packages.load_legacy_tools()
        members=parser.parse_package_bytes(packages.extract_package(image,0x800000),modern=False)['members']
        self.assertEqual(members['shy_candidate.eaf'],self.members['shy_candidate.eaf'])
        self.assertEqual(report['layout_manifest']['value'],json.loads(members[mirrors.FILE]))
        rows=self.run_player('neutral,eat,embarrassed,happy,neutral',members)
        self.assertEqual([r['mirror'] for r in rows],[False,True,True,True,False])
        self.assertTrue(all(r['first_image_covered'] and r['last_image_covered'] for r in rows))
        entries={r['id']:r for r in self.directory_manifest(members)['entries']}
        self.assertEqual((entries['neutral']['layout_id'],entries['eat']['layout_id']),('full_frame','eye_anim'))


if __name__=='__main__':unittest.main()
