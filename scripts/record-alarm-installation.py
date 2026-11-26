"""Record the verified installed App with its unchanged expression resources."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol import airboat_role_deployment as paired
from protocol import studio_app_deployment as app
from protocol.storage_capacity import image_facts
from protocol.wifi_trial_deployment import installed_pair


def record(source, destination, package):
    source,destination,package=map(lambda p:Path(p).resolve(),(source,destination,package))
    if source.parent!=ROOT/'tmp' or destination.parent!=ROOT/'tmp' or destination.exists():
        raise ValueError('fresh_workspace_reference_required')
    saved=app.load(source)
    state=app.Journal(source,saved).load()
    receipt=json.loads((source/'install-result.json').read_bytes())
    pins=json.loads((source/'wifi-pins.json').read_bytes())
    readback=receipt['readback'];boot=receipt['boot']
    if (not state or state['phase']!='installed' or receipt['result']!='candidate_installed'
            or state.get('readback')!=readback or state.get('boot')!=boot
            or not all(readback.get(k) is True for k in ('app_byte_equal','nvs_equal','static_outside_app_equal'))
            or readback['app_bytes']!=len(saved.candidate) or readback['app_sha256']!=app.sha(saved.candidate)
            or not all(boot.get(k) is True for k in ('firmware_matches_candidate','hardware_matches_target','normal_boot'))
            or boot['hello']['firmware']['app_elf_sha256']!=pins['app_elf_sha256']
            or app.sha(saved.image)!=pins['candidate_sha256']):
        raise ValueError('verified_installed_app_required')
    resources=(package/'assets-8m.bin').read_bytes()
    report=json.loads((package/'report.json').read_bytes())
    if resources!=saved.snapshot[paired.START:paired.START+paired.SIZE]:
        raise ValueError('unchanged_installed_resources_required')
    old=saved.snapshot[app.START:app.START+saved.metadata['original_bytes']]
    offset=image_facts(old)['segments'][0]['file_offset']
    reference_pins=dict(candidate_bytes=len(saved.image),candidate_sha256=app.sha(saved.image),
        original_bytes=len(old),identity_sha256=saved.metadata['identity_sha256'],
        original_static_sha256=app.sha(saved.snapshot[:0x9000]+saved.snapshot[0xD000:]),
        app_elf_sha256=pins['app_elf_sha256'],original_app_elf_sha256=old[offset+144:offset+176].hex(),
        source_commit=None,previous_run=source.name)
    plan=paired.prepare(saved.snapshot,saved.image,resources,reference_pins,report)
    plan.update(reference_only=True,verification_source=str(source))
    payloads={'candidate-app.bin':saved.candidate,'candidate-assets.bin':resources}
    paired.validate(plan,saved.snapshot,payloads)
    destination.mkdir()
    for name,raw in dict(payloads,**{'snapshot-32m.bin':saved.snapshot,'plan.json':app.canonical(plan),
            'pins.json':app.canonical(reference_pins)}).items():app.write_new(destination/name,raw)
    reference_state=dict(phase='installed',plan_sha256=app.sha(app.canonical(plan)),readback_equal=True,
        outside_targets_equal=True,nvs_equal=True,boot_verified=True,hardware_acceptance='pending',
        reference_only=True,verification_source=str(source),source_receipt_sha256=app.sha((source/'install-result.json').read_bytes()),
        resource_verification='outside_app_digest_unchanged_not_rewritten')
    app.write_new(destination/'state.json',app.canonical(reference_state))
    current,image,identity,graph=installed_pair(destination)
    assert current==saved.snapshot[:app.START]+saved.candidate+saved.snapshot[app.START+len(saved.candidate):]
    return dict(reference=str(destination),firmware_sha256=pins['app_elf_sha256'],graph_sha256=graph,
        app_bytes=len(image),reference_only=True,device_accessed=False)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--source',type=Path,required=True);cli.add_argument('--destination',type=Path,required=True)
    cli.add_argument('--package',type=Path,default=ROOT/'artifacts/layered-face-20261124-r8')
    args=cli.parse_args();print(json.dumps(record(args.source,args.destination,args.package)))
