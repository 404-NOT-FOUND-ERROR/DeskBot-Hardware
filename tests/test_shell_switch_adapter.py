"""Offline adapter tests over the production ControlLink and role reply codecs."""
from copy import deepcopy
import json
import unittest

from protocol.shell_switch_adapter import ShellSwitchAdapter, project_bindings, IDENTITY
from protocol.shell_switch_lab import SessionLab, SCENARIOS, run_session_lab, DEVICE, BOOT
from protocol.studio_control_bridge import validate_control
from protocol.studio_control_link import initial_snapshot, aged_control
from tests import test_studio_control_link as host
from tests import test_studio_device_roles_e3f9d as device

GRAPH = dict(roles=['round','old'],sha256='a'*64)


def bindings():
    return {s:dict(shell_id=s,shell_name=s,role_name=s,role_id='character-'+s,profile_revision=1,
        expression_role_id=e,expression_graph_sha256=GRAPH['sha256'],binding_sha256=s*64,ready=True,
        source=dict(device_id=DEVICE,archive_sha256='f'*64,magnet_profile_id='profile-'+s))
        for s,e in [('a','round'),('b','old')]}


def control(sequence=3):
    value = initial_snapshot('connection-'+'c'*32)
    reply = device.reply(seq=sequence)|dict(roles=GRAPH['roles'],default_index=0,target_index=0,
        current_index=0,active_index=0,saved_index=0,save_state='saved')
    value.update(enabled=True,status='ready',reason='sleep_unknown',target_id=DEVICE,boot_id=BOOT,
        session_id=host.SESSION,display=host.display('display-1'),age_ms=0,can_submit=True,
        roles=dict(status='current',reply=reply,age_ms=0,pending=None,error=None))
    return value


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.bindings=bindings();self.adapter=ShellSwitchAdapter(self.bindings,device_id=DEVICE)
        self.c=control();self.adapter.update(self.c,0)

    def fact(self,shell='b',seq=1,**changes):
        return dict(identity={k:self.c[k] for k in IDENTITY},sequence=seq,archive_sha256='f'*64,
                    observation=dict(status='known',profile_id='profile-'+shell))|changes

    def ready(self,shell='b'):
        self.adapter.observe(self.fact(shell,1),0);self.adapter.observe(self.fact(shell,2),800)
        return self.adapter.next_command(800)

    def result(self,command,at,*,status='applied',saved=True,result='state',outer='query-2',activation='wire-1',seq=4):
        c=control(seq)
        index=int(command['operation'].split('.')[2])
        c['last_command']=dict(command_id=command['command_id'],operation=command['operation'],status='processed',reason='command_processed')
        c['role_attempt']=dict(command_id=command['command_id'],operation=command['operation'],request_id='wire-1')
        c['roles']['reply'].update(request_id=outer,result=result,target_index=index,
            active_index=index if status=='applied' else 0,current_index=index if status=='applied' else 0,
            saved_index=index if saved else 0,save_state='saved' if saved else 'pending',
            activation=dict(request_id=activation,status=status))
        self.c=c;return self.adapter.update(c,at)

    def test_command_uses_current_device_directory_and_explicit_expression_role(self):
        command=self.ready();self.assertIsNotNone(command)
        self.assertEqual(command['operation'],'role.activate.1.'+'a'*64)
        self.assertEqual(command['session_id'],self.c['session_id'])
        self.assertEqual(self.adapter.attempt['target']['role_id'],'character-b')
        self.assertIsNone(self.adapter.next_command(900))

    def test_queued_first_frame_and_save_are_separate(self):
        command=self.ready()
        self.result(command,900,status='queued',saved=False,result='accepted',outer='wire-1')
        self.assertNotEqual(self.adapter.coordinator.actual.get('role_id'),'character-b')
        self.result(command,1000,saved=False,seq=5)
        self.assertEqual(self.adapter.coordinator.actual['role_id'],'character-b')
        self.assertNotEqual(self.adapter.coordinator.saved.get('role_id'),'character-b')
        self.result(command,1100,seq=6)
        self.assertEqual(self.adapter.coordinator.saved['role_id'],'character-b')
        self.assertEqual(self.adapter.attempt['status'],'saved')

    def test_rejection_envelope_matches_even_when_activation_is_old(self):
        command=self.ready();c=control(4)
        c['role_attempt']=dict(command_id=command['command_id'],operation=command['operation'],request_id='wire-1')
        c['roles']['reply'].update(request_id='wire-1',result='rejected',reason='player_unavailable')
        self.adapter.update(c,900)
        self.assertEqual(self.adapter.error,'player_unavailable')
        self.assertIsNone(self.adapter.next_command(1000))

    def test_unrelated_activation_cannot_confirm_request(self):
        command=self.ready();self.result(command,1000,activation='someone-else')
        self.assertFalse(self.adapter.attempt['first_frame'])

    def test_save_failure_requires_explicit_retry(self):
        command=self.ready();self.result(command,1000,saved=False)
        c=deepcopy(self.c);c['roles']['reply'].update(sequence=5,save_state='failed')
        self.adapter.update(c,1100)
        self.assertEqual(self.adapter.error,'save_failed')
        self.assertEqual(self.adapter.coordinator.actual['role_id'],'character-b')
        self.assertIsNone(self.adapter.next_command(1200))
        self.adapter.retry(1300)
        retry=self.adapter.next_command(1300)
        self.assertIsNotNone(retry);self.assertNotEqual(command['command_id'],retry['command_id'])
        self.assertEqual(command['operation'],retry['operation'])

    def test_taken_off_before_handoff_produces_no_command(self):
        self.adapter.observe(self.fact(seq=1),0);self.adapter.observe(self.fact(seq=2),800)
        self.adapter.observe(self.fact(seq=3,observation=dict(status='removed',profile_id=None)),850)
        self.assertIsNone(self.adapter.next_command(850))

    def test_sent_request_finishes_after_removal(self):
        command=self.ready()
        self.adapter.observe(self.fact(seq=3,observation=dict(status='removed',profile_id=None)),850)
        self.result(command,1000)
        self.assertEqual(self.adapter.attempt['status'],'saved')
        self.assertIsNone(self.adapter.coordinator.target)

    def test_known_requires_device_archive_and_verified_binding(self):
        for change in ({'archive_sha256':'e'*64},{'identity':dict(self.fact()['identity'],target_id='device-'+'e'*32)}):
            with self.subTest(change=change):
                self.setUp()
                self.adapter.observe(self.fact(seq=1,**change),0)
                self.adapter.observe(self.fact(seq=2,**change),800)
                self.assertIsNone(self.adapter.next_command(800))
        self.adapter.coordinator.bindings['b']['ready']=False
        self.adapter.observe(self.fact(seq=3),900);self.adapter.observe(self.fact(seq=4),1700)
        self.assertIsNone(self.adapter.next_command(1700))

    def test_repeated_same_fact_does_not_refresh_stability_or_age(self):
        fact=self.fact()
        self.adapter.observe(fact,0)
        for at in (500,1000,1600,2000):
            self.adapter.observe(fact,at)
        self.assertIsNone(self.adapter.coordinator.pending)
        self.assertIsNone(self.adapter.coordinator.candidate)
        self.assertIsNone(self.adapter.next_command(2000))

    def test_old_connection_and_sequence_do_not_unlock_new_session(self):
        self.ready();old=deepcopy(self.c)
        c=control(4);c['connection_id']='connection-'+'d'*32;c['session_id']='session-'+'b'*32+'-2'
        c['display']['session_id']=c['roles']['reply']['session_id']=c['session_id']
        self.adapter.update(c,900)
        self.adapter.update(old,1000)
        self.assertEqual(self.adapter.identity[0],c['connection_id'])
        self.assertIsNone(self.adapter.coordinator.pending)
        self.adapter.observe(self.fact(seq=3),1100)
        self.assertIsNone(self.adapter.coordinator.candidate)

    def test_stale_repeat_of_state_does_not_make_a_reply_younger(self):
        for at in (1000,1900,2100):self.adapter.update(self.c,at)
        self.assertEqual(self.adapter.reason,'fresh_display_required')

    def test_old_disabled_connection_cannot_retire_current_session(self):
        old_connection=self.c['connection_id']
        c=control(4);c['connection_id']='connection-'+'d'*32
        self.adapter.update(c,900)
        self.adapter.update(initial_snapshot(old_connection),1000)
        self.adapter.update(c,1100)
        self.assertEqual(self.adapter.identity[0],c['connection_id'])
        self.assertNotIn(tuple(c[k] for k in IDENTITY),self.adapter.retired)

    def test_graph_mismatch_prevents_operation(self):
        self.c['roles']['reply']['graph_sha256']='d'*64
        self.adapter.update(self.c,0)
        self.assertIsNone(self.ready())
        self.assertEqual(self.adapter.reason,'content_required')

    def test_binding_update_after_send_records_device_fact_without_confirming_new_binding(self):
        command=self.ready();new=deepcopy(self.bindings)
        new['b']['binding_sha256']='d'*64;new['b']['profile_revision']=2
        self.adapter.replace_bindings(new,850);self.result(command,1000)
        self.assertEqual(self.adapter.attempt['status'],'superseded')
        self.assertNotEqual(self.adapter.coordinator.actual.get('profile_revision'),2)
        self.assertEqual(self.adapter.snapshot()['device_expression']['actual'],'old')

    def test_invalid_snapshot_and_nonmonotonic_time(self):
        with self.assertRaises(ValueError):self.adapter.update({},0)
        self.adapter.next_command(100)
        with self.assertRaises(ValueError):self.adapter.next_command(99)

    def test_changed_binding_waits_for_old_save_terminal_then_converges(self):
        for failed in (False,True):
            with self.subTest(failed=failed):
                self.setUp();command=self.ready();new=deepcopy(self.bindings)
                new['b']['binding_sha256']='d'*64;new['b']['profile_revision']=2
                self.adapter.replace_bindings(new,850)
                self.adapter.observe(self.fact(seq=3),900);self.adapter.observe(self.fact(seq=4),1700)
                self.result(command,1800,saved=False,seq=5)
                self.assertEqual(self.adapter.attempt['status'],'visible')
                self.assertIsNone(self.adapter.next_command(1800))
                if failed:
                    c=deepcopy(self.c);c['roles']['reply'].update(sequence=6,save_state='failed')
                    self.adapter.update(c,1900)
                    self.assertTrue(any(t['reason']=='previous_binding_save_failed' for t in self.adapter.trace))
                else:self.result(command,1900,seq=6)
                self.assertEqual(self.adapter.attempt['status'],'superseded')
                self.assertIsNotNone(self.adapter.next_command(1900))
                self.assertEqual(self.adapter.attempt['target']['profile_revision'],2)

    def test_timeout_queries_before_retry_and_late_save_completes_same_request(self):
        command=self.ready()
        self.adapter.read_requested=True  # An earlier directory query has already completed.
        self.adapter.update(control(5),18800)
        self.assertEqual(self.adapter.error,'result_unknown')
        query=self.adapter.next_command(18800)
        self.assertEqual(query['operation'],'read_roles')
        self.assertIsNone(self.adapter.next_command(18801))
        self.result(command,19000,seq=6)
        self.assertIsNone(self.adapter.error)
        self.assertEqual(self.adapter.attempt['status'],'saved')
        self.assertIsNone(self.adapter.next_command(19000))

    def test_handoff_response_loss_queries_instead_of_resending(self):
        command=self.ready();self.adapter.submission_failed(command['command_id'],900)
        self.assertEqual(self.adapter.next_command(900)['operation'],'read_roles')
        self.result(command,1100,seq=5)
        self.assertEqual(self.adapter.attempt['status'],'saved')
        self.assertIsNone(self.adapter.error)

    def test_handoff_not_sent_queries_then_offers_explicit_retry(self):
        command=self.ready();self.adapter.submission_failed(command['command_id'],900)
        self.assertEqual(self.adapter.next_command(900)['operation'],'read_roles')
        self.adapter.update(control(4),1000)
        self.assertEqual(self.adapter.error,'retry_required')
        self.assertIsNone(self.adapter.next_command(1000))
        self.adapter.retry(1100)
        retry=self.adapter.next_command(1100)
        self.assertEqual(retry['operation'],command['operation'])
        self.assertNotEqual(retry['command_id'],command['command_id'])

    def test_legacy_worker_without_request_correlation_cannot_confirm_completion(self):
        command=self.ready();c=control(4)
        c.pop('role_attempt');c['roles']['reply'].update(active_index=1,current_index=1,saved_index=1)
        self.adapter.update(c,900)
        self.assertFalse(self.adapter.attempt['first_frame'])
        self.assertNotEqual(self.adapter.coordinator.actual.get('role_id'),'character-b')

    def test_visible_then_timeout_query_pending_and_saved_keep_original_decision(self):
        command=self.ready();self.result(command,1000,saved=False)
        self.adapter.next_command(19000)
        self.assertEqual(self.adapter.error,'result_unknown')
        self.result(command,19100,saved=False,seq=5)
        self.assertEqual(self.adapter.attempt['status'],'visible')
        self.assertIsNotNone(self.adapter.coordinator.pending)
        self.result(command,20000,seq=6)
        self.assertEqual(self.adapter.coordinator.saved['role_id'],'character-b')
        self.assertIsNone(self.adapter.next_command(20000))

    def test_other_requests_rejection_does_not_cancel_matching_activation(self):
        command=self.ready()
        self.result(command,1000,status='queued',saved=False,result='rejected',outer='other-request')
        self.assertIsNone(self.adapter.error)
        self.result(command,1100,seq=5)
        self.assertEqual(self.adapter.coordinator.saved['role_id'],'character-b')

    def test_projected_production_binding_requires_validated_registration(self):
        source=dict(device_id=DEVICE,archive_sha256='f'*64,magnet_profile_id='b')
        binding=dict(role_id='character-b',profile_revision=1,expression_role_id='old',
                     expression_graph_sha256='a'*64,source=source)
        state=dict(shells=[dict(shell_id='b',name='B',role_name='伙伴',binding=binding,
                              role_current=True,recognition='historical_only')])
        self.assertFalse(project_bindings(state)['b']['ready'])
        state['shells'][0]['recognition']='validated_offline'
        self.assertTrue(project_bindings(state)['b']['ready'])


class LinkCorrelationTests(host.LinkTests):
    def test_host_to_wire_id_survives_queries_and_clears_on_disconnect(self):
        self.ready();self.command('read_roles',session_id=host.SESSION,now=420)
        wire=json.loads(self.link.tick(800));self.link.accept(device.reply(wire['request_id']),810)
        self.command('role.activate.0.'+'a'*64,session_id=host.SESSION,now=820,command_id='automatic-1')
        self.assertIsNone(self.link.snapshot(820)['role_attempt'])
        wire=json.loads(self.link.tick(1200));attempt=self.link.snapshot(1200)['role_attempt']
        self.assertEqual(attempt['command_id'],'automatic-1');self.assertEqual(attempt['request_id'],wire['request_id'])
        self.link.accept(device.reply(wire['request_id'],4,1210,'accepted'),1210)
        self.assertTrue(validate_control(self.link.snapshot(1210)))
        self.assertEqual(self.link.snapshot(2000)['role_attempt'],attempt)
        self.assertIsNone(aged_control(self.link.snapshot(2000),0,connected=False)['role_attempt'])
        self.link.disconnect(2100);self.assertIsNone(self.link.snapshot(2100)['role_attempt'])

    def test_old_worker_snapshot_without_correlation_remains_valid(self):
        row=control();row.pop('role_attempt')
        self.assertTrue(validate_control(row))
        row['role_attempt']=dict(command_id='c',operation='read_roles',request_id='r')
        self.assertFalse(validate_control(row))


class SessionIntegrationTests(unittest.TestCase):
    def test_all_protocol_scenarios_and_terminal_behaviors(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                report=run_session_lab(bindings(),GRAPH,scenario,'a','b')
                self.assertEqual(report['device_commands'],0)
                self.assertTrue(report['wire'])
                self.assertIsNone(report['adapter']['error'])
                actual=report['state']['actual']
                expected='character-a' if scenario in ('rapid','removed','unknown','stale_fact') else 'character-b'
                self.assertEqual(actual['role_id'],expected)
                self.assertLessEqual(len(report['timeline']),160)
                if scenario=='unknown':self.assertTrue(report['state']['registration_prompt'])
                if scenario=='save_failure':self.assertTrue(any(r['reason']=='save_failed' for r in report['timeline']))
                if scenario=='rejected':self.assertTrue(any(r['reason']=='player_unavailable' for r in report['timeline']))


if __name__=='__main__':unittest.main()
