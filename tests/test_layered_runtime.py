"""Production mount/player adapter: owner pause, reattach, allocation failure and teardown."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from protocol import studio_resource_package as p

ROOT=Path(__file__).resolve().parents[1]
BOARD=ROOT/'firmware/body/main/boards/esp_vocat'
VENDOR=ROOT/'tests/vendor/studio_catalog'


class LayeredRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out=ROOT/'tmp/layered-face-20261122/runtime-tests';cls.out.mkdir(parents=True,exist_ok=True)
        spec=importlib.util.spec_from_file_location('face_compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        command,kind=helper.find_compiler()
        flags=['-O2','-UNDEBUG','-DMBEDTLS_CONFIG_FILE="catalog_mbedtls_config.h"','-DDESKBOT_LAYERED_FACE=1',
               '-DCONFIG_DESKBOT_ASSET_LAYOUT_ENABLE=1','-DCONFIG_DESKBOT_ASSET_REFLOW_ENABLE=1',
               '-DCONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE=1','-DCONFIG_DESKBOT_ROLE_PLAYER_ENABLE=1',
               '-DCONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE=1']
        for path in (ROOT/'tests/cpp/face_stubs',ROOT/'tests/cpp/layout_stubs',ROOT/'tests/cpp/catalog_stubs',BOARD,
                     ROOT/'firmware/body/main',VENDOR/'cjson',VENDOR/'mbedtls/include',cls.out): flags+=['-I',str(path)]
        import hashlib
        locked=ROOT/'tests/vendor/layered_gfx'
        for name,digest in json.loads((locked/'SOURCES.json').read_bytes())['sha256'].items():
            if hashlib.sha256((locked/name).read_bytes()).hexdigest()!=digest:
                raise AssertionError('layered GFX fixture differs: '+name)
        from protocol.layered_gfx_patch import patch
        source=patch((ROOT/'tests/vendor/layered_gfx/gfx_anim.c').read_bytes()).decode()
        start=source.index('static void gfx_anim_timer_callback(void *arg)\n{')
        end=source.index('\n/**********************',start)
        (cls.out/'gfx_role_timer.inc').write_text(source[start:end])
        def compile(cmd):
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
            if r.returncode:raise RuntimeError(r.stdout+r.stderr)
        objects=[]
        for name,path in [('json',VENDOR/'cjson/cJSON.c'),('sha',VENDOR/'mbedtls/library/sha256.c')]:
            obj=cls.out/(name+'.o');compile(command+(['cc'] if kind=='zig-c++' else ['-x','c'])+flags+['-std=c11','-c',str(path),'-o',str(obj)]);objects.append(str(obj))
        cls.exe=cls.out/'runtime.exe'
        compile(command+(['c++'] if kind=='zig-c++' else [])+flags+['-std=c++17','-Wall','-Wextra','-Werror',
            '-include',str(ROOT/'tests/cpp/catalog_stubs/sdkconfig.h'),
            *[str(BOARD/(name+'.cc')) for name in ('studio_role_graph','studio_asset_layout','studio_asset_layout_manifest','studio_role_player')],
            str(ROOT/'tests/cpp/studio_role_player_test.cc'),*objects,'-o',str(cls.exe)])
        parser,_=p.load_legacy_tools();image=(ROOT/'artifacts/layered-face-20261122/assets-8m.bin').read_bytes()
        cls.members=parser.parse_package_bytes(p.extract_package(image,0x800000),modern=False)['members']
        cls.fixture=cls.out/'fixture';cls.fixture.mkdir(exist_ok=True)
        for name,raw in cls.members.items(): (cls.fixture/name).write_bytes(raw)

    def run_player(self,commands,env=None):
        r=subprocess.run([str(self.exe),str(self.fixture),commands],capture_output=True,text=True,timeout=90,env={**os.environ,**(env or {})})
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        return [json.loads(line) for line in r.stdout.splitlines()]

    def test_mount_activation_and_owned_frame(self):
        rows=self.run_player('role:airboat-face-v1,tick,tick')
        self.assertTrue(rows[-1]['loaded']);self.assertEqual(rows[-1]['active'],0)
        self.assertEqual(rows[-1]['selection'],rows[-1]['rendered_selection'])
        self.assertEqual(rows[-1]['fps'],25)

    def test_pause_resume_each_owner_and_reload(self):
        for reason in ('menu','listen','sleep','overlay'):
            with self.subTest(reason=reason):
                rows=self.run_player('happy,tick,'+reason+'-on,tick,'+reason+'-off,tick,reset,configure,happy,tick')
                self.assertFalse(rows[2]['playing']);self.assertEqual(rows[3]['cursor'],rows[1]['cursor'])
                self.assertTrue(rows[4]['playing']);self.assertGreater(rows[5]['cursor'],rows[4]['cursor'])
                self.assertTrue(rows[-1]['loaded']);self.assertFalse(rows[-1]['failed'])

    def test_repeat_refresh_cancel(self):
        rows=self.run_player('happy,tick,happy,replay:happy,tick,cancel,tick')
        self.assertEqual(rows[1]['cursor'],rows[2]['cursor']);self.assertEqual(rows[3]['cursor'],0)
        self.assertEqual(rows[-1]['phase'],4)

    def test_actual_gfx_dirty_regions_and_full_resume(self):
        rows=self.run_player('idle,tick,tick,menu-on,wait:2000,menu-off,tick,tick')
        self.assertEqual(rows[1]['dirty_pixels'],360*360)
        self.assertLess(rows[2]['dirty_pixels'],360*360//2)
        self.assertGreater(rows[2]['dirty_pixels'],0)
        self.assertEqual(rows[6]['dirty_pixels'],360*360)
        self.assertLess(rows[7]['dirty_pixels'],360*360//2)

    def test_system_fallback_and_actual_patched_timer(self):
        rows=self.run_player('role:airboat-face-v1,tick,system:system_test,owners:speaking,tick')
        self.assertEqual(rows[1]['observations'],1)
        self.assertEqual(rows[2]['result'],0)
        self.assertEqual(rows[2]['source'],'face_idle.eaf')
        self.assertEqual(rows[-1]['source'],'')
        self.assertTrue(rows[-1]['playing'])
        self.assertGreater(rows[-1]['cursor'],rows[1]['cursor'])

    def test_actual_startup_semantic_uses_layered_idle(self):
        rows=self.run_player('microchip_ai,tick')
        self.assertEqual(rows[0]['result'],0)
        self.assertTrue(rows[1]['playing'])
        self.assertEqual(rows[1]['source'],'')

    def test_delayed_frames_preserve_authored_duration(self):
        rows=self.run_player('replay:happy,wait:160,tick,wait:120,tick,wait:1400,tick,wait:320,tick')
        self.assertEqual(rows[2]['cursor'],5)  # 200 ms of the 360 ms entry.
        self.assertEqual(rows[4]['phase'],3)   # Entry completed at real 360 ms.
        self.assertEqual(rows[6]['phase'],4)   # Full 1440 ms action completed.
        self.assertEqual(rows[8]['phase'],3)   # 360 ms exit returns to idle.

    def test_pause_and_new_requests_reset_timing(self):
        for owner in ('menu','listen','sleep','overlay'):
            with self.subTest(owner=owner):
                rows=self.run_player('replay:happy,tick,'+owner+'-on,wait:5000,'+owner+'-off,tick')
                self.assertEqual(rows[-1]['cursor'],2)  # Only two active 40 ms ticks.
        rows=self.run_player('replay:happy,tick,wait:5000,replay:crying,tick,cancel,tick')
        self.assertEqual(rows[4]['cursor'],1)
        self.assertEqual(rows[6]['cursor'],1)
        rows=self.run_player('happy,wait:5000,crying,tick')
        # Both requests begin at Entry/0. A changed semantic is still a new
        # action and must not inherit the previous request's wall time.
        self.assertEqual(rows[3]['cursor'],1)
        self.assertEqual(rows[3]['phase'],2)

    def test_partial_allocations_and_reset(self):
        attempts=self.run_player('happy')[0]['psram_attempts']
        for number in range(1,attempts+1):
            with self.subTest(number=number):
                row=self.run_player('happy',{'ROLE_ALLOC_FAIL':str(number)})[0]
                # The first three attempts belong to the optional legacy layout
                # lookup; its fallback does not stop a valid procedural surface.
                if number>3:self.assertFalse(row['loaded'])

if __name__=='__main__':unittest.main()
