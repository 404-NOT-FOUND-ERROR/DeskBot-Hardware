"""Install the base-insert App against the verified installed r8 resource pair."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import airboat_role_deployment as tx
from protocol import studio_app_deployment as app
from protocol.wifi_trial_deployment import installed_pair

spec = importlib.util.spec_from_file_location('insert_pair_device', ROOT / 'scripts/deploy-layered-face.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
PREVIOUS = ROOT / 'tmp/layered-face-live-20261124-r8'
PROFILE = ROOT / 'tmp/layered-face-insert-20261124/expression'
PACKAGE = ROOT / 'artifacts/layered-face-20261124-r8'


def reviewed():
    snapshot, original, identity, graph = installed_pair(PREVIOUS)
    report = json.loads((PROFILE / 'build/report.json').read_bytes())
    resources = json.loads((PACKAGE / 'report.json').read_bytes())
    candidate = (PROFILE / 'build/xiaozhi.bin').read_bytes()
    image = (PACKAGE / 'assets-8m.bin').read_bytes()
    current_build = json.loads((PACKAGE / 'build-report.json').read_bytes())
    base_source = 'main/boards/esp_vocat/base_control.cc'
    if (report.get('layered_face') is not True or not report.get('network_settings')
            or not report.get('credential_renewal') or report.get('lab') is not False
            or not report.get('effective_config_equal') or len(report.get('protected_equal', {})) != 6
            or not all(report['protected_equal'].values())
            or report['app'] != dict(bytes=len(candidate), sha256=app.sha(candidate))
            or identity != tx.IDENTITY or graph != resources['download_sha256']['deskbot-roles.json']
            or image != snapshot[tx.START:tx.START + tx.SIZE]):
        raise ValueError('reviewed_insert_build_and_current_r8_pair_required')
    if ({k: v for k, v in report['source_sha256'].items() if k != base_source}
            != current_build['source_sha256']):
        raise ValueError('unexpected_source_change_outside_base_insert')
    # Pin the exact patched donor file, including its original heartbeat gate.
    from protocol.base_insert_expression import patch
    donor = ROOT / 'tmp/touch-e3f9d4/p/main/boards/esp_vocat/base_control.cc'
    if report['source_sha256'].get(base_source) != app.sha(patch(donor.read_bytes())):
        raise ValueError('base_insert_patch_not_in_compiled_sources')
    previous = json.loads((PREVIOUS / 'plan.json').read_bytes())
    return snapshot, original, previous['app_elf_sha256'], report, resources, candidate, image


def prepare(folder):
    expected, original, old_elf, report, resources, candidate, image = reviewed()
    folder.mkdir()
    device = base.Device(folder)
    try:
        # Reuse the verified full reference only after a fresh identity, current
        # NVS read and whole-flash digest check on this connected device.
        snapshot = device.read_verified(expected)
        pins = dict(original_static_sha256=app.sha(snapshot[:0x9000] + snapshot[0xD000:]),
            candidate_sha256=app.sha(candidate), candidate_bytes=len(candidate),
            identity_sha256=tx.IDENTITY, original_bytes=len(original),
            app_elf_sha256=report['app_elf_sha256'], original_app_elf_sha256=old_elf,
            source_commit=None, source_afm_sha256=resources['source_afm_sha256'],
            previous_run=PREVIOUS.name, accepted_fallback_run='airboat-role-live-20261121-v3')
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
            raise ValueError('current_r8_boot_unconfirmed')


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
    cli.add_argument('--run', default='layered-face-live-20261124-insert')
    args = cli.parse_args()
    if (not args.run.startswith('layered-face-live-') or
            any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.run)):
        cli.error('use a layered-face-live- run name')
    if args.phase == 'reference':
        _, original, old_elf, report, _, _, _ = reviewed()
        result = dict(reference_verified=True, original_app_elf_sha256=old_elf,
                      app_bytes=len(original), candidate_app_elf_sha256=report['app_elf_sha256'], device_accessed=False)
    else:
        result = run(args.phase, ROOT / 'tmp' / args.run)
    print(json.dumps(result), flush=True)
