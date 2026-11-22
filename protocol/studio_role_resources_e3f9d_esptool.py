"""COM4 resource transport for the reviewed two-role release."""
from pathlib import Path

from protocol import studio_role_release_e3f9d as release
from protocol import studio_resource_installation as resources
from protocol.studio_resource_e3d2b_esptool import inspect_boot
from protocol.storage_upper_experiment_esptool import (
    CAPTURE_SCRIPT,CAPTURE_SCRIPT_SHA256,ESPYTHON,EsptoolDevice,Runner,_MAC)

ROOT=release.ROOT


class ResourceDevice(EsptoolDevice):
    simulation_only=False
    def __init__(self,folder,*,plan=None,reference=None,write_enabled=False,runner=None):
        folder=resources._safe(Path(folder),'.').resolve()
        if (folder.parent!=(ROOT/'tmp').resolve() or not folder.is_dir() or not ESPYTHON.is_file()
                or resources.sha(CAPTURE_SCRIPT.read_bytes())!=CAPTURE_SCRIPT_SHA256):
            raise ValueError('role_resource_toolchain_or_path_changed')
        if plan is not None:
            if not isinstance(plan,(release.ResourcePlan,release.ResourceRecovery)):
                raise ValueError('role_resource_profile_required')
            plan.validate()
        if type(write_enabled) is not bool or write_enabled and (plan is None or plan.metadata['mode']!='live'):
            raise ValueError('role_resource_live_plan_required')
        if plan is None: release.validate_resource_snapshot(reference)
        self.port,self.run_dir,self.python='COM4',folder,ESPYTHON
        self.plan,self.reference,self._armed=plan,reference,write_enabled
        self.runner,self._identity=runner or Runner(),None
        self._reserved_logs=set();self._sequence=self._last_evidence_sequence()

    def _require_plan_identity(self):
        value=self._fresh_identity()
        if value.digest!=release.IDENTITY_SHA: raise ValueError('role_resource_device_changed')
        return value

    def identity(self): return self._require_plan_identity().digest

    def _invoke(self,label,args,*,timeout=180,log_path=None):
        tail=tuple(args)
        if tail in (('flash_id',),('get_security_info',)): pass
        elif tail and tail[0]=='read_flash':
            if (len(tail)!=7 or tail[1:4]!=('--no-progress','--flash_size','keep')
                    or not 0<=int(tail[4],16)<resources.FLASH or not 0<int(tail[5],16)<=0x100000
                    or int(tail[4],16)+int(tail[5],16)>resources.FLASH
                    or resources._safe(Path(tail[6]).parent,Path(tail[6]).name).parent!=self.run_dir/'captures'):
                raise ValueError('role_resource_read_arguments_changed')
        elif tail and tail[0]=='verify_flash':
            if tail[:-1]!=('verify_flash','--flash_mode','keep','--flash_size','keep','--flash_freq','keep','0x0'):
                raise ValueError('role_resource_full_verification_required')
            path=resources._safe(Path(tail[-1]).parent,Path(tail[-1]).name)
            if path.parent!=self.run_dir/'payloads' or path.stat().st_size!=resources.FLASH:
                raise ValueError('role_resource_full_verification_required')
        elif tail and tail[0]=='write_flash':
            if not self._armed or tail[:-1]!=('write_flash','--verify','--no-progress','--flash_mode','keep',
                    '--flash_freq','keep','--flash_size','keep',hex(resources.START)):
                raise ValueError('role_resource_write_arguments_changed')
            saved=release.load_resources(self.run_dir,recovery=True)
            state=resources.Journal(self.run_dir,saved).load()
            if not self.plan or saved.id!=self.plan.id or not state: raise ValueError('role_resource_write_intent_required')
            if state['phase']=='write_intent': expected=release.load_resources(self.run_dir).image
            elif state['phase']=='restore_intent': expected=saved.rollback
            else: raise ValueError('role_resource_write_intent_required')
            path=resources._safe(Path(tail[-1]).parent,Path(tail[-1]).name)
            if path.parent!=self.run_dir/'payloads' or path.read_bytes()!=expected:
                raise ValueError('role_resource_payload_changed')
        else: raise ValueError('role_resource_operation_changed')
        output=EsptoolDevice._invoke(self,label,tail,timeout=timeout,log_path=log_path)
        if tail[0] in ('read_flash','write_flash','verify_flash') and (
                self._identity is None or {x.lower() for x in _MAC.findall(output)}!={self._identity.mac}):
            raise ValueError('role_resource_transfer_identity_changed')
        return output

    def read_full(self):
        self.identity()
        original=self.plan.snapshot if self.plan else self.reference
        prefix=self._capture('role-resource-prefix',0,0x10000)
        raw=prefix+original[0x10000:]
        state=resources.Journal(self.run_dir,self.plan).load() if self.plan else None
        if state:
            image=self._capture('role-resource-readback',resources.START,resources.SIZE)
            raw=raw[:resources.START]+image+raw[resources.START+resources.SIZE:]
        path=self._new_path('payloads','role-full-reference.bin');resources.write_new(path,raw)
        output=self._invoke('verify-role-resource-reference',('verify_flash','--flash_mode','keep',
            '--flash_size','keep','--flash_freq','keep','0x0',str(path)))
        if output.count('-- verify OK (digest matched)')!=1: raise ValueError('role_resource_reference_unconfirmed')
        self.identity();return raw

    def write_assets(self,raw):
        if not self._armed or type(raw) is not bytes or len(raw)!=resources.SIZE:
            raise ValueError('role_resource_exact_payload_required')
        saved=release.load_resources(self.run_dir,recovery=True)
        state=resources.Journal(self.run_dir,saved).load()
        if not self.plan or saved.id!=self.plan.id or not state: raise ValueError('role_resource_write_intent_required')
        expected=release.load_resources(self.run_dir).image if state['phase']=='write_intent' else saved.rollback if state['phase']=='restore_intent' else None
        if raw!=expected: raise ValueError('role_resource_payload_changed')
        path=self._new_path('payloads','role-assets-8m.bin');resources.write_new(path,raw)
        self.identity()
        output=self._invoke('write-role-resources',('write_flash','--verify','--no-progress','--flash_mode','keep',
            '--flash_freq','keep','--flash_size','keep',hex(resources.START),str(path)))
        if 'Hash of data verified' not in output: raise ValueError('role_resource_write_unconfirmed')

    def boot(self):
        log=self._capture_serial('role-resource-boot',45)
        ok=('USB input ready: /dev/secondary' in log and inspect_boot(log,self._identity.mac,expected_elf=release.APP_ELF_SHA)
            and not any(x in log for x in ('Role graph unavailable','Asset layout configuration unavailable',
                'USB input unavailable','USB input configuration unavailable','USB input owner changed')))
        return ok

    def read_flash(self,*args): raise ValueError('use_role_resource_snapshot')
    def write_flash(self,*args): raise ValueError('use_role_resource_transaction')
    def snapshot(self,*args): raise ValueError('use_role_resource_snapshot')
    def capture_probe(self,*args): raise ValueError('use_role_resource_normal_boot')
