"""Use the existing verified pair transaction and daily-service handoff."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from protocol.layered_face import ROOT,sha
from protocol import airboat_role_deployment as tx
from protocol import studio_app_deployment as app
from protocol.wifi_trial_deployment import installed_pair


def device_adapter():
    spec=importlib.util.spec_from_file_location('expression_pair_device',ROOT/'scripts/deploy-layered-face.py')
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    return adapter


def host_configuration():
    launcher=ROOT.parents[2]/'DeskBot Online/client/scripts/open-companion.ps1'
    if not launcher.is_file():raise ValueError('找不到已登记的日常后台入口')
    runtime=Path(os.environ['LOCALAPPDATA'])/'DeskBot/companion/runtime.json'
    config=json.loads(runtime.read_bytes());profile=Path(config['expression_wifi_config'])
    return ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(launcher)],profile


def connected_head(bridge,*,recovering=False):
    previous=bridge.snapshot()['connection']
    if not recovering and (previous.get('status') not in ('connected','connecting') or not previous.get('port')):
        raise ValueError('请先在连接页接通头部 USB')
    port='COM4' if recovering else previous['port']
    row=next((r for r in bridge.ports()['ports'] if r['port']==port),None)
    # The existing audited capture helper addresses COM4 explicitly. Do not
    # silently flash that port if the user has selected another USB device.
    if not row or row['port']!='COM4':raise ValueError('请连接当前已登记的 COM4 头部后再安装')
    return row['port'],row['key']


def reviewed(project,folder):
    build=project['build'];out=folder/build['folder']
    if (build.get('folder')!=f"build-{project['revision']}" or out.is_symlink()
            or out.resolve().parent!=Path(folder).resolve()):raise ValueError('安装目录无效，请重新生成')
    for name,digest in build.get('files',{}).items():
        path=out/name
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes())!=digest:
            raise ValueError('安装产物摘要已变化，请重新生成')
    manifest=json.loads((out/'delivery.json').read_bytes())
    if (build['revision']!=project['revision'] or manifest!=build['manifest']
            or manifest['edits']!=project['bindings'] or manifest['baseline_run']!=project['baseline_run']):
        raise ValueError('安装包与当前方案不同，请重新生成')
    snapshot,original,identity,graph=installed_pair(Path(project['baseline_run']))
    candidate=(out/'xiaozhi.bin').read_bytes();image=(out/'assets-8m.bin').read_bytes()
    report=json.loads((out/'report.json').read_bytes())
    if (candidate!=original or sha(candidate)!=manifest['app_sha256'] or len(candidate)!=manifest['app_bytes']
            or sha(image)!=manifest['resource_sha256'] or report!=build['report']
            or report['image_sha256']!=sha(image)
            or sha((out/'deskbot-roles.json').read_bytes())!=manifest['graph_sha256']):
        raise ValueError('安装产物摘要已变化，请重新生成')
    from protocol.expression_pipeline_native import validate
    if sha((out/'face-motion.dfm').read_bytes())!=report['binary_sha256']:raise ValueError('预览与安装资源不同')
    from protocol import studio_resource_package as resources
    parser,_=resources.load_legacy_tools()
    members=parser.parse_package_bytes(resources.extract_package(image,0x800000),modern=False)['members']
    for name in ('face-motion.dfm','deskbot-roles.json','deskbot-layout.json'):
        if members.get(name)!=(out/name).read_bytes():raise ValueError('预览文件与资源包内容不同')
    validate(out/'face-motion.dfm')
    return out,snapshot,original,identity,report,candidate,image


def install(store,project,bridge):
    out,expected,original,identity,report,candidate,image=reviewed(project,store.folder(project['id']))
    # New projects must start from the active installed version; old drafts can
    # still be previewed/downloaded, but never overwrite a newer installed pack.
    if Path(project['baseline_run'])!=store.baseline_run:raise ValueError('设备基线已更新，请新建方案后安装')
    command,profile=host_configuration()
    profile_before=profile.read_bytes();settings=json.loads(profile_before)
    old_graph=json.loads((Path(project['baseline'])/'report.json').read_bytes())['graph_sha256']
    if settings.get('graph_sha256')!=old_graph:raise ValueError('主机表情摘要与当前安装基线不一致')
    active_before=store.active.read_bytes() if store.active.exists() else None
    old_plan=json.loads((store.baseline_run/'plan.json').read_bytes());elf=old_plan['app_elf_sha256']
    adapter=device_adapter()
    run=Path(store.job.get('run') or ROOT/'tmp'/('expression-live-'+uuid.uuid4().hex))
    port,key=connected_head(bridge)
    bridge.operation.acquire()
    stopped=False
    safe_to_restart=True
    host_consistent=True
    try:
        with bridge.lock:bridge.network_maintenance=True
        bridge._disconnect()
        stopped=True  # A timed-out stop may already have stopped the service.
        p=subprocess.run(command+['-Stop'],capture_output=True,text=True,timeout=60)
        if p.returncode:raise ValueError('日常后台尚未完成串口交接')
        store.stage('释放设备连接')
        run.mkdir()
        store.stage('准备可恢复的成对安装',run=str(run))
        device=adapter.Device(run)
        store.job['device_accessed']=True
        store.stage('核对头部身份与当前 Flash')
        safe_to_restart=False
        try:
            snapshot=device.read_verified(expected)
            pins=dict(original_static_sha256=sha(snapshot[:0x9000]+snapshot[0xD000:]),
                candidate_sha256=sha(candidate),candidate_bytes=len(candidate),identity_sha256=identity,
                original_bytes=len(original),app_elf_sha256=elf,original_app_elf_sha256=elf,
                previous_run=store.baseline_run.name,source_commit=None)
            plan=tx.prepare(snapshot,candidate,image,pins,report)
            payload=candidate+snapshot[app.START+len(candidate):app.START+plan['targets'][0]['bytes']]
            for name,raw in {'snapshot-32m.bin':snapshot,'candidate-app.bin':payload,'candidate-assets.bin':image,
                            'plan.json':app.canonical(plan),'pins.json':app.canonical(pins)}.items():app.write_new(run/name,raw)
        finally:
            if device._identity and device._identity.digest==identity:
                safe_to_restart=device.boot(elf)
                if not safe_to_restart:raise ValueError('安装前当前版本启动未确认')
        store.stage('保存设备当前版本',rollback_run=str(run))
        try:
            safe_to_restart=False
            result=tx.execute(run,adapter.Device(run))
            safe_to_restart=result.get('phase')=='installed' and result.get('boot_verified') is True
            if not safe_to_restart:raise ValueError('新版本尚未通过启动核验')
        except BaseException:
            try:
                if (run/'state.json').exists():
                    recovered=tx.execute(run,adapter.Device(run),recover=True)
                    safe_to_restart=recovered.get('phase')=='restored' and recovered.get('boot_verified') is True
                else:
                    # No write intent exists. Reuse the freshly identified
                    # device from preparation rather than an unarmed instance.
                    safe_to_restart=device.boot(elf)
            except BaseException:
                safe_to_restart=False
            raise
        # Preserve local credentials and the USB touch binding. Only the
        # expression graph changes; the paired App remains byte identical.
        from protocol.expression_pipeline import atomic
        host_consistent=False
        try:
            store.stage('设备回读与启动核验',**result)
            # All adoption IO is covered, including creation of its backup.
            backup=run/'host-adoption';backup.mkdir();shutil.copy2(profile,backup/'expression.before.json')
            settings=json.loads(profile_before);settings['graph_sha256']=report['graph_sha256']
            atomic(profile,settings)
            atomic(store.active,dict(package=str(out),run=str(run)))
            store.baseline=out;store.baseline_run=run
            if getattr(store,'on_install',None):store.on_install(out)
            p=store.load(project['id']);p['build']['installed']=True
            p['installation']=dict(status='installed',run=str(run),readback_equal=True,boot_verified=True,physical_acceptance='pending')
            store.save(p);store.stage('采用新角色资源',graph_sha256=report['graph_sha256'])
            host_consistent=True
        except BaseException:
            try:
                recovered=tx.execute(run,adapter.Device(run),recover=True)
                safe_to_restart=recovered.get('phase')=='restored' and recovered.get('boot_verified') is True
                profile.write_bytes(profile_before)
                if active_before is None:store.active.unlink(missing_ok=True)
                else:store.active.write_bytes(active_before)
                store.baseline=Path(project['baseline']);store.baseline_run=Path(project['baseline_run'])
                if getattr(store,'on_install',None):store.on_install(store.baseline)
                p=store.load(project['id']);p['build']['installed']=False
                p['installation']=dict(status='restored',run=str(run),message='本次安装已回退至上一版')
                store.save(p);host_consistent=True
            except BaseException:safe_to_restart=False
            raise
    finally:
        try:
            store.job['recovery_required']=not(safe_to_restart and host_consistent)
            if stopped and safe_to_restart and host_consistent:
                store.job['recovery_required']=True
                p=subprocess.run(command,capture_output=True,text=True,timeout=240)
                if p.returncode:
                    store.job['recovery_required']=True
                    raise ValueError('设备已处理，日常后台尚未启动；可使用恢复入口接续')
                store.stage('恢复小智与共同记忆')
                store.job['recovery_required']=False
            elif stopped:
                raise ValueError('启动与回退均未核验，日常后台保持停止；请根据本次安装目录恢复')
            if safe_to_restart and host_consistent:
                try:bridge._connect(port,key)
                except (OSError,ValueError):store.stage('USB 观察待恢复',status='pending')
        finally:
            with bridge.lock:bridge.network_maintenance=False
            bridge.operation.release()


def recover(store,bridge):
    """Restore the pre-install paired snapshot after an interrupted transaction."""
    job=store.job or {}
    run=Path(job.get('run','')).resolve()
    if run.parent!=(ROOT/'tmp').resolve() or not run.name.startswith('layered-face-live-'):
        raise ValueError('找不到本次安装的可恢复现场')
    pending=store.root/'install-pending.json'
    record=json.loads(pending.read_bytes())
    if record.get('run')!=str(run):raise ValueError('安装现场编号不匹配')
    previous_package=Path(record['previous_package']).resolve()
    previous_run=Path(record['previous_run']).resolve()
    if previous_package!=Path(job['previous_package']).resolve() or previous_run!=Path(job['previous_run']).resolve():
        raise ValueError('回退资源记录不一致')
    command,profile=host_configuration()
    port,key=connected_head(bridge,recovering=True)
    adapter=device_adapter()
    bridge.operation.acquire()
    port=key=None;stopped=False;restored=False
    try:
        port,key=connected_head(bridge,recovering=True)
        with bridge.lock:bridge.network_maintenance=True
        bridge._disconnect()
        result=subprocess.run(command+['-Stop'],capture_output=True,text=True,timeout=60)
        if result.returncode:raise ValueError('日常后台尚未完成串口交接')
        stopped=True;store.stage('释放设备连接')
        if (run/'state.json').is_file():
            # Run in this worker, with the transaction's per-operation bounds.
            # A fixed five-minute subprocess timeout can interrupt full readback.
            state=tx.execute(run,adapter.Device(run),recover=True)
            if state.get('phase')!='restored' or state.get('boot_verified') is not True:
                raise ValueError('旧版本尚未完成启动核验')
        else:
            previous_plan=json.loads((previous_run/'plan.json').read_bytes())
            expected,_,identity,_=installed_pair(previous_run)
            run.mkdir(exist_ok=True)
            device=adapter.Device(run)
            try:
                device.read_verified(expected)
            except BaseException:
                if device._identity and device._identity.digest==identity:
                    device.boot(previous_plan['app_elf_sha256'])
                raise
            if not device._identity or device._identity.digest!=identity:raise ValueError('头部身份未核验')
            # Read-only verification enters the ROM loader too. Return the
            # known head to its retained App before adopting the old host graph.
            if not device.boot(previous_plan['app_elf_sha256']):raise ValueError('设备原版本启动未核验')
            state=dict(phase='unchanged',boot_verified=True)
        restored=True;store.stage('旧版本与资源已恢复',**state)
        old_graph=json.loads((previous_package/'report.json').read_bytes())['graph_sha256']
        settings=json.loads(profile.read_bytes());settings['graph_sha256']=old_graph
        from protocol.expression_pipeline import atomic
        atomic(profile,settings);atomic(store.active,dict(package=str(previous_package),run=str(previous_run)))
        store.baseline=previous_package;store.baseline_run=previous_run
        if getattr(store,'on_install',None):store.on_install(previous_package)
        project=store.load(record['project'])
        if project.get('build'):project['build']['installed']=False
        project['installation']=dict(status='restored',run=str(run),message='已恢复至安装前版本')
        store.save(project)
        companion=subprocess.run(command,capture_output=True,text=True,timeout=240)
        if companion.returncode:raise ValueError('旧版本已恢复，日常后台需要重新打开')
        stopped=False;store.stage('小智与共同记忆已恢复')
        try:bridge._connect(port,key)
        except (OSError,ValueError):store.stage('USB 观察待恢复',status='pending')
    finally:
        if stopped:store.stage('恢复尚未完成，现场已保留',status='pending')
        with bridge.lock:bridge.network_maintenance=False
        bridge.operation.release()
