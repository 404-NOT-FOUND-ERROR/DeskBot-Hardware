"""Install App then two-role resources; recover resources before the App."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from protocol import studio_role_release_e3f9d as reviewed
from protocol import studio_app_deployment as app
from protocol import studio_resource_installation as resources
from protocol.studio_role_resources_e3f9d_esptool import ResourceDevice
from protocol.storage_upper_experiment_esptool import EsptoolDevice,_MAC

spec=importlib.util.spec_from_file_location('role9d_app_transaction',ROOT/'scripts/deploy-studio-eat.py')
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
entry.reviewed=reviewed;entry.base.reviewed=reviewed;entry.base.entry.reviewed=reviewed
entry.base.entry.ROLLBACK_TARGET=reviewed.ROLLBACK_TARGET


class RoleAppDevice(entry.EatingDevice):
    def capture(self): raise ValueError('role_release_uses_normal_boot')
    def capture_probe(self,*args): raise ValueError('role_release_uses_normal_boot')

    def _verify_restore_outside(self,saved):
        # Check original resources and every static byte outside the App before
        # writing its rollback. Current NVS remains owned by the running device.
        end=app.START+len(saved.rollback)
        ranges=((0,saved.snapshot[:0x9000]),(0xD000,saved.snapshot[0xD000:app.START]),(end,saved.snapshot[end:]))
        args=['verify_flash','--flash_mode','keep','--flash_size','keep','--flash_freq','keep']
        for offset,raw in ranges:
            path=self._new_path('payloads',f'restore-outside-{offset:08x}.bin')
            app.write_new(path,raw);args.extend((hex(offset),str(path)))
        output=self._invoke('verify-role-restore-order',args)
        if output.count('-- verify OK (digest matched)')!=len(ranges):
            raise ValueError('restore_resources_before_App')

    def _invoke(self,label,args,*,timeout=180,log_path=None):
        tail=tuple(args)
        if tail in (('flash_id',),('get_security_info',)):pass
        elif tail and tail[0]=='read_flash':
            if (len(tail)!=7 or tail[1:4]!=('--no-progress','--flash_size','keep')
                    or not 0<=int(tail[4],16)<app.FLASH or not 0<int(tail[5],16)<=0x100000
                    or int(tail[4],16)+int(tail[5],16)>app.FLASH
                    or resources._safe(Path(tail[6]).parent,Path(tail[6]).name).parent!=self.run_dir/'captures'):
                raise ValueError('role_App_read_arguments_changed')
        elif tail and tail[0]=='write_flash':
            if tail[:-1]!=('write_flash','--verify','--flash_mode','keep','--flash_size','keep','--flash_freq','keep',hex(app.START)):
                raise ValueError('role_App_write_arguments_changed')
            saved=reviewed.check_plan(app.load_recovery(self.run_dir),candidate=False)
            state=app.Journal(self.run_dir,saved).load()
            if not self.deployment_plan or saved.id!=self.deployment_plan.id or not state:
                raise ValueError('role_App_write_intent_required')
            allowed=[saved.rollback] if state['phase'] in ('restore_required','restoring','needs_recovery') else []
            if state['phase']=='restore_required':allowed.append(reviewed.check_plan(app.load(self.run_dir)).candidate)
            path=resources._safe(Path(tail[-1]).parent,Path(tail[-1]).name)
            payload=path.read_bytes()
            if path.parent!=self.run_dir/'payloads' or payload not in allowed:
                raise ValueError('role_App_payload_changed')
            if payload==saved.rollback:self._verify_restore_outside(saved)
        elif tail and tail[0]=='verify_flash':
            if tail[1:7]!=('--flash_mode','keep','--flash_size','keep','--flash_freq','keep') or len(tail)<9 or (len(tail)-7)%2:
                raise ValueError('role_App_verification_arguments_changed')
            for offset,name in zip(tail[7::2],tail[8::2]):
                path=resources._safe(Path(name).parent,Path(name).name)
                start=int(offset,16)
                if (path.parent!=self.run_dir/'payloads' and path!=self.run_dir/'rollback-app-union.bin'
                        or not path.is_file() or not 0<=start<app.FLASH or start+path.stat().st_size>app.FLASH):
                    raise ValueError('role_App_verification_arguments_changed')
        else:raise ValueError('role_App_operation_changed')
        output=EsptoolDevice._invoke(self,label,tail,timeout=timeout,log_path=log_path)
        if tail[0] in ('read_flash','write_flash','verify_flash') and (
                self._identity is None or {x.lower() for x in _MAC.findall(output)}!={self._identity.mac}):
            raise ValueError('role_App_transfer_identity_changed')
        return output


entry.base.MenuTestDevice=RoleAppDevice


def folder(label):
    if not re.fullmatch(r'e3c-live-[0-9]{8}-e3f9d-[a-z0-9-]{1,24}',label): raise ValueError('role_run_label_changed')
    path=resources._safe(ROOT/'tmp',label).resolve()
    if path.parent!=(ROOT/'tmp').resolve(): raise ValueError('role_run_path_changed')
    return path


def resource_folder(run):
    parent=folder(run)
    return resources._safe(parent.parent,parent.name+'-resources').resolve()


def resource_reference(app_folder):
    plan=reviewed.check_plan(app.load(app_folder))
    state=app.Journal(app_folder,plan).load()
    receipt=json.loads((app_folder/'install-result.json').read_bytes())
    if (not state or state['phase']!='installed' or receipt['result']!='candidate_installed'
            or not receipt['readback']['app_byte_equal'] or not receipt['boot']['firmware_matches_candidate']):
        raise ValueError('installed_role_App_required')
    end=app.START+len(plan.candidate)
    return plan.snapshot[:app.START]+plan.candidate+plan.snapshot[end:]


def prepare_resources(run):
    reviewed.check_build_evidence()
    work=resource_folder(run)
    if work.exists(): raise FileExistsError('resource_run_exists')
    work.mkdir()
    reference=resource_reference(folder(run))
    device=ResourceDevice(work,reference=reference)
    try:
        raw=device.read_full();reviewed.validate_resource_snapshot(raw)
        plan=reviewed.prepare_resources(raw,(ROOT/reviewed.RESOURCE).read_bytes())
        resources.persist(work,plan)
        resources.write_new(work/'preflight.json',resources.canonical(dict(edition=reviewed.EDITION,
            result='live_resource_plan_ready',plan_id=plan.id,app_plan=run,device_written=False)))
        return resources.check(plan)
    except BaseException:
        device.boot();raise


def recover(run):
    work=resource_folder(run)
    if work.exists() and (work/'resource-state.json').exists():
        plan=reviewed.load_resources(work,recovery=True)
        journal=resources.Journal(work,plan)
        if journal.load()['phase']!='restored':
            resources.recover(plan,ResourceDevice(work,plan=plan,write_enabled=True),journal)
    # App recovery tolerates current NVS while requiring the original resources.
    return entry.base.run('recover',folder(run))


def run(phase,label,source=None):
    if phase=='restart':
        plan=reviewed.check_plan(app.load(folder(label)))
        if app.Journal(folder(label),plan).load()['phase']!='installed':
            raise ValueError('installed_role_App_required')
        try:
            boot=RoleAppDevice(folder(label),reviewed.IDENTITY_SHA,plan).boot(True)
            result=dict(result='restart_verified',boot=boot)
            app.write_new(folder(label)/'restart-result.json',app.canonical(result));return result
        except BaseException:
            recover(label);raise
    if phase in ('prepare','check','prepare-live','install','restart'):
        return entry.base.run(phase,folder(label),source=folder(source) if source else None)
    if phase=='resources-prepare':return prepare_resources(label)
    if phase=='recover':return recover(label)
    if phase=='resources-install':
        reviewed.check_build_evidence();work=resource_folder(label)
        plan=reviewed.load_resources(work)
        device=ResourceDevice(work,plan=plan,write_enabled=True)
        result=resources.install(plan,device,resources.Journal(work,plan))
        resources.write_new(work/'install-result.json',resources.canonical(result));return result
    raise ValueError('role_release_phase_changed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('prepare','check','prepare-live','install','restart','resources-prepare','resources-install','recover'))
    parser.add_argument('--run',required=True);parser.add_argument('--from-run')
    args=parser.parse_args()
    if (args.phase=='prepare-live')!=(args.from_run is not None):parser.error('prepare-live needs --from-run')
    print(json.dumps(run(args.phase,args.run,args.from_run)),flush=True)
