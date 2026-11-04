#!/usr/bin/env python3
"""Isolated identity-off/on builds; retain existing console and protected outputs."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.studio_resource_package import TARGET_DIR, load_target, json_bytes

spec=importlib.util.spec_from_file_location('e3b_release_builder',ROOT/'scripts/build-deskbot-studio-e2b-firmware.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
BASE=release.BASE
PUBLIC=ROOT/'artifacts/deskbot-studio-e3b-20261104'
WORK=ROOT/'tmp/e3b-identity-20261104'


def printf_portability(config_text, inputs):
    spec=importlib.util.spec_from_file_location('e3b_printf_guard',ROOT/'tools/audit-printf-portability.py')
    guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    previous=json.loads((ROOT/'artifacts/deskbot-studio-e2b-20261104/firmware-build-report.json').read_bytes())['source_sha256']
    before=[];after=[]
    for name,digest in inputs.items():
        if previous.get(name)==digest or Path(name).suffix not in guard.SOURCE_SUFFIXES: continue
        old=ROOT/'tmp/e2b-release-20261104/p'/name
        if name in previous:
            if release.record(old)['sha256']!=previous[name]: raise ValueError('printf baseline source changed: '+name)
            before.extend(guard.detect(old.read_text(encoding='utf-8'),name))
        after.extend(guard.detect((BASE.SOURCE/name).read_text(encoding='utf-8'),name))
    config=guard.nano_configuration(config_text)
    report=guard.classify(before,after,config['nano_enabled'])
    if report['status']!='pass': raise ValueError('nano printf format rejected: '+repr(report['new_blocking']))
    return {'configuration':config,**report}


def inspect(build,enabled,inputs):
    actual=json.loads((build/'config/sdkconfig.json').read_bytes())
    baseline=json.loads((ROOT/'tmp/e2b-release-20261104/o/config/sdkconfig.json').read_bytes())
    delta={k:{'before':baseline.get(k),'after':actual.get(k)} for k in sorted(actual.keys()|baseline.keys())
           if actual.get(k)!=baseline.get(k)}
    if set(delta)-{'DESKBOT_STUDIO_IDENTITY_ENABLE'}: raise ValueError('unexpected effective config delta: '+repr(delta))
    if actual.get('DESKBOT_STUDIO_IDENTITY_ENABLE',False)!=enabled: raise ValueError('identity config')
    if not actual.get('ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG') or not actual.get('ESP_CONSOLE_UART_DEFAULT'):
        raise ValueError('console topology changed')
    release.effective_octal_config(actual,32)
    release.effective_octal_config(json.loads((build/'bootloader/config/sdkconfig.json').read_bytes()),32)
    artifacts={name:release.record(build/name) for name in release.FILES}
    protected={name:release.record(ROOT/'tmp/e2b-release-20261104/o'/name)==artifacts[name] for name in
               ('bootloader/bootloader.bin','partition_table/partition-table.bin','srmodels/srmodels.bin','ota_data_initial.bin')}
    if not all(protected.values()): raise ValueError('protected artifacts differ: '+repr(protected))
    headroom=0x5DC000-artifacts['xiaozhi.bin']['bytes']
    if headroom<1048576: raise ValueError('App headroom')
    env,_,_=BASE.environment()
    nm=shutil.which('xtensa-esp32s3-elf-nm.exe',path=env['PATH'])
    symbols=subprocess.check_output([nm,'-C',str(build/'xiaozhi.elf')],env=env,text=True)
    present='deskbot_studio_link::' in symbols
    if present!=enabled: raise ValueError('identity symbols do not match option')
    if any(name in symbols for name in ('RunContinuousMappingOnce','RunUpperPatternOnce','ExportFlashDiagnosticsOnce')):
        raise ValueError('temporary diagnostic present')
    flash=json.loads((build/'flasher_args.json').read_bytes())
    expected_flash={0x0:'bootloader/bootloader.bin',0x8000:'partition_table/partition-table.bin',
                    0xD000:'ota_data_initial.bin',0x10000:'xiaozhi.bin',0xBC8000:'srmodels/srmodels.bin'}
    if {int(address,16):name for address,name in flash['flash_files'].items()}!=expected_flash:
        raise ValueError('unexpected generated flash map')
    if release.image_facts((build/'xiaozhi.bin').read_bytes())['configured_flash_bytes']!=33554432: raise ValueError('image capacity')
    if inputs!=BASE.HELPER.HELPER.inventory(): raise ValueError('source changed during build')
    return {'enabled':enabled,'artifacts':artifacts,'app_headroom_bytes':headroom,'config_delta':delta,
            'printf_portability':printf_portability((build/'sdkconfig').read_text(encoding='utf-8'),inputs),
            'protected_matches':protected,'identity_symbols_present':present,'console_topology_unchanged':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    path=PUBLIC/'firmware-build-report.json'
    if args.check:
        report=json.loads(path.read_bytes())
        if report['source_sha256']!=BASE.HELPER.HELPER.inventory(): raise ValueError('source changed')
        for row in report['builds']:
            build=WORK/('on' if row['enabled'] else 'off')
            for name,item in row['artifacts'].items():
                if release.record(build/name)!=item: raise ValueError('artifact changed: '+name)
        print('E3b identity-off/on saved builds checked');return
    if path.exists() and not args.resume: raise ValueError('saved build exists; use --check or --resume')
    inputs=BASE.HELPER.HELPER.inventory()
    printf_portability((release.REFERENCE/'sdkconfig').read_text(encoding='utf-8'),inputs)
    locked=yaml.safe_load((BASE.SOURCE/'dependencies.lock').read_bytes())
    project,components=BASE.HELPER.stage(WORK,BASE.DONOR,inputs,locked)
    (project/'partitions/deskbot-upper-assets-8m.csv').write_bytes((TARGET_DIR/'partitions.csv').read_bytes())
    env,python,idf=BASE.environment();steps=[];rows=[]
    for enabled in (False,True):
        label='on' if enabled else 'off';build=WORK/label;BASE.HELPER.private_directory(build)
        sdkconfig=build/'sdkconfig'
        overlay=(TARGET_DIR/'sdkconfig.defaults').read_text(encoding='utf-8')+'\n'+(
            'CONFIG_DESKBOT_STUDIO_IDENTITY_ENABLE=y\n' if enabled else '# CONFIG_DESKBOT_STUDIO_IDENTITY_ENABLE is not set\n')
        sdkconfig.write_text(release.apply_overlay((release.REFERENCE/'sdkconfig').read_text(encoding='utf-8'),overlay),encoding='utf-8')
        command=[str(python),str(idf/'tools/idf.py'),'--no-ccache','-B',str(build),'-D','IDF_TARGET=esp32s3',
                 '-D','BOARD_NAME=esp_vocat','-D','BOARD_TYPE=esp_vocat','-D',f'SDKCONFIG={sdkconfig}',
                 '-D',f'SDKCONFIG_DEFAULTS={project / "sdkconfig.defaults.o2f2"}']
        for action in ('reconfigure','build'):
            log=WORK/f'{label}-{action}.log'
            if log.exists():
                count=1
                while log.with_name(f'{label}-{action}-attempt-{count}.log').exists(): count+=1
                log.rename(log.with_name(f'{label}-{action}-attempt-{count}.log'))
            if action=='build': BASE.HELPER.HELPER.quote_image_effects_link_path(build,project)
            print(f'E3b {label}: {action}',flush=True)
            BASE.run(command+[action],project,env,log)
            steps.append({'configuration':label,'action':action,'returncode':0,'log':log.relative_to(ROOT).as_posix(),
                          'sha256':release.record(log)['sha256']})
        BASE.HELPER.verify_source(project,inputs,inputs[BASE.HELPER.PARTITION])
        rows.append(inspect(build,enabled,inputs))
    report={'schema':'deskbot.studio_identity_build/v1','edition':'E3b','revision':2,'result':'isolated_off_on_builds_verified',
            'source_sha256':inputs,'builds':rows,'steps':steps,'components':components,'target':load_target(),
            'task':{'priority':1,'stack_bytes':4096,'hello_interval_ms':10000},
            'device_written':False,'hardware_accessed':False,'hardware_acceptance':'pending'}
    PUBLIC.mkdir(parents=True,exist_ok=True);path.write_bytes(json_bytes(report))
    print(json.dumps({'result':report['result'],'app_bytes':[r['artifacts']['xiaozhi.bin']['bytes'] for r in rows]}))


if __name__=='__main__': main()
