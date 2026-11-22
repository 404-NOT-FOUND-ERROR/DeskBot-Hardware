"""Real two-role package -> production graph/layout/player -> locked GFX callback."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest

from protocol import studio_resource_package as packages
from tests import test_studio_catalog_native as native

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'firmware/body/main/boards/esp_vocat'
VENDOR = ROOT / 'tests/vendor/studio_catalog'


class RolePlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('role_compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        command,kind=helper.find_compiler()
        cls.temp=tempfile.TemporaryDirectory(prefix='role-player-',dir=ROOT/'tmp');cls.addClassCleanup(cls.temp.cleanup)
        cls.folder=Path(cls.temp.name)
        source=(ROOT/'tmp/studio-layout-manifest-e3f9a/p/managed_components/espressif2022__esp_emote_gfx/src/widget/gfx_anim.c').read_text()
        if getattr(cls,'EXTRA_FLAGS',[]):
            import ast
            text=(ROOT/'scripts/build-studio-role-activation-e3f9d.py').read_text(encoding='utf-8')
            tree=ast.parse(text);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='gfx_patch')
            scope={};exec(compile(ast.Module(body=[fn],type_ignores=[]),'gfx_patch','exec'),scope)
            source=scope['gfx_patch'](source.encode('utf-8')).decode('utf-8')
        start=source.index('static void gfx_anim_timer_callback(void *arg)\n{')
        end=source.index('\n/**********************',start)
        (cls.folder/'gfx_role_timer.inc').write_text(source[start:end])
        cls.compile=native.CatalogNativeTests.compile.__func__.__get__(cls,cls.__class__)
        flags=['-UNDEBUG','-DMBEDTLS_CONFIG_FILE="catalog_mbedtls_config.h"',
               '-DCONFIG_DESKBOT_ASSET_LAYOUT_ENABLE=1','-DCONFIG_DESKBOT_ASSET_REFLOW_ENABLE=1',
               '-DCONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE=1','-DCONFIG_DESKBOT_ROLE_PLAYER_ENABLE=1',*getattr(cls,'EXTRA_FLAGS',[])]
        for path in (ROOT/'tests/cpp/layout_stubs',ROOT/'tests/cpp/catalog_stubs',BOARD,ROOT/'firmware/body/main',
                     VENDOR/'cjson',VENDOR/'mbedtls/include',cls.folder):flags+=['-I',str(path)]
        objects=[]
        for name,path in [('json',VENDOR/'cjson/cJSON.c'),('sha',VENDOR/'mbedtls/library/sha256.c')]:
            out=cls.folder/(name+'.o');cls.compile(command+(['cc'] if kind=='zig-c++' else ['-x','c'])+
                flags+['-std=c11','-c',str(path),'-o',str(out)]);objects.append(str(out))
        cls.exe=cls.folder/'player.exe'
        cls.compile(command+(['c++'] if kind=='zig-c++' else [])+flags+['-std=c++17','-Wall','-Wextra','-Werror',
            '-include',str(ROOT/'tests/cpp/catalog_stubs/sdkconfig.h'),
            *[str(BOARD/(n+'.cc')) for n in ('studio_asset_layout','studio_asset_layout_manifest','studio_role_graph','studio_role_player')],
            str(ROOT/'tests/cpp/studio_role_player_test.cc'),*objects,'-o',str(cls.exe)])
        report=json.loads((ROOT/'artifacts/studio-role-package-e3f9b/default-package-report.json').read_bytes())
        image=(ROOT/'tmp/deskbot-role-packages-e3f9b'/report['draft_sha256']/'assets-8m.bin')
        raw=image.read_bytes()
        if len(raw)!=report['image_bytes'] or hashlib.sha256(raw).hexdigest()!=report['image_sha256']:
            raise AssertionError('E3f-9b fixture image differs from its recorded version')
        parser,_=packages.load_legacy_tools()
        cls.members=parser.parse_package_bytes(packages.extract_package(raw,0x800000),modern=False)['members']
        cls.graph=json.loads(cls.members['deskbot-roles.json'])
        cls.grok=next(r for r in cls.graph['roles'] if r['id'].startswith('grok'))
        cls.original=next(r for r in cls.graph['roles'] if r['id'].startswith('xiaozhi'))

    def run_player(self, commands, members=None, env=None):
        files=self.members if members is None else members
        folder=self.folder/'fixture';folder.mkdir(exist_ok=True)
        for path in folder.iterdir():
            if path.name not in files:path.unlink()
        for name,raw in files.items():
            path=folder/name
            if not path.exists() or path.read_bytes()!=raw:path.write_bytes(raw)
        process=subprocess.run([str(self.exe),str(folder),commands],cwd=ROOT,capture_output=True,text=True,
            timeout=20,env={**os.environ,**(env or {})})
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        return [json.loads(line) for line in process.stdout.splitlines()]

    def changed(self, mutate):
        graph=deepcopy(self.graph);mutate(graph)
        return {**self.members,'deskbot-roles.json':packages.json_bytes(graph)}

    def replaced_clip(self, name, raw, frames=None):
        """Re-sign all graph/layout references so a structural case reaches the loader."""
        files={**self.members,name:bytes(raw)};graph=deepcopy(self.graph)
        digest=hashlib.sha256(raw).hexdigest()
        for role in graph['roles']:
            for expression in role['expressions'].values():
                for clip in expression['resources'].values():
                    if clip['resource_id']==name:
                        clip['sha256']=digest
                        if frames is not None:clip['frames']=frames
        layout=json.loads(files['deskbot-layout.json'])
        for row in layout['assets']:
            if row['resource_id']==name:row['resource_sha256']=digest
        files['deskbot-layout.json']=packages.json_bytes(layout)
        graph['layout']['sha256']=hashlib.sha256(files['deskbot-layout.json']).hexdigest()
        files['deskbot-roles.json']=packages.json_bytes(graph)
        return files

    def test_default_single_and_full_frame_role(self):
        rows=self.run_player('neutral,finish,role:'+self.grok['id']+',finish,finish')
        self.assertEqual([r['phase'] for r in rows],[1,5,2,3,3])
        self.assertTrue(rows[0]['mirror']);self.assertFalse(rows[2]['mirror'])
        self.assertLess(rows[0]['json_peak_bytes'],128*1024)
        self.assertEqual(rows[0]['json_live_bytes'],0)
        self.assertEqual(rows[2]['source'],self.grok['expressions']['neutral']['resources']['entry']['resource_id'])

    def test_latest_target_finishes_entry_exit_and_returns_home(self):
        rows=self.run_player('role:'+self.grok['id']+',happy,sad,finish,happy,confused,finish,finish')
        self.assertEqual([r['phase'] for r in rows],[2,2,2,4,4,4,2,3])
        self.assertEqual(rows[-2]['source'],self.grok['expressions']['confused']['resources']['entry']['resource_id'])

    def test_pause_preserves_physical_cursor_and_loops_from_zero(self):
        rows=self.run_player('role:'+self.grok['id']+',tick,tick,menu-on,tick,listen-on,menu-off,tick,listen-off,tick,finish,finish')
        self.assertEqual(rows[3]['cursor'],2);self.assertEqual(rows[7]['cursor'],2)
        self.assertFalse(rows[6]['playing']);self.assertTrue(rows[8]['playing'])
        self.assertEqual(rows[9]['cursor'],3)
        self.assertEqual(rows[-1]['native_cursor'],0)

    def test_terminal_boundary_pause_recovery(self):
        count=self.grok['expressions']['neutral']['resources']['entry']['frames']
        rows=self.run_player('role:'+self.grok['id']+','+'tick,'*count+'sleep-on,sleep-off,pump')
        self.assertEqual(rows[-3]['cursor'],count)
        self.assertEqual(rows[-1]['phase'],3);self.assertTrue(rows[-1]['playing'])

    def test_sleep_and_overlay_independent(self):
        rows=self.run_player('role:'+self.grok['id']+',tick,sleep-on,overlay-on,sleep-off,tick,overlay-off,tick')
        self.assertFalse(rows[4]['playing']);self.assertEqual(rows[5]['cursor'],1)
        self.assertTrue(rows[6]['playing']);self.assertEqual(rows[7]['cursor'],2)

    def test_completion_queued_across_pause(self):
        rows=self.run_player('role:'+self.grok['id']+',finish-queued,menu-on,pump,menu-off,pump')
        self.assertEqual(rows[1]['queued'],1);self.assertEqual(rows[3]['phase'],2)
        self.assertEqual(rows[-1]['phase'],3)

    def test_reload_discards_old_queued_work(self):
        rows=self.run_player('role:'+self.grok['id']+',finish-queued,reset,configure,happy,pump')
        self.assertFalse(rows[2]['loaded']);self.assertFalse(rows[2]['visible']);self.assertEqual(rows[-1]['phase'],1)
        self.assertEqual(rows[-1]['plays'],2)

    def test_unknown_semantic_legacy_handoff(self):
        rows=self.run_player('role:'+self.grok['id']+',finish-queued,eat,pump,neutral')
        self.assertEqual(rows[2]['result'],2);self.assertEqual(rows[3]['phase'],0)
        self.assertTrue(rows[2]['visible']);self.assertFalse(rows[2]['playing'])
        self.assertEqual(rows[-1]['phase'],2)

    def test_unrelated_callbacks_and_duplicates_do_not_restart(self):
        rows=self.run_player('role:'+self.grok['id']+',neutral,other-event,tick,neutral')
        self.assertEqual([r['plays'] for r in rows],[1]*5);self.assertEqual(rows[-1]['cursor'],1)

    def test_allocation_failures_release_partial_tables(self):
        for number in (1,2,3):
            with self.subTest(number=number):
                rows=self.run_player('neutral',env={'ROLE_ALLOC_FAIL':str(number)})
                self.assertFalse(rows[0]['loaded'])

    def test_parser_allocation_failure_releases_every_node(self):
        total=self.run_player('neutral')[0]['psram_attempts']
        self.assertGreater(total,4)
        for number in range(1,total+1):
            with self.subTest(number=number):
                row=self.run_player('neutral',env={'ROLE_ALLOC_FAIL':str(number)})[0]
                self.assertFalse(row['loaded']);self.assertLessEqual(row['allocations'],1)
                # The independently loaded layout table may remain available;
                # the native harness asserts all allocations free after Reset.
                self.assertEqual(row['json_live_bytes'],0)

    def test_new_request_while_blocked_stops_previous_playback(self):
        # No role sequence exists yet, so menu-on cannot stop the running legacy
        # eye. The first blocked role request must take ownership and stop it.
        rows=self.run_player('legacy,menu-on,role:'+self.grok['id']+',tick,menu-off')
        self.assertTrue(rows[1]['playing']);self.assertEqual(rows[1]['phase'],0)
        self.assertFalse(rows[2]['playing']);self.assertEqual(rows[3]['cursor'],0)
        self.assertTrue(rows[-1]['playing']);self.assertEqual(rows[-1]['phase'],2)

    def test_resource_reload_keeps_actual_owner_blockers(self):
        for reason in ('menu','listen','sleep'):
            with self.subTest(reason=reason):
                rows=self.run_player('neutral,'+reason+'-on,reset,configure,neutral,tick,'+reason+'-off,tick')
                self.assertTrue(rows[3]['loaded']);self.assertNotEqual(rows[3]['blocked'],0)
                self.assertFalse(rows[4]['playing']);self.assertEqual(rows[5]['cursor'],0)
                self.assertTrue(rows[6]['playing']);self.assertEqual(rows[7]['cursor'],1)

    def test_unknown_and_transitioning_owners_resume_when_settled(self):
        for reason in ('unknown','transitioning','invalid','menu','listening'):
            with self.subTest(reason=reason):
                rows=self.run_player('role:'+self.grok['id']+',tick,owners:'+reason+',tick,owners:speaking,tick')
                self.assertFalse(rows[2]['playing']);self.assertEqual(rows[3]['cursor'],1)
                self.assertTrue(rows[4]['playing']);self.assertEqual(rows[5]['cursor'],2)

    def test_owner_synchronization_preserves_sleep_block(self):
        rows=self.run_player('role:'+self.grok['id']+',tick,sleep-on,owners:unknown,owners:ready,tick,sleep-off,tick')
        self.assertEqual(rows[4]['blocked'],4);self.assertFalse(rows[4]['playing'])
        self.assertEqual(rows[5]['cursor'],1);self.assertTrue(rows[6]['playing'])
        self.assertEqual(rows[7]['cursor'],2)

    def test_play_failure_recovery(self):
        for failure in ('src','segment','start'):
            rows=self.run_player('fail:'+failure+',role:'+self.grok['id']+',neutral')
            self.assertEqual(rows[1]['phase'],0);self.assertEqual(rows[-1]['phase'],2)

    def test_original_single_loop_and_fps(self):
        def edit(graph):
            item=graph['roles'][1]['expressions']['happy']
            item.update(fps=60,loop=True,on_complete='repeat')
        rows=self.run_player('happy,finish,finish',self.changed(edit))
        self.assertEqual([r['phase'] for r in rows],[1]*3)
        self.assertEqual([r['fps'] for r in rows],[60]*3)
        self.assertEqual(rows[-1]['native_cursor'],0)

    def test_composite_to_single_finishes_exit(self):
        rows=self.run_player('role:'+self.grok['id']+',finish,role:'+self.original['id']+',finish,finish')
        self.assertEqual([r['phase'] for r in rows],[2,3,3,4,1])
        self.assertTrue(rows[-1]['mirror'])

    def test_absent_graph_uses_legacy_path(self):
        files=dict(self.members);files.pop('deskbot-roles.json')
        self.assertEqual(self.run_player('neutral',files)[0]['result'],2)

    def test_terminal_marker_rejected_with_matching_digests(self):
        item=self.grok['expressions']['happy']['resources']['entry']
        name=item['resource_id'];raw=bytearray(self.members[name]);count=struct.unpack_from('<I',raw,4)[0]
        # A valid full-size image alias passes layout geometry. The role loader
        # must reject it because this GFX version excludes the final record.
        raw[16+(count-1)*8:16+count*8]=raw[16:24]
        struct.pack_into('<I',raw,8,sum(raw[16:]) & 0xffffffff)
        self.assertFalse(self.run_player('neutral',self.replaced_clip(name,raw))[0]['loaded'])

    def test_one_image_clip_is_rendered_before_held_phase(self):
        item=self.original['expressions']['neutral']['resources']['single']
        name=item['resource_id'];raw=self.members[name];count=struct.unpack_from('<I',raw,4)[0]
        storage=16+count*8
        image_size,image_at=struct.unpack_from('<II',raw,16)
        tail_size,tail_at=struct.unpack_from('<II',raw,16+(count-1)*8)
        image=raw[storage+image_at:storage+image_at+image_size]
        tail=raw[storage+tail_at:storage+tail_at+tail_size]
        payload=struct.pack('<IIII',len(image),0,len(tail),len(image))+image+tail
        eaf=b'\x89EAF'+struct.pack('<III',2,sum(payload) & 0xffffffff,len(payload))+payload
        rows=self.run_player('neutral,finish',self.replaced_clip(name,eaf,1))
        self.assertTrue(rows[0]['loaded']);self.assertEqual(rows[-1]['images'],1)
        self.assertEqual(rows[-1]['phase'],5);self.assertEqual(rows[-1]['metadata'],0)

    def test_invalid_graph_fields_and_bounds(self):
        mutations=[lambda g:g.update(schema='unknown'),lambda g:g.update(default_role_id='missing'),
            lambda g:g.update(extra=True),lambda g:g['layout'].update(sha256='0'*64),
            lambda g:g['roles'].append(deepcopy(g['roles'][0])),
            lambda g:g.update(roles=g['roles']*3),lambda g:g['required_capabilities'].append('unknown'),
            lambda g:g['transition'].update(pending='fifo'),
            lambda g:g['roles'][0]['expressions']['happy'].update(fps=0),
            lambda g:g['roles'][0]['expressions']['happy'].update(fps=60.5),
            lambda g:g['roles'][0]['expressions']['happy'].update(loop=False),
            lambda g:g['roles'][0]['expressions'].pop('neutral')]
        for mutation in mutations:
            rows=self.run_player('neutral',self.changed(mutation));self.assertFalse(rows[0]['loaded'])

    def test_resource_digest_layout_frames_and_terminal(self):
        for changes in ({'sha256':'0'*64},{'frames':10},{'mirror':True,'layout_id':'eye_anim'},
                        {'resource_id':'../bad.eaf'},{'tail_hold_ticks':511},{'extra':1}):
            def edit(graph):graph['roles'][0]['expressions']['happy']['resources']['entry'].update(changes)
            self.assertFalse(self.run_player('neutral',self.changed(edit))[0]['loaded'])

    def test_json_trailing_duplicate_nul_depth_size(self):
        raw=self.members['deskbot-roles.json']
        invalid=[raw+b'false',raw+b'\0',b' '*32769,b'[[[[[[[[[[]]]]]]]]]]',
            raw.replace(b'"fps": 20',b'"fps": 20, "fps": 20',1),
            raw.replace(b'role-pack',b'role-pack\\u0000',1)]
        for value in invalid:
            self.assertFalse(self.run_player('neutral',{**self.members,'deskbot-roles.json':value})[0]['loaded'])


if __name__=='__main__':unittest.main()
