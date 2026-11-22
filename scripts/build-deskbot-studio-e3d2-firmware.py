#!/usr/bin/env python3
"""Isolated E3d-2 catalog off/on builds against retained E3c-8 query-on."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import TARGET_DIR, load_target, json_bytes

spec = importlib.util.spec_from_file_location('e3d2_previous', ROOT / 'scripts/build-deskbot-studio-e3c5-firmware.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
BASE, release = previous.BASE, previous.release
PUBLIC = ROOT / 'artifacts/deskbot-studio-e3d2-20261105'
WORK = ROOT / 'tmp/e3d2-catalog-20261105'
PRIOR = ROOT / 'artifacts/deskbot-studio-e3c8-20261105/firmware-build-report.json'
OPTION = 'DESKBOT_STUDIO_CATALOG_ENABLE'
DELTA = {'main/Kconfig.projbuild', 'main/assets.cc',
    *('main/boards/esp_vocat/' + name for name in ('studio_usb_session.h', 'studio_usb_diagnostic.cc',
        'studio_catalog.h', 'studio_catalog_actions.h', 'studio_catalog_builder.cc',
        'studio_catalog_adapter.h', 'studio_catalog_adapter.cc'))}
PROTECTED = ('bootloader/bootloader.bin', 'partition_table/partition-table.bin',
             'srmodels/srmodels.bin', 'ota_data_initial.bin')


def baseline(inputs):
    report = json.loads(PRIOR.read_bytes())
    if report['edition'] != 'E3c-8' or report['revision'] != 1:
        raise ValueError('retained E3c-8 revision 1 required')
    before = report['source_sha256']
    changed = {name for name in before.keys() | inputs.keys() if before.get(name) != inputs.get(name)}
    if changed != DELTA:
        raise ValueError('unexpected catalog source delta: ' + repr(sorted(changed)))
    row = next(row for row in report['builds'] if row['enabled'])
    for name, expected in row['artifacts'].items():
        if release.record(ROOT / row['build_directory'] / name) != expected:
            raise ValueError('retained output changed: ' + name)
    return row


def inspect(build, enabled, inputs, env, prior):
    reference = ROOT / prior['build_directory']
    actual = json.loads((build / 'config/sdkconfig.json').read_bytes())
    old = json.loads((reference / 'config/sdkconfig.json').read_bytes())
    delta = {key: dict(before=old.get(key), after=actual.get(key))
             for key in sorted(actual.keys() | old.keys()) if actual.get(key) != old.get(key)}
    if set(delta) != {OPTION} or actual.get(OPTION) is not enabled:
        raise ValueError('only catalog configuration may change: ' + repr(delta))
    if not all(actual.get(key) for key in ('DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE',
        'DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE', 'DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE',
        'ESP_CONSOLE_UART_DEFAULT', 'ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG', 'COMPILER_CXX_RTTI', 'SPIRAM')):
        raise ValueError('original query/control or memory configuration changed')
    release.effective_octal_config(actual, 32)
    if json.loads((build / 'bootloader/config/sdkconfig.json').read_bytes()) != json.loads(
            (reference / 'bootloader/config/sdkconfig.json').read_bytes()):
        raise ValueError('bootloader configuration changed')
    artifacts = {name: release.record(build / name) for name in release.FILES}
    protected = {name: artifacts[name] == release.record(reference / name) for name in PROTECTED}
    if not all(protected.values()):
        raise ValueError('protected output differs: ' + repr(protected))
    target = load_target()
    headroom = 0x5DC000 - artifacts['xiaozhi.bin']['bytes']
    if headroom < target['minimum_app_headroom_bytes']:
        raise ValueError('App headroom below 1 MiB')
    if artifacts['partition_table/partition-table.bin']['sha256'] != target['partition_table_sha256']:
        raise ValueError('partition table differs')
    expected_flash = {0: 'bootloader/bootloader.bin', 0x8000: 'partition_table/partition-table.bin',
        0xD000: 'ota_data_initial.bin', 0x10000: 'xiaozhi.bin', 0xBC8000: 'srmodels/srmodels.bin'}
    flash = json.loads((build / 'flasher_args.json').read_bytes())
    if {int(address, 16): name for address, name in flash['flash_files'].items()} != expected_flash:
        raise ValueError('Flash map differs')
    nm = shutil.which('xtensa-esp32s3-elf-nm.exe', path=env['PATH'])
    if not nm:
        raise ValueError('pinned nm unavailable')
    symbols = subprocess.check_output([nm, '-S', '-C', str(build / 'xiaozhi.elf')], env=env, text=True)
    for marker in ('deskbot_studio_session::Start(', 'deskbot_studio_expression::AdapterHooks()'):
        if marker not in symbols:
            raise ValueError('original adapter missing: ' + marker)
    present = 'deskbot_studio_catalog::BuildFromMounted(' in symbols
    if present != enabled or (b'deskbot.studio_catalog_page/v1' in (build / 'xiaozhi.bin').read_bytes()) != enabled:
        raise ValueError('catalog symbols/configuration differ')
    if any(marker in symbols for marker in ('RunContinuousMappingOnce', 'RunUpperPatternOnce', 'ExportFlashDiagnosticsOnce')):
        raise ValueError('temporary diagnostic present')
    stacks = {}
    for path in (build / 'esp-idf/main').rglob('*.su'):
        if path.name.startswith(('studio_catalog_', 'studio_usb_diagnostic', 'assets.cc')):
            for line in path.read_text(encoding='utf-8').splitlines():
                parts = line.split('\t')
                if len(parts) >= 3:
                    stacks[parts[0]] = dict(bytes=int(parts[1]), allocation=parts[2])
    if not stacks or enabled and not any('BuildFromMounted' in key for key in stacks):
        raise ValueError('compiler stack usage missing')
    if any(row['bytes'] > 2048 or row['allocation'] not in ('static', 'dynamic,bounded') for row in stacks.values()):
        raise ValueError('catalog/USB single-frame stack budget exceeded')
    if inputs != BASE.HELPER.HELPER.inventory():
        raise ValueError('source changed during build')
    return dict(enabled=enabled, build_directory=build.relative_to(ROOT).as_posix(), artifacts=artifacts,
        config_delta=delta, app_headroom_bytes=headroom, protected_matches=protected,
        neutral_request_enabled=True, catalog_present=present, compiler_stack_usage=stacks,
        console_topology_unchanged=True)


def build_ninja(build, project, env, log):
    """Build the configured tree without idf.py regenerating patched Ninja files."""
    patches = BASE.HELPER.HELPER.quote_image_effects_link_path(build, project)
    if patches != 1:
        raise ValueError(f"expected one image-effects path repair, found {patches}")
    ninja = shutil.which("ninja.exe", path=env["PATH"]) or shutil.which("ninja", path=env["PATH"])
    if not ninja:
        raise ValueError("pinned Ninja executable unavailable")
    BASE.run([ninja, "-C", str(build)], project, env, log)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    inputs = BASE.HELPER.HELPER.inventory()
    prior = baseline(inputs)
    env, python, idf = BASE.environment()
    path = PUBLIC / 'firmware-build-report.json'
    if args.check:
        saved = json.loads(path.read_bytes())
        if saved['source_sha256'] != inputs or saved['prior_report'] != release.record(PRIOR):
            raise ValueError('saved inputs differ')
        for row in saved['builds']:
            if row != inspect(ROOT / row['build_directory'], row['enabled'], inputs, env, prior):
                raise ValueError('saved build differs')
        for step in saved['steps']:
            if release.record(ROOT / step['log'])['sha256'] != step['sha256']:
                raise ValueError('saved log differs')
        print('E3d-2 off/on outputs checked')
        return
    if (path.exists() or WORK.exists()) and not args.resume:
        raise ValueError('isolated build exists; use --resume or --check')
    locked = yaml.safe_load((BASE.SOURCE / 'dependencies.lock').read_bytes())
    project, components = BASE.HELPER.stage(WORK, BASE.DONOR, inputs, locked)
    (project / 'partitions/deskbot-upper-assets-8m.csv').write_bytes((TARGET_DIR / 'partitions.csv').read_bytes())
    rows, steps = [], []
    for enabled in (False, True):
        label = 'on' if enabled else 'off'
        build = WORK / label
        BASE.HELPER.private_directory(build)
        sdkconfig = build / 'sdkconfig'
        overlay = f'CONFIG_{OPTION}=y\n' if enabled else f'# CONFIG_{OPTION} is not set\n'
        sdkconfig.write_text(release.apply_overlay((ROOT / prior['build_directory'] / 'sdkconfig').read_text(encoding='utf-8'), overlay), encoding='utf-8')
        command = [str(python), str(idf / 'tools/idf.py'), '--no-ccache', '-B', str(build),
            '-D', 'IDF_TARGET=esp32s3', '-D', 'BOARD_NAME=esp_vocat', '-D', 'BOARD_TYPE=esp_vocat',
            '-D', 'CMAKE_CXX_FLAGS=-fstack-usage', '-D', f'SDKCONFIG={sdkconfig}',
            '-D', f'SDKCONFIG_DEFAULTS={project / "sdkconfig.defaults.o2f2"}']
        for action in ('reconfigure', 'build'):
            log = WORK / f'{label}-{action}.log'
            if log.exists():
                attempt = 1
                while log.with_name(f'{label}-{action}-attempt-{attempt}.log').exists():
                    attempt += 1
                log.rename(log.with_name(f'{label}-{action}-attempt-{attempt}.log'))
            print(f'E3d-2 {label}: {action}', flush=True)
            if action == 'reconfigure':
                BASE.run(command + [action], project, env, log)
            else:
                build_ninja(build, project, env, log)
            steps.append(dict(configuration=label, action=action, returncode=0,
                log=log.relative_to(ROOT).as_posix(), sha256=release.record(log)['sha256']))
        BASE.HELPER.verify_source(project, inputs, inputs[BASE.HELPER.PARTITION])
        if BASE.HELPER.HELPER.normalized_lock(locked) != BASE.HELPER.HELPER.normalized_lock(yaml.safe_load((project / 'dependencies.lock').read_bytes())):
            raise ValueError('component lock changed')
        rows.append(inspect(build, enabled, inputs, env, prior))
    report = dict(schema='deskbot.studio_catalog_build/v1', edition='E3d-2', revision=1,
        result='isolated_off_on_builds_verified', source_sha256=inputs, source_delta=sorted(DELTA),
        source_manifest=release.record(BASE.SOURCE / 'SOURCE_MANIFEST.sha256'),
        prior_report=release.record(PRIOR), builds=rows, steps=steps, components=components,
        target=load_target(), new_tasks=0, new_serial_owners=0, max_entries=128,
        page_size=2, reply_workspace_bytes=2048, snapshot_allocation='PSRAM',
        runtime_build_ms=None, runtime_free_heap=None, hardware_accessed=False,
        device_written=False, hardware_acceptance='pending')
    PUBLIC.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(report))
    print(json.dumps(dict(result=report['result'], app_bytes=[row['artifacts']['xiaozhi.bin']['bytes'] for row in rows])))


if __name__ == '__main__':
    main()
