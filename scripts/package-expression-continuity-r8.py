"""Stage the r8 firmware/resource pair and the last accepted rollback pair."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / 'artifacts/layered-face-20261124-r8'
BUILD = ROOT / 'tmp/layered-face-20261124-r8/expression/build'
BASELINE = ROOT / 'tmp/body-credential-renewal-20261122-r2/expression/build'
BASELINE_REPORT = BASELINE / 'report.json'
BASELINE_ASSET = ROOT / 'artifacts/layered-face-20261122/assets-8m.bin'
BASELINE_INSTALL = ROOT / 'artifacts/layered-face-20261122/installation.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_copy(source, destination, expected_sha, expected_bytes=None):
    if not source.is_file() or digest(source) != expected_sha:
        raise ValueError('source_digest_mismatch: ' + source.name)
    if expected_bytes is not None and source.stat().st_size != expected_bytes:
        raise ValueError('source_size_mismatch: ' + source.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if digest(destination) != expected_sha:
        raise ValueError('copied_digest_mismatch: ' + destination.name)
    return {'file': destination.relative_to(ASSET).as_posix(), 'bytes': destination.stat().st_size,
            'sha256': expected_sha}


def main():
    face = json.loads((ASSET / 'report.json').read_bytes())
    app = json.loads((BUILD / 'report.json').read_bytes())
    baseline = json.loads(BASELINE_REPORT.read_bytes())
    installed = json.loads(BASELINE_INSTALL.read_bytes())
    if (app.get('edition') != 'E3f-9d.7-network-settings' or not app.get('network_settings')
            or not app.get('credential_renewal') or not app.get('layered_face')
            or not all(app.get('protected_equal', {}).values()) or not app.get('effective_config_equal')):
        raise ValueError('candidate_firmware_report_incomplete')
    if (baseline.get('edition') != 'E3f-9d.7-network-settings'
            or baseline.get('app', {}).get('sha256') != digest(BASELINE / 'xiaozhi.bin')
            or installed.get('result') != 'installed_and_verified'):
        raise ValueError('accepted_current_app_unavailable')
    asset_target = next(row for row in installed['targets'] if row['file'] == 'candidate-assets.bin')
    if asset_target['sha256'] != digest(BASELINE_ASSET) or asset_target['bytes'] != BASELINE_ASSET.stat().st_size:
        raise ValueError('accepted_current_resources_unavailable')

    candidate = verified_copy(BUILD / 'xiaozhi.bin', ASSET / 'firmware/xiaozhi.bin',
                              app['app']['sha256'], app['app']['bytes'])
    rollback_app = verified_copy(BASELINE / 'xiaozhi.bin', ASSET / 'rollback/xiaozhi.bin',
                                 baseline['app']['sha256'], baseline['app']['bytes'])
    rollback_assets = verified_copy(BASELINE_ASSET, ASSET / 'rollback/assets-8m.bin', asset_target['sha256'],
                                    asset_target['bytes'])
    # Only this candidate's private profile is rebound. The running daily
    # service continues to use the accepted App/graph until installation.
    profile_path = BUILD.parent / 'receiver.json'
    profile = json.loads(profile_path.read_bytes())
    if profile.get('firmware_sha256') != app['app_elf_sha256']:
        raise ValueError('candidate_receiver_app_mismatch')
    profile['graph_sha256'] = face['graph_sha256']
    profile_path.write_text(json.dumps(profile, indent=2) + '\n', encoding='utf-8')
    delivery = {
        'schema': 'deskbot.expression_continuity_delivery/v1',
        'edition': 'E3f-9d.7-network-settings + layered-r8',
        'device_written': False,
        'physical_acceptance': 'pending',
        'candidate': {
            'app': candidate,
            'app_elf_sha256': app['app_elf_sha256'],
            'dfm': {'file': 'face-motion.dfm', 'bytes': face['binary_bytes'],
                    'sha256': face['binary_sha256']},
            'resource_image': {'file': 'assets-8m.bin', 'bytes': 8388608,
                               'sha256': face['image_sha256'], 'used_bytes': face['package_bytes'],
                               'free_bytes': face['partition_free_bytes']},
            'source_afm_sha256': face['source_afm_sha256'],
            'graph_sha256': face['graph_sha256'],
            'semantics': 29,
            'aliases': {'neutral': 'idle', 'sleepy': 'tried'},
        },
        'rollback': {
            'edition': 'E3f-9d.7-network-settings + layered-r7',
            'app_elf_sha256': baseline['app_elf_sha256'],
            'app': rollback_app,
            'resource_image': rollback_assets,
            'nvs_written_by_staging': False,
        },
        'build_checks': {
            'protected_equal': app['protected_equal'],
            'effective_config_equal': app['effective_config_equal'],
            'internal_static_delta_bytes': app['internal_static_delta_bytes'],
            'flash_bytes': 33554432,
            'psram_mode': 'octal',
        },
        'install_gate': [
            'Read live device identity and the current App/resources before writing.',
            'Save the live App, resources, NVS and affected erase sectors.',
            'Confirm the live baseline matches the packaged E3f-9d.7 + layered-r7 pair.',
            'Write the App/resources pair, read both back, verify unaffected Flash/NVS and boot.',
            'Run the physical acceptance steps in docs/expression-continuity-r8.md.',
        ],
    }
    path = ASSET / 'delivery.json'
    path.write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'delivery': str(path), 'candidate_app': candidate,
                      'rollback_app': rollback_app, 'rollback_assets': rollback_assets}, ensure_ascii=False))


if __name__ == '__main__':
    main()
