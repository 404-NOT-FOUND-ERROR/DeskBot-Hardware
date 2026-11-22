"""Compare JS pixels to the actual locked GFX rendering loops."""
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
NODE=os.environ.get('DESKBOT_NODE') or shutil.which('node') or 'node'

class PixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('pixel_compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        compiler,kind=helper.find_compiler()
        cls.temp=tempfile.TemporaryDirectory(prefix='mirror-pixels-',dir=ROOT/'tmp');cls.addClassCleanup(cls.temp.cleanup)
        cls.folder=Path(cls.temp.name)
        text=(ROOT/'tmp/studio-common-e3f7/p/managed_components/espressif2022__esp_emote_gfx/src/widget/gfx_anim.c').read_text()
        functions=[]
        for bits in (4,8,24):
            start=text.rindex(f'static void gfx_anim_render_{bits}bit_pixels(')
            pos=text.index('{',start);depth=1;end=pos+1
            while depth:
                if text[end]=='{':depth+=1
                if text[end]=='}':depth-=1
                end+=1
            functions.append(text[start:end])
        (cls.folder/'gfx_mirror_pixels.inc').write_text('\n'.join(functions))
        cls.exe=cls.folder/'pixels.exe'
        command=compiler+(['c++'] if kind=='zig-c++' else [])+['-std=c++17','-UNDEBUG','-I',str(cls.folder),
            str(ROOT/'tests/cpp/studio_mirror_pixels_test.cc'),'-o',str(cls.exe)]
        run=subprocess.run(command,capture_output=True,text=True,timeout=120)
        if run.returncode:raise RuntimeError(run.stdout+run.stderr)

    def test_native_renderer_and_workbench_pixels(self):
        script="""const {project}=require('./scripts/deskbot-mirror-render.js');
const rows=JSON.parse(require('fs').readFileSync(0,'utf8'));
console.log(JSON.stringify(rows.map(({width,bits,transparent})=>{
 const h=3,p=new Uint8ClampedArray(width*h*4);
 for(let y=0;y<h;y++)for(let x=0;x<width;x++){
 const v=bits!==24?(transparent&&x%3===0?0:(x+y*7)%(bits===4?15:255)+1):x+y*700,i=(y*width+x)*4;
  p.set([v&255,v>>8,0,bits!==24&&!v?0:255],i);
 }
 const out=project(p,width,h),screen=Array(360*h).fill(0);
 for(let y=0;y<h;y++)for(let x=0;x<340;x++){const i=(y*340+x)*4;screen[y*360+10+x]=out[i]|out[i+1]<<8;}
 return screen;
})));"""
        rows=[dict(width=w,bits=b,transparent=t) for w in (1,125,160,170,171,200,340,360) for b in (4,8,24) for t in (0,1) if b!=4 or w%2==0]
        result=subprocess.run([str(NODE),'-e',script],input=json.dumps(rows),capture_output=True,text=True,cwd=ROOT,check=True,timeout=30)
        expected=json.loads(result.stdout)
        for row,js in zip(rows,expected):
            with self.subTest(**row):
                run=subprocess.run([str(self.exe),str(row['width']),str(row['bits']),str(row['transparent'])],
                                   capture_output=True,text=True,check=True,timeout=10)
                self.assertEqual([int(v) for v in run.stdout.split()],js)

if __name__=='__main__':unittest.main()
