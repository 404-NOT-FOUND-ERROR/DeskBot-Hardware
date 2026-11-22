"""Offline raw telemetry, M2, host protocol and persistence integration."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import io
import json
from types import SimpleNamespace
import unittest

from protocol.magnetic_stream import MagneticStream, sample_time, validate_stream, LIMIT, IDENTITY
from protocol.magnetic_telemetry import decode_payload
from protocol.shell_profile_archive import build_archive
from protocol.shell_repeatability import evaluate_suite, _load_suite
from protocol.shell_switch_runtime import ShellSwitchRuntime
from protocol.shell_runtime_replay import replay_recordings
from protocol.shell_switch_lab import SessionLab, DEVICE
from protocol.studio_control_bridge import ControlBridge
from tests import test_shell_profile_archive_s1b as history
from tests import test_shell_switch_adapter as adapter_fixture
from tests import test_studio_control_link as host


@lru_cache(maxsize=1)
def archive():
    return build_archive(evaluate_suite(history.SUITE, history.ROOT), history.CONTEXT)


def bindings():
    value = adapter_fixture.bindings()
    for row, profile in zip(value.values(),archive()['profiles']):
        row['source'].update(archive_sha256=archive()['payload_sha256'],magnet_profile_id=profile['profile_id'])
    return value


def line(profile=1):
    row = archive()['profiles'][profile]
    payload = bytes([row['kind']])+b''.join(round(v).to_bytes(2,'big',signed=True) for v in row['center'])
    return ('Handle: cmd=06, '+payload.hex(' ')).encode('ascii')


def control(at):
    value = adapter_fixture.control(3+at//100)
    value['display']['sequence'] = 3+at//100
    return value


class StreamTests(unittest.TestCase):
    def test_original_payload_clock_and_bounded_sequence(self):
        stream = MagneticStream();c = control(0)
        for at in range(LIMIT+4):stream.line(line(),c,at)
        state = validate_stream(stream.snapshot(c))
        self.assertEqual(len(state['events']),LIMIT)
        self.assertEqual(state['events'][0]['sequence'],5)
        self.assertEqual(state['events'][-1]['at_ms'],LIMIT+3)
        state['events'].clear()
        self.assertEqual(len(stream.snapshot(c)['events']),LIMIT)

    def test_invalid_callback_resets_but_normal_log_does_not(self):
        s=MagneticStream();c=control(0)
        s.line(line(),c,0);s.line(b'I (20) App: hello',c,1)
        self.assertEqual(s.sequence,1)
        s.line(b'Handle: cmd=06, 01 00',c,2)
        self.assertEqual(s.reset_sequence,2);self.assertFalse(s.events)
        s.line(line(),c,3)
        self.assertEqual(validate_stream(s.snapshot(c))['events'][0]['sequence'],3)

    def test_identity_change_clears_old_samples(self):
        s=MagneticStream();c=control(0);s.line(line(),c,0)
        c['boot_id']='boot-'+'e'*32
        self.assertEqual(s.snapshot(c)['sequence'],0)
        self.assertFalse(s.snapshot(c)['events'])

    def test_schema_and_clock_boundaries(self):
        s=MagneticStream();c=control(0);s.line(line(),c,0);good=s.snapshot(c)
        for change in [dict(events=[dict(good['events'][0],payload_hex='0601')]),
                       dict(events=[dict(good['events'][0],at_ms=True)]),dict(events=good['events']*65),
                       dict(reset_sequence=2),dict(identity={})]:
            with self.subTest(change=change),self.assertRaises(ValueError):validate_stream(good|change)
        for at in (-1,True,2**53-1):
            with self.subTest(at=at),self.assertRaises(ValueError):sample_time(at)
        self.assertIsNotNone(sample_time(7200000))

    def test_same_read_boot_change_resets_session_before_following_callback(self):
        link=host.control.ControlLink();obs=host.worker.Observation(link,clock=lambda:10.0)
        obs.link('connected','COM4')
        link.observe(host.hello(),10000)
        link.command(dict(operation='enable',command_id='test-enable',connection_id=link.connection_id,
            target_id=DEVICE,boot_id=host.BOOT,session_id=None),10000)
        old_connection=link.connection_id
        for boot in ('b','e'):
            obs.feed(('I (10000) DeskBotLink: '+json.dumps(host.hello(boot))+'\n').encode()+line()+b'\n')
            state=obs.magnetic.snapshot(link.snapshot(10000))
            self.assertEqual(len(state['events']),1)
            self.assertEqual(link.hello['boot_id'],'boot-'+boot*32)
            self.assertEqual(state['sequence'],1)
            if boot=='e':
                self.assertNotEqual(state['identity']['connection_id'],old_connection)
                self.assertFalse(link.enabled)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.bindings=bindings()
        self.runtime=ShellSwitchRuntime(self.bindings,device_id=DEVICE,archive=archive(),context=history.CONTEXT)
        self.stream=MagneticStream()

    def feed(self,at,*,sample=True,c=None):
        c=c or control(at)
        if sample:self.stream.line(line(),c,at)
        return self.runtime.update(c,self.stream.snapshot(c),at)

    def warm(self,start=0):
        for at in range(start,start+2000,100):self.feed(at)

    def test_raw_samples_reach_recognizer_then_real_command(self):
        self.warm()
        self.assertEqual(self.runtime.snapshot()['recognition']['status'],'known')
        self.assertTrue(self.runtime.next_command(1900)['operation'].startswith('role.activate.1.'))

    def test_duplicates_never_renew_original_sample_age(self):
        self.warm()
        for at in (2200,2700,3500):self.feed(at,sample=False)
        self.assertIsNone(self.runtime.next_command(3500))
        self.assertEqual(self.runtime.reason,'sample_expired')
        self.assertIsNone(self.runtime.adapter.coordinator.target)

    def test_delayed_batch_expires_even_when_only_next_command_advances_time(self):
        c=control(2300)
        for at in range(1000,2000,100):self.stream.line(line(),c,at)
        self.runtime.update(c,self.stream.snapshot(c),2300)
        self.assertEqual(self.runtime.snapshot()['recognition']['status'],'known')
        self.assertIsNone(self.runtime.next_command(3500))
        self.assertEqual(self.runtime.reason,'sample_expired')

    def test_batch_does_not_backdate_stability(self):
        c=control(1900)
        for at in range(1000,2000,100):self.stream.line(line(),c,at)
        self.runtime.update(c,self.stream.snapshot(c),1900)
        self.assertIsNone(self.runtime.next_command(1900))
        self.assertEqual(self.runtime.adapter.coordinator.since,1900)

    def test_future_expired_and_sensor_mismatch_restart_window(self):
        for sample_at,now in [(2000,1000),(0,2000)]:
            with self.subTest(sample_at=sample_at):
                self.setUp();c=control(now);self.stream.line(line(),c,sample_at)
                result=self.runtime.update(c,self.stream.snapshot(c),now)
                self.assertEqual(result['recognition']['status'],'missing')
                self.assertEqual(result['discarded'],1)
        self.setUp();c=control(0)
        self.stream.line(line().replace(b'cmd=06, 01',b'cmd=06, 02'),c,0)
        self.runtime.update(c,self.stream.snapshot(c),0)
        self.assertEqual(self.runtime.reason,'sensor_changed')

    def test_ring_overflow_and_missing_tail_discard_unsent_target(self):
        self.warm()
        for at in range(2000,9000,100):self.stream.line(line(),control(at),at)
        result=self.runtime.update(control(8900),self.stream.snapshot(control(8900)),8900)
        self.assertGreater(result['gaps'],0)
        self.assertIsNone(self.runtime.next_command(8900))
        stream=self.stream.snapshot(control(9000));stream['sequence']+=1
        self.runtime.update(control(9000),stream,9000)
        self.assertEqual(self.runtime.reason,'sample_gap')

    def test_invalid_packet_marker_resets_existing_candidate(self):
        self.warm();c=control(2000)
        self.stream.line(b'Handle: cmd=06, 01',c,2000)
        self.runtime.update(c,self.stream.snapshot(c),2000)
        self.assertIsNone(self.runtime.next_command(2000))
        self.assertEqual(self.runtime.reason,'invalid_sample')

    def test_motion_end_discards_unpolled_during_motion_buffer(self):
        self.feed(0,sample=False);self.runtime.motion(True,1000);c=control(2100)
        for at in range(1100,2100,100):self.stream.line(line(),c,at)
        self.runtime.motion(False,2100);self.runtime.update(c,self.stream.snapshot(c),2100)
        self.assertEqual(self.runtime.snapshot()['recognition']['status'],'missing')
        self.assertIsNone(self.runtime.next_command(2900))
        self.warm(3000)
        self.assertIsNotNone(self.runtime.next_command(4900))

    def test_archive_and_recording_boundaries_skip_queued_old_samples(self):
        for boundary in ('archive','recording'):
            with self.subTest(boundary=boundary):
                self.setUp();self.feed(0,sample=False);c=control(2000)
                for at in range(1000,2000,100):self.stream.line(line(),c,at)
                if boundary=='archive':self.runtime.replace(self.bindings,archive=archive(),context=history.CONTEXT,now_ms=2000)
                else:self.runtime.reset_segment(2000)
                self.runtime.update(c,self.stream.snapshot(c),2000)
                self.assertEqual(self.runtime.snapshot()['recognition']['status'],'missing')

    def test_disconnected_or_changed_identity_clears_recognition(self):
        self.warm();c=control(2000);c['enabled']=False;c['status']='off';c['can_submit']=False
        self.runtime.update(c,self.stream.snapshot(c),2000)
        self.assertIsNone(self.runtime.adapter.coordinator.target)
        self.assertIsNone(self.runtime.next_command(2000))

    def test_long_running_clock_and_invalid_time(self):
        self.warm(7200000)
        self.assertIsNotNone(self.runtime.next_command(7201900))
        before=self.runtime.snapshot()
        for at in (1,True,2**53-1):
            with self.subTest(at=at),self.assertRaises(ValueError):self.runtime.next_command(at)
        self.assertEqual(before,self.runtime.snapshot())

    def test_known_needs_current_validated_registration(self):
        self.runtime.adapter.coordinator.bindings['b']['ready']=False
        self.warm()
        self.assertEqual(self.runtime.snapshot()['recognition']['status'],'known')
        self.assertIsNone(self.runtime.next_command(1900))


class ReplayTests(unittest.TestCase):
    def capture(self):
        raw=bytes.fromhex('06'+line().decode().split('cmd=06, ')[1].replace(' ',''))
        origin=datetime(2026,11,16,tzinfo=timezone.utc)
        samples=[decode_payload(raw,host_received_at=origin+timedelta(seconds=i*.1)) for i in range(36)]
        return dict(device_id=DEVICE,archive=archive(),context=history.CONTEXT,session_id='capture-test',
                    recordings=[dict(id='original-window',scenario='remount',samples=samples)])

    def test_raw_m2_protocol_first_frame_and_save_integration(self):
        result=replay_recordings(self.capture(),bindings(),adapter_fixture.GRAPH)
        row=result['rows'][0]
        self.assertEqual(row['recognition']['status'],'known')
        self.assertEqual(row['tail_recognition']['status'],'missing')
        self.assertEqual(row['state']['saved']['role_id'],'character-b')
        self.assertEqual(row['simulated_activation_count'],1)
        self.assertTrue({'request','first_frame','saved'} <= {r['event'] for r in row['timeline']})
        self.assertFalse(result['hardware_accessed']);self.assertEqual(result['device_commands'],0)

    def test_historical_identity_does_not_qualify_for_automatic_switch(self):
        capture=self.capture();capture['device_id']='device-test'
        result=replay_recordings(capture,bindings(),adapter_fixture.GRAPH)
        self.assertEqual(result['simulated_activation_count'],0)
        self.assertEqual(result['rows'][0]['recognition']['status'],'known')

    def test_handoff_finishes_after_observation_expires_gap_or_motion(self):
        for boundary in ('expiry','gap','motion'):
            with self.subTest(boundary=boundary):
                lab=SessionLab(bindings(),adapter_fixture.GRAPH)
                runtime=ShellSwitchRuntime(bindings(),device_id=DEVICE,archive=archive(),context=history.CONTEXT)
                stream=MagneticStream()
                for at in range(0,10001,100):
                    c=lab.pump(at)
                    if 2000<=at<=4000:stream.line(line(),c,at)
                    if at==4100:
                        self.assertIsNotNone(runtime.adapter.attempt)
                        if boundary=='motion':runtime.motion(True,at)
                    snapshot=stream.snapshot(c)
                    if boundary=='gap' and at>=4100:
                        snapshot.update(sequence=snapshot['sequence']+1,reset_sequence=snapshot['sequence']+1,events=[])
                    runtime.update(c,snapshot,at)
                    command=runtime.next_command(at)
                    if command:lab.command(command['operation'],at,command)
                self.assertEqual(runtime.adapter.coordinator.saved['role_id'],'character-b')
                self.assertIsNone(runtime.adapter.coordinator.target)
                self.assertEqual(len([c for c in lab.commands if c['operation'].startswith('role.activate.')]),1)

    def test_real_history_replays_through_m2_with_original_intervals(self):
        _,_,recordings,_,_=_load_suite(history.SUITE,history.ROOT)
        capture=dict(device_id=DEVICE,archive=archive(),context=history.CONTEXT,session_id='history',
                     recordings=[r for r in recordings if r['id']=='up-reinstalled'])
        result=replay_recordings(capture,bindings(),adapter_fixture.GRAPH)
        self.assertEqual(result['total_samples'],len(capture['recordings'][0]['samples']))
        self.assertEqual(result['rows'][0]['recognition']['status'],'known')

    def test_independent_windows_never_join_confirmation_state(self):
        capture=self.capture();window=capture['recordings'][0];window['samples']=window['samples'][:5]
        capture['recordings']=[window,dict(window,id='second')]
        result=replay_recordings(capture,bindings(),adapter_fixture.GRAPH)
        self.assertTrue(all(r['recognition']['status']=='warming_up' for r in result['rows']))
        self.assertEqual(result['simulated_activation_count'],0)

    def test_duration_budget_is_checked_before_schedule_allocation(self):
        capture=self.capture();samples=capture['recordings'][0]['samples']
        samples[-1]=replace(samples[-1],host_received_at='2099-01-01T00:00:00.000+00:00')
        with self.assertRaisesRegex(ValueError,'分段'):replay_recordings(capture,bindings(),adapter_fixture.GRAPH)


class BridgeTests(unittest.TestCase):
    def envelope(self):
        obs=host.worker.Observation(host.control.ControlLink(),clock=lambda:1.0)
        obs.link('connected','COM4')
        stream=MagneticStream();c=control(1000);stream.line(line(),c,900)
        return dict(schema='deskbot.studio_transport/v1',observation=obs.snapshot(),control=c,
                    generated_at_ms=1000,magnetic=stream.snapshot(c))

    def read(self,value):
        bridge=ControlBridge(clock=lambda:1.25)
        observed=[]
        class Output(io.StringIO):
            def readline(self,*args):
                row=super().readline(*args)
                if not row:observed.append(deepcopy(bridge.magnetic_state))
                return row
        process=SimpleNamespace(stdout=Output(json.dumps(value)+'\n'),stdin=io.StringIO(),poll=lambda:0)
        bridge.process=process;bridge._read(process)
        return bridge,observed

    def test_optional_stream_uses_original_clock_and_old_transport_still_works(self):
        value=self.envelope();_,observed=self.read(value)
        self.assertEqual(observed[0]['events'][0]['at_ms'],900)
        del value['magnetic'];_,observed=self.read(value)
        self.assertEqual(observed[0]['sequence'],0)

    def test_mismatched_identity_and_future_events_rejected(self):
        for key in ('identity','clock'):
            value=self.envelope()
            if key=='identity':value['magnetic']['identity']['boot_id']='boot-'+'e'*32
            else:value['magnetic']['events'][0]['at_ms']=1001
            bridge,observed=self.read(value)
            self.assertFalse(observed)
            self.assertFalse(bridge.magnetic()['events'])


if __name__=='__main__':unittest.main()
