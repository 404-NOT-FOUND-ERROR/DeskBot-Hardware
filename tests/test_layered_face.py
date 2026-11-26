"""Exercise the actual C++ DFM parser/state machine/compositor on the pinned pack."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import unittest

from protocol.layered_face import ROOT,AFM_SHA,sha,package

BOARD=ROOT/'firmware/body/main/boards/esp_vocat'
ASSET=ROOT/os.environ.get('DESKBOT_LAYERED_ASSET','artifacts/layered-face-20261122')


class LayeredFaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('face_compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        command,kind=helper.find_compiler()
        cls.out=ROOT/'tmp'/ASSET.name/'native';cls.out.mkdir(parents=True,exist_ok=True)
        cls.exe=cls.out/'face.exe'
        command+=['c++'] if kind=='zig-c++' else []
        cls.reference=cls.out/'face-reference.exe'
        for exe,flags in ((cls.exe,[]),(cls.reference,['-DDESKBOT_FACE_REFERENCE_MASKS=1'])):
            result=subprocess.run(command+['-O2','-std=c++17','-Wall','-Wextra','-Werror','-UNDEBUG','-I',str(BOARD),
                *flags,str(ROOT/'tests/cpp/layered_face_test.cc'),'-o',str(exe)],capture_output=True,text=True,timeout=180)
            if result.returncode: raise RuntimeError(result.stdout+result.stderr)
        cls.raw=(ASSET/'face-motion.dfm').read_bytes()
        cls.report=json.loads((ASSET/'report.json').read_bytes())

    def run_native(self,commands='suite',data=None,output=None,reference=False):
        path=ASSET/'face-motion.dfm'
        if data is not None:path=self.out/'invalid.dfm';path.write_bytes(data)
        cmd=[str(self.reference if reference else self.exe),str(path),commands]+([str(output)] if output else [])
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        return json.loads(p.stdout)

    def test_production_player(self):
        result=self.run_native()
        self.assertEqual(result['semantics'],29)
        self.assertTrue(result['interrupt_continuity']);self.assertTrue(result['cancel_continuity'])
        (self.out/'report.json').write_text(json.dumps(result,indent=2))

    def test_resource_boundaries(self):
        mutations=[]
        def u32(at,value):
            raw=bytearray(self.raw);struct.pack_into('<I',raw,at,value);mutations.append(raw)
        u32(12,len(self.raw)+1);u32(16,2049);u32(24,30);u32(40,0xffffffff);u32(48,4)
        u32(64+8,len(self.raw));u32(64+12,len(self.raw));u32(64+16,0xffffffff)
        sprite_at=struct.unpack_from('<I',self.raw,20)[0]
        rows=struct.unpack_from('<I',self.raw,sprite_at+8)[0]
        u32(rows+4,0);u32(rows+4,0xffffffff)
        keys=struct.unpack_from('<I',self.raw,36)[0]
        bad=bytearray(self.raw);struct.pack_into('<H',bad,keys,65534);mutations.append(bad)
        bad=bytearray(self.raw);struct.pack_into('<f',bad,keys+4,float('nan'));mutations.append(bad)
        mutations.extend([self.raw[:-1],self.raw[:63],b''])
        for n,raw in enumerate(mutations):
            with self.subTest(n=n): self.assertFalse(self.run_native('validate',raw)['valid'])

    def test_dirty_regions_reconstruct_full_compositor(self):
        result=self.run_native('damage-suite')
        self.assertEqual(result['damage_frames'],29*9)
        self.assertGreater(result['partial_frames'],29)

    def test_mirror_change_interrupt_and_cancel_continuity(self):
        raw=bytearray(self.raw)
        count,at=struct.unpack_from('<II',raw,24)
        for i in range(count):
            name=bytes(raw[at+i*48:at+i*48+32]).split(b'\0')[0]
            if name in (b'happy',b'crying',b'confused'):raw[at+i*48+43]=1
        result=self.run_native('suite',raw)
        self.assertTrue(result['interrupt_continuity']);self.assertTrue(result['cancel_continuity'])
        self.assertEqual(self.run_native('damage-suite',raw)['damage_frames'],29*9)

    def test_source_lock_and_package_shared_dependencies(self):
        from protocol import studio_resource_package as p
        self.assertEqual(sha((ROOT/'assets/layered-face/source/face-motion.afm').read_bytes()),AFM_SHA)
        self.assertEqual(sha(self.raw),self.report['binary_sha256'])
        files,report=package(self.raw,self.report)
        self.assertLess(report['package_bytes'],0x800000)
        parser,_=p.load_legacy_tools()
        members=parser.parse_package_bytes(p.extract_package(files['assets-8m.bin'],0x800000),modern=False)['members']
        index=json.loads(members['index.json'])
        for name in [index['srmodels'],index['text_font']]+[r['file'] for group in ('emoji_collection','icon_collection') for r in index[group]]:
            self.assertIn(name,members)
        self.assertIn('face-motion.dfm',members)

    def test_preview_samples(self):
        for semantic in ('idle','happy','winking','confused','crying','eat'):
            out=self.out/(semantic+'.ppm')
            self.assertTrue(self.run_native('replay:'+semantic+',t:760',output=out)['valid'])
            self.assertEqual(out.stat().st_size,360*360*3+15)

    def test_each_pupil_is_hidden_without_its_own_white(self):
        if not self.report.get('flags',0)&1:
            self.skipTest('legacy pack does not request pupil clipping')
        count,at=struct.unpack_from('<II',self.raw,32)
        missing_white=bytearray(self.raw)
        for i in range(count):
            missing_white[at+i*100:at+i*100+20]=struct.pack('<HBB4f',65535,0,0,0,0,0,0)
        missing_eye=bytearray(missing_white)
        for i in range(count):
            missing_eye[at+i*100+20:at+i*100+40]=struct.pack('<HBB4f',65535,0,0,0,0,0,0)
        for semantic in ('idle','thinking','laughing','tried'):
            commands='replay:'+semantic+',t:760'
            self.assertEqual(self.run_native(commands,missing_white)['hash'],
                             self.run_native(commands,missing_eye)['hash'])
        struct.pack_into('<I',missing_white,48,2)
        self.assertNotEqual(self.run_native('replay:idle,t:760',missing_white)['hash'],
                            self.run_native('replay:idle,t:760',missing_eye)['hash'])

    def test_optimized_scanlines_preserve_reference_pixels(self):
        # Captured from the first independent per-pixel compositor before the
        # row/region optimization: all 28 actions plus interrupt/cancel poses.
        reference=json.loads((ROOT/'tests/fixtures/layered-face-pixels-v1.json').read_bytes())
        for commands,expected in reference.items():
            with self.subTest(commands=commands):
                oracle=self.run_native(commands,reference=True)['hash'] if self.report.get('flags',0) else expected
                self.assertEqual(self.run_native(commands)['hash'],oracle)

    def test_solid_spans_equal_direct_sampling(self):
        count,at=struct.unpack_from('<II',self.raw,24)
        names=[self.raw[at+i*48:at+i*48+32].split(b'\0')[0].decode() for i in range(count)]
        mirrored=bytearray(self.raw)
        for i in range(count):mirrored[at+i*48+43]=1
        for data in (None,mirrored):
            for name in names:
                for ms in (0,60,140,320,400,520,760,1000,1400,1900):
                    commands=f'replay:{name},t:{ms}'
                    with self.subTest(mirrored=data is not None,commands=commands):
                        self.assertEqual(self.run_native(commands,data)['hash'],
                                         self.run_native(commands,data,reference=True)['hash'])
            for commands in ('happy,t:120,crying,t:80,confused,t:40',
                             'winking,t:760,eat,t:180,cancel,t:120',
                             'crying,t:760,confused,t:80,happy,t:40,cancel,t:80'):
                with self.subTest(mirrored=data is not None,commands=commands):
                    self.assertEqual(self.run_native(commands,data)['hash'],
                                     self.run_native(commands,data,reference=True)['hash'])

if __name__=='__main__':unittest.main()
