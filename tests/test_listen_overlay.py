import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ListenOverlayTests(unittest.TestCase):
    def test_actual_gfx_draw_with_all_real_listen_frames(self):
        from protocol.layered_gfx_patch import patch
        from protocol.eaf_preview import _rle_decode
        from protocol.eaf_compression import huffman_decode
        folder=ROOT/'tmp/layered-face-20261124-r8/listen-gfx';folder.mkdir(parents=True,exist_ok=True)
        source=patch((ROOT/'tests/vendor/layered_gfx/gfx_anim.c').read_bytes()).decode()
        start=source.index('static void gfx_draw_animation(gfx_obj_t *obj, int x1, int y1, int x2, int y2, const void *dest_buf, bool swap)\n{')
        end=source.index('\n/**',start)
        (folder/'listen_gfx_draw.inc').write_text(source[start:end])
        raw=(ROOT/'firmware/body/main/boards/esp_vocat/assets/emoji_large/listen.eaf').read_bytes()
        count=struct.unpack_from('<I',raw,4)[0];base=16+count*8;frames=[]
        for i in range(count):
            size,offset=struct.unpack_from('<II',raw,16+i*8);frame=raw[base+offset:base+offset+size]
            if frame[:4]==b'ZZ_C':continue
            self.assertEqual(frame[:4],b'ZZ_S');content=frame[2:]
            depth=content[9];w,h,blocks,bh=struct.unpack_from('<HHHH',content,10)
            lengths=struct.unpack_from('<'+'I'*blocks,content,18)
            palette_at=18+blocks*4;end=palette_at+(1<<depth)*4
            parts=[struct.pack('<5I',w,h,depth,bh,blocks),content[palette_at:end]]
            for b,size in enumerate(lengths):
                divisor=2 if depth==4 else 1
                data=content[end:end+size];end+=size;expected=w*bh//divisor;valid=w*min(bh,h-b*bh)//divisor
                if data[0]==0: pixels=_rle_decode(data[1:],expected,'listen',valid)
                elif data[0]==1: pixels=_rle_decode(huffman_decode(data[1:],expected*2),expected,'listen',valid)
                elif data[0]==3:pixels=huffman_decode(data[1:],expected)
                else:self.fail('unexpected listen codec')
                pixels=pixels.ljust(expected,b'\0');parts.extend((struct.pack('<I',len(pixels)),pixels))
            frames.append(b''.join(parts))
        fixture=folder/'listen-blocks.bin';fixture.write_bytes(struct.pack('<I',len(frames))+b''.join(frames))
        spec=importlib.util.spec_from_file_location('face_compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper);command,kind=helper.find_compiler()
        command+=['c++'] if kind=='zig-c++' else []
        exe=folder/'listen-gfx.exe'
        build=subprocess.run(command+['-std=c++17','-O2','-UNDEBUG','-Wall','-Wextra','-Werror','-I',str(folder),
            '-I',str(ROOT/'firmware/body/main/boards/esp_vocat'),str(ROOT/'tests/cpp/listen_gfx_test.cc'),'-o',str(exe)],
            capture_output=True,text=True,timeout=180)
        self.assertEqual(build.returncode,0,build.stdout+build.stderr)
        run=subprocess.run([str(exe),str(fixture)],capture_output=True,text=True,timeout=120)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        result=json.loads(run.stdout);self.assertEqual(result['frames'],50);self.assertGreater(result['pixel_checks'],1000000)
        (ROOT/'artifacts/layered-face-20261124-r8/listen-gfx-tests.json').write_text(json.dumps(result,indent=2)+'\n')

    def test_rgb565_blend_is_bounded_and_monotone(self):
        spec = importlib.util.spec_from_file_location('face_compiler', ROOT / 'scripts/test-expression-e1-26-cpp.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        command, kind = helper.find_compiler()
        output = ROOT / 'tmp/layered-face-20261124-r8/listen-overlay-test.exe'
        output.parent.mkdir(parents=True, exist_ok=True)
        command += ['c++'] if kind == 'zig-c++' else []
        build = subprocess.run(command + ['-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-UNDEBUG',
            '-I', str(ROOT / 'firmware/body/main/boards/esp_vocat'),
            str(ROOT / 'tests/cpp/listen_overlay_rgb565_test.cc'), '-o', str(output)],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        run = subprocess.run([str(output)], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
