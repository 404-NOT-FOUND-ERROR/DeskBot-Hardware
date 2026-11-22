"""Binding, exactly-once handoff, expiry and original HTTP integration."""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import tempfile
import threading
import unittest

from protocol.client_presentation import ClientPresentationService, ClientHTTP, ClientHTTPError, iso, HANDOFF_BUDGET_SECONDS
from protocol.client_presentation_ledger import PresentationLedger
from protocol.presentation_coordinator import PresentationCoordinator
from protocol.studio_control_link import expression_request_id
from tests.client_presentation_fixture import MemoryPresentationOwner, DEVICE

ROOT = Path(__file__).resolve().parents[1]
START = 1794790800.0  # 2026-11-16 01:00 UTC


class FakeHTTP:
    def __init__(self):
        self.commands=[];self.acks=[];self.chats=[];self.lose_ack=False

    def request(self, method, path, value=None):
        if path=='/api/chat':
            self.chats.append(deepcopy(value));return dict(accepted=True,turn=dict(reply='很开心'))
        if path=='/api/devices/hello':return dict(accepted=True)
        if path.startswith('/api/outbox?'):return dict(commands=deepcopy(self.commands))
        if path.endswith('/ack'):
            self.acks.append(deepcopy(value))
            if self.lose_ack:raise OSError('lost_ack')
            return dict(accepted=True,command=dict(status=value['status']))
        raise AssertionError((method,path))


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'ledger.sqlite'
        self.ledger=PresentationLedger(self.path);self.addCleanup(lambda:self.ledger.close())
        self.owner=MemoryPresentationOwner();self.http=FakeHTTP();self.now=START
        self.service=self.make()

    def make(self):
        return ClientPresentationService(self.owner,self.http,self.ledger,device_id=DEVICE,wall=lambda:self.now)

    def queue(self, semantic='happy',event='turn-1',command='command-1'):
        self.service.chat(event,'correlation-'+event,'今天开心')
        row=self.ledger.get('input',event)
        value=dict(command_id=command,source_event_id='reply-'+event,type='render.expression',target='vocat',
            device_id=DEVICE,character_id='shaping-001',role_revision=row['role_revision'],correlation_id='correlation-'+event,
            queued_at=iso(self.now),expires_at=None,payload=dict(expression=semantic))
        self.http.commands.append(value)
        return value

    def row(self,key='command-1'):
        return self.ledger.get('command',key)

    def test_full_route_saves_correlated_player_receipt_and_only_acks_once(self):
        self.queue();self.service.tick();self.service.tick()
        self.assertEqual(len(self.owner.sent),1)
        self.assertEqual(self.row()['phase'],'acknowledged')
        self.assertEqual(self.row()['ack']['result']['completion_scope'],'player_dispatched')
        self.assertEqual(len(self.http.acks),1)
        self.assertEqual(self.row()['usb']['request_id'],self.row()['ack']['result']['receipt']['request_id'])

    def test_unsupported_semantic_is_preserved_and_failed(self):
        self.queue('surprised');self.service.tick()
        self.assertEqual(self.row()['command']['payload']['expression'],'surprised')
        self.assertEqual(self.row()['ack']['error']['code'],'unsupported_expression')
        self.assertFalse(self.owner.sent)

    def test_busy_waits_then_expiry_is_fixed_across_restart(self):
        self.queue();self.owner.busy();self.service.tick()
        deadline=self.row()['deadline'];self.service.close();self.owner.restart();self.service=self.make()
        self.now+=31;self.service.tick()
        self.assertEqual(self.row()['deadline'],deadline)
        self.assertEqual(self.row()['ack']['error']['code'],'expired');self.assertFalse(self.owner.sent)

    def test_busy_then_home_sends_original_command(self):
        self.queue();self.owner.busy();self.service.tick();self.assertFalse(self.owner.sent)
        self.owner.busy(False);self.service.tick();self.assertEqual(len(self.owner.sent),1)

    def test_sleeping_and_blocked_wait_until_awake_and_clear(self):
        self.queue();reply=self.owner.link.roles['reply'];reply['sleep']='sleeping'
        self.service.tick();self.assertEqual(self.row()['phase'],'claimed');self.assertFalse(self.owner.sent)
        reply['sleep']='awake';reply['blocked']=1;self.service.tick();self.assertFalse(self.owner.sent)
        reply['blocked']=0;self.service.tick();self.assertEqual(len(self.owner.sent),1)

    def test_original_expiry_can_be_earlier_than_local_limit(self):
        command=self.queue();command['expires_at']=iso(self.now+2)
        self.owner.busy();self.service.tick();self.now+=3;self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'expired')

    def test_workbench_expression_invalidates_queued_generation(self):
        self.queue();control=self.owner.control()
        self.owner.command(dict(operation='neutral',command_id='workbench-1',
            **{k:control[k] for k in ('connection_id','target_id','boot_id','session_id')}))
        self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'superseded')
        self.assertEqual([c['command_id'] for c in self.owner.sent],['workbench-1'])

    def test_change_and_return_role_still_invalidates_old_generation(self):
        self.queue();self.owner.presentation.advance();self.owner.presentation.advance();self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'superseded')

    def test_workbench_and_client_handoff_share_the_same_lock(self):
        self.queue();entered=threading.Event();release=threading.Event();manual_done=threading.Event()
        original=self.owner._command
        def slow(value,*,before_write=None):
            if value['command_id'].startswith('client-'):
                entered.set()
                if not release.wait(3):raise AssertionError('handoff not released')
            return original(value,before_write=before_write)
        self.owner._command=slow
        tick=threading.Thread(target=self.service.tick);tick.start();self.assertTrue(entered.wait(3))
        def manual():
            control=self.owner.control()
            self.owner.command(dict(operation='neutral',command_id='concurrent-workbench',
                **{k:control[k] for k in ('connection_id','target_id','boot_id','session_id')}))
            manual_done.set()
        thread=threading.Thread(target=manual);thread.start()
        self.assertFalse(manual_done.wait(0.05));release.set();tick.join(3);thread.join(3)
        self.assertFalse(tick.is_alive());self.assertFalse(thread.is_alive());self.assertTrue(manual_done.is_set())
        self.assertEqual(len(self.owner.sent),2)
        self.assertTrue(self.owner.sent[0]['command_id'].startswith('client-'))
        self.assertEqual(self.row()['ack']['status'],'completed')

    def test_workbench_switch_during_reply_uses_input_binding(self):
        original=self.http.request
        def switch(method,path,value=None):
            response=original(method,path,value)
            if path=='/api/chat':self.owner.presentation.advance()
            return response
        self.http.request=switch;self.queue();self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'superseded');self.assertFalse(self.owner.sent)

    def test_queued_ack_conflict_remains_pending_confirmation(self):
        self.queue();original=self.http.request
        def conflict(method,path,value=None):
            if path.endswith('/ack'):raise ClientHTTPError(409,dict(error='command_device_mismatch'))
            if path.startswith('/api/outbox/') and method=='GET':return dict(command=dict(status='queued'))
            return original(method,path,value)
        self.http.request=conflict;self.service.tick()
        self.assertEqual(self.row()['phase'],'terminal');self.assertEqual(len(self.owner.sent),1)

    def test_terminal_server_expiry_preserves_both_outcomes(self):
        self.queue();original=self.http.request
        def conflict(method,path,value=None):
            if path.endswith('/ack'):raise ClientHTTPError(409,dict(error='ack_conflict'))
            if path.startswith('/api/outbox/') and method=='GET':return dict(command=dict(status='failed',
                acknowledgment=dict(error=dict(code='command_expired'))))
            return original(method,path,value)
        self.http.request=conflict;self.service.tick()
        self.assertEqual(self.row()['phase'],'acknowledged')
        self.assertEqual(self.row()['ack']['status'],'completed')
        self.assertEqual(self.row()['server_response']['command']['status'],'failed')

    def test_missing_binding_fails_without_guessing(self):
        command=self.queue();command['source_event_id']='reply-unknown';self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'missing_binding')

    def test_http_ack_loss_and_reopen_never_resend(self):
        self.queue();self.http.lose_ack=True;self.service.tick()
        self.assertEqual(self.row()['phase'],'terminal')
        self.service.close();self.owner.restart();self.service=self.make()
        self.http.lose_ack=False;self.service.tick()
        self.assertEqual(len(self.owner.sent),1);self.assertEqual(self.row()['phase'],'acknowledged')

    def test_unknown_send_after_restart_is_not_replayed(self):
        self.queue();self.owner.auto_receipt=False;self.service.tick()
        self.assertEqual(self.row()['phase'],'prepared')
        self.service.close();self.owner.restart();self.service=self.make();self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'delivery_unknown');self.assertEqual(len(self.owner.sent),1)

    def test_late_correlated_receipt_before_recovery_resolves_original(self):
        self.queue();self.owner.auto_receipt=False;self.service.tick()
        self.service.close();self.owner.restart();self.service=self.make()
        self.owner.receipt('dispatched');self.service.tick()
        self.assertEqual(self.row()['ack']['status'],'completed');self.assertEqual(len(self.owner.sent),1)

    def test_wrong_boot_receipt_cannot_complete_command(self):
        self.queue();self.owner.auto_receipt=False;self.service.tick()
        receipt=dict(self.row()['usb'],source='device',correlated=True,status='dispatched',boot_id='wrong')
        control=self.owner.control();control['receipts']=[receipt];self.service.observe(control)
        self.assertEqual(self.row()['phase'],'prepared')

    def test_same_semantic_distinct_commands_have_distinct_wire_ids(self):
        self.queue();self.service.tick()
        self.queue(event='turn-2',command='command-2');self.service.tick()
        self.assertEqual(len(self.owner.sent),2)
        self.assertNotEqual(self.row()['usb']['request_id'],self.row('command-2')['usb']['request_id'])

    def test_input_retry_keeps_frozen_binding_and_rejects_changed_body(self):
        self.queue();self.owner.presentation.advance()
        self.service.chat('turn-1','correlation-turn-1','今天开心')
        self.assertEqual(len(self.http.chats),1)
        with self.assertRaisesRegex(ValueError,'event_id_conflict'):
            self.service.chat('turn-1','correlation-turn-1','不同内容')

    def test_reconnect_before_send_rechecks_session_but_keeps_role_binding(self):
        self.queue();self.owner.busy();self.service.tick()
        replacement=MemoryPresentationOwner();replacement.presentation.attach(self.ledger)
        self.service.close();self.owner=replacement;self.service=self.make();self.service.tick()
        self.assertEqual(len(self.owner.sent),1)

    def test_request_id_includes_connection_and_session(self):
        self.queue();self.service.tick();value=self.owner.sent[0]
        for field in ('command_id','connection_id','target_id','boot_id','session_id'):
            self.assertNotEqual(expression_request_id(value),expression_request_id(dict(value,**{field:'changed'})))

    def test_client_revision_change_invalidates_pending_output(self):
        self.queue();self.service.client_stage='next-stage';self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'superseded');self.assertFalse(self.owner.sent)

    def test_current_device_change_fails_original_device_ack(self):
        self.queue();self.owner.busy();self.service.tick();self.owner.busy(False)
        self.service.device_id='different-device';self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'device_mismatch')
        self.assertEqual(self.row()['ack']['device_id'],DEVICE);self.assertFalse(self.owner.sent)

    def test_actual_role_change_invalidates_pending_output(self):
        self.queue()
        self.owner.link.roles['reply'].update(active_index=1,current_index=1,target_index=1)
        self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'superseded')

    def test_outbox_filters_other_targets_devices_types_and_body_commands(self):
        command=self.queue()
        for n,change in enumerate([dict(device_id='other'),dict(target='web'),dict(type='speak'),dict(source_event_id='body-perception:one')]):
            self.http.commands.append(dict(command,command_id='ignored-'+str(n),**change))
        self.service.tick();self.assertEqual(len(self.ledger.all('command')),1)

    def test_wire_exception_after_durable_receipt_preserves_success(self):
        self.queue();self.owner.raise_after_send=True;self.service.tick()
        self.assertEqual(self.row()['ack']['status'],'completed');self.assertEqual(len(self.owner.sent),1)

    def test_wire_exception_without_receipt_is_unknown_and_not_retried(self):
        self.queue();self.owner.auto_receipt=False;self.owner.raise_after_send=True
        self.service.tick();self.assertEqual(self.row()['phase'],'prepared')
        self.now+=HANDOFF_BUDGET_SECONDS+0.1;self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'delivery_unknown');self.assertEqual(len(self.owner.sent),1)

    def test_partial_ipc_error_still_accepts_later_correlated_receipt(self):
        self.queue();self.owner.auto_receipt=False;self.owner.raise_after_send=True
        self.service.tick();self.assertEqual(self.row()['phase'],'prepared')
        self.now+=6;self.owner.receipt('dispatched');self.service.tick()
        self.assertEqual(self.row()['ack']['status'],'completed');self.assertEqual(len(self.owner.sent),1)

    def test_chat_response_loss_retries_original_event_and_binding(self):
        original=self.http.request;lost=True
        def fail_once(method,path,value=None):
            nonlocal lost
            response=original(method,path,value)
            if path=='/api/chat' and lost:lost=False;raise OSError('response_lost')
            return response
        self.http.request=fail_once
        with self.assertRaises(OSError):self.service.chat('pending-turn','pending-correlation','今天开心')
        binding=self.ledger.get('input','pending-turn')['binding']
        self.service.close();self.owner.restart();self.service=self.make();self.service.tick()
        self.assertEqual(self.http.chats[0],self.http.chats[1])
        self.assertEqual(self.ledger.get('input','pending-turn')['binding'],binding)
        self.assertEqual(self.service.state()['inputs'][0]['status'],'completed')

    def test_started_recovery_error_is_visible_and_not_retried_each_tick(self):
        self.http.request=lambda method,path,value=None: (_ for _ in ()).throw(OSError('offline'))
        with self.assertRaises(OSError):self.service.chat('pending-turn','pending-correlation','今天开心')
        attempts=[];original=FakeHTTP().request
        def pending(method,path,value=None):
            if path=='/api/chat':
                attempts.append(value);raise ClientHTTPError(409,dict(error='turn_recovery_pending'))
            return original(method,path,value)
        self.http.request=pending;self.service.tick();self.service.tick()
        self.assertEqual(len(attempts),1)
        self.assertEqual(self.service.state()['inputs'][0]['status'],'turn_recovery_pending')

    def test_terminal_receipt_survives_closing_and_reopening_actual_ledger(self):
        self.queue();self.http.lose_ack=True;self.service.tick();self.service.close()
        self.ledger.close();self.ledger=PresentationLedger(self.path)
        self.owner.restart();self.service=self.make();self.http.lose_ack=False;self.service.tick()
        self.assertEqual(self.row()['ack']['status'],'completed');self.assertEqual(len(self.owner.sent),1)

    def test_prepared_survives_actual_ledger_reopen_as_unknown(self):
        self.queue();self.owner.auto_receipt=False;self.service.tick();self.service.close()
        self.ledger.close();self.ledger=PresentationLedger(self.path)
        self.owner.restart();self.service=self.make();self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'delivery_unknown');self.assertEqual(len(self.owner.sent),1)

    def test_claim_interruption_revalidates_missing_binding_after_reopen(self):
        command=self.queue();self.http.commands=[]
        self.ledger.put('command',command['command_id'],dict(command=command,phase='claimed',
            binding=None,claimed_at=self.now,deadline=self.now+30))
        self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'missing_binding')
        self.assertFalse(self.owner.sent)

    def test_slow_valid_usb_receipt_outlives_eight_seconds(self):
        self.queue();self.owner.auto_receipt=False;self.service.tick()
        self.now+=7.5;self.owner.receipt('accepted');self.service.tick()
        self.now+=0.6;self.service.tick();self.assertEqual(self.row()['phase'],'prepared')
        self.now+=0.4;self.owner.receipt('dispatched');self.service.tick()
        self.assertEqual(self.row()['ack']['status'],'completed')

    def test_pending_receipt_uses_full_shared_budget_without_replay(self):
        self.queue();self.owner.auto_receipt=False;self.service.tick()
        self.now+=HANDOFF_BUDGET_SECONDS-0.1;self.service.tick()
        self.assertEqual(self.row()['phase'],'prepared')
        self.now+=0.2;self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'delivery_unknown');self.assertEqual(len(self.owner.sent),1)

    def test_receipt_after_queue_deadline_still_records_submitted_result(self):
        command=self.queue();command['expires_at']=iso(self.now+1)
        self.owner.auto_receipt=False;self.service.tick();self.now+=3
        self.owner.receipt('dispatched');self.service.tick()
        self.assertEqual(self.row()['ack']['status'],'completed')

    def test_time_rollback_is_reported(self):
        self.queue();self.owner.busy();self.service.tick();self.now-=10;self.service.tick()
        self.assertEqual(self.row()['ack']['error']['code'],'clock_invalid')


@unittest.skipUnless(os.environ.get('DESKBOT_CLIENT_CHECKOUT') and os.environ.get('DESKBOT_NODE'),
                     'Set DESKBOT_CLIENT_CHECKOUT and DESKBOT_NODE for original HTTP integration')
class OriginalHTTPTests(unittest.TestCase):
    def test_real_process_exit_after_enqueue_recovers_original_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);ledger=PresentationLedger(path/'ledger.sqlite');owner=MemoryPresentationOwner()
            def launch(crash=False):
                env=dict(os.environ)
                if crash:env['DESKBOT_4B1A_CRASH']='output_enqueued'
                else:env.pop('DESKBOT_4B1A_CRASH',None)
                process=subprocess.Popen([env['DESKBOT_NODE'],str(ROOT/'integrations/client-4b1a/http-fixture.mjs'),
                    env['DESKBOT_CLIENT_CHECKOUT'],str(path/'client.sqlite')],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
                return process,ClientHTTP(json.loads(process.stdout.readline())['url'])
            first,http=launch(True)
            service=ClientPresentationService(owner,http,ledger,device_id=DEVICE,wall=lambda:START)
            try:
                with self.assertRaises(OSError):service.chat('crash-turn','crash-correlation','今天做饭很开心')
                first.communicate(timeout=10);self.assertEqual(first.returncode,73)
                second,http=launch();service.http=http
                try:
                    service.tick()
                    self.assertEqual(service.state()['inputs'][0]['status'],'completed')
                    response=service.chat('crash-turn','crash-correlation','今天做饭很开心')
                    self.assertEqual(response['turn']['trace']['calls'],1)
                    service.tick();service.tick();self.assertEqual(len(owner.sent),1)
                    self.assertEqual(ledger.all('command')[0]['ack']['status'],'completed')
                    self.assertEqual(len(http.request('GET','/api/outbox?target=vocat&device_id='+DEVICE)['commands']),1)
                finally:
                    second.communicate('stop\n',timeout=15);self.assertEqual(second.returncode,0)
            finally:
                if first.poll() is None:first.terminate();first.communicate(timeout=10)
                service.close();ledger.close()

    def test_original_chat_sqlite_outbox_and_production_control_link_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            process=subprocess.Popen([os.environ['DESKBOT_NODE'],str(ROOT/'integrations/client-4b1a/http-fixture.mjs'),
                os.environ['DESKBOT_CLIENT_CHECKOUT'],str(Path(directory)/'client.sqlite')],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            ledger=PresentationLedger(Path(directory)/'ledger.sqlite')
            try:
                line=process.stdout.readline();self.assertTrue(line,process.stderr.read() if process.poll() is not None else 'missing URL')
                http=ClientHTTP(json.loads(line)['url']);owner=MemoryPresentationOwner()
                service=ClientPresentationService(owner,http,ledger,device_id=DEVICE,wall=lambda:START)
                response=service.chat('http-turn','http-correlation','今天做饭很开心')
                self.assertTrue(response['accepted'])
                service.tick();service.tick()
                rows=ledger.all('command');self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['phase'],'acknowledged',service.error)
                self.assertEqual(rows[0]['ack']['status'],'completed')
                self.assertEqual(rows[0]['server_response']['command']['acknowledgment']['payload']['completion_scope'],'player_dispatched')
                self.assertEqual(len(owner.sent),1)
                service.chat('http-turn','http-correlation','今天做饭很开心');service.tick()
                self.assertEqual(len(owner.sent),1)
                # Non-expression outputs remain available for their own consumer.
                rest=http.request('GET','/api/outbox?target=vocat&device_id='+DEVICE)['commands']
                self.assertEqual([c['type'] for c in rest],['speak'])
                service.close()
            finally:
                ledger.close();process.communicate('stop\n',timeout=15)
                self.assertEqual(process.returncode,0)


if __name__=='__main__':unittest.main()
