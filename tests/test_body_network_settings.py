"""Production NVS adapter, USB endpoint and host codec in one native fixture."""
from copy import deepcopy
from collections import deque
import importlib.util
import json
from pathlib import Path
from queue import Queue
import subprocess
import tempfile
from threading import Thread
import unittest

from protocol import body_network_settings as network
from protocol import studio_usb_session as session
from tests.test_wifi_expression import compile_native, ROOT
from tests.test_device_link import fixture as hello_fixture

NVS_STUB = '''#pragma once
#include <cstddef>
#include <cstdint>
#ifndef ESP_OK
#define ESP_OK 0
#endif
#ifndef ESP_FAIL
#define ESP_FAIL -1
#endif
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define ESP_ERR_NVS_REMOVE_FAILED 0x1108
#define NVS_READONLY 0
#define NVS_READWRITE 1
using nvs_handle_t=unsigned;
int nvs_open(const char*,int,nvs_handle_t*);
int nvs_get_blob(nvs_handle_t,const char*,void*,size_t*);
int nvs_set_blob(nvs_handle_t,const char*,const void*,size_t);
int nvs_commit(nvs_handle_t);
void nvs_close(nvs_handle_t);
'''
DEVICE, BOOT = 'device-' + 'a' * 32, 'boot-' + 'b' * 32
FIRST = 'session-' + 'b' * 32 + '-1'


class NetworkSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='body-network-', dir=ROOT / 'tmp')
        cls.addClassCleanup(cls.folder.cleanup)
        folder = Path(cls.folder.name)
        (folder / 'nvs.h').write_text(NVS_STUB, encoding='utf-8')
        cls.binary = folder / 'settings.exe'
        compile_native(ROOT / 'tests/cpp/body_network_config_test.cc', cls.binary, (folder, ROOT / 'tests/cpp'))
        spec = importlib.util.spec_from_file_location('network_maintenance', ROOT / 'scripts/configure-body-network.py')
        cls.maintenance = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.maintenance)

    def maintenance_transport(self, mode=None, hello=None):
        binary = self.binary
        class Transport:
            def __init__(self):
                self.now, self.sent = 0, []
                self.rows = deque([b'noise\n', self.line(hello or hello_fixture())])
                self.process = subprocess.Popen([str(binary), '--pipe'] + (['set-uncertain'] if mode == 'uncertain' else []),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.replies = Queue()
                self.reader = Thread(target=lambda: [self.replies.put(line) for line in self.process.stdout], daemon=True)
                self.reader.start()

            def line(self, row):
                return b'\x1b[0;32mI (1000) DeskBotSession: ' + json.dumps(row).encode() + b'\x1b[0m\n'

            def write(self, raw):
                request = json.loads(raw)
                self.sent.append(request)
                if mode == 'conflict' and request['operation'].startswith('net-set:'):
                    request['operation'] = request['operation'].replace('net-set:0:', 'net-set:1:')
                self.process.stdin.write(str(self.now).encode() + b'|' + json.dumps(request).encode() + b'\n')
                self.process.stdin.flush()
                reply = json.loads(self.replies.get(timeout=5))
                if request['operation'].startswith('net-set:'):
                    if mode == 'reboot':
                        self.rows.append(self.line(hello_fixture(boot='c')))
                    if mode == 'drop':
                        return len(raw)
                self.rows.append(self.line(reply))
                return len(raw)

            def read_until(self, *args, **kwargs):
                self.now += 50 if self.rows else 200
                return self.rows.popleft() if self.rows else b''

            def sleep(self, seconds): self.now += round(seconds * 1000)

            def close(self):
                self.process.stdin.close()
                self.process.wait(timeout=5)
                self.reader.join(timeout=5)
                self.process.stdout.close(); self.process.stderr.close()
        transport = Transport()
        self.addCleanup(transport.close)
        return transport

    def maintenance_client(self, transport):
        return self.maintenance.Maintenance(transport, dict(device_id=DEVICE, firmware_sha256='c' * 64),
            clock=lambda: transport.now, sleep=transport.sleep, nonce='client-test')

    def test_maintenance_tool_actual_endpoint_save_read_repeat_reset_and_close(self):
        transport = self.maintenance_transport()
        client = self.maintenance_client(transport)
        result = client.run('set', host='10.0.0.9', touch_port=6318, expression_port=6319)
        self.assertEqual(result['result'], 'saved')
        self.assertTrue(result['worker_applied'])
        self.assertEqual(result['https_delivery'], 'pending')
        self.assertEqual(transport.sent[-1]['operation'], 'close')
        self.assertEqual(result['settings']['revision'], 1)
        # The same process retains NVS across distinct authenticated sessions.
        transport.sleep(10)
        transport.rows.append(transport.line(hello_fixture()))
        client = self.maintenance_client(transport)
        self.assertEqual(client.run('set', host='10.0.0.9', touch_port=6318, expression_port=6319)['result'], 'unchanged')
        transport.sleep(10)
        transport.rows.append(transport.line(hello_fixture()))
        result = self.maintenance_client(transport).run('reset')
        self.assertEqual((result['settings']['source'], result['settings']['host'], result['settings']['revision']),
                         ('defaults', '10.0.0.1', 2))

    def test_maintenance_rejects_wrong_device_firmware_and_public_address_before_writes(self):
        for change in ('device', 'firmware'):
            hello = hello_fixture()
            if change == 'device': hello['device_id'] = 'device-' + 'd' * 32
            else: hello['firmware']['app_elf_sha256'] = 'd' * 64
            transport = self.maintenance_transport(hello=hello)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'connected_device_or_build_mismatch'):
                self.maintenance_client(transport).run()
            self.assertEqual(transport.sent, [])
        transport = self.maintenance_transport()
        with self.assertRaisesRegex(ValueError, 'invalid_network_address'):
            self.maintenance_client(transport).run('set', host='8.8.8.8')
        self.assertEqual(transport.sent, [])

    def test_maintenance_conflict_and_lost_reply_release_the_session(self):
        for mode, error in (('conflict', ValueError), ('drop', TimeoutError)):
            transport = self.maintenance_transport(mode)
            with self.subTest(mode=mode), self.assertRaises(error):
                self.maintenance_client(transport).run('set', host='10.0.0.9')
            self.assertEqual(transport.sent[-1]['operation'], 'close')

    def test_maintenance_aborts_on_boot_change(self):
        transport = self.maintenance_transport('reboot')
        with self.assertRaisesRegex(ValueError, 'device_restarted_during_settings'):
            self.maintenance_client(transport).run('set', host='10.0.0.9')
        self.assertNotEqual(transport.sent[-1]['operation'], 'close')

    def test_maintenance_reports_uncertain_nvs_write_and_releases_session(self):
        transport = self.maintenance_transport('uncertain')
        with self.assertRaisesRegex(ValueError, 'body_network_save_uncertain'):
            self.maintenance_client(transport).run('set', host='10.0.0.9')
        self.assertEqual(transport.sent[-1]['operation'], 'close')

    def test_page_revision_conflict_closes_without_writing_actual_endpoint(self):
        transport = self.maintenance_transport()
        with self.assertRaisesRegex(ValueError, 'body_network_conflict'):
            self.maintenance_client(transport).run('set', host='10.0.0.9', expected_revision=3)
        self.assertEqual([row['operation'] for row in transport.sent], ['open', 'net-get', 'close'])

    def test_revision_type_checked_before_usb_session(self):
        transport = self.maintenance_transport()
        for value in (True, -1, 0x100000000, '0'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_network_revision'):
                self.maintenance_client(transport).run('set', host='10.0.0.9', expected_revision=value)
        self.assertEqual(transport.sent, [])

    def wire(self, op='net-get', **changes):
        return session.encode_request(**dict(operation=op, request_id='request-test', target_id=DEVICE,
            boot_id=BOOT, client_nonce='client-test', session_id=FIRST) | changes).decode().strip()

    def trace(self, commands):
        text = '\n'.join(f'{time}|{wire}' for time, wire in commands) + '\n'
        result = subprocess.run([str(self.binary), '--pipe'], input=text, capture_output=True, text=True, timeout=10, check=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_production_nvs_save_retry_failure_reset_and_boot_restore(self):
        result = subprocess.run([str(self.binary)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_usb_session_checks_identity_lease_and_readback(self):
        rows = self.trace([(0, self.wire('open', session_id=None)),
            (500, self.wire('net-set:0:10.0.0.2:5318:5319', target_id='device-other')),
            (1000, self.wire('net-set:0:10.0.0.2:5318:5319', boot_id='boot-other')),
            (1500, self.wire('net-set:0:10.0.0.2:5318:5319', client_nonce='client-other')),
            (2000, self.wire()), (2500, self.wire('net-set:0:10.0.0.2:5318:5319')),
            (3000, self.wire('net-set:0:10.0.0.2:5318:5319')), (3500, self.wire()),
            (33500, self.wire('net-reset:1'))])
        self.assertEqual([row['reason'] for row in rows[1:4]], ['target_mismatch', 'boot_changed', 'session_changed'])
        self.assertEqual(rows[4]['revision'], 0)
        self.assertEqual([row['result'] for row in rows[5:8]], ['saved', 'unchanged', 'state'])
        self.assertEqual(network.validate_reply(rows[7])['revision'], 1)
        self.assertEqual(rows[-1]['reason'], 'lease_expired')

    def test_host_client_correlates_cpp_replies_and_rejects_stale_or_wrong_session(self):
        client = network.Client(DEVICE, BOOT, 'client-test')
        opened = client.request('open', 0).decode().strip()
        replies = self.trace([(0, opened)])
        client.accept(replies[0], 0)
        wire = client.request(network.operation('set', expected=0, host='10.0.0.2'), 500).decode().strip()
        row = self.trace([(0, opened), (500, wire)])[-1]
        for key, value in [('request_id', 'request-wrong'), ('session_id', FIRST + '-wrong')]:
            invalid = {**row, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError): client.accept(invalid, 501)
        self.assertEqual(client.accept(row, 502)['result'], 'saved')
        with self.assertRaises(ValueError): client.accept(row, 503)

    def test_address_contract_boundaries(self):
        for host in ('10.1.2.3', '172.16.0.1', '192.168.1.1', 'deskbot.example', 'my-deskbot'):
            self.assertTrue(network.valid_host(host))
            self.assertTrue(network.valid_operation(network.operation('set', expected=0xffffffff, host=host)))
        for host in ('127.0.0.1', '8.8.8.8', '100.64.0.1', '192.168.01.1', 'host..example', 'host-.example',
                     'https://example.com', 'HOST', 'x' * 64, 'a@b', 'a/b'):
            self.assertFalse(network.valid_host(host))
        for op in ('net-set:00:10.0.0.1:1:2', 'net-reset:-1', 'net-set:0:10.0.0.1:1:1', 'net-set:0:10.0.0.1:0:1'):
            self.assertFalse(network.valid_operation(op))
