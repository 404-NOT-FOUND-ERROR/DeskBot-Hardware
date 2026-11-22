"""Prepare/install/recover the pinned DeskBot App + full AirBoat role on COM4."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol import airboat_role_deployment as tx
from protocol import studio_app_deployment as app
from protocol.wifi_trial_deployment import pin, ReviewedTrial
from protocol.storage_upper_experiment_esptool import EsptoolDevice, ESPYTHON, Runner, FlashReadTransferError
from protocol.studio_resource_e3d2b_esptool import inspect_boot

PROFILE = Path('tmp/airboat-role-20261121/expression')
BASELINE = Path('tmp/e3c-live-20261121-wifi-network-live2')
PACKAGE = ROOT/'artifacts/airboat-role-20261121/package'


class Device(EsptoolDevice):
    def __init__(self,folder):
        self.port,self.run_dir,self.python='COM4',folder,ESPYTHON
        self.runner,self._identity=Runner(),None
        self._reserved_logs=set();self._sequence=self._last_evidence_sequence()
        self.pair_plan,self.payloads=None,None

    def _require_plan_identity(self):
        identity=self._fresh_identity()
        if identity.digest != tx.IDENTITY:raise ValueError('head_identity_changed')
        return identity

    def _invoke(self,label,args,**kwargs):
        args=tuple(args)
        if args in (('flash_id',),('get_security_info',)):pass
        elif args and args[0]=='read_flash':
            if (len(args)!=7 or args[1:4]!=('--no-progress','--flash_size','keep')
                    or not 0<=int(args[4],16)<app.FLASH or not 0<int(args[5],16)<=0x100000
                    or int(args[4],16)+int(args[5],16)>app.FLASH
                    or Path(args[6]).parent!=self.run_dir/'captures'):
                raise ValueError('read_range_changed')
        elif args and args[0]=='verify_flash':
            if (args[:-1]!=('verify_flash','--flash_mode','keep','--flash_size','keep','--flash_freq','keep','0x0')
                    or Path(args[-1]).parent!=self.run_dir/'payloads'
                    or Path(args[-1]).stat().st_size!=app.FLASH):raise ValueError('full_verification_required')
        elif args and args[0]=='write_flash':
            expected=['write_flash','--verify','--no-progress','--flash_mode','keep','--flash_freq','keep','--flash_size','keep']
            if not self.pair_plan or not (self.run_dir/'state.json').exists():raise ValueError('pair_write_intent_required')
            state=json.loads((self.run_dir/'state.json').read_bytes())
            if state['phase'] not in ('write_intent','restore_intent'):raise ValueError('write_phase_changed')
            for target in self.pair_plan['targets']:
                path=Path(args[len(expected)+1])
                if path.parent!=self.run_dir/'payloads' or path.read_bytes()!=self.payloads[target['file']]:
                    raise ValueError('write_payload_changed')
                expected += [hex(target['offset']),str(path)]
            if tuple(expected)!=args:raise ValueError('pair_write_range_changed')
        else:raise ValueError('unsupported_pair_operation')
        try:return super()._invoke(label,args,**kwargs)
        except OSError:
            # Retry the observed read-only USB packet interruption through the
            # existing three-attempt capture path, including a fresh identity.
            log=self.run_dir/'logs'/f'{self._sequence:03d}-{label}.log'
            if args[0]=='read_flash' and log.is_file() and 'Serial data stream stopped: Possible serial noise or corruption.' in log.read_text('utf-8'):
                raise FlashReadTransferError('read_packet_stream_interrupted')
            raise

    def arm(self,plan,payloads):
        self.pair_plan,self.payloads=plan,payloads

    def read_verified(self,reference,*,targets=False):
        self._require_plan_identity()
        # Fresh prefix includes live NVS. A whole-flash digest proves that the
        # retained reference exactly supplies all remaining backup bytes.
        raw=bytearray(reference)
        raw[:0x10000]=self._capture('live-prefix',0,0x10000)
        if targets:
            for target in self.pair_plan['targets']:
                offset,size=target['offset'],target['bytes']
                raw[offset:offset+size]=self._capture('pair-readback',offset,size)
        raw=bytes(raw)
        path=self._new_path('payloads','full-verification.bin');app.write_new(path,raw)
        output=self._invoke('verify-full-pair',('verify_flash','--flash_mode','keep','--flash_size','keep',
            '--flash_freq','keep','0x0',str(path)))
        if output.count('-- verify OK (digest matched)')!=1:raise ValueError('full_snapshot_unconfirmed')
        return raw

    def write(self,payloads):
        self._require_plan_identity()
        args=['write_flash','--verify','--no-progress','--flash_mode','keep','--flash_freq','keep','--flash_size','keep']
        for target in self.pair_plan['targets']:
            path=self._new_path('payloads',target['file']);app.write_new(path,payloads[target['file']])
            args += [hex(target['offset']),str(path)]
        output=self._invoke('write-pair',args)
        if output.count('Hash of data verified')!=2:raise ValueError('pair_write_unconfirmed')

    def boot(self,elf):
        log=self._capture_serial('pair-boot',45)
        return inspect_boot(log,self._identity.mac,expected_elf=elf) and 'DeskBotBodyWifi: ready' in log and not any(
            error in log for error in ('Role graph unavailable','Asset layout configuration unavailable','USB input unavailable'))


def run(phase,folder):
    folder=folder.resolve()
    if folder.parent!=(ROOT/'tmp').resolve() or folder.is_symlink():raise ValueError('workspace_run_required')
    if phase=='prepare':
        folder.mkdir()
        pins=pin(ROOT,PROFILE,BASELINE,'E3f-9d.7-network-settings')
        pins['original_app_elf_sha256']=json.loads((ROOT/BASELINE/'wifi-pins.json').read_bytes())['app_elf_sha256']
        reviewed=ReviewedTrial(ROOT,pins)
        device=Device(folder)
        try:
            snapshot=device.read_verified(reviewed.load_reference()['snapshot'])
            candidate=reviewed.reviewed_candidate()
            image=(PACKAGE/'assets-8m.bin').read_bytes()
            report=json.loads((PACKAGE/'report.json').read_bytes())
            plan=tx.prepare(snapshot,candidate,image,pins,report)
            payload=candidate+snapshot[app.START+len(candidate):app.START+plan['targets'][0]['bytes']]
            for name,raw in {'snapshot-32m.bin':snapshot,'candidate-app.bin':payload,'candidate-assets.bin':image,
                'plan.json':app.canonical(plan)}.items():app.write_new(folder/name,raw)
            tx.validate(plan,snapshot,{'candidate-app.bin':payload,'candidate-assets.bin':image})
            return dict(result='prepared',targets=plan['targets'],device_written=False)
        finally:
            # Preparation returns the currently installed App to normal use.
            if device._identity:device.boot(json.loads((ROOT/BASELINE/'wifi-pins.json').read_bytes())['app_elf_sha256'])
    device=Device(folder)
    if phase=='install':
        try:return tx.execute(folder,device)
        except BaseException:
            if (folder/'state.json').exists():tx.execute(folder,Device(folder),recover=True)
            raise
    return tx.execute(folder,device,recover=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('prepare','install','recover'))
    parser.add_argument('--run',required=True)
    args=parser.parse_args()
    if not args.run.startswith('airboat-role-live-') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.run):
        parser.error('use an airboat-role-live- run name')
    print(json.dumps(run(args.phase,ROOT/'tmp'/args.run)),flush=True)
