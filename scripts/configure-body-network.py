"""Preview or apply body service addresses through the existing USB session."""
import argparse
import json
from pathlib import Path
import re
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import body_network_settings as network
from protocol import device_link, studio_usb_session as session
from protocol.studio_timing import REQUEST_TIMEOUT_MS


def binding(profile):
    config = json.loads((profile / 'receiver.json').read_bytes())
    report = json.loads((profile / 'build/report.json').read_bytes())
    if (config.get('schema') != 'deskbot.expression-wifi-config.v1'
            or report.get('edition') != 'E3f-9d.7-network-settings' or report.get('network_settings') is not True
            or not session.DEVICE.fullmatch(config.get('device_id', ''))
            or not re.fullmatch('[a-f0-9]{64}', config.get('firmware_sha256') or '')
            or config['firmware_sha256'] != report.get('app_elf_sha256')):
        raise ValueError('network_settings_build_binding_required')
    return dict(device_id=config['device_id'], firmware_sha256=config['firmware_sha256'])


class Maintenance:
    """Caller owns the port; no resets, discovery writes or service management."""
    REPLY_SCHEMA = network.SCHEMA
    def __init__(self, transport, expected, *, clock=None, sleep=time.sleep, nonce=None):
        self.transport, self.expected = transport, expected
        self.clock = clock or (lambda: int(time.monotonic() * 1000))
        self.sleep, self.nonce = sleep, nonce or ('network-' + secrets.token_hex(12))
        self.client, self.hello, self.last_sent = None, None, None

    def check_hello(self, row):
        row = device_link.validate_hello(row)
        if (row['device_id'] != self.expected['device_id']
                or row['firmware']['app_elf_sha256'] != self.expected['firmware_sha256']
                or row['hardware']['flash_bytes'] != 32 * 1024 * 1024
                or row['hardware']['psram_bytes'] != 16 * 1024 * 1024):
            raise ValueError('connected_device_or_build_mismatch')
        if self.hello and row['boot_id'] != self.hello['boot_id']:
            if self.client:
                self.client.disconnect()
            raise ValueError('device_restarted_during_settings')
        return row

    def receive(self, deadline):
        while self.clock() < deadline:
            raw = self.transport.read_until(b'\n', size=4096)
            raw = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', raw)
            if not raw.endswith(b'\n') or b'{' not in raw:
                continue
            try:
                row = json.loads(raw[raw.index(b'{'):].decode('utf-8'), object_pairs_hook=session._unique)
            except (ValueError, UnicodeError):
                continue
            if type(row) is dict:
                if row.get('schema') == device_link.SCHEMA:
                    return self.check_hello(row)
                if row.get('schema') in (session.SCHEMA, self.REPLY_SCHEMA):
                    return row
        raise TimeoutError('body_network_reply_timeout')

    def exchange(self, operation):
        if self.last_sent is not None:
            remaining = 300 - (self.clock() - self.last_sent)
            if remaining > 0:
                self.sleep(remaining / 1000)
        now = self.clock()
        wire = self.client.request(operation, now)
        self.last_sent = now
        if self.transport.write(wire) != len(wire):
            raise OSError('incomplete_usb_write')
        deadline = now + REQUEST_TIMEOUT_MS
        request_id = self.client._pending['request_id']
        while self.clock() < deadline:
            row = self.receive(deadline)
            if row.get('schema') == device_link.SCHEMA:
                continue  # A periodic hello confirms the same boot while waiting.
            if row.get('request_id') != request_id:
                continue
            result = self.client.accept(row, self.clock())
            if result['schema'] == session.SCHEMA and result['state'] != 'ready':
                raise ValueError('body_network_session_' + result['reason'])
            return result
        raise TimeoutError('body_network_reply_timeout')

    def close(self):
        if not self.client or not self.client.session_id or not self.client.connected:
            return
        # Best effort release, including on a failed save/readback. The normal
        # session timeout also releases ownership if the cable has gone away.
        if self.last_sent is not None:
            remaining = 300 - (self.clock() - self.last_sent)
            if remaining > 0:
                self.sleep(remaining / 1000)
        wire = session.encode_request(operation='close', request_id='close-' + self.nonce,
            target_id=self.client.target_id, boot_id=self.client.boot_id,
            client_nonce=self.nonce, session_id=self.client.session_id)
        try:
            if self.transport.write(wire) != len(wire):
                raise OSError('incomplete_usb_write')
            deadline = self.clock() + REQUEST_TIMEOUT_MS
            while self.clock() < deadline:
                row = self.receive(deadline)
                if row.get('request_id') != 'close-' + self.nonce:
                    continue
                row = session.validate_reply(row)
                if (row['target_id'] != self.client.target_id or row['boot_id'] != self.client.boot_id
                        or row['session_id'] != self.client.session_id or row['state'] != 'closed'
                        or row['reason'] != 'session_closed' or row['sequence'] <= self.client._sequence):
                    raise ValueError('body_network_close_mismatch')
                return
        finally:
            self.client.disconnect()

    def run(self, action='get', *, host=None, touch_port=4318, expression_port=4319, expected_revision=None):
        # Validate before even opening a diagnostic session.
        if expected_revision is not None and (type(expected_revision) is not int or not 0 <= expected_revision <= 0xffffffff):
            raise ValueError('invalid_network_revision')
        network.operation(action, expected=0, host=host, touch_port=touch_port, expression_port=expression_port)
        deadline = self.clock() + 15000
        while self.clock() < deadline:
            row = self.receive(deadline)
            if row.get('schema') == device_link.SCHEMA:
                self.hello = row
                break
        if self.hello is None:
            raise TimeoutError('body_network_hello_timeout')
        self.client = network.Client(self.hello['device_id'], self.hello['boot_id'], self.nonce)
        try:
            self.exchange('open')
            before = self.exchange('net-get')
            if expected_revision is not None and before['revision'] != expected_revision:
                raise ValueError('body_network_conflict')
            result, current = 'read', before
            if action != 'get':
                operation = network.operation(action, expected=before['revision'], host=host,
                    touch_port=touch_port, expression_port=expression_port)
                changed = self.exchange(operation)
                if changed['result'] not in ('saved', 'unchanged'):
                    raise ValueError('body_network_' + changed['result'])
                result = changed['result']
                current = self.exchange('net-get')
                fields = ('host', 'touch_port', 'expression_port', 'revision', 'source')
                if any(current[key] != changed[key] for key in fields):
                    raise ValueError('body_network_readback_changed')
                if action == 'set' and (current['host'], current['touch_port'], current['expression_port']) != (host, touch_port, expression_port):
                    raise ValueError('body_network_readback_mismatch')
                # A slow HTTPS request can take 3.5 s before the worker adopts
                # a saved revision. This bounded wait does not claim delivery.
                apply_deadline = self.clock() + 6000
                while current['applied_revision'] != current['revision'] and self.clock() < apply_deadline:
                    current = self.exchange('net-get')
                    if any(current[key] != changed[key] for key in fields):
                        raise ValueError('body_network_readback_changed')
            return dict(schema='deskbot.body_network_maintenance/v1', result=result,
                device_id=self.hello['device_id'], firmware_sha256=self.expected['firmware_sha256'],
                settings=current, worker_applied=current['applied_revision'] == current['revision'],
                https_delivery='pending', restarted=False)
        finally:
            try:
                self.close()
            except (OSError, ValueError, TimeoutError):
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('get', 'set', 'reset'), nargs='?', default='get')
    parser.add_argument('--profile', type=Path, required=True, help='built E7 expression profile')
    parser.add_argument('--port', help='explicit USB port; omit to preview without hardware access')
    parser.add_argument('--host')
    parser.add_argument('--touch-port', type=int, default=4318)
    parser.add_argument('--expression-port', type=int, default=4319)
    parser.add_argument('--expected-revision', type=int)
    args = parser.parse_args()
    expected = binding(args.profile.resolve())
    network.operation(args.action, expected=0, host=args.host, touch_port=args.touch_port, expression_port=args.expression_port)
    if args.expected_revision is not None and not 0 <= args.expected_revision <= 0xffffffff:
        raise ValueError('invalid_network_revision')
    if not args.port:
        return dict(schema='deskbot.body_network_maintenance/v1', mode='preview', hardware_accessed=False,
            binding=expected, action=args.action, host=args.host, touch_port=args.touch_port,
            expression_port=args.expression_port, revision='read_before_save')
    if not re.fullmatch(r'COM[1-9][0-9]*', args.port):
        raise ValueError('explicit_windows_serial_port_required')
    import serial
    port = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=7,
                         rtscts=False, dsrdtr=False, exclusive=True)
    port.dtr = False
    port.rts = False
    port.port = args.port
    try:
        port.open()  # Windows opens exclusively; the workbench releases it first.
        return Maintenance(port, expected).run(args.action, host=args.host,
            touch_port=args.touch_port, expression_port=args.expression_port, expected_revision=args.expected_revision)
    finally:
        port.close()


if __name__ == '__main__':
    print(json.dumps(main()), flush=True)
