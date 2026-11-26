"""Install the reviewed r8 pair using the existing DeskBot COM4 transaction."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import airboat_role_deployment as tx
from protocol import studio_app_deployment as app
from protocol.storage_capacity import image_facts

spec = importlib.util.spec_from_file_location('r8_pair_device', ROOT / 'scripts/deploy-layered-face.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
PACKAGE = ROOT / 'artifacts/layered-face-20261124-r8'
BASELINE = ROOT / 'tmp/e3c-live-20261122-wifi-cred-live1'


def reference():
    saved = app.load(BASELINE)
    state = app.Journal(BASELINE, saved).load()
    receipt = json.loads((BASELINE / 'install-result.json').read_bytes())
    if (not state or state['phase'] != 'installed' or receipt['result'] != 'candidate_installed'
            or not receipt['readback']['app_byte_equal']
            or not receipt['boot']['firmware_matches_candidate']):
        raise ValueError('verified_current_daily_baseline_required')
    delivery = json.loads((PACKAGE / 'delivery.json').read_bytes())
    previous = delivery['rollback']
    if (app.sha(saved.image) != previous['app']['sha256'] or len(saved.image) != previous['app']['bytes']):
        raise ValueError('packaged_rollback_differs_from_current_daily_app')
    raw = saved.snapshot[:app.START] + saved.candidate + saved.snapshot[app.START + len(saved.candidate):]
    image_at = image_facts(saved.image)['segments'][0]['file_offset']
    elf = saved.image[image_at + 144:image_at + 176].hex()
    if (elf != previous['app_elf_sha256'] or
            app.sha(raw[tx.START:tx.START + tx.SIZE]) != previous['resource_image']['sha256']):
        raise ValueError('current_daily_baseline_identity_changed')
    return raw, elf, len(saved.image)


def prepare(folder):
    # Read both reviewed payloads and validate the archived current reference
    # before taking the port or resetting the device.
    expected, old_elf, old_bytes = reference()
    delivery = json.loads((PACKAGE / 'delivery.json').read_bytes())
    resources = json.loads((PACKAGE / 'report.json').read_bytes())
    candidate = (PACKAGE / delivery['candidate']['app']['file']).read_bytes()
    image = (PACKAGE / 'assets-8m.bin').read_bytes()
    for raw, row in ((candidate, delivery['candidate']['app']), (image, delivery['candidate']['resource_image'])):
        if len(raw) != row['bytes'] or app.sha(raw) != row['sha256']:
            raise ValueError('reviewed_r8_payload_changed')
    folder.mkdir()
    device = base.Device(folder)
    try:
        snapshot = device.read_verified(expected)
        pins = dict(original_static_sha256=app.sha(snapshot[:0x9000] + snapshot[0xD000:]),
            candidate_sha256=app.sha(candidate), candidate_bytes=len(candidate),
            identity_sha256=tx.IDENTITY, original_bytes=old_bytes,
            app_elf_sha256=delivery['candidate']['app_elf_sha256'], original_app_elf_sha256=old_elf,
            source_commit=None, source_afm_sha256=resources['source_afm_sha256'],
            previous_run=BASELINE.name, accepted_fallback_run=BASELINE.name)
        plan = tx.prepare(snapshot, candidate, image, pins, resources)
        plan['source_afm_sha256'] = resources['source_afm_sha256']
        payload = candidate + snapshot[app.START + len(candidate):app.START + plan['targets'][0]['bytes']]
        payloads = {'candidate-app.bin': payload, 'candidate-assets.bin': image}
        tx.validate(plan, snapshot, payloads)
        for name, raw in {**payloads, 'snapshot-32m.bin': snapshot,
                          'plan.json': app.canonical(plan), 'pins.json': app.canonical(pins)}.items():
            app.write_new(folder / name, raw)
        return dict(result='prepared', targets=plan['targets'], device_written=False)
    finally:
        if device._identity and not device.boot(old_elf):
            raise ValueError('retained_current_app_boot_unconfirmed')


def run(phase, folder):
    if phase == 'prepare':
        return prepare(folder)
    if phase == 'recover':
        return tx.execute(folder, base.Device(folder), recover=True)
    try:
        return tx.execute(folder, base.Device(folder))
    except BaseException:
        if (folder / 'state.json').exists():
            tx.execute(folder, base.Device(folder), recover=True)
        raise


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('phase', choices=('reference', 'prepare', 'install', 'recover'))
    cli.add_argument('--run', default='layered-face-live-20261124-r8')
    args = cli.parse_args()
    if (not args.run.startswith('layered-face-live-') or
            any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.run)):
        cli.error('use a layered-face-live- run name')
    if args.phase == 'reference':
        raw, elf, size = reference()
        result = dict(reference_verified=True, original_app_elf_sha256=elf, app_bytes=size, device_accessed=False)
    else:
        result = run(args.phase, ROOT / 'tmp' / args.run)
    print(json.dumps(result), flush=True)
