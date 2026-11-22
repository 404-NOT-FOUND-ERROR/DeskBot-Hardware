"""Role codec, one-flight USB scheduling, and production C++ activation tests."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from protocol import studio_device_roles as roles
from protocol import studio_usb_session as session
from protocol.studio_control_bridge import validate_control
from tests import test_studio_control_link as fixtures
from tests import test_studio_role_player_e3f9c as player

ROOT=Path(__file__).resolve().parents[1]


def reply(request='query-1',seq=3,at=810,result='state'):
    return dict(schema=roles.SCHEMA,message_type='roles',request_id=request,target_id=fixtures.DEVICE,
        boot_id=fixtures.BOOT,session_id=fixtures.SESSION,sequence=seq,at_ms=at,lease_ms=30000,
        result=result,reason='none',loaded=True,graph_sha256='a'*64,
        roles=['grok-round-personal-v1','xiaozhi-e145'],default_index=1,target_index=1,current_index=1,
        active_index=1,phase='held',blocked=0,sleep='unknown',saved_index=-1,save_state='none',
        activation=dict(request_id='',status='idle'))


class DeviceRoleHostTests(fixtures.LinkTests):
    # Reuse only connection setup; predecessor regressions run separately.
    def test_role_codec_boundaries(self):
        roles.validate_reply(reply())
        for change in ({'roles':['a']*5},{'target_index':2},{'active_index':True},
                       {'graph_sha256':'a'*63},{'extra':1},{'blocked':8},{'loaded':False}):
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                roles.validate_reply(reply()|change)

    def test_one_owner_queries_and_activation_without_replay(self):
        self.ready();self.command('read_roles',session_id=fixtures.SESSION,now=420)
        wire=json.loads(self.link.tick(800));self.assertEqual(wire['operation'],'role.query')
        self.link.accept(reply(wire['request_id']),810)
        state=self.link.snapshot(810);self.assertTrue(validate_control(state))
        op='role.activate.0.'+'a'*64
        self.command(op,session_id=fixtures.SESSION,now=820)
        wire=json.loads(self.link.tick(1200));self.assertEqual(wire['operation'],op)
        self.assertIsNone(self.link.tick(1201))
        self.link.accept(reply(wire['request_id'],4,1210,'accepted')|{'activation':{'request_id':wire['request_id'],'status':'queued'}},1210)
        self.assertTrue(validate_control(self.link.snapshot(1210)))
        self.assertEqual(json.loads(self.link.tick(1600))['operation'],'display')
        self.assertIsNone(self.link.role_outgoing)

    def test_stale_graph_disconnect_and_old_reply(self):
        self.ready();self.command('read_roles',session_id=fixtures.SESSION,now=420)
        wire=json.loads(self.link.tick(800));self.link.accept(reply(wire['request_id']),810)
        self.command('role.activate.0.'+'b'*64,session_id=fixtures.SESSION,now=820)
        self.assertEqual(self.link.last_command['reason'],'graph_changed')
        self.assertIsNone(self.link.snapshot(3810)['roles']['reply'])
        self.link.disconnect(4000);self.assertIsNone(self.link.snapshot(4000)['roles']['reply'])
        self.link.accept(reply(wire['request_id']),4010);self.assertEqual(self.link.rejected,1)

    def test_correlated_role_timeout_unblocks_display(self):
        self.ready();self.command('read_roles',session_id=fixtures.SESSION,now=420)
        self.link.tick(800);self.link.tick(8800)
        self.assertEqual(self.link.roles['error'],'role_reply_timeout')
        self.assertIsNone(self.link.role_pending)

    def test_malformed_operation_rejected(self):
        for op in ('role.activate.4.'+'a'*64,'role.activate.00.'+'a'*64,'role.activate.0.'+'a'*63):
            self.assertFalse(roles.operation(op))


class DeviceRoleNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('activation_compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        command,kind=helper.find_compiler()
        cls.temp=tempfile.TemporaryDirectory(prefix='activation-native-',dir=ROOT/'tmp');cls.addClassCleanup(cls.temp.cleanup)
        cls.exe=Path(cls.temp.name)/'activation.exe'
        board=ROOT/'firmware/body/main/boards/esp_vocat'
        flags=['-DCONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE=1','-DCONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE=1']
        built=subprocess.run(command+(['c++'] if kind=='zig-c++' else [])+flags+['-I',str(board),'-std=c++17',
            '-Wall','-Wextra','-Werror',str(ROOT/'tests/cpp/studio_role_activation_test.cc'),'-o',str(cls.exe)],
            cwd=ROOT,capture_output=True,text=True,timeout=180)
        if built.returncode: raise RuntimeError(built.stdout+built.stderr)

    def test_production_control_and_usb(self):
        for mode in ('decode','dedup','expire','disconnect','latest','many','unmount','frame-failure','interrupted','first-frame','save-failure','reboot','unchanged','graph-index','usb'):
            with self.subTest(mode=mode):
                run=subprocess.run([str(self.exe),mode],capture_output=True,text=True,timeout=20)
                self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_cpp_wire_matches_python_contract(self):
        for mode,at in (('format',100),('format-max',9007199254740991)):
            with self.subTest(mode=mode):
                run=subprocess.run([str(self.exe),mode],capture_output=True,text=True,timeout=20)
                self.assertEqual(run.returncode,0,run.stderr)
                row=roles.validate_reply(json.loads(run.stdout))
                self.assertEqual(row['at_ms'],at)


class DeviceRolePlayerTests(player.RolePlayerTests):
    EXTRA_FLAGS=['-DCONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE=1']
    def test_unmapped_semantic_finishes_pending_selection(self):
        rows=self.run_player('role:'+self.grok['id']+',eat')
        self.assertEqual(rows[-1]['phase'],0)
        self.assertEqual(rows[-1]['current'],255)
        self.assertFalse(rows[-1]['failed'])

    def test_actual_role_waits_for_new_frame_and_exit(self):
        rows=self.run_player('neutral,tick,role:'+self.grok['id']+',finish,tick,finish,role:'+self.original['id']+',finish,tick,finish,tick')
        self.assertEqual(rows[0]['active'],255)
        self.assertEqual(rows[1]['active'],1)
        self.assertNotEqual(rows[2]['selection'],rows[2]['rendered_selection'])
        self.assertEqual(rows[4]['active'],0)
        self.assertEqual(rows[-1]['active'],1)
        self.assertEqual(rows[-1]['selection'],rows[-1]['rendered_selection'])

    def test_prepare_and_later_phase_failures_have_terminal_state(self):
        rows=self.run_player('role:'+self.grok['id']+',fail:prepare,tick')
        self.assertTrue(rows[-1]['failed']);self.assertEqual(rows[-1]['rendered_selection'],0)
        self.assertFalse(rows[-1]['playing']);self.assertEqual(rows[-1]['phase'],0)
        rows=self.run_player('role:'+self.grok['id']+',fail:src,finish')
        self.assertTrue(rows[-1]['failed']);self.assertEqual(rows[-1]['phase'],0)

    def test_same_role_preserves_playback_and_sleep_known(self):
        rows=self.run_player('role:'+self.grok['id']+',tick,role:'+self.grok['id']+',sleep-on,sleep-off')
        self.assertEqual(rows[1]['plays'],rows[2]['plays'])
        self.assertEqual(rows[1]['cursor'],rows[2]['cursor'])
        self.assertFalse(rows[2]['sleep_known']);self.assertTrue(rows[-1]['sleep_known'])


if __name__=='__main__':unittest.main()
