import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import sys
sys.path.insert(0,str(Path.home()/'AppData/Local/DeskBot/expression-runtime/site-packages'))
from PIL import Image,ImageDraw
from protocol.expression_pipeline import decode,replace_clips,unpack,repack,ExpressionProjects,ROOT,sha

BASE=ROOT/'artifacts/layered-face-20261124-r8'
RUN=ROOT/'tmp/layered-face-live-20261124-insert'


def sample_gif():
    a=Image.new('RGBA',(80,40),(0,0,0,0));ImageDraw.Draw(a).rectangle((0,0,25,39),fill=(255,40,60,255))
    b=Image.new('RGBA',(80,40),(0,0,0,0));ImageDraw.Draw(b).rectangle((54,0,79,39),fill=(40,80,255,255))
    stream=io.BytesIO();a.save(stream,format='GIF',save_all=True,append_images=[b],duration=[80,160],disposal=2,loop=0)
    return stream.getvalue()


class ExpressionPipelineTests(unittest.TestCase):
    def test_gif_timeline_fit_and_transparency(self):
        frames,info=decode(sample_gif(),'.gif')
        self.assertEqual(info['source_duration_ms'],240);self.assertEqual(len(frames),6)
        self.assertEqual(frames[0],frames[1]);self.assertNotEqual(frames[1],frames[2])
        self.assertEqual(frames[2],frames[-1]);self.assertEqual(frames[0][3],0)
        self.assertGreater(frames[0][(180*360+30)*4],200)
        self.assertTrue(all(all(frame[i+j]<=frame[i+3] for j in range(3))
            for frame in frames for i in range(0,len(frame),4)))
        mirrored,_=decode(sample_gif(),'.gif',mirror=True)
        left=(180*360+30)*4;right=(180*360+330)*4
        self.assertEqual(frames[0][left:left+4],mirrored[0][right:right+4])
        self.assertEqual(frames[0][right:right+4],mirrored[0][left:left+4])

    def test_real_eaf_and_terminal(self):
        from protocol.eaf_encode import encode_rgba_frames
        from protocol.eaf_legacy_terminal import append_official_terminal
        raw=encode_rgba_frames([bytes([255,0,0,255])*16]*2,4,4,block_height=4)
        for data in (raw,append_official_terminal(raw)):
            frames,info=decode(data,'.eaf',20)
            self.assertEqual(info['source_duration_ms'],100);self.assertEqual(len(frames),3)

    def test_unsupported_and_invalid_inputs(self):
        for raw,suffix in ((b'bad','.gif'),(b'bad','.eaf'),(b'bad','.zip')):
            with self.assertRaises((ValueError,OSError)):decode(raw,suffix)
        with self.assertRaises(ValueError):decode(sample_gif(),'.gif',True)

    def test_unedited_clips_and_alias_rules(self):
        raw=(BASE/'face-motion.dfm').read_bytes();frames,_=decode(sample_gif(),'.gif')
        mirrored,_=decode(sample_gif(),'.gif',mirror=True)
        binary=replace_clips(raw,{'sleepy':(mirrored,False)})
        old_s,old,_=unpack(raw);sprites,new,_=unpack(binary)
        self.assertEqual(new['sleepy']['keys'],new['tried']['keys'])
        self.assertEqual(new['sleepy']['loop'],1);self.assertEqual(new['tried']['loop'],0)
        self.assertEqual(new['tried']['mirror'],0)
        import struct
        for name in old:
            if name in ('sleepy','tried'):continue
            self.assertEqual(len(old[name]['keys']),len(new[name]['keys']))
            for a,b in zip(old[name]['keys'],new[name]['keys']):
                for l in range(5):
                    ia=struct.unpack_from('<H',a,l*20)[0];ib=struct.unpack_from('<H',b,l*20)[0]
                    self.assertEqual(a[l*20+2:l*20+20],b[l*20+2:l*20+20])
                    if ia!=65535:self.assertEqual(old_s[ia],sprites[ib])

    def test_real_package_and_production_parser(self):
        frames,_=decode(sample_gif(),'.gif');binary=replace_clips((BASE/'face-motion.dfm').read_bytes(),{'happy':(frames,False)})
        files,report=repack(BASE,binary,'test',3)
        self.assertEqual(len(files['assets-8m.bin']),8388608)
        self.assertTrue(report['unchanged_members_verified'])
        from protocol.expression_pipeline_native import validate
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'face-motion.dfm';p.write_bytes(binary);self.assertTrue(validate(p)['valid'])
            import subprocess
            executable=next((ROOT/'tmp/expression-native').glob('*/face.exe'))
            result=subprocess.run([str(executable),str(p),'replay:happy,t:360,t:40,replay:sad,t:80,cancel,t:360'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['valid'])

    @unittest.skipUnless((RUN/'plan.json').is_file(), 'requires local installed App/resource backup')
    def test_project_revision_build_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ExpressionProjects(directory,BASE,RUN);p=store.create('联调样例')
            p=store.upload(p['id'],1,'fresh.gif',sample_gif());self.assertEqual(p['revision'],2)
            with self.assertRaises(ValueError):store.bind(p['id'],1,'happy',None,False,25)
            p=store.bind(p['id'],2,'happy',p['assets'][0]['id'],False,25)
            store.start_build(p['id'],3)
            until=time.monotonic()+90
            while store.job['status']=='running' and time.monotonic()<until:time.sleep(.05)
            self.assertEqual(store.job['status'],'completed',store.job)
            current=store.load(p['id']);self.assertEqual(current['build']['revision'],3)
            out=store.folder(p['id'])/'build-3';self.assertTrue((out/'deskbot-expression.zip').is_file())
            from protocol.expression_pipeline_install import reviewed
            self.assertEqual(reviewed(current,store.folder(p['id']))[0],out)
            reopened=ExpressionProjects(directory,BASE,RUN)
            self.assertEqual(reopened.state()['projects'][0]['build']['revision'],3)
            # Changing a binding invalidates delivery and prevents stale install.
            p=store.bind(p['id'],3,'happy',p['assets'][0]['id'],True,25)
            self.assertIsNone(p['build'])
            with self.assertRaises(ValueError):store.start_install(p['id'],4,True,None)


if __name__=='__main__':unittest.main()
