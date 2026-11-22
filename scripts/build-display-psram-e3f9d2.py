"""Build one audited transport change; preserve the original frozen project."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from e3f9d2_display_dma import BOARD, REPORT_SHA, patch_board

spec = importlib.util.spec_from_file_location('dma_role_parent', ROOT / 'scripts/build-studio-role-activation-e3f9d.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
H, runtime = parent.H, parent.runtime
SOURCE = ROOT / 'tmp/studio-role-activation-e3f9d/p'
REFERENCE = SOURCE.parent / 'on'
BASE_REPORT = ROOT / 'artifacts/studio-role-query-fix-e3f9d1/firmware-build-report.json'
WORK = ROOT / 'tmp/display-psram-e3f9d2'
PUBLIC = ROOT / 'artifacts/display-psram-e3f9d2'


def inputs():
    raw = BASE_REPORT.read_bytes()
    if H.sha256(raw) != REPORT_SHA:
        raise ValueError('retained E3f-9d.1 build report changed')
    report = json.loads(raw)
    H.verify_frozen_sources(SOURCE, report['source_sha256'])
    if H.file_record(REFERENCE / 'xiaozhi.bin') != report['builds'][1]['artifacts']['xiaozhi.bin']:
        raise ValueError('baseline App changed')
    manifest = dict(report['source_sha256'])
    manifest[BOARD] = H.sha256(patch_board((SOURCE / BOARD).read_bytes()))
    return report, manifest


def build(check=False):
    baseline, manifest = inputs()
    project, folder = WORK / 'p', WORK / 'on'
    if not project.exists():
        if check:
            raise FileNotFoundError('retained display project required')
        project.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SOURCE, project)
        H.rebind_dependency_lock(project, SOURCE)
        (project / BOARD).write_bytes(patch_board((SOURCE / BOARD).read_bytes()))
    H.verify_frozen_sources(project, manifest)
    locked = H.load_locked_dependencies(project)
    components = parent.validate_components(project, locked)
    inventory = H.verify_project_file_inventory(project, manifest, locked)
    env, python, idf = runtime.environment()
    folder.mkdir(exist_ok=True)
    if not check:
        config = (REFERENCE / 'sdkconfig').read_bytes()
        if (folder / 'sdkconfig').exists() and (folder / 'sdkconfig').read_bytes() != config:
            raise ValueError('saved display configuration changed')
        (folder / 'sdkconfig').write_bytes(config)
        command = [str(python), str(idf / 'tools/idf.py'), '--no-ccache', '-B', str(folder),
            '-D', 'IDF_TARGET=esp32s3', '-D', 'BOARD_NAME=esp_vocat', '-D', 'BOARD_TYPE=esp_vocat',
            '-D', 'CMAKE_CXX_FLAGS=-fstack-usage', '-D', f'SDKCONFIG={folder / "sdkconfig"}',
            '-D', f'SDKCONFIG_DEFAULTS={project / "sdkconfig.defaults.o2f2"}']
        print('E3f-9d.2: reconfigure', flush=True)
        runtime.run(command + ['reconfigure'], project, env, WORK / 'reconfigure.log')
        if runtime.HELPER.HELPER.quote_image_effects_link_path(folder, project) != 1:
            raise ValueError('image-effects link repair changed')
        ninja = shutil.which('ninja.exe', path=env['PATH'])
        if not ninja:
            raise FileNotFoundError('pinned Ninja required')
        print('E3f-9d.2: build', flush=True)
        runtime.run([ninja, '-j', '8', '-C', str(folder)], project, env, WORK / 'build.log')
    for action in ('reconfigure', 'build'):
        parent.stable.validate_log(WORK / (action + '.log'), action)
    config = json.loads((folder / 'config/sdkconfig.json').read_bytes())
    if config != json.loads((REFERENCE / 'config/sdkconfig.json').read_bytes()):
        raise ValueError('effective firmware configuration changed')
    parent.stable.release.effective_octal_config(config, 32)
    artifacts = {name: H.file_record(folder / name) for name in H.ARTIFACTS}
    protected = {name: artifacts[name] == H.file_record(REFERENCE / name) for name in H.PROTECTED}
    if not all(protected.values()):
        raise ValueError('protected outputs changed')
    elf = H.validate_app_elf((folder / 'xiaozhi.bin').read_bytes(), (folder / 'xiaozhi.elf').read_bytes())
    size = shutil.which('xtensa-esp32s3-elf-size.exe', path=env['PATH'])
    nm = shutil.which('xtensa-esp32s3-elf-nm.exe', path=env['PATH'])
    if not size or not nm:
        raise FileNotFoundError('pinned ELF tools required')
    internal = H.static_data_bytes(folder / 'xiaozhi.elf', size, env)
    if internal != baseline['builds'][1]['internal_static_data_bytes']:
        raise ValueError('internal static memory changed')
    symbols = subprocess.check_output([nm, '-S', '-C', str(folder / 'xiaozhi.elf')], env=env, text=True)
    for marker in ('deskbot_role_activation::Observe', 'deskbot_role::OnFrame',
                   'deskbot_studio_runtime::ReadDisplay()', 'deskbot_studio_catalog::BuildFromMounted('):
        if marker not in symbols:
            raise ValueError('inherited feature missing: ' + marker)
    if any(marker in symbols for marker in ('RunContinuousMappingOnce', 'RunUpperPatternOnce', 'ExportFlashDiagnosticsOnce')):
        raise ValueError('temporary experiment linked')
    H.verify_frozen_sources(project, manifest)
    if (parent.validate_components(project, locked) != components or
            H.verify_project_file_inventory(project, manifest, locked) != inventory or inputs()[1] != manifest):
        raise ValueError('build inputs changed')
    result = dict(schema='deskbot.display_psram_e3f9d2_build/v1', edition='E3f-9d.2',
        baseline='E3f-9d.1', result='build_verified', baseline_report_sha256=REPORT_SHA,
        source_sha256=manifest, source_delta=[BOARD], components=components, input_inventory=inventory,
        artifacts=artifacts, app_elf_sha256=elf, protected_equal=protected,
        effective_config_equal=True, internal_static_data_bytes=internal, internal_static_delta_bytes=0,
        qspi_clock_hz=40000000, max_transfer_bytes=4092, queue_depth=2,
        psram_dma_direct=True, device_accessed=False, device_written=False, hardware_acceptance='pending',
        steps={a: H.file_record(WORK / (a + '.log')) for a in ('reconfigure', 'build')})
    PUBLIC.mkdir(exist_ok=True)
    output = PUBLIC / 'firmware-build-report.json'
    if check:
        if json.loads(output.read_bytes()) != result:
            raise ValueError('saved build evidence changed')
    else:
        with output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps({k: result[k] for k in ('edition', 'result', 'artifacts', 'internal_static_data_bytes')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    build(parser.parse_args().check)
