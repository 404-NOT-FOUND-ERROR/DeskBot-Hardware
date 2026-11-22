#!/usr/bin/env python3
"""Prepare an offline App transaction and resource references; no hardware I/O."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol import studio_common_e3f7_deployment as baseline
from protocol.storage_capacity import image_facts

WORK = ROOT / 'tmp/studio-layout-manifest-e3f9a'
PUBLIC = ROOT / 'artifacts/studio-layout-manifest-e3f9a'
REFERENCE = ROOT / 'tmp/e3c-live-20261111-e3f7-live1'
REFERENCE_PLAN_SHA = '255d511784b7d15ead5ca0ae7a32c6b6275235cf40d3efea949d3be25cedc813'
REFERENCE_INSTALL_SHA = '651a881bd99eb8f65883f63bb3bf42a0c22d6331f652a39d3bfc21b54807de66'
REFERENCE_SNAPSHOT_SHA = '61e696f1018fcc5b48cdbf246ad349642b11da1a03ce2caa9329180cc5f7c8cb'


def reference():
    for name, expected in [('plan.json', REFERENCE_PLAN_SHA), ('install-result.json', REFERENCE_INSTALL_SHA)]:
        if tx.sha((REFERENCE / name).read_bytes()) != expected:
            raise ValueError('E3f-7 reference changed: ' + name)
    previous = tx.load(REFERENCE)
    state = tx.Journal(REFERENCE, previous).load()
    if (state is None or state['phase'] != 'installed' or tx.sha(previous.image) != baseline.APP_SHA
            or previous.metadata['identity_sha256'] != baseline.IDENTITY_SHA):
        raise ValueError('installed E3f-7 reference required')
    # The historical snapshot precedes installation. Overlay the complete saved
    # erase union so the next rollback contains E3f-7, including its App tail.
    raw = previous.snapshot[:tx.START] + previous.candidate + previous.snapshot[tx.START + len(previous.candidate):]
    if tx.sha(raw) != REFERENCE_SNAPSHOT_SHA:
        raise ValueError('reconstructed E3f-7 snapshot changed')
    return raw


def prepare():
    build_raw = (PUBLIC / 'firmware-build-report.json').read_bytes()
    validation_raw = (PUBLIC / 'offline-validation.json').read_bytes()
    build, validation = json.loads(build_raw), json.loads(validation_raw)
    if (build['result'] != 'off_on_builds_verified' or validation['result'] != 'passed'
            or validation.get('build', {}).get('sha256') != tx.sha(build_raw)):
        raise ValueError('matching build and offline validation required')
    image = (WORK / 'on/xiaozhi.bin').read_bytes()
    record = build['builds'][1]['artifacts']['xiaozhi.bin']
    if record != dict(bytes=len(image), sha256=tx.sha(image)):
        raise ValueError('built App changed')
    facts = image_facts(image)
    app_start = facts['segments'][0]['file_offset']
    if (facts['configured_flash_bytes'] != tx.FLASH
            or image[app_start + 144:app_start + 176].hex() != build['builds'][1]['app_elf_sha256']):
        raise ValueError('App hardware profile or ELF changed')
    raw = reference()
    plan = tx.prepare(raw, image, baseline.IDENTITY_SHA, baseline.APP_SIZE, edition='E3c')
    folder = WORK / 'offline-app-plan'
    if folder.exists():
        saved = tx.load(folder)
        if saved.id != plan.id or saved.snapshot != raw or saved.image != image:
            raise ValueError('retained offline plan changed')
    else:
        folder.mkdir()
        tx.persist(folder, plan)
    candidates = json.loads((PUBLIC / 'resource-candidates.json').read_bytes())
    for row in candidates['candidates']:
        resource = (ROOT / row['image_file']).read_bytes()
        if len(resource) != 0x800000 or tx.sha(resource) != row['image_sha256']:
            raise ValueError('resource candidate changed')
    report = dict(schema='deskbot.layout_manifest_preparation/v1', edition='E3f-9a',
        result='offline_app_plan_and_resource_images_ready',
        app_plan=folder.relative_to(ROOT).as_posix(), plan_id=plan.id,
        candidate=dict(bytes=len(image), sha256=tx.sha(image), app_elf_sha256=build['builds'][1]['app_elf_sha256']),
        write=dict(offset=tx.START, bytes=len(plan.candidate), end_exclusive=tx.START + len(plan.candidate)),
        rollback=dict(app='E3f-7', resources='E1.45', bytes=len(plan.rollback), sha256=tx.sha(plan.rollback)),
        reference_snapshot_sha256=tx.sha(raw), build_report_sha256=tx.sha(build_raw),
        offline_validation_sha256=tx.sha(validation_raw), resource_images=candidates['candidates'],
        target='body_COM4', fresh_device_backup_required=True,
        serial_opened=False, device_written=False, hardware_acceptance='pending')
    (PUBLIC / 'offline-preparation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(prepare(), ensure_ascii=True))
