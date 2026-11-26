"""Real GIF/EAF -> DFM1 -> paired DeskBot delivery; no implicit device writes."""
import base64
import io
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import threading
import uuid
import zipfile

from protocol.layered_face import ROOT, SIDE, FPS, row_encode, sha
from protocol import studio_resource_package as resources
from protocol.wifi_trial_deployment import installed_pair

MAX_UPLOAD = 32 * 1024 * 1024
ALIASES = {'neutral': 'idle', 'sleepy': 'tried'}
EMPTY_REF = struct.pack('<HBB4f', 65535, 0, 0, 0, 0, 0, 0)
LABELS = dict(idle='待机', neutral='平静', happy='开心', sad='难过', angry='生气',
    crying='哭泣', loving='喜欢', embarrassed='害羞', sleepy='困倦', tried='疲惫',
    thinking='思考', listening='聆听', insert='连接底座', eat='进食', surprised='惊讶',
    shocked='震惊', question='疑问', look_around='张望', winking='眨眼', beat_swing='摇摆',
    confused='困惑', relaxed='放松', laughing='大笑', funny='逗趣', confident='自信',
    delicious='美味', silly='呆萌', look_left='看左边', look_right='看右边')


def atomic(path, value):
    temp = path.with_suffix('.new')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def decode(raw, suffix, fps=25, mirror=False):
    """Decode bounded inputs; GIF disposal is resolved by Pillow before resizing."""
    from PIL import Image, ImageOps
    if (not 0 < len(raw) <= MAX_UPLOAD or type(fps) is not int or not 1 <= fps <= 60
            or type(mirror) is not bool):
        raise ValueError('素材大小或帧率超出范围')
    images, durations = [], []
    if suffix == '.eaf':
        from protocol.eaf_preview import decode_eaf
        animation = decode_eaf(raw, fps=fps)
        if animation.metadata_frame_count:
            from protocol.eaf_legacy_terminal import append_official_terminal
            # Known exclusive-end metadata is preserved semantically as the
            # clip boundary. Unknown/interior metadata must not be discarded.
            append_official_terminal(raw)
        size = animation.width, animation.height
        sequence = ((Image.frombytes('RGBA', size, frame), 1000 / fps) for frame in animation.frames)
    elif suffix in ('.gif', '.png'):
        source_image = Image.open(io.BytesIO(raw))
        if source_image.format not in ('GIF', 'PNG') or max(source_image.size) > 1024 or source_image.width * source_image.height * getattr(source_image, 'n_frames', 1) > 25_000_000:
            raise ValueError('素材应小于 1024 像素，解码总量不超过 2500 万像素')
        size = source_image.size
        def frames():
            for i in range(getattr(source_image, 'n_frames', 1)):
                source_image.seek(i)
                yield source_image.convert('RGBA'), max(10, source_image.info.get('duration', 40))
        sequence = frames()
    else:
        raise ValueError('请选择 GIF、EAF 或 PNG')
    for image, duration in sequence:
        if len(images) >= 300 or sum(durations) + duration > 12000:
            raise ValueError('单个表演最多 300 帧、12 秒，请缩短素材')
        fitted = ImageOps.contain(image.convert('RGBA').convert('RGBa'), (SIDE, SIDE), Image.Resampling.LANCZOS)
        left,top=(SIDE-fitted.width)//2,(SIDE-fitted.height)//2
        canvas=bytearray(SIDE*SIDE*4);pixels=bytearray(fitted.tobytes())
        # Lanczos can overshoot near translucent edges. The production DFM
        # compositor requires premultiplied RGB <= alpha for every pixel.
        for i in range(0,len(pixels),4):
            a=pixels[i+3]
            pixels[i]=min(pixels[i],a);pixels[i+1]=min(pixels[i+1],a);pixels[i+2]=min(pixels[i+2],a)
        for y in range(fitted.height):
            start=y*fitted.width*4
            target=((top+y)*SIDE+left)*4
            canvas[target:target+fitted.width*4]=pixels[start:start+fitted.width*4]
        fitted_canvas=Image.frombytes('RGBa',(SIDE,SIDE),bytes(canvas))
        if mirror:fitted_canvas=ImageOps.mirror(fitted_canvas)
        images.append(fitted_canvas.tobytes());durations.append(duration)
    if not images:
        raise ValueError('素材没有可播放画面')
    total = sum(durations)
    count = max(2, math.ceil(total * FPS / 1000))
    timeline, index, end = [], 0, durations[0]
    for frame in range(count):
        at = min(total - .001, frame * 1000 / FPS)
        while index + 1 < len(images) and at >= end:
            index += 1; end += durations[index]
        timeline.append(images[index])
    return timeline, dict(source_width=size[0], source_height=size[1], source_frames=len(images),
        source_duration_ms=round(total), canvas=SIDE, fps=FPS, frames=count,
        duration_ms=count*40, source_sha256=sha(raw), frame_rate_source='EAF 手动设置' if suffix=='.eaf' else '素材时长')


def unpack(raw):
    if len(raw) < 64 or raw[:4] != b'DFM1' or struct.unpack_from('<I', raw, 12)[0] != len(raw):
        raise ValueError('基线 DFM 长度不正确')
    ns, sa, nc, ca, nk, ka = struct.unpack_from('<6I', raw, 16)
    sprites = []
    for i in range(ns):
        w, h, channels, rows, at, size, _ = struct.unpack_from('<HHB3xIIII', raw, sa+i*24)
        sprites.append((w, h, channels, raw[rows:at], raw[at:at+size]))
    clips = {}
    for i in range(nc):
        p = ca+i*48
        name = raw[p:p+32].split(b'\0')[0].decode('ascii')
        first, count, body, loop, mirror, r, g, b = struct.unpack_from('<IIHBB3B', raw, p+32)
        clips[name] = dict(keys=[raw[ka+k*100:ka+(k+1)*100] for k in range(first, first+count)],
                           body=body, loop=loop, mirror=mirror, color=(r,g,b))
    return sprites, clips, struct.unpack_from('<I', raw, 48)[0]


def replace_clips(raw, edits):
    sprites, clips, flags = unpack(raw)
    reused = {}
    for semantic, (frames, mirror) in edits.items():
        if semantic not in clips or type(mirror) is not bool:
            raise ValueError('目标表情或镜像选项不正确')
        keys = []
        for pixels in frames:
            digest = sha(pixels)
            if digest not in reused:
                rows, data = row_encode(pixels, SIDE, SIDE, 4)
                if len(sprites)>=65535:raise ValueError('素材帧数过多')
                reused[digest] = len(sprites)
                sprites.append((SIDE, SIDE, 4, rows, data))
            keys.append(EMPTY_REF*4 + struct.pack('<HBB4f', reused[digest], 0, 0, 0, 0, SIDE, SIDE))
        target = ALIASES.get(semantic, semantic)
        for name in clips:
            if ALIASES.get(name, name) == target:
                clips[name].update(keys=keys, body=len(keys), mirror=int(mirror))
    # Compact unreachable old frames/sprites so repeated edits do not accumulate.
    remap, compact, key_table, ranges, seen_keys = {}, [], [], {}, {}
    for name, clip in clips.items():
        adjusted = []
        for key in clip['keys']:
            key = bytearray(key)
            for l in range(5):
                index = struct.unpack_from('<H', key, l*20)[0]
                if index == 65535: continue
                if index not in remap:
                    remap[index] = len(compact); compact.append(sprites[index])
                struct.pack_into('<H', key, l*20, remap[index])
            adjusted.append(bytes(key))
        joined = b''.join(adjusted)
        if joined not in seen_keys:
            seen_keys[joined] = len(key_table); key_table.extend(adjusted)
        ranges[name] = seen_keys[joined], len(adjusted)
    if len(compact)>2048 or len(key_table)>2048:
        raise ValueError('素材超出当前固件的图层/关键帧容量，请减少动画帧数')
    sa, ca = 64, 64+len(compact)*24
    ka = ca+len(clips)*48; pa = ka+len(key_table)*100
    records, payload = bytearray(), bytearray()
    for w,h,ch,rows,data in compact:
        row_at=pa+len(payload);payload.extend(rows);at=pa+len(payload);payload.extend(data)
        records.extend(struct.pack('<HHB3xIIII',w,h,ch,row_at,at,len(data),w*h*ch))
    table=b''.join(struct.pack('<32sIIHBB3Bx',name.encode(),*ranges[name],c['body'],c['loop'],c['mirror'],*c['color']) for name,c in clips.items())
    header=struct.pack('<4sHHHH13I',b'DFM1',1,SIDE,FPS,360,pa+len(payload),len(compact),sa,len(clips),ca,len(key_table),ka,pa,len(payload),flags,0,0,0)
    return header+records+table+b''.join(key_table)+payload


def repack(baseline, binary, project, revision):
    """Use this exact installed package, retaining every shared system member."""
    parser, packer = resources.load_legacy_tools()
    image=(baseline/'assets-8m.bin').read_bytes()
    members=parser.parse_package_bytes(resources.extract_package(image,0x800000),modern=False)['members']
    original=dict(members)
    graph=json.loads(members['deskbot-roles.json'])
    graph['catalog_id']=sha(binary);graph['renderer']['sha256']=sha(binary)
    graph['renderer']['source_project']=project;graph['renderer']['source_revision']=revision
    members['face-motion.dfm']=binary;members['deskbot-roles.json']=resources.canonical(graph)
    data=packer.build_legacy_package(members)
    if len(data)>0x800000: raise ValueError(f'资源包需要 {len(data)/1048576:.2f} MiB，超过 8 MiB，请缩短素材')
    if parser.parse_package_bytes(data,modern=False)['members']!=members: raise ValueError('资源包往返核对失败')
    files={'assets-8m.bin':data+b'\xff'*(0x800000-len(data)), 'face-motion.dfm':binary,
           'deskbot-roles.json':members['deskbot-roles.json'], 'deskbot-layout.json':members['deskbot-layout.json']}
    baseline_report=json.loads((baseline/'report.json').read_bytes())
    sprites,clips,flags=unpack(binary)
    sprite_count,_,semantic_count,_,keyframe_count,_=struct.unpack_from('<6I',binary,16)
    report=dict(baseline_report,binary_sha256=sha(binary),binary_bytes=len(binary),
        sprites=sprite_count,semantics=semantic_count,keyframes=keyframe_count,flags=flags,
        package_bytes=len(data),partition_free_bytes=0x800000-len(data),image_sha256=sha(files['assets-8m.bin']),
        graph_sha256=sha(members['deskbot-roles.json']),download_sha256={name:sha(raw) for name,raw in files.items()},
        source_project=project,source_revision=revision,device_written=False,
        unchanged_members_verified=all(v==members[k] for k,v in original.items() if k not in ('face-motion.dfm','deskbot-roles.json')))
    return files,report


class ExpressionProjects:
    def __init__(self, root, baseline, baseline_run):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.baseline=Path(baseline).resolve();self.baseline_run=Path(baseline_run).resolve()
        self.lock=threading.RLock();self.job=None;self.worker=None;self.closed=False
        self.active=self.root/'active.json'
        if self.active.exists():
            a=json.loads(self.active.read_bytes());self.baseline=Path(a['package']);self.baseline_run=Path(a['run'])
        pending=self.root/'install-pending.json'
        if pending.exists():
            self.job=json.loads(pending.read_bytes())
            self.job.update(status='recovery_required',error='工作台曾在安装期间关闭，请先检查或恢复设备')

    def folder(self, ident):
        if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('项目编号不正确')
        return self.root/ident

    def load(self,ident):
        return json.loads((self.folder(ident)/'project.json').read_bytes())

    def catalog(self):
        _,clips,_=unpack((self.baseline/'face-motion.dfm').read_bytes())
        return [dict(id=n,label=LABELS.get(n,n),loop=bool(c['loop']),mirror=bool(c['mirror']),
                     duration_ms=c['body']*40,alias=ALIASES.get(n),trigger=('底座重新连接' if n=='insert' else '客户端表情播放' if n=='eat' else '语音 / 客户端情绪' if n not in ('idle','neutral','listening','thinking','sleepy') else '系统状态')) for n,c in clips.items()]

    def state(self):
        with self.lock:
            projects=[json.loads(p.read_bytes()) for p in sorted(self.root.glob('*/project.json'),key=lambda p:p.stat().st_mtime,reverse=True)]
            try:
                from PIL import Image
                image_ready=True
            except ImportError:image_ready=False
            return dict(catalog=self.catalog(),projects=projects,job=self.job,canvas=360,partition_bytes=0x800000,
                baseline_run=str(self.baseline_run),
                image_processing_ready=image_ready,
                baseline_graph_sha256=json.loads((self.baseline/'report.json').read_bytes())['graph_sha256'])

    def save(self,p):
        atomic(self.folder(p['id'])/'project.json',p)

    def create(self,name='新表情方案'):
        with self.lock:
            if self.job and self.job['status'] in ('running','recovery_required'):raise ValueError('请先完成当前任务')
            if not isinstance(name,str) or not 1<=len(name.strip())<=60: raise ValueError('方案名称应为 1–60 字')
            ident=uuid.uuid4().hex;self.folder(ident).mkdir()
            p=dict(id=ident,name=name.strip(),revision=1,assets=[],bindings={},build=None,
                   baseline=str(self.baseline),baseline_run=str(self.baseline_run),installation=None)
            self.save(p);return p

    def upload(self,ident,revision,name,raw):
        suffix=Path(name).suffix.lower()
        from PIL import Image
        timeline,info=decode(raw,suffix)
        with self.lock:
            p=self.checked(ident,revision);asset=uuid.uuid4().hex
            folder=self.folder(ident)/'sources';folder.mkdir(exist_ok=True)
            (folder/(asset+'.source'+suffix)).write_bytes(raw)
            # Source GIF preview is derived from the decoded 360px timeline.
            from PIL import Image
            imgs=[Image.frombytes('RGBa',(360,360),v).convert('RGBA') for v in timeline]
            imgs[0].save(folder/(asset+'.gif'),save_all=True,append_images=imgs[1:],duration=40,loop=0,disposal=2)
            p['assets'].append(dict(id=asset,name=Path(name).name[:120],suffix=suffix,**info))
            p['revision']+=1;p['build']=None;self.save(p);return p

    def checked(self,ident,revision):
        p=self.load(ident)
        if type(revision) is not int or revision!=p['revision']:raise ValueError('方案已更新，请刷新后再操作')
        if self.job and self.job['status'] in ('running','recovery_required'):raise ValueError('当前处理尚未完成，请先完成构建或恢复')
        return p

    def bind(self,ident,revision,semantic,asset,mirror,fps):
        with self.lock:
            p=self.checked(ident,revision)
            if semantic not in [c['id'] for c in self.catalog()] or type(mirror) is not bool or type(fps) is not int or not 1<=fps<=60:
                raise ValueError('绑定、镜像或帧率不正确')
            target=ALIASES.get(semantic,semantic)
            if asset is None:p['bindings'].pop(target,None)
            else:
                if asset not in [a['id'] for a in p['assets']]:raise ValueError('请先上传素材')
                p['bindings'][target]=dict(asset=asset,mirror=mirror,fps=fps)
            p['revision']+=1;p['build']=None;self.save(p);return p

    def stage(self,name,status='done',**details):
        with self.lock:
            if self.job is None:return
            self.job['stages'].append(dict(name=name,status=status,**details))
            if self.job.get('run') and (self.root/'install-pending.json').exists():
                atomic(self.root/'install-pending.json',self.job)

    def start_build(self,ident,revision):
        with self.lock:
            p=self.checked(ident,revision)
            try:from PIL import Image
            except ImportError as error:raise ValueError('图像处理依赖尚未就绪，请重启工作台') from error
            if self.job and self.job['status'] in ('running','recovery_required'):raise ValueError('请先完成当前安装恢复')
            if not p['bindings']:raise ValueError('请先把新素材绑定到一个表情')
            if (p['build'] and p['build']['revision']==revision and p['build'].get('status')=='ready'
                    and p['build'].get('files') and all((self.folder(ident)/p['build']['folder']/n).is_file()
                        and sha((self.folder(ident)/p['build']['folder']/n).read_bytes())==v for n,v in p['build']['files'].items())):
                return dict(project=ident,revision=revision,status='completed',stages=[dict(name='当前方案已生成')])
            self.job=dict(project=ident,revision=revision,status='running',stages=[],error=None)
            self.worker=threading.Thread(target=self._build,args=(p,),daemon=True);self.worker.start()
            return self.job

    def start_install(self,ident,revision,confirmed,bridge):
        with self.lock:
            p=self.checked(ident,revision)
            if confirmed is not True or not p['build'] or p['build']['revision']!=revision:
                raise ValueError('请先生成当前方案，再确认安装')
            if self.job and self.job['status'] in ('running','recovery_required'):raise ValueError('请先完成当前安装恢复')
            run=str(ROOT/'tmp'/('layered-face-live-expression-'+uuid.uuid4().hex))
            self.job=dict(project=ident,revision=revision,status='running',stages=[],error=None,run=run,
                previous_package=str(self.baseline),previous_run=str(self.baseline_run))
            atomic(self.root/'install-pending.json',self.job)
            self.worker=threading.Thread(target=self._install,args=(p,bridge),daemon=True);self.worker.start()
            return self.job

    def start_recovery(self,confirmed,bridge):
        with self.lock:
            if confirmed is not True or not self.job or self.job.get('status')!='recovery_required':
                raise ValueError('没有待恢复的安装现场')
            if self.worker and self.worker.is_alive():raise ValueError('恢复任务已在执行')
            pending=json.loads((self.root/'install-pending.json').read_bytes())
            self.job=pending;self.job.update(status='running',stages=list(pending.get('stages',[])),error=None)
            self.worker=threading.Thread(target=self._recover,args=(bridge,),daemon=True);self.worker.start()
            return self.job

    def start_rollback(self,ident,confirmed,bridge):
        with self.lock:
            p=self.load(ident);installation=p.get('installation') or {}
            if (confirmed is not True or installation.get('status')!='installed'
                    or Path(installation.get('run',''))!=self.baseline_run):
                raise ValueError('请选择当前已安装方案再恢复上一版')
            if self.job and self.job['status'] in ('running','recovery_required'):
                raise ValueError('请先完成当前任务')
            self.job=dict(project=ident,revision=p['revision'],status='recovery_required',stages=[],error=None,
                run=installation['run'],previous_package=p['baseline'],previous_run=p['baseline_run'])
            atomic(self.root/'install-pending.json',self.job)
            return self.start_recovery(True,bridge)

    def _install(self,p,bridge):
        try:
            from protocol.expression_pipeline_install import install
            install(self,p,bridge)
            with self.lock:
                self.job['status']='completed';self.worker=None
                (self.root/'install-pending.json').unlink(missing_ok=True)
        except Exception as error:
            with self.lock:
                self.job.update(status='failed',error=str(error));self.worker=None
                # Device recovery and host adoption form one result. A restored
                # Flash alone does not prove the host profile has been restored.
                needs_recovery=self.job.get('recovery_required',self.job.get('device_accessed',False))
                if needs_recovery:
                    self.job['status']='recovery_required';atomic(self.root/'install-pending.json',self.job)
                else:(self.root/'install-pending.json').unlink(missing_ok=True)

    def _recover(self,bridge):
        from protocol.expression_pipeline_install import recover
        try:
            recover(self,bridge)
            with self.lock:
                self.job['status']='completed';self.worker=None
                (self.root/'install-pending.json').unlink(missing_ok=True)
        except Exception as error:
            with self.lock:
                self.job.update(status='recovery_required',error=str(error));self.worker=None
                atomic(self.root/'install-pending.json',self.job)

    def _build(self,p):
        try:
            folder=self.folder(p['id']);baseline=Path(p['baseline']);run=Path(p['baseline_run'])
            snapshot,app,_,graph=installed_pair(run)
            if sha((baseline/'deskbot-roles.json').read_bytes())!=graph:raise ValueError('方案基线与已安装记录不一致，请新建方案')
            self.stage('核对已安装基线',app_sha256=sha(app),graph_sha256=graph)
            edits={}
            for semantic,binding in p['bindings'].items():
                asset=next(a for a in p['assets'] if a['id']==binding['asset'])
                raw=(folder/'sources'/(asset['id']+'.source'+asset['suffix'])).read_bytes()
                if sha(raw)!=asset['source_sha256']:raise ValueError('源素材摘要已变化')
                frames,info=decode(raw,asset['suffix'],binding['fps'],binding['mirror']);edits[semantic]=(frames,False)
                self.stage('解码并适配 '+semantic,**info)
            binary=replace_clips((baseline/'face-motion.dfm').read_bytes(),edits)
            self.stage('编译完整表演',binary_bytes=len(binary),canvas=360)
            files,report=repack(baseline,binary,p['id'],p['revision'])
            self.stage('打包与容量核对',bytes=report['package_bytes'],free_bytes=report['partition_free_bytes'])
            out=folder/('build-'+str(p['revision']))
            if out.exists():out.rename(folder/('incomplete-'+out.name+'-'+uuid.uuid4().hex[:8]))
            out.mkdir()
            for name,raw in files.items():(out/name).write_bytes(raw)
            (out/'xiaozhi.bin').write_bytes(app)
            report['mirror_overrides']={n:b['mirror'] for n,b in p['bindings'].items()}
            atomic(out/'report.json',report)
            # This exact production-parser validation is required before delivery.
            from protocol.expression_pipeline_native import validate
            check=validate(out/'face-motion.dfm')
            self.stage('生产播放器核验',**check)
            manifest=dict(schema='deskbot.expression_delivery/v1',project=p['id'],revision=p['revision'],
                baseline_run=str(run),baseline_package=str(baseline),graph_sha256=report['graph_sha256'],
                app_sha256=sha(app),app_bytes=len(app),resource_sha256=report['image_sha256'],
                edits=p['bindings'],device_written=False)
            atomic(out/'delivery.json',manifest)
            template=(ROOT/'scripts/layered-face-preview.html').read_text('utf-8')
            (out/'index.html').write_text(template.replace('__DFM_BASE64__',base64.b64encode(binary).decode())
                .replace('__BUILD_REPORT__',json.dumps(report,ensure_ascii=False)).replace('__LABELS__',json.dumps(LABELS,ensure_ascii=False)),encoding='utf-8')
            for n,source in (('preview.js','layered-face-preview.js'),('device.js','layered-face-device.js')):
                (out/n).write_bytes((ROOT/'scripts'/source).read_bytes())
            with zipfile.ZipFile(out/'deskbot-expression.zip','w',zipfile.ZIP_DEFLATED) as archive:
                for name in ('xiaozhi.bin','assets-8m.bin','delivery.json','report.json','deskbot-roles.json',
                             'deskbot-layout.json','face-motion.dfm','index.html','preview.js','device.js'):
                    archive.write(out/name,name)
            with self.lock:
                current=self.load(p['id'])
                hashes={f.name:sha(f.read_bytes()) for f in out.iterdir() if f.is_file()}
                current['build']=dict(revision=p['revision'],folder=out.name,report=report,manifest=manifest,files=hashes,
                    native=check,status='ready',installed=False)
                self.save(current);self.stage('配套安装包已生成');self.job['status']='completed';self.worker=None
        except Exception as error:
            with self.lock:self.job.update(status='failed',error=str(error));self.worker=None

    def close(self):
        with self.lock:self.closed=True;worker=self.worker
        if worker:worker.join()
