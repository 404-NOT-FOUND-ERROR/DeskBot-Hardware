#!/usr/bin/env python3
"""Collect reproducible E3f-9a offline evidence; never opens a device."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'artifacts/studio-layout-manifest-e3f9a'
LOGS = {
    'manifest_native': ('tmp/e3f9a-native-tests.log', 14),
    'existing_layout_regression': ('tmp/e3f9a-regression.log', 80),
    'actual_gfx_pixels': ('tmp/e3f9a-pixel-tests.log', 1),
}
SOURCES = (
    'firmware/body/main/Kconfig.projbuild',
    'firmware/body/main/boards/esp_vocat/studio_asset_layout.cc',
    'firmware/body/main/boards/esp_vocat/studio_asset_layout_manifest.cc',
    'firmware/body/main/boards/esp_vocat/studio_asset_layout_manifest.h',
    'scripts/deskbot-mirror-render.js',
    'scripts/deskbot-runtime-roles-e3f8.js',
    'scripts/build-deskbot-studio-e3f8.py',
    'protocol/studio_mirror_options.py',
    'protocol/studio_resource_package.py',
    'tests/test_studio_layout_manifest_e3f9a.py',
    'tests/test_studio_mirror_pixels_native.py',
    'tests/cpp/studio_reflow_test.cc',
    'tests/cpp/studio_catalog_test.cc',
    'tests/cpp/studio_mirror_pixels_test.cc',
    'tests/studio-mirror-render.cjs',
    'tests/studio-mirror-e3f8-browser.cjs',
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def record(name):
    raw = (ROOT / name).read_bytes()
    return dict(path=name, bytes=len(raw), sha256=digest(raw))


def audit():
    evidence = {}
    for key, (name, expected) in LOGS.items():
        text = (ROOT / name).read_text(encoding='utf-8')
        if not re.search(r'Ran ' + str(expected) + r' tests? in [\d.]+s\s+OK\s*$', text):
            raise ValueError('successful unittest log required: ' + name)
        evidence[key] = dict(tests=expected, **record(name))
    node = 'tmp/e3f9a-node-tests.log'
    text = (ROOT / node).read_text(encoding='utf-8')
    if not all(re.search(r'# ' + key + r' ' + str(count) + r'\b', text)
               for key, count in [('tests', 44), ('pass', 44), ('fail', 0), ('skipped', 0)]):
        raise ValueError('successful Node log required')
    evidence['node'] = dict(tests=44, **record(node))
    browser = 'tmp/deskbot-studio-e3f8-20261111/qa/mirror-option-browser-report.json'
    qa = json.loads((ROOT / browser).read_bytes())
    if len(qa['checks']) != 42 or qa['errors']:
        raise ValueError('browser checks required')
    evidence['browser'] = dict(checks=42, **record(browser))
    resources = json.loads((PUBLIC / 'resource-candidates.json').read_bytes())
    if resources['result'] != 'offline_resources_ready' or not all(
            row['all_original_members_byte_equal'] and row['manifest_resources'] == 19
            and row['soft_limit_free_bytes'] > 1024 * 1024 for row in resources['candidates']):
        raise ValueError('resource preservation evidence required')
    for row in resources['candidates']:
        if record(row['image_file'])['sha256'] != row['image_sha256']:
            raise ValueError('candidate changed')
    report = dict(schema='deskbot.layout_manifest_offline_validation/v1', edition='E3f-9a',
        result='passed', unittest_tests=95, node_tests=44, browser_checks=42,
        actual_gfx_pixel_combinations=42, errors=0, failures=0, skipped=0,
        evidence=evidence, source_sha256={name: record(name)['sha256'] for name in SOURCES},
        resources=record('artifacts/studio-layout-manifest-e3f9a/resource-candidates.json'),
        serial_opened=False, device_written=False, hardware_acceptance='pending')
    build_path = PUBLIC / 'firmware-build-report.json'
    if build_path.exists():
        build = json.loads(build_path.read_bytes())
        if build['result'] != 'off_on_builds_verified':
            raise ValueError('paired build not verified')
        for name, value in report['source_sha256'].items():
            if name.startswith('firmware/body/') and build['source_sha256'].get(name[14:]) != value:
                raise ValueError('build does not match tested source: ' + name)
        report['build'] = record(build_path.relative_to(ROOT).as_posix())
    (PUBLIC / 'offline-validation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=True))
