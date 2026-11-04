from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import jsonschema
from referencing import Registry, Resource

from protocol.device_link import decode_hello, validate_hello
from protocol.studio_identity_observation import IdentityObservation, aged_identity_snapshot
from protocol.studio_identity_bridge import IdentityBridge

ROOT = Path(__file__).resolve().parents[1]


def fixture(sequence=0, boot='b', device='a', uptime=None):
    return {'schema':'deskbot.device_link/v1','message_type':'hello','device_id':'device-'+device*32,
            'boot_id':'boot-'+boot*32,'sequence':sequence,'uptime_ms':uptime if uptime is not None else 1000+sequence*10000,
            'firmware':{'edition':'E3b','version':'2.0.5','app_elf_sha256':'c'*64},
            'hardware':{'chip':'esp32s3','board':'esp_vocat','flash_bytes':33554432,'psram_bytes':16777216},
            'capabilities':['device_identity'],'transport':'usb_console','controls_available':False,'catalog_id':None,
            'limits':{'max_frame_bytes':2048,'hello_interval_ms':10000}}


def line(value):
    return b'I (1000) DeskBotLink: '+json.dumps(value,separators=(',',':')).encode()+b'\r\n'


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.o=IdentityObservation(clock=lambda:self.now)
        self.o.link('connected','COM4')

    def feed(self,value):
        self.o.feed(line(value));return self.o.snapshot()

    def test_valid_contract(self):
        schema=json.loads((ROOT/'protocol/deskbot-device-link-v1.schema.json').read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(fixture(),schema)
        self.assertEqual(validate_hello(fixture()),fixture())

    def test_every_fragment_boundary(self):
        raw=line(fixture())
        for index in range(1,len(raw)):
            o=IdentityObservation();o.link('connected','COM4')
            o.feed(raw[:index]);self.assertIsNone(o.snapshot()['identity']['hello'])
            o.feed(raw[index:]);self.assertEqual(o.snapshot()['identity']['status'],'confirmed')

    def test_one_byte_fragments(self):
        for byte in line(fixture()): self.o.feed(bytes([byte]))
        self.assertEqual(self.o.snapshot()['identity']['hello'],fixture())

    def test_multiple_frames_and_logs(self):
        self.o.feed(line(fixture())+b'I (2000) Application: STATE: idle\n'+line(fixture(1)))
        value=self.o.snapshot()
        self.assertEqual(value['identity']['hello']['sequence'],1)
        self.assertEqual(value['application_state']['value'],'idle')

    def test_console_text_never_becomes_identity(self):
        self.o.feed(b'I (2000) Application: STATE: speaking\n'+json.dumps(fixture()).encode()+b'\n')
        self.assertEqual(self.o.snapshot()['identity']['status'],'waiting')

    def test_ansi_framing(self):
        self.o.feed(b'\x1b[0;32m'+line(fixture()).rstrip()+b'\x1b[0m\n')
        self.assertEqual(self.o.snapshot()['identity']['status'],'confirmed')

    def test_truncated_frame_waits_for_boundary(self):
        self.o.feed(line(fixture())[:-3])
        self.assertIsNone(self.o.snapshot()['identity']['hello'])

    def test_oversize_frame_discards_to_newline_then_recovers(self):
        self.o.feed(b'I (1) DeskBotLink: '+b'x'*4096+b'\n'+line(fixture()))
        self.assertEqual(self.o.snapshot()['dropped_lines'],1)
        self.assertEqual(self.o.snapshot()['identity']['status'],'confirmed')
        self.assertLessEqual(len(self.o.buffer),2048)

    def test_invalid_json_and_utf8_then_recovery(self):
        for raw in (b'{',b'{"bad":\xff}',b'{}{}',b'NaN'):
            self.o.feed(b'I (1) DeskBotLink: '+raw+b'\n')
        self.assertIsNone(self.o.snapshot()['identity']['hello'])
        self.assertEqual(self.feed(fixture())['identity']['status'],'confirmed')

    def test_duplicate_json_keys_rejected(self):
        raw=json.dumps(fixture()).encode().replace(b'{',b'{"schema":"wrong",',1)
        with self.assertRaises(ValueError): decode_hello(raw)

    def test_unknown_or_control_capabilities_rejected(self):
        for patch in ({'schema':'deskbot.device_link/v2'},{'capabilities':['expression']},
                      {'controls_available':True},{'catalog_id':'c'*64},{'transport':'wifi'}):
            value=fixture();value.update(patch)
            with self.assertRaises(ValueError): validate_hello(value)

    def test_strict_types_and_bounds(self):
        for field in ('sequence','uptime_ms'):
            for bad in (True,-1,1.5,None,'1',float('inf')):
                value=fixture();value[field]=bad
                with self.assertRaises(ValueError): validate_hello(value)
        for patch in ({'extra':1},{'firmware':[]},{'hardware':None},{'limits':[]},{'device_id':7}):
            value=fixture();value.update(patch)
            with self.assertRaises(ValueError): validate_hello(value)

    def test_duplicates_do_not_refresh_freshness(self):
        self.feed(fixture());self.now=31
        value=self.feed(fixture())
        self.assertEqual(value['identity']['status'],'stale')
        self.assertEqual(value['identity']['rejected_messages'],1)

    def test_sequence_and_time_regression(self):
        self.feed(fixture(3))
        for value in (fixture(2),fixture(4,uptime=1)):
            self.feed(value)
        self.assertEqual(self.o.snapshot()['identity']['hello']['sequence'],3)

    def test_same_boot_firmware_cannot_change(self):
        self.feed(fixture())
        value=fixture(1);value['firmware']['version']='changed'
        self.feed(value)
        self.assertEqual(self.o.snapshot()['identity']['hello']['sequence'],0)

    def test_new_boot_clears_previous_state(self):
        self.feed(fixture());self.o.feed(b'I (3000) Application: STATE: speaking\n')
        value=self.feed(fixture(0,boot='d'))
        self.assertIsNone(value['application_state'])
        self.assertEqual(value['identity']['hello']['boot_id'],'boot-'+'d'*32)

    def test_retired_boot_is_not_revived(self):
        self.feed(fixture());self.feed(fixture(0,boot='d'));self.feed(fixture(4))
        self.assertEqual(self.o.snapshot()['identity']['hello']['boot_id'],'boot-'+'d'*32)

    def test_rom_banner_invalidates_identity(self):
        self.feed(fixture());self.o.feed(b'ESP-ROM:esp32s3-20210327\n')
        self.assertIsNone(self.o.snapshot()['identity']['hello'])

    def test_device_change_does_not_replace_pinned_identity(self):
        self.feed(fixture());self.feed(fixture(1,device='e'));value=self.feed(fixture(2,device='e'))
        self.assertEqual(value['identity']['reason'],'device_changed')
        self.assertIsNone(value['identity']['hello'])

    def test_reconnect_clears_partial_and_old_identity(self):
        self.feed(fixture());self.o.feed(line(fixture(1))[:50])
        self.o.link('missing','COM4');self.o.link('connected','COM4')
        self.assertIsNone(self.o.snapshot()['identity']['hello'])
        self.feed(fixture(2))
        self.assertEqual(self.o.snapshot()['identity']['status'],'confirmed')

    def test_disconnect_hides_identity(self):
        self.feed(fixture());self.o.link('missing','COM4')
        self.assertEqual(self.o.snapshot()['identity']['status'],'disconnected')
        self.assertIsNone(self.o.snapshot()['identity']['hello'])

    def test_snapshot_age_and_worker_failure(self):
        value=self.feed(fixture())
        self.assertEqual(aged_identity_snapshot(value,30001)['identity']['status'],'stale')
        failed=aged_identity_snapshot(value,0,worker_alive=False)
        self.assertIsNone(failed['identity']['hello'])
        self.assertEqual(failed['identity']['status'],'disconnected')

    def test_snapshots_are_copied(self):
        value=self.feed(fixture());value['identity']['hello']['firmware']['version']='bad'
        self.assertEqual(self.o.snapshot()['identity']['hello']['firmware']['version'],'2.0.5')

    def test_snapshot_schema(self):
        schemas=[json.loads((ROOT/'protocol'/name).read_bytes()) for name in
                 ('deskbot-device-link-v1.schema.json','deskbot-body-observation-v1.schema.json','deskbot-body-observation-v2.schema.json')]
        registry=Registry().with_resources((s['$id'],Resource.from_contents(s)) for s in schemas)
        validator=jsonschema.Draft202012Validator(schemas[-1],registry=registry,format_checker=jsonschema.FormatChecker())
        for value in (self.o.snapshot(),self.feed(fixture()),aged_identity_snapshot(self.o.snapshot(),31000),
                      aged_identity_snapshot(self.o.snapshot(),0,worker_alive=False)):
            validator.validate(value)

    def test_bridge_without_runtime_is_inactive(self):
        bridge=IdentityBridge();self.assertFalse(bridge.ports()['available'])
        self.assertEqual(bridge.snapshot()['identity']['status'],'disconnected')
        self.assertFalse(bridge.snapshot()['hardware_accessed'])

    def test_bridge_rejects_incomplete_or_inconsistent_worker_snapshot(self):
        bridge=IdentityBridge()
        valid=self.feed(fixture())
        self.assertTrue(bridge.validate_snapshot(valid))
        for mutate in (lambda v:v.pop('identity'),
                       lambda v:v['identity'].update(hello=None),
                       lambda v:v['identity'].update(age_ms=31000),
                       lambda v:v.update(controls_available=True),
                       lambda v:v.update(schema='deskbot.body_observation/v1')):
            value=deepcopy(valid);mutate(value)
            self.assertFalse(bridge.validate_snapshot(value))

    def test_firmware_wire_encoder(self):
        # Use the same verified native compiler locator as E1.26.
        spec=importlib.util.spec_from_file_location('link_cpp_helper',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        compiler=helper.find_compiler()
        self.assertIsNotNone(compiler,'native compiler required')
        command,kind=compiler
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='e3b-cpp-') as directory:
            output=Path(directory)/'link-test.exe'
            cmd=command+(['c++'] if kind=='zig-c++' else [])+['-std=c++17','-Wall','-Wextra','-Werror','-I',
                 str(ROOT/'firmware/body/main/boards/esp_vocat'),str(ROOT/'tests/cpp/studio_device_link_test.cc'),'-o',str(output)]
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stderr)
            run=subprocess.run([str(output)],capture_output=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            rows=[decode_hello(raw) for raw in run.stdout.splitlines()]
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0],fixture())
            self.assertEqual(rows[1]['sequence'],0xFFFFFFFF)


if __name__=='__main__': unittest.main()
