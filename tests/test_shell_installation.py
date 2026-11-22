"""Owner installs through production stores, scheduling and ControlLink memory device."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import tempfile
import threading
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from protocol.character_profiles import CharacterProfiles, CharacterConflict
from protocol.shell_characters import ShellCharacters
from protocol.shell_installation import ShellInstallation
from protocol.shell_switch_service import ShellRuntimeConfig, ShellSwitchService, RuntimeConflict
from protocol.shell_workbench import SessionStore
from protocol.presentation_coordinator import PresentationCoordinator
from protocol.shell_switch_lab import SessionLab, DEVICE
from protocol.role_activation import RoleStorageError
from tests.test_shell_characters import GRAPH, CATALOG, character


class Clock:
    ms = 0

    def __call__(self):
        return self.ms / 1000


class InstallationOwner:
    def __init__(self, clock):
        self.clock, self.lock = clock, threading.RLock()
        self.lab = SessionLab({}, GRAPH)
        self.presentation = PresentationCoordinator(self)
        self.lost_return = False

    def advance(self, at):
        with self.lock:
            self.clock.ms = at
            self.lab.pump(at)
            self.presentation.observe(self.control())

    def control(self):
        with self.lock:
            return self.lab.link.snapshot(self.clock.ms) | dict(host_command=None)

    def command(self, value):
        return self.presentation.submit(value)

    def _command(self, value, *, before_write=None):
        if before_write:
            before_write()
        self.lab.command(value['operation'], self.clock.ms, value)
        if self.lost_return and value['operation'].startswith('role.activate.'):
            self.lost_return = False
            raise OSError('fixture_lost_return')
        return self.control()

    @property
    def activations(self):
        return [row for row in self.lab.commands if row['operation'].startswith('role.activate.')]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.characters = CharacterProfiles(self.folder/'characters.json', catalog=CATALOG, graph=GRAPH)
        self.addCleanup(self.characters.close)
        self.sessions = SessionStore(self.folder/'sessions', project_root=self.folder, graph=GRAPH)
        self.addCleanup(self.sessions.close)
        self.registry = ShellCharacters(self.folder/'shells.json', characters=self.characters, sessions=self.sessions)
        self.addCleanup(self.registry.close)
        for shell, expression in (('a', 'round'), ('b', 'old')):
            profile=character('profile-'+shell, expression)
            profile['display_name']='圆团' if shell=='a' else '原有角色'
            self.characters.save(profile, 0)
            self.registry.save_shell(shell, '外壳'+shell.upper(), self.registry.state()['revision'])
            self.registry.confirm(shell, 'profile-'+shell, 1, None, None, self.registry.state()['revision'])
        self.clock = Clock()
        self.owner = InstallationOwner(self.clock)
        self.automatic = ShellSwitchService(ShellRuntimeConfig(self.registry), self.owner, clock=self.clock, background=False)
        self.addCleanup(self.automatic.close)
        self.path = self.folder/'installations.json'
        self.service = self.make()
        self.addCleanup(lambda:self.service.close())
        self.run_to(1500)

    def make(self):
        return ShellInstallation(self.path, self.registry, self.owner, self.automatic,
                                 character_id='same-individual', clock=self.clock, background=False)

    def request(self, shell='b', ident='install-1'):
        choice = next(row for row in self.service.state()['choices'] if row['shell_id'] == shell)
        return dict(installation_id=ident, character_id='same-individual', device_id=DEVICE,
                    shell_id=shell, binding_sha256=choice['binding_sha256'], plan_id=None,
                    expected_revision=self.service.state()['revision'])

    def confirm(self, shell='b', ident='install-1'):
        return self.service.confirm(**self.request(shell, ident))

    def row(self, ident=None):
        state = self.service.state()
        return next(row for row in state['records'] if row['installation_id'] == (ident or state['current']))

    def run_to(self, end):
        for at in range(self.clock.ms+100, end+1, 100):
            self.owner.advance(at)
            self.service.tick()

    def test_manual_without_magnet_uses_one_identity_and_real_receipts(self):
        self.confirm(); self.run_to(6000)
        row = self.row()
        self.assertEqual(row['phase'], 'saved')
        self.assertEqual(row['source'], 'owner_manual')
        self.assertEqual(row['character_id'], 'same-individual')
        self.assertTrue(row['first_frame'] and row['saved'])
        self.assertEqual(row['scope'], 'device_expression')
        self.assertEqual(len(self.owner.activations), 1)
        self.assertEqual(self.service.state()['device']['actual'], 'old')
        self.assertEqual(self.service.state()['client_role_adoption'], 'pending')
        self.assertEqual(row['profile']['persona'], '好奇')
        self.assertEqual(row['device_result']['activation']['request_id'],row['request_result']['request_id'])
        self.assertEqual(len(row['attempts']),1)

    def test_repeat_confirmation_deduplicates_before_revision_check(self):
        request = self.request()
        self.service.confirm(**request); self.run_to(6000)
        self.service.confirm(**request)
        self.assertEqual(len(self.service.state()['records']), 1)
        self.assertEqual(len(self.owner.activations), 1)
        with self.assertRaises(CharacterConflict):
            self.service.confirm(**dict(request, plan_id='other'))

    def test_wrong_identity_and_stale_binding_are_rejected(self):
        with self.assertRaises(CharacterConflict):
            self.service.confirm(**dict(self.request(), character_id='other'))
        with self.assertRaises(CharacterConflict):
            self.service.confirm(**dict(self.request(), binding_sha256='f'*64))

    def test_speaking_menu_and_sleep_wait_for_home(self):
        for mode in ('speaking', 'menu', 'sleep'):
            with self.subTest(mode=mode):
                device=self.owner.lab.device
                device.app='speaking' if mode=='speaking' else 'idle'
                device.page='other' if mode=='menu' else 'home'
                self.owner.advance(self.clock.ms+100)
                if mode=='sleep':
                    self.owner.lab.link.roles['reply']['sleep']='sleeping'
                self.confirm(ident='install-'+mode)
                for _ in range(2):
                    self.service.tick()
                self.assertNotEqual(self.row()['phase'], 'saved')
                device.app,device.page='idle','home'
                if mode=='sleep':self.owner.lab.link.roles['reply']['sleep']='awake'
                self.run_to(self.clock.ms+4500)
                self.assertEqual(self.row()['phase'], 'saved')

    def test_missing_content_keeps_confirmation_and_sends_no_activation(self):
        self.owner.lab.device.graph='d'*64
        self.owner.lab.read_requested=False
        self.run_to(2600)
        self.confirm(); self.run_to(6000)
        self.assertFalse(self.owner.activations)
        self.assertEqual(self.row()['reason'], 'content_required')

    def test_profile_edit_requires_new_confirmation(self):
        self.owner.lab.device.page='other'; self.run_to(1900)
        self.confirm()
        profile=deepcopy(self.characters.profiles[1]);profile['persona']='新版'
        self.characters.save(profile, 1)
        self.run_to(2400)
        self.assertEqual(self.row()['phase'], 'needs_confirmation')
        self.assertFalse(self.owner.activations)

    def test_latest_unsent_installation_replaces_old_target(self):
        self.owner.lab.device.page='other';self.run_to(4000)
        self.confirm('b', 'first');self.run_to(4200)
        self.confirm('a', 'latest');self.run_to(4400)
        self.owner.lab.device.page='home';self.run_to(9000)
        self.assertEqual(self.row('first')['phase'], 'superseded')
        self.assertEqual(self.row()['installation_id'], 'latest')
        self.assertEqual(self.row()['phase'], 'saved')
        self.assertEqual(len(self.owner.activations), 1)

    def test_saved_profile_change_retains_fact_and_requires_confirmation(self):
        self.confirm();self.run_to(6500)
        original=deepcopy(self.row()['profile'])
        profile=deepcopy(self.characters.profiles[1]);profile['persona']='新版'
        self.characters.save(profile,1);self.run_to(6800)
        self.assertEqual(self.row()['phase'],'needs_confirmation')
        self.assertTrue(self.row()['saved'])
        self.assertEqual(self.row()['profile'],original)
        self.assertEqual(len(self.owner.activations),1)

    def test_saved_binding_change_requires_confirmation(self):
        self.confirm();self.run_to(6500)
        self.registry.confirm('b','profile-a',1,None,None,self.registry.state()['revision'])
        self.run_to(6800)
        self.assertEqual(self.row()['phase'],'needs_confirmation')
        self.assertTrue(self.row()['saved'])
        self.assertEqual(self.row()['binding']['role_id'],'profile-b')
        self.assertEqual(len(self.owner.activations),1)

    def test_sent_old_installation_finishes_before_latest(self):
        self.confirm('b','first');self.run_to(1700)
        self.assertEqual(len(self.owner.activations),1)
        self.confirm('a','latest');self.run_to(9000)
        self.assertEqual(self.row('first')['phase'],'saved')
        self.assertEqual(self.row()['installation_id'],'latest')
        self.assertEqual(self.row()['phase'],'saved')
        self.assertEqual(self.service.state()['device']['actual'],'round')
        self.assertEqual(len(self.owner.activations),2)

    def test_reopen_reconciles_saved_device_without_resending(self):
        self.confirm();self.run_to(2600)
        self.service.close();self.run_to_device(5000)
        self.service=self.make();self.run_to(6500)
        self.assertEqual(self.row()['phase'],'saved')
        self.assertEqual(self.row()['completion_source'],'device_snapshot')
        self.assertEqual(len(self.owner.activations),1)

    def run_to_device(self,end):
        for at in range(self.clock.ms+100,end+1,100):self.owner.advance(at)

    def test_reopen_different_device_state_requires_explicit_retry(self):
        self.confirm();self.run_to(2600);self.service.close()
        self.owner.lab.device.changed_at=None
        self.owner.lab.device.active=self.owner.lab.device.saved=self.owner.lab.device.target=0
        self.owner.lab.device.phase='idle';self.owner.lab.device.request_id='';self.run_to_device(5000)
        self.service=self.make();self.run_to(6500)
        self.assertEqual(self.row()['phase'],'retry_required')
        self.assertEqual(len(self.owner.activations),1)
        self.service.retry('install-1',self.service.state()['revision']);self.run_to(11000)
        self.assertEqual(self.row()['phase'],'saved')
        self.assertEqual(len(self.owner.activations),2)

    def test_snapshot_current_alone_never_confirms_saved(self):
        self.confirm();self.run_to(2600);self.service.close()
        self.owner.lab.device.changed_at=None;self.owner.lab.device.active=1
        self.owner.lab.device.phase='applied';self.owner.lab.device.save_state='pending'
        self.run_to_device(5000);self.service=self.make();self.run_to(6500)
        self.assertFalse(self.row()['saved'])
        self.assertEqual(self.row()['phase'],'reconciling')
        self.assertTrue(self.row()['first_frame'])

    def test_reopen_with_queued_owner_command_waits_for_new_role_query(self):
        self.confirm();self.run_to(1700);self.service.close()
        self.service=self.make();self.service.tick()
        self.assertEqual(self.row()['phase'],'reconciling')
        self.assertFalse(self.row()['saved'])
        self.run_to(7000)
        self.assertEqual(self.row()['phase'],'saved')
        self.assertEqual(self.row()['completion_source'],'device_snapshot')
        self.assertEqual(len(self.owner.activations),1)

    def test_reopen_drains_older_sent_installation_before_latest(self):
        self.confirm('b','first');self.run_to(2600)
        self.confirm('a','latest');self.service.close()
        self.service=self.make();self.run_to(11000)
        self.assertEqual(self.row('first')['phase'],'saved')
        self.assertEqual(self.row('first')['completion_source'],'device_snapshot')
        self.assertEqual(self.row()['installation_id'],'latest')
        self.assertEqual(self.row()['phase'],'saved')
        self.assertEqual(self.service.state()['device']['actual'],'round')
        self.assertEqual(len(self.owner.activations),2)

    def test_confirm_before_first_reopened_tick_preserves_sent_installation(self):
        self.confirm('b','first');self.run_to(1700);self.service.close()
        self.service=self.make();self.confirm('a','latest')
        self.assertNotEqual(self.row('first')['phase'],'superseded')
        self.assertTrue(self.service.busy())
        self.run_to(11000)
        self.assertEqual(self.row('first')['phase'],'saved')
        self.assertEqual(self.row('first')['completion_source'],'device_snapshot')
        self.assertEqual(self.row()['phase'],'saved')
        self.assertEqual(self.service.state()['device']['actual'],'round')
        self.assertEqual(len(self.owner.activations),2)

    def test_save_failure_has_explicit_retry(self):
        self.owner.lab.device.fail_save=True
        self.confirm();self.run_to(6500)
        self.assertEqual(self.row()['phase'],'failed')
        self.assertTrue(self.row()['first_frame']);self.assertFalse(self.row()['saved'])
        self.service.retry('install-1',self.service.state()['revision']);self.run_to(11000)
        self.assertEqual(self.row()['phase'],'saved')

    def test_old_retry_cannot_reclaim_latest_target(self):
        self.owner.lab.device.fail_save=True
        self.confirm('b','first');self.run_to(6500)
        self.confirm('a','latest')
        with self.assertRaises(ValueError):self.service.retry('first',self.service.state()['revision'])

    def test_storage_failure_before_io_leaves_no_activation(self):
        self.confirm()
        self.service.writer=lambda *args: (_ for _ in ()).throw(OSError('disk full'))
        with self.assertRaises(RoleStorageError):self.service.tick()
        self.assertFalse(self.owner.activations)

    def test_automatic_stays_paused_after_manual_confirmation(self):
        from tests.test_shell_switch_service import ConfigFixture
        from protocol.shell_switch_runtime import ShellSwitchRuntime
        config=ConfigFixture().value
        self.automatic.runtime=ShellSwitchRuntime(config['bindings'],device_id=DEVICE,
            archive=config['archive'],context=config['context'])
        self.automatic.enabled=True
        self.confirm()
        self.assertFalse(self.automatic.enabled)
        self.assertTrue(self.row()['automatic_was_enabled'])
        with self.assertRaises(RuntimeConflict):self.automatic.start('anything',self.automatic.generation)
        self.run_to(6500)
        self.assertFalse(self.automatic.enabled)

    def test_status_reads_do_not_schedule_and_scratch_reopens(self):
        count=len(self.owner.lab.commands)
        self.service.state();self.service.state()
        self.assertEqual(len(self.owner.lab.commands),count)
        self.confirm();self.service.close();self.service=self.make()
        self.assertEqual(self.row()['character_id'],'same-individual')

    def test_lost_owner_return_queries_original_without_repeat(self):
        self.owner.lost_return=True
        self.confirm();self.run_to(22000)
        self.assertEqual(self.row()['phase'],'saved')
        self.assertEqual(len(self.owner.activations),1)

    def test_lost_owner_return_then_profile_edit_still_finishes_sent_result(self):
        self.owner.lost_return=True;self.confirm();self.service.tick()
        self.assertEqual(len(self.owner.activations),1)
        profile=deepcopy(self.characters.profiles[1]);profile['persona']='新版'
        self.characters.save(profile,1)
        self.service.tick()
        self.assertNotEqual(self.row()['phase'],'needs_confirmation')
        self.run_to(22000)
        self.assertEqual(self.row()['phase'],'needs_confirmation')
        self.assertTrue(self.row()['first_frame'] and self.row()['saved'])
        self.assertEqual(self.row()['device_result']['saved'],'old')
        self.assertEqual(len(self.owner.activations),1)

    def test_manual_path_does_not_parse_magnetic_sources(self):
        self.sessions.registration_sources=lambda: (_ for _ in ()).throw(AssertionError('magnetic path'))
        self.confirm();self.run_to(6000)
        self.assertEqual(self.row()['phase'],'saved')


class InstallationHTTPTests(unittest.TestCase):
    def setUp(self):
        from tests.test_character_profiles import service_module
        self.case=InstallationTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),service_module().Handler)
        self.server.shell_installation=self.case.service
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.opener=build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)

    def request(self, action, value=None, headers=None, raw=None):
        if value is not None:raw=json.dumps(value).encode('utf-8')
        headers=headers if headers is not None else {'Content-Type':'application/json','X-DeskBot-Studio':'shell-installation-v1'}
        try:
            with self.opener.open(Request(self.base+'/api/shell-installation/'+action,data=raw,headers=headers),timeout=5) as response:
                return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)

    def test_http_confirmation_executes_and_returns_exact_stored_result(self):
        value=self.case.request()
        code,state=self.request('confirm',value);self.assertEqual(code,200,state)
        self.case.run_to(6000)
        code,state=self.request('state');self.assertEqual(code,200)
        self.assertEqual(state['records'][0]['phase'],'saved')
        self.assertEqual(state['records'][0]['character_id'],'same-individual')
        self.assertEqual(self.request('confirm',value)[0],200)
        self.assertEqual(len(self.case.owner.activations),1)

    def test_http_conflict_returns_latest_state(self):
        value=self.case.request();self.request('confirm',value)
        code,state=self.request('confirm',dict(value,plan_id='another'))
        self.assertEqual(code,409)
        self.assertEqual(state['state']['current'],'install-1')

    def test_origin_header_duplicate_fields_and_schema_rejections(self):
        request=self.case.request()
        code,_=self.request('confirm',request,headers={'Origin':'https://example.invalid','Content-Type':'application/json','X-DeskBot-Studio':'shell-installation-v1'})
        self.assertEqual(code,403)
        self.assertEqual(self.request('confirm',request,headers={'Content-Type':'application/json'})[0],415)
        self.assertEqual(self.request('confirm',dict(request,extra=True))[0],422)
        self.assertEqual(self.request('confirm',raw=b'{"installation_id":"a","installation_id":"b"}')[0],422)
        self.assertFalse(self.case.owner.activations)

    def test_lost_http_response_reuses_original_installation(self):
        value=self.case.request();self.request('confirm',value)
        self.case.run_to(2100)
        self.assertEqual(self.request('confirm',value)[0],200)
        self.case.run_to(6500)
        self.assertEqual(len(self.request('state')[1]['records']),1)
        self.assertEqual(len(self.case.owner.activations),1)


if __name__=='__main__':unittest.main()
