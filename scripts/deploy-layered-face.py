"""Pair the layered App/resources with a fresh verified current-version backup."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from protocol import airboat_role_deployment as tx
from protocol import studio_app_deployment as app
from protocol.layered_face import COMMIT

spec=importlib.util.spec_from_file_location('paired_role_device',ROOT/'scripts/deploy-airboat-role.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
PROFILE=ROOT/'tmp/layered-face-20261122/expression'
PACKAGE=ROOT/'artifacts/layered-face-20261122'
PREVIOUS=ROOT/'tmp/airboat-role-live-20261121-v3'


class Device(base.Device):
    def boot(self,elf):
        log=self._capture_serial('pair-boot',45)
        return base.inspect_boot(log,self._identity.mac,expected_elf=elf) and 'DeskBotBodyWifi: ready' in log and not any(
            error in log for error in ('Role graph unavailable','Asset layout configuration unavailable','USB input unavailable'))


def reference(previous=PREVIOUS):
    previous=Path(previous).resolve()
    if previous.parent!=(ROOT/'tmp').resolve() or previous.is_symlink():raise ValueError('workspace_previous_required')
    plan=json.loads((previous/'plan.json').read_bytes())
    state=json.loads((previous/'state.json').read_bytes())
    # A verified in-progress revision is a byte reference, not user acceptance.
    # Each update retains its own pair; the accepted EAF recovery remains saved.
    if state['phase']!='installed' or not all(state.get(k) is True for k in ('readback_equal','outside_targets_equal','nvs_equal','boot_verified')):
        raise ValueError('verified_previous_version_required')
    snapshot=(previous/'snapshot-32m.bin').read_bytes()
    payloads={t['file']:(previous/t['file']).read_bytes() for t in plan['targets']}
    tx.validate(plan,snapshot,payloads)
    return tx.patched(plan,snapshot,payloads),plan['app_elf_sha256'],plan['targets'][0]['bytes']


def prepare(folder,previous=PREVIOUS):
    folder.mkdir()
    expected,old_elf,old_bytes=reference(previous)
    device=Device(folder)
    try:
        snapshot=device.read_verified(expected)
        candidate=(PROFILE/'build/xiaozhi.bin').read_bytes()
        report=json.loads((PROFILE/'build/report.json').read_bytes())
        resources=json.loads((PACKAGE/'report.json').read_bytes())
        if (report.get('layered_face') is not True or report['app']['sha256']!=app.sha(candidate)
                or report['effective_config_equal'] is not True or not all(report['protected_equal'].values())):
            raise ValueError('reviewed_layered_build_required')
        pins=dict(original_static_sha256=app.sha(snapshot[:0x9000]+snapshot[0xD000:]),candidate_sha256=app.sha(candidate),
            candidate_bytes=len(candidate),identity_sha256=tx.IDENTITY,original_bytes=old_bytes,
            app_elf_sha256=report['app_elf_sha256'],original_app_elf_sha256=old_elf,source_commit=COMMIT,
            previous_run=Path(previous).name,accepted_fallback_run=PREVIOUS.name)
        image=(PACKAGE/'assets-8m.bin').read_bytes()
        plan=tx.prepare(snapshot,candidate,image,pins,resources)
        payload=candidate+snapshot[app.START+len(candidate):app.START+plan['targets'][0]['bytes']]
        values={'snapshot-32m.bin':snapshot,'candidate-app.bin':payload,'candidate-assets.bin':image,
                'plan.json':app.canonical(plan),'pins.json':app.canonical(pins)}
        for name,raw in values.items():app.write_new(folder/name,raw)
        tx.validate(plan,snapshot,{'candidate-app.bin':payload,'candidate-assets.bin':image})
        return dict(result='prepared',targets=plan['targets'],device_written=False)
    finally:
        if device._identity:device.boot(old_elf)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('phase',choices=('prepare','install','recover'))
    cli.add_argument('--run',default='layered-face-live-20261122')
    cli.add_argument('--previous',type=Path,default=PREVIOUS,help='Verified current paired run; preserves accepted fallback separately')
    args=cli.parse_args()
    if not args.run.startswith('layered-face-live-') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.run):
        cli.error('use a layered-face-live- run name')
    folder=ROOT/'tmp'/args.run
    if args.phase=='prepare':result=prepare(folder,args.previous)
    else:
        try:result=tx.execute(folder,Device(folder),recover=args.phase=='recover')
        except BaseException:
            if args.phase=='install' and (folder/'state.json').exists():tx.execute(folder,Device(folder),recover=True)
            raise
    print(json.dumps(result),flush=True)
