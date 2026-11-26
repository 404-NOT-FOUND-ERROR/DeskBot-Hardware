"""Prepare, apply or restore the local body HTTPS pair through a fitted USB device."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from urllib.request import Request, build_opener, ProxyHandler
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import body_credentials as keys
from protocol import device_link, studio_usb_session as session

FIELDS = ('touch_wifi_config', 'expression_wifi_config')
SCHEMAS = ('deskbot.head-touch-wifi-config.v1', 'deskbot.expression-wifi-config.v1')


def sibling(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save_json(path, value):
    # Keep both receiver paths in one atomic runtime or journal replacement.
    with NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        scratch = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            scratch.unlink()
            raise
    try:
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


def pair(runtime):
    rows, parts, digest = [], [], hashlib.sha256()
    for field, schema in zip(FIELDS, SCHEMAS):
        path = Path(runtime[field])
        if not path.is_absolute():
            raise ValueError('absolute_receiver_config_required')
        row = read_json(path)
        if (row.get('schema') != schema or not session.DEVICE.fullmatch(row.get('device_id', ''))
                or not re.fullmatch('[a-f0-9]{64}', row.get('firmware_sha256') or '')
                or type(row.get('port')) is not int or not 1 <= row['port'] <= 65535):
            raise ValueError('invalid_receiver_binding')
        rows.append(row)
        digest.update(path.read_bytes())
        credentials = [(path.parent / row[name]).read_bytes() for name in ('token_file', 'cert_file', 'key_file')]
        for part in credentials:
            digest.update(len(part).to_bytes(8, 'big'))
            digest.update(part)
        parts.append((credentials[0].decode('ascii').strip(), credentials[1].decode('ascii')))
    if any(rows[0][field] != rows[1][field] for field in ('device_id', 'firmware_sha256', 'host')) \
            or rows[0]['port'] == rows[1]['port']:
        raise ValueError('receiver_pair_mismatch')
    return rows, parts, digest.hexdigest()


def bundle(parts):
    return keys.encode_bundle(parts[0][0], parts[1][0], parts[0][1], parts[1][1])


def prepare(runtime_path, directory):
    before = read_json(runtime_path)
    rows, parts, before_sha = pair(before)
    before_id = keys.bundle_id(bundle(parts))
    generate = sibling('prepare-head-touch-wifi').prepare
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    after = dict(before)
    for field, label, row in zip(FIELDS, ('touch', 'expression'), rows):
        output = directory / label
        generate(output, row['host'], row['port'], lab=row.get('lab', False))
        # The installed App keeps the existing TLS server name. Its generated
        # header is unnecessary for a USB renewal.
        (output / 'head_touch_wifi.local.h').unlink()
        destination = output / 'receiver.json'
        row.update(token_file='device-token.txt', cert_file='server-cert.pem', key_file='server-key.pem')
        save_json(destination, row)
        after[field] = str(destination)
    _, parts, after_sha = pair(after)
    after_id = keys.bundle_id(bundle(parts))
    plan_path = directory / 'plan.json'
    save_json(plan_path, dict(schema='deskbot.body_credential_renewal/v1', runtime=str(runtime_path),
        before=before, after=after, before_id=before_id, after_id=after_id, before_sha256=before_sha,
        after_sha256=after_sha))
    return {'state': 'prepared', 'plan': str(plan_path)}


NetworkMaintenance = sibling('configure-body-network').Maintenance


class KeyMaintenance(NetworkMaintenance):
    REPLY_SCHEMA = keys.SCHEMA

    def start(self):
        deadline = self.clock() + 15000
        while self.clock() < deadline:
            row = self.receive(deadline)
            if row['schema'] == device_link.SCHEMA:
                self.hello = row
                self.client = keys.Client(row['device_id'], row['boot_id'], self.nonce)
                self.exchange('open')
                return
        raise TimeoutError('body_credentials_hello_timeout')

    def trial(self, raw, active):
        identifier = keys.bundle_id(raw)
        for operation in keys.upload(raw, active):
            result = self.exchange(operation)
            if result['result'] not in ('staging', 'staged') or result['active'] != active:
                raise ValueError('credential_upload_' + result['result'])
        if result['result'] != 'staged' or result['candidate'] != identifier:
            raise ValueError('credential_upload_readback_mismatch')
        self.wait_for_probe('key-test', active, identifier)

    def check(self, active):
        return self.wait_for_probe('key-check', active, '')

    def wait_for_probe(self, operation, active, candidate):
        queued = self.exchange(operation)
        if queued['result'] != 'testing' or queued['active'] != active or queued['candidate'] != candidate:
            raise ValueError('credential_probe_not_started')
        deadline = self.clock() + 14000
        while self.clock() < deadline:
            result = self.exchange('key-get')
            if result['candidate'] != candidate or result['active'] != active:
                raise ValueError('credential_candidate_changed')
            if result['trial'] == 'passed':
                return result
            if result['trial'] == 'failed':
                raise ValueError('credential_probe_failed')
        raise TimeoutError('credential_probe_timeout')


class USBDevice:
    def __init__(self, transport, binding):
        self.transport, self.binding = transport, binding

    @contextmanager
    def session(self):
        client = KeyMaintenance(self.transport, self.binding)
        try:
            client.start()
            yield client
        finally:
            try:
                client.close()
            except (OSError, TimeoutError, ValueError):
                pass

    def address(self):
        return NetworkMaintenance(self.transport, self.binding).run()['settings']


class RecoveryNeeded(RuntimeError):
    pass


def apply(plan_path, direction, device, restart):
    plan = read_json(plan_path)
    runtime_path = Path(plan['runtime'])
    before, after = plan['before'], plan['after']
    current = read_json(runtime_path)
    if current not in (before, after) or any(before.get(key) != after.get(key)
            for key in set(before) | set(after) if key not in FIELDS):
        raise ValueError('runtime_changed_since_prepare')
    choices = {}
    for side, config in (('before', before), ('after', after)):
        rows, parts, digest = pair(config)
        if digest != plan[side + '_sha256']:
            raise ValueError('receiver_files_changed_since_prepare')
        raw = bundle(parts)
        if keys.bundle_id(raw) != plan[side + '_id']:
            raise ValueError('invalid_renewal_plan_binding')
        choices[side] = (rows, raw)
    left, right = (choices[side][0] for side in ('before', 'after'))
    if any({k: v for k, v in a.items() if k not in ('token_file', 'cert_file', 'key_file')} !=
           {k: v for k, v in b.items() if k not in ('token_file', 'cert_file', 'key_file')} for a, b in zip(left, right)):
        raise ValueError('receiver_binding_changed_since_prepare')
    with device.session() as usb:
        status = usb.exchange('key-get')
        active = status['active']
        if status['storage'] in ('read_failed', 'write_uncertain'):
            raise RecoveryNeeded('read_back_credentials_after_device_restart')
    address = device.address()
    for field, expected in (('host', left[0]['host']), ('touch_port', left[0]['port']), ('expression_port', left[1]['port'])):
        if address[field] != expected or address['applied_revision'] != address['revision']:
            raise ValueError('device_endpoints_must_match_receiver_pair')
    owner = ('after' if active == plan['after_id'] else 'before'
             if active == plan['before_id'] else None)
    if owner is None:
        raise RecoveryNeeded('device_pair_mismatch_resume_previous_plan')

    raw = choices[direction][1]
    target = keys.bundle_id(raw)
    possible_save = False

    def select(side):
        selected = read_json(runtime_path)
        if selected not in (before, after):
            raise ValueError('runtime_changed_during_renewal')
        if selected != plan[side]:
            save_json(runtime_path, plan[side])
        restart(runtime_path)

    try:
        select(direction)
        with device.session() as usb:
            status = usb.exchange('key-get')
            if status['active'] != active or status['storage'] in ('read_failed', 'write_uncertain'):
                possible_save = True
                raise RecoveryNeeded('credential_state_changed_after_service_restart')
            if owner != direction:
                usb.trial(raw, active)
                possible_save = True  # A lost reply can follow a durable commit.
                result = usb.exchange('key-save:' + target)
                if result['result'] in ('save_failed', 'conflict', 'not_verified'):
                    possible_save = False
                if result['result'] not in ('saved', 'unchanged') or result['active'] != target:
                    raise RecoveryNeeded('credential_commit_' + result['result'])
                status = usb.exchange('key-get')
                if status['active'] != target or status['storage'] not in ('saved', 'restored'):
                    raise RecoveryNeeded('credential_readback_mismatch')
            verified = usb.check(status['active'])
        return {'state': 'current', 'active': verified['active'], 'storage': verified['storage'],
                'direction': direction, 'https_channels_verified': 2, 'live_gesture_and_playback': 'verify_separately'}
    except (OSError, TimeoutError, ValueError, RuntimeError) as error:
        if not possible_save and owner != direction:
            try:
                select(owner)
            except (OSError, TimeoutError, ValueError, RuntimeError):
                raise RecoveryNeeded('reconnect_usb_and_resume_saved_plan') from None
            try:
                with device.session() as usb:
                    usb.check(active)
            except (OSError, TimeoutError, ValueError, RuntimeError):
                raise RecoveryNeeded('previous_receivers_selected_but_unreachable') from None
            raise RecoveryNeeded('previous_receivers_restored') from error
        raise RecoveryNeeded('reconnect_usb_and_resume_saved_plan') from error


def launcher(client_root):
    script = client_root / 'scripts/companion-resident.mjs'
    node = shutil.which('node.exe')
    if not script.is_file() or node is None:
        raise ValueError('companion_launcher_unavailable')
    local = build_opener(ProxyHandler({}))

    def control(action, runtime):
        try:
            result = subprocess.run([node, str(script), action, str(runtime)],
                                    capture_output=True, timeout=8, check=True)
            return json.loads(result.stdout)
        except (subprocess.SubprocessError, ValueError):
            raise RuntimeError('companion_control_unavailable') from None

    def get(port, name):
        request = Request(f'http://127.0.0.1:{port}/api/companion/{name}',
                          headers={'X-DeskBot-Client': 'companion'})
        with local.open(request, timeout=3) as response:
            return json.load(response)

    def restart(runtime):
        old = control('state', runtime)
        port = read_json(runtime).get('port', 4311)
        control('restart', runtime)
        deadline = time.monotonic() + 245
        while time.monotonic() < deadline:
            state = control('state', runtime)
            if state['phase'] in ('port_conflict', 'identity_conflict'):
                raise RuntimeError('companion_restart_failed')
            if state['configuration_current'] and state['phase'] == 'ready' and state['instance_id'] != old['instance_id']:
                try:
                    reported = get(port, 'state')['runtime']
                    listeners = get(port, 'body')['listeners']
                except (OSError, ValueError, KeyError):
                    pass
                else:
                    if (reported['config_id'] == state['config_id'] and reported['instance_id'] == state['instance_id']
                            and reported['pid'] == state['service_pid'] and listeners['touch'] and listeners['expression']):
                        return
            time.sleep(0.5)
        raise TimeoutError('companion_restart_timeout')
    return restart


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    plan = actions.add_parser('prepare')
    plan.add_argument('--runtime', type=Path, required=True)
    for name in ('apply', 'restore', 'status'):
        action = actions.add_parser(name)
        action.add_argument('--plan', required=True, type=Path)
        action.add_argument('--port', required=True)
        if name != 'status':
            action.add_argument('--client-root', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        root = Path(os.environ['LOCALAPPDATA']) / 'DeskBot' / 'companion' / 'credential-renewals'
        return prepare(args.runtime.resolve(), (root / uuid.uuid4().hex).resolve())
    if not re.fullmatch(r'COM[1-9][0-9]*', args.port):
        raise ValueError('explicit_windows_serial_port_required')
    plan_path = args.plan.resolve()
    plan = read_json(plan_path)
    if plan.get('schema') != 'deskbot.body_credential_renewal/v1':
        raise ValueError('invalid_renewal_plan')
    rows, _, _ = pair(plan['before'])
    binding = {field: rows[0][field] for field in ('device_id', 'firmware_sha256')}
    restart = launcher(args.client_root.resolve()) if args.action != 'status' else None
    import serial
    with serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=7,
                       rtscts=False, dsrdtr=False, exclusive=True) as port:
        port.dtr = port.rts = False
        port.port = args.port
        port.open()
        device = USBDevice(port, binding)
        if args.action == 'status':
            with device.session() as usb:
                state = usb.exchange('key-get')
                return {field: state[field] for field in ('storage', 'active', 'trial')}
        return apply(plan_path, 'after' if args.action == 'apply' else 'before', device, restart)


if __name__ == '__main__':
    try:
        print(json.dumps(main()))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        # Whitelist stable error codes, never echo file contents or USB data.
        code = str(error)
        if not re.fullmatch('[a-z0-9_]+', code):
            code = type(error).__name__
        print(json.dumps({'state': 'interrupted', 'reason': code}), file=sys.stderr)
        sys.exit(1)
