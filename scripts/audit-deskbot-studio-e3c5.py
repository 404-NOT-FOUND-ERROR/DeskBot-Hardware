#!/usr/bin/env python3
"""Independently compare scheduled-expression output with frozen E3c-4."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_capacity import image_facts
from protocol.expression_control import validate_catalog
from protocol.studio_resource_package import json_bytes, load_target

PUBLIC = ROOT / 'artifacts/deskbot-studio-e3c5-20261105'
EXPECTED = {
    'main/Kconfig.projbuild', 'main/boards/esp_vocat/studio_usb_session.h',
    'main/boards/esp_vocat/studio_usb_diagnostic.cc', 'main/boards/esp_vocat/studio_expression_request.h',
    'main/boards/esp_vocat/studio_expression_control.h', 'main/boards/esp_vocat/studio_expression_adapter.h',
    'main/boards/esp_vocat/studio_expression_adapter.cc',
}


def record(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def audit():
    before = json.loads((ROOT/'artifacts/deskbot-studio-e3c4-20261105/firmware-build-report.json').read_bytes())
    after = json.loads((PUBLIC/'firmware-build-report.json').read_bytes())
    old, new = before['source_sha256'], after['source_sha256']
    changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    if set(changed) != EXPECTED or after['target'] != load_target():
        raise ValueError('source scope or target differs')
    for name, digest in new.items():
        if record(ROOT/'firmware/body'/name)['sha256'] != digest:
            raise ValueError('current source differs: '+name)
    prior = next(row for row in before['builds'] if row['enabled'])
    reference_dir = ROOT/'tmp/e3c4-display-20261105/on'
    for name, item in prior['artifacts'].items():
        if record(reference_dir/name) != item: raise ValueError('reference output differs: '+name)
    reference = (reference_dir/'xiaozhi.bin').read_bytes()
    disabled = (ROOT/'tmp/e3c5-expression-20261105/off/xiaozhi.bin').read_bytes()
    ref, off = image_facts(reference), image_facts(disabled)
    if len(reference) != len(disabled) or [(s['load_address'],s['bytes']) for s in ref['segments']] != [
            (s['load_address'],s['bytes']) for s in off['segments']]:
        raise ValueError('disabled segment layout or size differs')
    first = off['segments'][0]['file_offset']
    if struct.unpack_from('<I',disabled,first)[0] != 0xABCD5432: raise ValueError('App description magic')
    elf_hash = first+144
    allowed = set(range(elf_hash,elf_hash+32)) | {off['checksum_offset']} | set(range(len(disabled)-32,len(disabled)))
    differences = [i for i,(a,b) in enumerate(zip(reference,disabled)) if a != b]
    if not set(differences) <= allowed: raise ValueError('disabled code or data changed')
    if [row['enabled'] for row in after['builds']] != [False,True]: raise ValueError('off/on builds required')
    for row in after['builds']:
        enabled = row['enabled']
        if (row['printf_portability']['status'] != 'pass' or row['expression_adapter_present'] != enabled
            or row['expression_schema_present'] != enabled or not all(row['protected_matches'].values())
            or not row['bootloader_configuration_equal'] or row['app_headroom_bytes'] < 1048576
            or row['neutral_request_enabled'] != enabled or row['display_store_bytes'] != 8):
            raise ValueError('build checks differ')
        if enabled and not (0 < row['control_store_bytes'] <= 12000 and 0 < row['input_workspace_bytes'] <= 2200):
            raise ValueError('expression state bounds')
        if not enabled and (row['control_store_bytes'] or row['input_workspace_bytes']): raise ValueError('off state retained')
        for name,item in row['artifacts'].items():
            if record(ROOT/row['build_directory']/name) != item: raise ValueError('saved output differs: '+name)
        if row['config_delta'] != {'DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE': {'before':None,'after':enabled}}:
            raise ValueError('configuration scope differs')
    catalog = json.loads((ROOT/'artifacts/deskbot-studio-e2c-20261104/expression-catalog.json').read_bytes())
    validate_catalog(catalog)
    header = (ROOT/'firmware/body/main/boards/esp_vocat/studio_expression_request.h').read_text(encoding='utf-8')
    if catalog['catalog_id'] not in header or catalog['role_id'] not in header: raise ValueError('catalog binding differs')
    neutral = next(row for row in catalog['expressions'] if row['semantic_id']=='neutral')
    if neutral['resource_id'] != 'winking.eaf' or neutral['implicit_action_id'] is not None:
        raise ValueError('neutral expression has changed')
    return {
        'schema':'deskbot.studio_expression_request_audit/v1','edition':'E3c-5','result':'passed',
        'source_delta':changed,'source_files_before':len(old),'source_files_after':len(new),
        'disabled_comparison':{'reference':'E3c-4 display-on','bytes':len(disabled),'differing_bytes':len(differences),
            'elf_hash_range':[elf_hash,elf_hash+32],'only_elf_hash_and_image_checksums_changed':True,
            'executable_and_other_data_equal':True},
        'protected_artifacts_equal':True,'owner_application_ui_player_unchanged':True,
        'new_tasks':0,'scheduled_callback_limit':1,'terminal_mailbox_limit':1,'dedup_entries':64,
        'sleep_state':'unknown','receipt_evidence':'submitted_to_player_api','neutral_resource':neutral['resource_id'],
        'neutral_implicit_action':None,'firmware_fingerprint':'mbedtls_sha256',
        'device_written':False,'hardware_accessed':False,'hardware_acceptance':'pending',
    }


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--check',action='store_true'); args=parser.parse_args()
    raw=json_bytes(audit()); path=PUBLIC/'firmware-audit.json'
    if args.check:
        if path.read_bytes()!=raw: raise ValueError('saved audit differs')
    else: path.write_bytes(raw)
    print('E3c-5 audit passed; disabled executable/data match E3c-4')
