"""Exercise the production USB and NVS transaction and the host recovery journal."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from protocol import body_credentials as keys
from protocol import studio_usb_session as session
from tests.test_body_network_settings import NVS_STUB
from tests.test_wifi_expression import compiler, ROOT, BOARD

DEVICE, FIRMWARE = 'device-' + 'a' * 32, 'd' * 64
TOKEN1, TOKEN2 = b'\xdd' * 32, b'\xee' * 32
BUILTIN = '6' * 32
CERT = "-----BEGIN CERTIFICATE-----\nMAA=\n-----END CERTIFICATE-----"
PAIR = keys.encode_bundle(TOKEN1.hex(), TOKEN2.hex(), CERT, CERT)
NEW = keys.bundle_id(PAIR)
PREFIX = 'session-'


def credential_stubs():
    return {
        'esp_heap_caps.h': '''#pragma once
#include <cstdlib>
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
inline void* heap_caps_malloc(size_t size, unsigned) { return std::malloc(size); }
inline void heap_caps_free(void* value) { std::free(value); }
''',
    }


def compile_credentials(source, output, includes):
    sha = ROOT / 'tests/vendor/studio_catalog/mbedtls'
    flags = ['-DMBEDTLS_CONFIG_FILE="catalog_mbedtls_config.h"', '-I', str(sha / 'include'),
             '-I', str(ROOT / 'tests/cpp/catalog_stubs')]
    command = compiler()
    c = command[:-1] + ['cc'] if command[-1] == 'c++' else command + ['-x', 'c']
    zeroize = output.parent / 'zeroize.c'
    zeroize.write_text('#include <stddef.h>\nvoid mbedtls_platform_zeroize(void* p, size_t n) {'
                       'volatile unsigned char* b=p; while (n--) *b++=0;}\n', encoding='ascii')
    objects = []
    for path in (sha / 'library/sha256.c', zeroize):
        obj = output.parent / (path.stem + '.o')
        result = subprocess.run(c + flags + ['-std=c99', '-c', str(path), '-o', str(obj)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        objects.append(str(obj))
    include_flags = [argument for folder in (*includes, BOARD) for argument in ('-I', str(folder))]
    result = subprocess.run(command + include_flags + flags + ['-std=c++17', '-Wall', '-Wextra', '-Werror',
        str(source), *objects, '-o', str(output)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)


class BodyCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='key-fixture-', dir=ROOT / 'tmp')
        cls.addClassCleanup(cls.folder.cleanup)
        root = Path(cls.folder.name)
        (root / 'nvs.h').write_text(NVS_STUB, encoding='utf-8')
        for name, content in credential_stubs().items():
            (root / name).write_text(content, encoding='utf-8')
        cls.binary = root / 'credential.exe'
        compile_credentials(ROOT / 'tests/cpp/body_network_credentials_test.cc', cls.binary, (root,))
        spec = importlib.util.spec_from_file_location('renewal_tool', ROOT / 'scripts/rotate-body-credentials.py')
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def wire_trace(self, ops):
        time, boot, epoch = 0, 'b' * 32, 0
        lines = []
        for op in ops:
            if op == '!boot':
                lines.append(f'{time}|{op}')
                boot, epoch = 'c' * 32, 0
            elif op.startswith('!'):
                lines.append(f'{time}|{op}')
            else:
                if op == 'open':
                    epoch += 1
                request = session.encode_request(operation=op, request_id=f'rq-{time}', target_id=DEVICE,
                    boot_id='boot-' + boot, client_nonce=f'fixture-{epoch}',
                    session_id=None if op == 'open' else f'{PREFIX}{boot}-{epoch}')
                lines.append(f'{time}|{request.decode().strip()}')
            time += 300
        run = subprocess.run([str(self.binary)], input='\n'.join(lines) + '\n', capture_output=True,
                             text=True, check=True, timeout=8)
        self.assertNotIn(TOKEN1.hex(), run.stdout)
        self.assertNotIn(TOKEN2.hex(), run.stdout)
        rows = {row['request_id']: row for row in map(json.loads, run.stdout.splitlines())}
        return [rows[f'rq-{i * 300}'] for i, op in enumerate(ops) if not op.startswith('!')]

    def test_two_verified_channels_commit_pair_and_restore_on_boot(self):
        rows = self.wire_trace(['open', *keys.upload(PAIR, BUILTIN), 'key-save:' + NEW, 'key-test', '!half',
                                'key-save:' + NEW, 'key-get', 'key-test', '!pass', 'key-save:' + NEW,
                                'key-save:' + NEW, '!boot', 'open', 'key-get', 'net-get'])
        before_test, testing, half, half_state, retry, saved, repeat, reopened, readback, address = rows[-10:]
        self.assertEqual(before_test['result'], 'not_verified')
        self.assertEqual(half['result'], 'not_verified')
        self.assertEqual((half_state['active'], half_state['trial']), (BUILTIN, 'failed'))
        self.assertEqual((saved['result'], repeat['result']), ('saved', 'unchanged'))
        self.assertEqual((readback['active'], readback['storage']), (NEW, 'restored'))
        self.assertEqual(address['revision'], 0)
        self.assertEqual(keys.validate_reply(readback)['active'], NEW)

    def test_lost_session_and_address_change_require_new_trial(self):
        stage = list(keys.upload(PAIR, BUILTIN))
        rows = self.wire_trace(['open', *stage, 'key-test', '!pass', 'net-set:0:10.0.0.2:4318:4319',
            'key-save:' + NEW, 'open', 'key-save:' + NEW, 'key-get'])
        self.assertEqual(rows[-4]['result'], 'not_verified')
        self.assertEqual(rows[-2]['result'], 'conflict')
        self.assertEqual(rows[-1]['active'], BUILTIN)

    def test_check_active_pair_can_use_builtins_and_does_not_allow_save(self):
        rows = self.wire_trace(['open', 'key-check', '!pass', 'key-get', 'key-save:' + NEW, 'key-get'])
        self.assertEqual(rows[1]['result'], 'testing')
        self.assertEqual((rows[2]['active'], rows[2]['trial'], rows[2]['candidate']), (BUILTIN, 'passed', ''))
        self.assertEqual(rows[3]['result'], 'conflict')
        self.assertEqual(rows[4]['active'], BUILTIN)

    def test_durable_commit_error_requires_boot_and_recovers_complete_pair(self):
        rows = self.wire_trace(['open', *keys.upload(PAIR, BUILTIN), 'key-test', '!pass', '!uncertain',
                                'key-save:' + NEW, 'key-save:' + NEW, '!boot', 'open', 'key-get'])
        self.assertEqual((rows[-4]['result'], rows[-4]['active']), ('save_uncertain', BUILTIN))
        self.assertEqual(rows[-3]['result'], 'save_uncertain')
        self.assertEqual((rows[-1]['active'], rows[-1]['storage']), (NEW, 'restored'))

    def test_upload_interruption_and_malformed_fragment_do_not_reach_flash(self):
        upload = list(keys.upload(PAIR, BUILTIN))
        rows = self.wire_trace(['open', upload[0], 'key-data:1:30', upload[1], '!boot', 'open', 'key-get'])
        self.assertEqual(rows[2]['result'], 'invalid')
        self.assertEqual(rows[-1]['active'], BUILTIN)
        self.assertEqual(rows[-1]['candidate'], '')

    def test_changed_payload_cannot_claim_the_original_pair_fingerprint(self):
        damaged = bytearray(PAIR)
        damaged[22] ^= 1
        with self.assertRaisesRegex(ValueError, 'invalid_credential_bundle'):
            keys.bundle_id(bytes(damaged))
        ops = [f'key-begin:{len(damaged)}:{BUILTIN}'] + [
            f'key-data:{offset}:{damaged[offset:offset+32].hex()}' for offset in range(0, len(damaged), 32)]
        rows = self.wire_trace(['open', *ops, 'key-get'])
        self.assertEqual((rows[-2]['result'], rows[-1]['active']), ('invalid', BUILTIN))

    def make_plan(self):
        from types import SimpleNamespace
        folder = tempfile.TemporaryDirectory(prefix='key-journal-', dir=ROOT / 'tmp')
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        configs = []
        for side, tokens in (('before', ('a', 'b')), ('after', ('c', 'd'))):
            runtime = dict(port=12345, database='fixture.sqlite')
            for index, field in enumerate(self.tool.FIELDS):
                path = root / (side + str(index))
                path.mkdir()
                (path / 'token').write_text(tokens[index] * 64, encoding='ascii')
                (path / 'cert').write_text(CERT, encoding='ascii')
                (path / 'key').write_bytes(b'test')
                self.tool.save_json(path / 'receiver.json', dict(schema=self.tool.SCHEMAS[index],
                    device_id=DEVICE, firmware_sha256=FIRMWARE, port=4318 + index, host='10.0.0.1',
                    token_file='token', cert_file='cert', key_file='key'))
                runtime[field] = str(path / 'receiver.json')
            configs.append(runtime)
        before, after = configs
        runtime_path, plan_path = root / 'runtime.json', root / 'plan.json'
        self.tool.save_json(runtime_path, before)
        ids, digests = [], []
        for runtime in configs:
            _, parts, digest = self.tool.pair(runtime)
            ids.append(keys.bundle_id(self.tool.bundle(parts)))
            digests.append(digest)
        self.tool.save_json(plan_path, dict(runtime=str(runtime_path), before=before, after=after,
            before_sha256=digests[0], after_sha256=digests[1], before_id=ids[0], after_id=ids[1]))
        return SimpleNamespace(runtime=runtime_path, plan=plan_path, before=before, after=after,
                               before_id=ids[0], after_id=ids[1])

    def fixture_device(self, active):
        from contextlib import contextmanager
        class Device:
            storage, fail_probe, lost_save, fail_current = 'builtin', False, False, False
            saves = 0
            checks = 0

            @contextmanager
            def session(self):
                yield self

            def exchange(self, command):
                if command.startswith('key-save:'):
                    self.active, self.storage = command[9:], 'saved'
                    self.saves += 1
                    if self.lost_save:
                        raise TimeoutError('lost_commit_ack')
                return dict(active=self.active, storage=self.storage, result='saved')

            def address(self):
                return dict(host='10.0.0.1', touch_port=4318, expression_port=4319, revision=1, applied_revision=1)

            def trial(self, raw, active):
                assert keys.bundle_id(raw) != active
                if self.fail_probe:
                    raise ValueError('probe_failed')

            def check(self, active):
                self.checks += 1
                assert active == self.active
                if self.fail_current:
                    raise ValueError('active_pair_rejected')
                return self.exchange('key-get')
        device = Device()
        device.active = active
        return device

    def test_host_reopens_previous_pair_when_trial_fails_before_save(self):
        files, order = self.make_plan(), []
        device = self.fixture_device(files.before_id)
        device.fail_probe = True
        with self.assertRaisesRegex(self.tool.RecoveryNeeded, 'previous_receivers_restored'):
            self.tool.apply(files.plan, 'after', device, lambda path: order.append(self.tool.read_json(path)))
        self.assertEqual(order, [files.after, files.before])
        self.assertEqual(device.saves, 0)
        self.assertEqual(device.checks, 1)

    def test_host_resumes_after_commit_reply_loss_and_restores_with_both_profile_paths(self):
        files, order = self.make_plan(), []
        device = self.fixture_device(files.before_id)
        device.lost_save = True

        def restart(path):
            order.append(self.tool.read_json(path))

        with self.assertRaisesRegex(self.tool.RecoveryNeeded, 'reconnect_usb_and_resume_saved_plan'):
            self.tool.apply(files.plan, 'after', device, restart)
        self.assertEqual(order, [files.after])
        device.lost_save = False
        self.assertEqual(self.tool.apply(files.plan, 'after', device, restart)['active'], files.after_id)
        self.assertEqual(device.saves, 1)
        self.assertEqual(self.tool.apply(files.plan, 'before', device, restart)['active'], files.before_id)
        self.assertEqual(self.tool.read_json(files.runtime), files.before)
        self.assertEqual(device.saves, 2)

    def test_host_cannot_register_wrong_live_pair_after_another_interrupted_plan(self):
        files = self.make_plan()
        device = self.fixture_device(BUILTIN)
        with self.assertRaisesRegex(self.tool.RecoveryNeeded, 'device_pair_mismatch_resume_previous_plan'):
            self.tool.apply(files.plan, 'before', device, lambda _: self.fail('unexpected restart'))
        self.assertEqual(device.saves, 0)

    def test_expired_old_certificate_does_not_block_renewal(self):
        files = self.make_plan()
        device = self.fixture_device(files.before_id)

        def restart(path):
            device.fail_current = self.tool.read_json(path) == files.before

        device.fail_current = True
        result = self.tool.apply(files.plan, 'after', device, restart)
        self.assertEqual((result['active'], result['https_channels_verified']), (files.after_id, 2))
        self.assertEqual(device.checks, 1)

    def test_host_rejects_changed_receiver_and_network_before_service_mutation(self):
        files = self.make_plan()
        device = self.fixture_device(files.before_id)

        def unexpected(_):
            self.fail('host must check both endpoints before any restart')

        address = device.address()
        device.address = lambda: deepcopy(address) | {'expression_port': 8000}
        with self.assertRaisesRegex(ValueError, 'device_endpoints_must_match_receiver_pair'):
            self.tool.apply(files.plan, 'after', device, unexpected)
        (Path(files.after['touch_wifi_config']).parent / 'token').write_text('f' * 64, encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'receiver_files_changed_since_prepare'):
            self.tool.apply(files.plan, 'after', device, unexpected)
