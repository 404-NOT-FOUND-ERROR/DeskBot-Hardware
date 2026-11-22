"""Paired App/resource update with a verified same-run backup and paired recovery."""
from pathlib import Path
import json

from protocol import studio_app_deployment as app
from protocol import studio_resource_package as assets

ROOT = Path(__file__).resolve().parents[1]
START, SIZE = 0x1000000, 0x800000
IDENTITY = '1232890adbc14b7a1b677968e67e0eab8df37784762209cc69987d1c98e938e2'


def erase_bytes(original_size, candidate_size):
    size = (max(original_size, candidate_size)+4095)//4096*4096
    if not 0 < size <= app.PARTITION:
        raise ValueError('app_partition_exceeded')
    return size


def prepare(snapshot, candidate, resources, pins, resource_report):
    if len(snapshot) != app.FLASH or app.sha(snapshot[:0x9000]+snapshot[0xD000:]) != pins['original_static_sha256']:
        raise ValueError('installed_baseline_changed')
    if app.sha(snapshot[0x8000:0x8c00]) != assets.TABLE_SHA:
        raise ValueError('partition_table_changed')
    if (app.sha(candidate) != pins['candidate_sha256'] or len(candidate) != pins['candidate_bytes']
            or len(resources) != SIZE or app.sha(resources) != resource_report['image_sha256']
            or pins['identity_sha256'] != IDENTITY):
        raise ValueError('reviewed_payload_changed')
    size = erase_bytes(pins['original_bytes'], len(candidate))
    payload = candidate + snapshot[app.START+len(candidate):app.START+size]
    return dict(schema='deskbot.airboat_role_installation/v1', identity_sha256=IDENTITY,
        source_commit=pins.get('source_commit','0d087e8dfe2925c96374a5bc07c3cebf9ae5a258'),
        snapshot_sha256=app.sha(snapshot), app_elf_sha256=pins['app_elf_sha256'],
        original_app_elf_sha256=pins['original_app_elf_sha256'],
        graph_sha256=resource_report['download_sha256']['deskbot-roles.json'],
        targets=[dict(offset=app.START, bytes=size, file='candidate-app.bin', sha256=app.sha(payload),
                      rollback='rollback-app.bin', rollback_sha256=app.sha(snapshot[app.START:app.START+size])),
                 dict(offset=START, bytes=SIZE, file='candidate-assets.bin', sha256=app.sha(resources),
                      rollback='rollback-assets.bin', rollback_sha256=app.sha(snapshot[START:START+SIZE]))])


def validate(plan, snapshot, payloads=None):
    if (plan['schema'] != 'deskbot.airboat_role_installation/v1' or plan['identity_sha256'] != IDENTITY
            or len(snapshot) != app.FLASH or app.sha(snapshot) != plan['snapshot_sha256']
            or len(plan['targets']) != 2):
        raise ValueError('installation_plan_changed')
    a,b = plan['targets']
    if (a['offset'] != app.START or not 0 < a['bytes'] <= app.PARTITION or a['bytes']%4096
            or (b['offset'],b['bytes']) != (START,SIZE)
            or [(t['file'],t['rollback']) for t in (a,b)] !=
               [('candidate-app.bin','rollback-app.bin'),('candidate-assets.bin','rollback-assets.bin')]):
        raise ValueError('installation_ranges_changed')
    for target in plan['targets']:
        offset,size = target['offset'],target['bytes']
        if app.sha(snapshot[offset:offset+size]) != target['rollback_sha256']:
            raise ValueError('recovery_bytes_changed')
        if payloads is not None:
            raw = payloads[target['file']]
            if len(raw) != size or app.sha(raw) != target['sha256']:
                raise ValueError('installation_payload_changed')


def patched(plan, before, payloads):
    raw = bytearray(before)
    for target in plan['targets']:
        offset = target['offset']
        raw[offset:offset+target['bytes']] = payloads[target['file']]
    return bytes(raw)


def outside_equal(plan, before, after):
    # Runtime may update NVS after a normal boot. Recovery preserves its latest
    # bytes; every other byte outside the two explicit targets must match.
    a = bytearray(before); b = bytearray(after)
    for offset,size in [(0x9000,0x4000),*[(t['offset'],t['bytes']) for t in plan['targets']]]:
        a[offset:offset+size] = b'\0'*size
        b[offset:offset+size] = b'\0'*size
    return a == b


def execute(folder, device, *, recover=False):
    folder = Path(folder)
    raw = (folder/'plan.json').read_bytes()
    plan = json.loads(raw)
    snapshot = (folder/'snapshot-32m.bin').read_bytes()
    payloads = {t['file']:(snapshot[t['offset']:t['offset']+t['bytes']] if recover
                           else (folder/t['file']).read_bytes()) for t in plan['targets']}
    validate(plan, snapshot, None if recover else payloads)
    if recover and not (folder/'state.json').exists():
        raise ValueError('write_intent_required_for_recovery')
    device.arm(plan, payloads)
    try:
        before = device.read_verified(snapshot, targets=recover)
        if (not outside_equal(plan,snapshot,before) or not recover and
                before[:0x9000]+before[0xD000:] != snapshot[:0x9000]+snapshot[0xD000:]):
            raise ValueError('device_baseline_changed')
    except BaseException:
        # A read-only preflight also enters the ROM loader. Return the retained
        # App to normal use when no new write intent has been recorded.
        if not recover:device.boot(plan['original_app_elf_sha256'])
        raise
    expected = patched(plan,before,payloads)
    state = dict(plan_sha256=app.sha(raw), phase='restore_intent' if recover else 'write_intent')
    save = lambda: atomic(folder/'state.json',state)
    save()
    try:
        device.write(payloads)
        after = device.read_verified(expected, targets=True)
        if after != expected:
            raise ValueError('target_readback_changed')
        state['phase'] = 'candidate_verified' if not recover else 'restore_verified'; save()
        boot = device.boot(plan['original_app_elf_sha256'] if recover else plan['app_elf_sha256'])
        if not boot:
            raise ValueError('boot_incomplete')
        state.update(phase='restored' if recover else 'installed', readback_equal=True,
            outside_targets_equal=outside_equal(plan,before,after), nvs_equal=before[0x9000:0xD000]==after[0x9000:0xD000],
            boot_verified=True, hardware_acceptance='pending')
        save()
    except BaseException:
        state['phase']='needs_recovery';save()
        raise
    return state


def atomic(path,value):
    from protocol.role_activation import atomic_save
    atomic_save(path,value)
