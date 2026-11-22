"""Full-character associations and switch timing over production persistence and HTTP."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol.character_profiles import CharacterProfiles, CharacterConflict
from protocol.character_switch import SwitchCoordinator, simulate, SCENARIOS
from protocol.role_activation import RoleStorageError
from protocol.shell_characters import ShellCharacters
from protocol.shell_workbench import SessionStore
from tests import test_character_profiles as character_tests

ROOT = Path(__file__).resolve().parents[1]
GRAPH = dict(sha256='a'*64, roles=['round', 'old'], names={'round':'圆团','old':'原有角色'}, source='test')
CATALOG = dict(catalog_id='b'*64, roles=[dict(id='round', name='圆团'),dict(id='old',name='原有角色')])
CONTEXT = dict(sensor_id='test', frame_id='fixed', sensor_kind=1, units='transport_counts')


def character(ident, expression):
    return dict(schema='deskbot.role_profile.v2', role_id=ident, display_name=ident, persona='好奇',
        voice=dict(provider='unconfigured',voice_id='',speaking_style=''), expression_role_id=expression,
        expression_graph_sha256=GRAPH['sha256'], preview_catalog_id=CATALOG['catalog_id'], profile_revision=0)


def bindings():
    return {s: dict(shell_id=s, role_id='character-'+s, profile_revision=1, expression_role_id=e,
                    binding_sha256=s*64,ready=True,role_name=s) for s,e in [('a','round'),('b','old')]}


class SwitchTests(unittest.TestCase):
    def setUp(self): self.c=SwitchCoordinator(bindings())
    def send(self, at, kind, **values): return self.c.feed(dict(at_ms=at,type=kind,**values))
    def known(self, at, shell): return self.send(at,'observe',status='known',shell_id=shell)
    def ack(self, at, phase):
        p=self.c.pending
        return self.send(at,'receipt',request_id=p['request_id'],epoch=p['epoch'],phase=phase)
    def activate_a(self):
        self.known(0,'a'); self.send(800,'tick'); self.ack(850,'first_frame'); self.ack(850,'saved')

    def test_stability_first_frame_and_save_are_distinct(self):
        self.known(0,'a'); self.send(799,'tick'); self.assertIsNone(self.c.pending)
        self.send(800,'tick'); self.assertIsNone(self.c.actual)
        self.ack(810,'saved'); self.assertIsNone(self.c.saved)
        self.ack(820,'first_frame'); self.assertEqual(self.c.actual['role_id'],'character-a'); self.assertIsNone(self.c.saved)
        self.ack(830,'saved'); self.assertEqual(self.c.saved,self.c.actual)

    def test_speaking_defers_until_sentence_finishes(self):
        self.activate_a(); self.send(900,'mode',mode='speaking'); self.known(1000,'b'); self.send(1800,'tick')
        self.assertEqual(self.c.target['role_id'],'character-b'); self.assertIsNone(self.c.pending)
        self.send(2000,'mode',mode='idle'); self.assertEqual(self.c.pending['target']['role_id'],'character-b')

    def test_last_stable_choice_replaces_waiting_target(self):
        self.activate_a(); self.send(900,'mode',mode='speaking'); self.known(1000,'b'); self.send(1800,'tick')
        self.known(1900,'a'); self.send(2700,'tick'); self.send(2800,'mode',mode='idle')
        self.assertIsNone(self.c.pending); self.assertEqual(self.c.target['role_id'],'character-a')
        self.assertEqual(sum(r['event']=='request' for r in self.c.trace),1)

    def test_removal_or_unknown_cancels_unsent_target(self):
        for status in ('removed','unknown','missing','unstable','stale'):
            with self.subTest(status=status):
                self.setUp(); self.activate_a(); self.send(900,'mode',mode='speaking');self.known(1000,'b');self.send(1800,'tick')
                self.send(1900,'observe',status=status,shell_id=None);self.send(2000,'mode',mode='idle')
                self.assertIsNone(self.c.pending);self.assertIsNone(self.c.target);self.assertEqual(self.c.actual['role_id'],'character-a')

    def test_interrupted_stream_does_not_switch_after_speaking(self):
        self.activate_a();self.send(900,'mode',mode='speaking');self.known(1000,'b');self.send(1800,'tick')
        self.send(4000,'mode',mode='idle');self.assertIsNone(self.c.target);self.assertIsNone(self.c.pending)
        self.known(4100,'b');self.send(4899,'tick');self.assertIsNone(self.c.pending)
        self.send(4900,'tick');self.assertIsNotNone(self.c.pending)

    def test_motion_requires_new_post_motion_observation(self):
        self.activate_a();self.send(900,'mode',mode='moving');self.known(1000,'b');self.send(1800,'tick')
        self.send(1900,'mode',mode='idle');self.assertIsNone(self.c.pending)
        self.known(2000,'b');self.send(2800,'tick');self.assertIsNotNone(self.c.pending)

    def test_observation_after_gap_starts_a_new_interval_without_an_intermediate_tick(self):
        self.activate_a(); self.known(1000,'b')
        self.known(5000,'b'); self.assertIsNone(self.c.pending)
        self.send(5799,'tick'); self.assertIsNone(self.c.pending)
        self.send(5800,'tick'); self.assertEqual(self.c.pending['target']['role_id'],'character-b')

    def test_content_waiting_rechecks_freshness(self):
        self.activate_a();self.send(900,'availability',ready=False);self.known(1000,'b');self.send(1800,'tick')
        self.assertIsNone(self.c.pending);self.send(4000,'availability',ready=True);self.assertIsNone(self.c.pending)
        self.known(4100,'b');self.send(4900,'tick');self.assertIsNotNone(self.c.pending)

    def test_unknown_prompts_once_per_unknown_episode(self):
        self.activate_a();self.send(1000,'observe',status='unknown',shell_id=None);self.send(2999,'tick')
        self.assertFalse(self.c.prompted)  # One old unknown sample does not establish sustained unknown.
        for at in (3000,4000,5000,6000): self.send(at,'observe',status='unknown',shell_id=None)
        self.assertEqual(sum(r['event']=='prompt' for r in self.c.trace),1)
        self.send(6100,'observe',status='removed',shell_id=None)
        for at in (6200,7200,8200): self.send(at,'observe',status='unknown',shell_id=None)
        self.assertEqual(sum(r['event']=='prompt' for r in self.c.trace),2)

    def test_reconnect_waits_for_snapshot_and_fresh_observation(self):
        self.activate_a();self.known(1000,'b');self.send(1800,'tick');old=deepcopy(self.c.pending)
        self.send(1900,'connection',connected=False);self.send(2000,'connection',connected=True)
        self.known(2100,'b');self.send(2900,'tick');self.assertIsNone(self.c.pending)
        self.send(2950,'receipt',request_id=old['request_id'],epoch=old['epoch'],phase='first_frame');self.assertIsNone(self.c.actual)
        self.send(3000,'device_snapshot',epoch=self.c.epoch,expression_role_id='old',saved_expression_role_id='round')
        self.assertEqual(self.c.actual['expression_role_id'],'old');self.assertIsNotNone(self.c.pending)

    def test_reconnect_discards_previous_connection_snapshot(self):
        self.activate_a(); old_epoch=self.c.epoch
        self.send(1000,'connection',connected=False); self.send(1100,'connection',connected=True)
        self.known(1200,'b'); self.send(2000,'tick')
        self.send(2050,'device_snapshot',epoch=old_epoch,expression_role_id='round',saved_expression_role_id='round')
        self.assertFalse(self.c.synced); self.assertIsNone(self.c.pending); self.assertIsNone(self.c.actual)
        self.send(2100,'device_snapshot',epoch=self.c.epoch,expression_role_id='round',saved_expression_role_id='round')
        self.assertTrue(self.c.synced); self.assertIsNotNone(self.c.pending)

    def test_same_expression_does_not_imply_same_full_character(self):
        self.c.bindings['b']['expression_role_id']='round'
        self.activate_a();self.known(1000,'b');self.send(1800,'tick')
        self.assertEqual(self.c.pending['target']['role_id'],'character-b')

    def test_shared_full_character_needs_no_extra_activation(self):
        self.c.bindings['b'].update(role_id='character-a',expression_role_id='round')
        self.activate_a();self.known(1000,'b');self.send(1800,'tick');self.assertIsNone(self.c.pending)

    def test_changed_binding_discards_old_receipt(self):
        self.known(0,'a');self.send(800,'tick');self.c.bindings['a']['ready']=False
        self.ack(850,'first_frame');self.assertIsNone(self.c.actual);self.assertIsNone(self.c.pending)

    def test_same_role_new_binding_retries_only_with_latest_confirmed_version(self):
        self.known(0,'a'); self.send(800,'tick'); old=deepcopy(self.c.pending)
        self.c.bindings['a']['binding_sha256']='c'*64
        self.send(850,'receipt',request_id=old['request_id'],epoch=old['epoch'],phase='first_frame')
        self.assertIsNone(self.c.actual)
        self.assertEqual(self.c.target['binding_sha256'],'c'*64)
        self.assertEqual(self.c.pending['target']['binding_sha256'],'c'*64)
        self.assertNotEqual(self.c.pending['request_id'],old['request_id'])
        self.ack(900,'first_frame'); self.ack(910,'saved')
        self.assertEqual(self.c.saved['binding_sha256'],'c'*64)
        self.assertEqual(sum(r['reason']=='binding_updated' for r in self.c.trace),1)

    def test_wrong_request_or_epoch_never_changes_actual(self):
        self.known(0,'a');self.send(800,'tick');p=self.c.pending
        self.send(850,'receipt',request_id='wrong',epoch=p['epoch'],phase='first_frame')
        self.send(860,'receipt',request_id=p['request_id'],epoch=p['epoch']+1,phase='first_frame')
        self.assertIsNone(self.c.actual)

    def test_all_product_scenarios_are_executable(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                report=simulate(bindings(),scenario,'a','b')
                self.assertEqual(report['device_commands'],0)
                self.assertTrue(report['timeline']);self.assertEqual(report['state']['voice_service'],'pending')
        self.assertEqual(simulate(bindings(),'speaking','a','b')['state']['actual']['role_id'],'character-b')
        self.assertEqual(simulate(bindings(),'rapid','a','b')['state']['actual']['role_id'],'character-a')

    def test_event_validation_and_monotonic_clock(self):
        for event in [dict(at_ms=True,type='tick'),dict(at_ms=0,type='tick',extra=1),dict(at_ms=-1,type='tick'),
                      dict(at_ms=0,type='mode',mode='bad'),dict(at_ms=0,type='connection',connected=1)]:
            with self.assertRaises(ValueError):self.c.feed(event)
        self.send(100,'tick')
        with self.assertRaises(ValueError):self.send(99,'tick')


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='shell-characters-');self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.characters=CharacterProfiles(self.folder/'characters.json',catalog=CATALOG,graph=GRAPH);self.addCleanup(self.characters.close)
        self.sessions=SessionStore(self.folder/'sessions',project_root=ROOT,graph=GRAPH);self.addCleanup(self.sessions.close)
        self.store=ShellCharacters(self.folder/'shell-characters.json',characters=self.characters,sessions=self.sessions);self.addCleanup(self.store.close)
        self.profiles=[self.characters.save(character('character-'+e,e),0)['profile'] for e in ('round','old')]

    def add(self,ident='shell-a',name='A'):
        return self.store.save_shell(ident,name,self.store.state()['revision'])
    def confirm(self,ident='shell-a',role=0,source=None,revision=None):
        p=self.profiles[role]
        return self.store.confirm(ident,p['role_id'],p['profile_revision'],source['key'] if source else None,
            source['archive_sha256'] if source else None,self.store.state()['revision'] if revision is None else revision)
    def trained(self):
        self.doc=self.sessions.create(name='offline-test',device_id='device-test',context=CONTEXT,shell_names=['A','B'])['session']
        self.sequence=0
        for task in [t for t in self.doc['tasks'] if t['use']=='train']:
            self.window(task,100 if task['shell_id']==self.doc['shells'][0]['shell_id'] else 500)
        self.doc=self.sessions.evaluate(self.doc['id'],self.doc['revision'])['session']
        return self.sessions.registration_sources()
    def window(self,task,x):
        self.sequence+=1;start=datetime(2026,11,13,tzinfo=timezone.utc)+timedelta(seconds=self.sequence*30)
        payload=b'\x01'+x.to_bytes(2,'big',signed=True)+b'\x00\x14\x00\x1e'
        raw=''.join(f'{(start+timedelta(seconds=i*.5)).isoformat()}\tHandle: cmd=06, {payload.hex(" ")}\n' for i in range(22))
        self.doc=self.sessions.import_window(self.doc['id'],self.doc['revision'],task_id=task['id'],capture_id='capture-'+str(self.sequence),mount_id='mount-'+str(self.sequence),log_text=raw)['session']

    def test_create_confirm_and_reopen_without_samples(self):
        self.add();state=self.confirm();row=state['shells'][0]
        self.assertTrue(row['role_current']);self.assertEqual(row['recognition'],'not_registered')
        self.assertEqual(row['sync_status'],'pending_device');self.assertEqual(row['binding']['role_id'],'character-round')
        self.store.close();other=ShellCharacters(self.store.path,characters=self.characters,sessions=self.sessions);self.addCleanup(other.close)
        self.assertEqual(other.state(),state)

    def test_confirmation_retries_are_idempotent_and_versioned(self):
        self.add();state=self.confirm();again=self.confirm(revision=1);self.assertEqual(state,again)
        changed=self.confirm(role=1);self.assertEqual(changed['revision'],state['revision']+1)
        self.assertEqual(len(changed['shells']),1)
        with self.assertRaises(CharacterConflict):self.confirm(role=0,revision=state['revision'])

    def test_two_shells_can_share_one_character(self):
        self.add();self.confirm();self.add('shell-b','B');state=self.confirm('shell-b')
        self.assertEqual([s['binding']['role_id'] for s in state['shells']],['character-round']*2)

    def test_profile_edit_marks_binding_old_until_explicit_confirmation(self):
        self.add();self.confirm();p=deepcopy(self.profiles[0]);p['persona']='新性格'
        self.profiles[0]=self.characters.save(p,1)['profile']
        self.assertFalse(self.store.state()['shells'][0]['role_current'])
        self.assertEqual(self.store.state()['shells'][0]['next_action'],'confirm_role_update')
        self.assertTrue(self.confirm()['shells'][0]['role_current'])

    def test_stale_profile_revision_cannot_be_confirmed(self):
        self.add();p=deepcopy(self.profiles[0]);p['persona']='new';self.characters.save(p,1)
        with self.assertRaises(CharacterConflict):self.confirm()

    def test_source_links_current_archive_and_requires_reconfirmation_after_new_data(self):
        sources=self.trained();self.add();self.confirm(source=sources[0])
        self.assertEqual(self.store.state()['shells'][0]['recognition'],'needs_review')
        task=next(t for t in self.doc['tasks'] if t['scenario']=='remount' and t['shell_id']==self.doc['shells'][0]['shell_id'])
        self.window(task,100)
        self.assertEqual(self.store.state()['shells'][0]['recognition'],'source_updated')
        with self.assertRaises(CharacterConflict):self.confirm(source=sources[0])

    def test_one_magnetic_source_has_only_one_registered_shell(self):
        sources=self.trained();self.add();self.confirm(source=sources[0]);self.add('shell-b','B')
        with self.assertRaises(ValueError):self.confirm('shell-b',source=sources[0])

    def test_original_logs_are_reidentified_and_mapped_to_full_character(self):
        self.trained()
        for shell,x in zip(self.doc['shells'],(100,500)):
            self.window(next(t for t in self.doc['tasks'] if t['scenario']=='remount' and t['shell_id']==shell['shell_id']),x)
        self.doc=self.sessions.evaluate(self.doc['id'],self.doc['revision'])['session']
        sources=self.sessions.registration_sources()
        self.add();self.confirm(source=sources[0]);self.add('shell-b','B');self.confirm('shell-b',role=1,source=sources[1])
        before=list((self.folder/'sessions').rglob('*'))
        state=self.store.run(self.store.state()['revision'],session_id=self.doc['id'])
        self.assertEqual([r['role_id'] for r in state['last_run']['rows']],['character-round','character-old'])
        self.assertEqual(list((self.folder/'sessions').rglob('*')),before)
        self.assertEqual(state['last_run']['input_kind'],'stored_log_reidentification')

    def test_saved_rehearsal_becomes_old_after_config_edit(self):
        self.add();self.confirm();self.add('shell-b','B');self.confirm('shell-b',role=1)
        state=self.store.run(self.store.state()['revision'],scenario='speaking',shell_a='shell-a',shell_b='shell-b')
        self.assertTrue(state['last_run']['current']);self.add('shell-a','New name')
        self.assertFalse(self.store.state()['last_run']['current'])

    def test_original_sample_session_reopens_without_qualifying_incomplete_sources(self):
        sources=self.trained()
        for shell,x in zip(self.doc['shells'],(100,500)):
            self.window(next(t for t in self.doc['tasks'] if t['scenario']=='remount' and t['shell_id']==shell['shell_id']),x)
        self.doc=self.sessions.evaluate(self.doc['id'],self.doc['revision'])['session']
        sources=self.sessions.registration_sources()
        self.add();self.confirm(source=sources[0]);self.add('shell-b','B');self.confirm('shell-b',role=1,source=sources[1])
        before={p:p.read_bytes() for p in self.sessions.root.rglob('*') if p.is_file() and p.suffix!='.lock'}
        state=self.store.replay_session(self.store.state()['revision'],session_id=self.doc['id'])
        run=state['last_run']
        self.assertEqual(run['kind'],'runtime_replay');self.assertEqual(run['total_samples'],44)
        self.assertEqual([r['role_name'] for r in run['rows']],[p['display_name'] for p in self.profiles])
        self.assertTrue(all(r['recognition']['status']=='known' for r in run['rows']))
        self.assertTrue(all(not r['registration_ready'] for r in run['rows']))
        self.assertEqual(run['simulated_activation_count'],0)
        self.assertEqual({p:p.read_bytes() for p in self.sessions.root.rglob('*') if p.is_file() and p.suffix!='.lock'},before)
        self.store.close()
        reopened=ShellCharacters(self.store.path,characters=self.characters,sessions=self.sessions);self.addCleanup(reopened.close)
        self.assertEqual(reopened.state(),state)
        reopened.save_shell('shell-a','新名字',state['revision'])
        self.assertFalse(reopened.state()['last_run']['current'])

    def test_original_sample_session_verifies_source_hash_before_saving(self):
        self.trained()
        task=next(t for t in self.doc['tasks'] if t['scenario']=='remount')
        self.window(task,100)
        self.doc=self.sessions.evaluate(self.doc['id'],self.doc['revision'])['session']
        path=next(self.sessions.root.rglob('logs/*.log'))
        path.write_bytes(path.read_bytes()+b'changed\n')
        before=self.store.path.read_bytes() if self.store.path.exists() else None
        with self.assertRaises(ValueError):self.store.replay_session(self.store.state()['revision'],session_id=self.doc['id'])
        self.assertEqual(self.store.path.read_bytes() if self.store.path.exists() else None,before)

    def test_session_lab_reopens_with_pinned_versions_and_no_registration_edits(self):
        self.add();self.confirm();self.add('shell-b','B');self.confirm('shell-b',role=1)
        before=deepcopy(self.store.state()['shells'])
        state=self.store.run_session(self.store.state()['revision'],scenario='save_failure',shell_a='shell-a',shell_b='shell-b')
        run=state['last_run']
        self.assertEqual(run['kind'],'session_lab')
        self.assertEqual(run['state']['saved']['role_id'],'character-old')
        self.assertEqual(run['simulated_activation_count'],3)
        self.assertEqual(state['shells'],before)
        self.assertFalse(run['hardware_accessed']);self.assertEqual(run['device_commands'],0)
        self.store.close();other=ShellCharacters(self.store.path,characters=self.characters,sessions=self.sessions);self.addCleanup(other.close)
        self.assertEqual(other.state(),state)

    def test_session_lab_requires_confirmed_current_roles(self):
        self.add();self.confirm()
        p=deepcopy(self.profiles[0]);p['persona']='new';self.characters.save(p,1)
        with self.assertRaises(ValueError):
            self.store.run_session(self.store.state()['revision'],scenario='switch',shell_a='shell-a',shell_b='missing')

    def test_save_failure_retains_prior_registry_and_can_retry(self):
        self.add();before=self.store.path.read_bytes()
        with patch.object(self.store,'writer',side_effect=OSError('full')),self.assertRaises(RoleStorageError):self.confirm()
        self.assertEqual(self.store.path.read_bytes(),before);self.assertIsNone(self.store.state()['shells'][0]['binding'])
        self.assertTrue(self.confirm()['shells'][0]['binding'])

    def test_concurrent_confirmation_cannot_overwrite_another_choice(self):
        self.add();revision=self.store.state()['revision']
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(self.confirm,role=i,revision=revision) for i in (0,1)]
        self.assertEqual(sum(j.exception() is None for j in jobs),1)

    def test_boundaries_owner_and_close(self):
        for ident,name in [('../a','A'),('shell-a',''),('shell-a','a'*41)]:
            with self.assertRaises(ValueError):self.add(ident,name)
        self.add()
        with self.assertRaises(ValueError):self.add('shell-b','a')
        with self.assertRaises(RoleStorageError):ShellCharacters(self.store.path,characters=self.characters,sessions=self.sessions)
        with self.assertRaises(CharacterConflict):self.store.save_shell('shell-c','C',True)
        self.store.close()
        with self.assertRaises(RoleStorageError):self.store.state()


class ShellHTTPTests(character_tests.HTTPTests):
    """Inherit the established real server fixture; keep its character regressions too."""
    def shell_request(self,action,value=None,headers=None,raw=None):
        from urllib.request import Request
        from urllib.error import HTTPError
        if value is not None:raw=json.dumps(value).encode('utf-8')
        if raw is not None and headers is None:headers={'Content-Type':'application/json','X-DeskBot-Studio':'shell-characters-v1'}
        try:
            with self.opener.open(Request(self.origin+'/api/shell-characters/'+action,data=raw,headers=headers or {}),timeout=10) as r:return r.status,json.load(r)
        except HTTPError as error:return error.code,json.load(error)

    def test_product_http_flow_and_simulation_reopens(self):
        _,result=self.request('save',dict(profile=self.row,expected_revision=0));p=result['profile']
        code,state=self.shell_request('save-shell',dict(shell_id='shell-http',name='白色外壳',expected_revision=0));self.assertEqual(code,200,state)
        payload=dict(shell_id='shell-http',role_id=p['role_id'],profile_revision=1,source_key=None,source_sha256=None,expected_revision=state['revision'])
        code,state=self.shell_request('confirm',payload);self.assertEqual(code,200,state)
        self.assertEqual(self.shell_request('confirm',payload)[1]['revision'],state['revision'])
        code,state=self.shell_request('simulate',dict(expected_revision=state['revision'],scenario='unknown',shell_a='shell-http',shell_b='shell-http'))
        self.assertEqual(code,200,state);self.assertTrue(state['last_run']['state']['registration_prompt'])
        self.assertEqual(self.shell_request('state')[1],state);self.assertFalse(state['device_written'])

    def test_product_http_bad_requests_and_conflict(self):
        row=dict(shell_id='shell-http',name='A',expected_revision=0)
        self.assertEqual(self.shell_request('save-shell',row,headers={'Content-Type':'application/json'})[0],415)
        self.assertEqual(self.shell_request('save-shell',raw=b' '*8193)[0],413)
        self.assertEqual(self.shell_request('save-shell',raw=b'{"name":"a","name":"b"}')[0],422)
        self.assertEqual(self.shell_request('save-shell',row)[0],200)
        self.assertEqual(self.shell_request('save-shell',dict(row,name='B'))[0],409)

    def test_session_lab_http_flow_reopens_and_rejects_stale_revision(self):
        _,result=self.request('save',dict(profile=self.row,expected_revision=0));p=result['profile']
        _,state=self.shell_request('save-shell',dict(shell_id='shell-http',name='A',expected_revision=0))
        _,state=self.shell_request('confirm',dict(shell_id='shell-http',role_id=p['role_id'],profile_revision=1,source_key=None,source_sha256=None,expected_revision=state['revision']))
        value=dict(expected_revision=state['revision'],scenario='speaking',shell_a='shell-http',shell_b='shell-http')
        code,state=self.shell_request('session-lab',value)
        self.assertEqual(code,200,state);self.assertEqual(state['last_run']['kind'],'session_lab')
        self.assertEqual(self.shell_request('state')[1],state)
        self.assertEqual(self.shell_request('session-lab',value)[0],409)
        value['expected_revision']=state['revision'];value['scenario']='invalid'
        self.assertEqual(self.shell_request('session-lab',value)[0],422)

    def test_original_sample_session_http_history_reopens_and_rejects_old_revision(self):
        from urllib.request import Request
        payload=b'{}'
        request=Request(self.origin+'/api/shells/example',data=payload,
            headers={'Content-Type':'application/json','X-DeskBot-Studio':'shells-v1'})
        with self.opener.open(request,timeout=10) as response:session=json.load(response)['session']
        value=dict(expected_revision=0,session_id=session['id'])
        code,state=self.shell_request('replay-session',value)
        self.assertEqual(code,200,state)
        self.assertEqual(state['last_run']['kind'],'runtime_replay')
        self.assertEqual(state['last_run']['simulated_activation_count'],0)
        self.assertFalse(state['last_run']['hardware_accessed'])
        self.assertEqual(self.shell_request('state')[1],state)
        self.assertEqual(self.shell_request('replay-session',value)[0],409)
        value['expected_revision']=state['revision'];value['session_id']='missing'
        self.assertEqual(self.shell_request('replay-session',value)[0],503)


if __name__=='__main__':unittest.main()
