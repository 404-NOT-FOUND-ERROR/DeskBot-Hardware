#!/usr/bin/env python3
"""Audit the bounded drain against the retained E3c-7 source and builds."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_capacity import image_facts
from protocol.studio_resource_package import json_bytes, load_target

PUBLIC = ROOT / 'artifacts/deskbot-studio-e3c8-20261105'
SOURCE = 'main/boards/esp_vocat/studio_usb_diagnostic.cc'


def record(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def audit():
    prior_path = ROOT / 'artifacts/deskbot-studio-e3c7-20261105/firmware-build-report.json'
    before = json.loads(prior_path.read_bytes())
    after = json.loads((PUBLIC / 'firmware-build-report.json').read_bytes())
    old, new = before['source_sha256'], after['source_sha256']
    if (after['edition'] != 'E3c-8' or after['prior_report'] != record(prior_path)
            or after['target'] != load_target()
            or {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)} != {SOURCE}):
        raise ValueError('single USB task delta required')
    for name, digest in new.items():
        if record(ROOT / 'firmware/body' / name)['sha256'] != digest:
            raise ValueError('current source differs: ' + name)
    previous = ROOT / 'tmp/e3c7-usb-secondary-20261105/p' / SOURCE
    if record(previous)['sha256'] != old[SOURCE]:
        raise ValueError('retained USB source differs')
    old_block = '''            const auto count = read(fd, input, sizeof(input));
            for (ssize_t i = 0; i < count; ++i) {
                if (endpoint.Feed(input[i], now, reply, sizeof(reply))) {
                    ESP_LOGI("DeskBotSession", "%s", reply);
                }
            }'''
    new_block = '''            // IDF's basic VFS can return one byte even with a larger buffer.
            // Drain a bounded batch before yielding, including under flood.
            for (size_t read_count = 0, received = 0;
                 read_count < 512 && received < 512; ++read_count) {
                const size_t room = 512 - received;
                const auto count = read(fd, input, room < sizeof(input) ? room : sizeof(input));
                if (count <= 0) break;
                received += static_cast<size_t>(count);
                for (ssize_t i = 0; i < count; ++i) {
                    if (endpoint.Feed(input[i], now, reply, sizeof(reply))) {
                        ESP_LOGI("DeskBotSession", "%s", reply);
                    }
                }
            }'''
    original = previous.read_text(encoding='utf-8')
    if original.count(old_block) != 1 or (ROOT / 'firmware/body' / SOURCE).read_text(encoding='utf-8') != original.replace(old_block, new_block):
        raise ValueError('unexpected USB task changes')
    if [row['enabled'] for row in after['builds']] != [False, True]:
        raise ValueError('both configurations required')
    rows = []
    for row in after['builds']:
        prior = next(item for item in before['builds'] if item['enabled'] == row['enabled'])
        folder, previous_folder = ROOT / row['build_directory'], ROOT / prior['build_directory']
        for name, expected in row['artifacts'].items():
            if record(folder / name) != expected:
                raise ValueError('build output differs: ' + name)
        for name in ('bootloader/bootloader.bin', 'partition_table/partition-table.bin', 'srmodels/srmodels.bin', 'ota_data_initial.bin'):
            if record(folder / name) != record(previous_folder / name):
                raise ValueError('protected output differs: ' + name)
        if json.loads((folder / 'config/sdkconfig.json').read_bytes()) != json.loads((previous_folder / 'config/sdkconfig.json').read_bytes()):
            raise ValueError('configuration changed')
        if (image_facts((folder / 'xiaozhi.bin').read_bytes())['configured_flash_bytes'] != 33554432
                or row['app_headroom_bytes'] < 1048576 or not all(row['protected_matches'].values())
                or not row['console_topology_unchanged'] or row['printf_portability']['status'] != 'pass'):
            raise ValueError('build checks failed')
        rows.append({'enabled': row['enabled'], 'app': row['artifacts']['xiaozhi.bin'],
                     'headroom_bytes': row['app_headroom_bytes']})
    return {'schema': 'deskbot.studio_usb_drain_audit/v1', 'edition': 'E3c-8', 'result': 'passed',
        'source_delta': [SOURCE], 'max_read_calls_per_tick': 512, 'max_input_bytes_per_tick': 512,
        'task_delay_ms': 10, 'new_tasks': 0, 'state_valid_ms': 2000,
        'protected_outputs_equal': True, 'configurations_unchanged': True,
        'original_application_ui_player_unchanged': True, 'builds': rows,
        'hardware_accessed': False, 'hardware_acceptance': 'pending'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    raw = json_bytes(audit())
    path = PUBLIC / 'firmware-audit.json'
    if args.check:
        if path.read_bytes() != raw:
            raise ValueError('saved audit differs')
    else:
        path.write_bytes(raw)
    print('E3c-8 bounded USB drain, configurations and protected outputs verified')
