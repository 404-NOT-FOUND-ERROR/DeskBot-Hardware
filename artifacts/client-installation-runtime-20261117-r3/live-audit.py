"""Read-only live checks and a consistent online SQLite evidence backup."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = Path(__file__).resolve().parent
DEVICE = 'device-9046b3127b3c912b392774b8ffadd819'
IDENTITY = dict(device_id=DEVICE, connection_id='connection-1856130e068346788cf9b4a0e1ddedf7',
    boot_id='boot-6fb6f62014c461d2957c99b6d53ff18f',
    session_id='session-6fb6f62014c461d2957c99b6d53ff18f-1')
opener = build_opener(ProxyHandler({}))


def get(url):
    with opener.open(url, timeout=8) as response:
        return json.load(response)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def rows(path):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
        return {(ns, key): raw for ns, key, raw in connection.execute(
            'select namespace,record_id,payload_json from records order by namespace,record_id')}


def changed_paths(before, after, path=''):
    if type(before) is not type(after):
        return [path]
    if type(before) is dict:
        return [item for key in set(before) | set(after) for item in (
            [path + '/' + key] if key not in before or key not in after else
            changed_paths(before[key], after[key], path + '/' + key))]
    return [] if before == after else [path]


def main(label):
    output = ARCHIVE / label
    output.mkdir(exist_ok=False)
    values = dict(
        world=get('http://127.0.0.1:4314/api/world/state'),
        devices=get('http://127.0.0.1:4314/api/devices'),
        presentation=get('http://127.0.0.1:8780/api/client-presentation/state'),
        installation=get('http://127.0.0.1:8782/api/shell-installation/state'),
        received=get('http://127.0.0.1:4314/api/hardware/installation?character_id=shaping-001&device_id=' + DEVICE),
    )
    for name, value in values.items():
        save(output / (name + '.json'), value)
    database = ROOT / 'tmp/client-expression-4b1b/client.sqlite'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(output / 'client.sqlite') as target:
            source.backup(target)
            integrity = target.execute('pragma integrity_check').fetchone()[0]
    original = rows(ARCHIVE / 'before/client.sqlite')
    current = rows(output / 'client.sqlite')
    checks = dict(sqlite_integrity=integrity == 'ok', original_rows_retained=set(original) <= set(current))
    permitted = {
        'canonical-world.states': {'/logical_time/day', '/logical_time/minute_of_day', '/logical_time/tick', '/world_revision', '/updated_at'},
        'canonical-world.wall-clock': {'/last_sync_at', '/last_wall_at'},
        'devices.records': {'/last_seen_at'},
    }
    changes = []
    for key, raw in original.items():
        if current.get(key) != raw:
            paths = sorted(changed_paths(json.loads(raw), json.loads(current[key])))
            allowed = key[0] in permitted and set(paths) <= permitted[key[0]]
            changes.append(dict(namespace=key[0], record_id=key[1], paths=paths, expected=allowed))
    added = [(key, json.loads(raw)) for key, raw in current.items() if key not in original]
    mutations = [value for (ns, _), value in added if ns == 'canonical-world.mutations']
    checks['existing_records_only_clock_and_heartbeat_changes'] = all(item['expected'] for item in changes)
    checks['new_records_expected_namespaces'] = all(key[0] in {
        'canonical-world.mutations', 'canonical-world.checkpoints', 'hardware.installations',
        'hardware.current-installation'} for key, _ in added)
    checks['new_world_mutations_are_original_clock'] = all(
        item.get('event_source') == 'world-wall-clock' and item.get('action') == 'advance_time' for item in mutations)
    before_state = json.loads(original['canonical-world.states', 'deskbot-small-world'])
    after_state = json.loads(current['canonical-world.states', 'deskbot-small-world'])
    checks['same_individual_and_life_data'] = before_state['protagonist'] == after_state['protagonist']
    prior_presentation = json.loads((ARCHIVE / 'before/client-presentation.json').read_text(encoding='utf-8'))
    for key in ('inputs', 'commands', 'device_id', 'character_id'):
        checks['presentation_' + key + '_preserved'] = values['presentation'][key] == prior_presentation[key]
    initial_installation = json.loads((ARCHIVE / 'before/installation.json').read_text(encoding='utf-8'))
    installation = values['installation']
    row = next(item for item in installation['records'] if item['installation_id'] == installation['current'])
    prior_row = next(item for item in initial_installation['records'] if item['installation_id'] == initial_installation['current'])
    checks['current_installation_unchanged'] = installation['current'] == initial_installation['current'] and row == prior_row
    checks['usb_identity_preserved'] = all(installation['device'][key] == value for key, value in IDENTITY.items())
    checks['device_role_saved'] = all(installation['device'][key] == 'xiaozhi-e145' for key in ('current','actual','target','saved'))
    sync = installation['client_sync']
    original_receipt = json.loads((ARCHIVE / 'live-installation.json').read_text(encoding='utf-8'))['client_sync']['last_receipt']
    checks['same_verified_receipt'] = sync['verified'] is True and sync['status'] == 'synchronized' and sync['last_receipt'] == original_receipt
    checks['client_current_matches'] = values['received']['current']['receipt'] == original_receipt
    device = next(item for item in values['devices']['devices'] if item['device_id'] == DEVICE)
    checks['client_device_identity'] = device['character_id'] == 'shaping-001' and device['firmware'] == 'E3f-9d.3'
    report = dict(at=datetime.now(timezone.utc).isoformat(), status='passed' if all(checks.values()) else 'failed',
        checks=checks, record_changes=changes, original_records=len(original), current_records=len(current),
        added_namespaces={ns: sum(key[0] == ns for key, _ in added) for ns in sorted({key[0] for key, _ in added})},
        original_world_revision=before_state['world_revision'], current_world_revision=after_state['world_revision'],
        original_clock_mutations=len(mutations), receipt=sync['last_receipt'], usb=IDENTITY,
        installation_id=installation['current'], installation_revision=installation['revision'],
        database_sha256=hashlib.sha256((output/'client.sqlite').read_bytes()).hexdigest())
    save(output / 'report.json', report)
    print(json.dumps(dict(status=report['status'], checks=checks, original_records=len(original), current_records=len(current),
        world_revision=after_state['world_revision'], added_namespaces=report['added_namespaces']), ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
