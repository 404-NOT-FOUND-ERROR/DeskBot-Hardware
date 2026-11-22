"""Background scheduling over original M2 inputs and the production host/device codecs."""
from contextlib import contextmanager
from copy import deepcopy
import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request

from protocol.magnetic_stream import MagneticStream
from protocol.shell_switch_lab import SessionLab, DEVICE
from protocol.shell_switch_service import ShellRuntimeConfig, ShellSwitchService, RuntimeConflict
from protocol.studio_control_bridge import ControlBridge
from tests import test_shell_switch_runtime as raw
from tests import test_shell_switch_adapter as adapter_fixture
from tests import test_character_profiles as character_fixture
from tests import test_shell_characters as registry_fixture


class Clock:
    def __init__(self): self.ms = 0
    def __call__(self): return self.ms / 1000


class ConfigFixture:
    """Explicitly simulated ready bindings; never upgrades a saved registration."""
    def __init__(self):
        self.lock = threading.RLock()
        self.version = 1
        self.ready = True
        self.prepares = 0
        self.value = dict(pin=dict(session_id='project-test', name='内存联测', device_id=DEVICE,
            archive_sha256=raw.archive()['payload_sha256'], associations=2), bindings=raw.bindings(),
            archive=raw.archive(), context=raw.history.CONTEXT, paths=(), token=1)

    @contextmanager
    def transaction(self):
        with self.lock: yield self

    def describe(self):
        return [dict(session_id='project-test', name='内存联测', ready=self.ready,
                     reason='ready' if self.ready else 'registration_required', associations=2)]

    def token(self, paths): return self.version

    def prepare(self, session_id):
        if session_id != 'project-test' or not self.ready: raise ValueError('采样尚未完成')
        self.prepares += 1
        return deepcopy(self.value) | dict(token=self.version)


class MemoryOwner:
    """The sole in-memory owner pumps the same host protocol used by the USB worker."""
    def __init__(self, clock, config):
        self.lock = threading.RLock()
        self.clock = clock
        self.lab = SessionLab(config.value['bindings'], adapter_fixture.GRAPH)
        self.stream = MagneticStream()
        self.motion = False
        self.fail_read = self.fail_handoff = self.lose_handoff_reply = False
        self.reject_queries = 0
        self.drop_queries = False

    def advance(self, at, *, sample=True, profile=1):
        with self.lock:
            self.clock.ms = at
            control = self.lab.pump(at)
            if sample: self.stream.line(raw.line(profile), control, at)

    def shell_inputs(self):
        with self.lock:
            if self.fail_read: raise OSError('owner unavailable')
            control = self.lab.link.snapshot(self.clock.ms)
            return dict(control=control, magnetic=self.stream.snapshot(control), motion=self.motion)

    def command(self, value):
        if self.fail_handoff: raise OSError('handoff failed')
        if value['operation'] == 'read_roles' and (self.reject_queries or self.drop_queries):
            self.lab.commands.append(dict(at_ms=self.clock.ms, **deepcopy(value)))
            if self.reject_queries:
                self.reject_queries -= 1
                self.lab.link.last_command = dict(command_id=value['command_id'], operation='read_roles',
                                                 status='rejected', reason='pending_request')
            return
        self.lab.command(value['operation'], self.clock.ms, value)
        if self.lose_handoff_reply and value['operation'].startswith('role.activate.'):
            self.lose_handoff_reply = False
            raise OSError('handoff response lost')

    @property
    def activations(self):
        return [c for c in self.lab.commands if c['operation'].startswith('role.activate.')]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.config = ConfigFixture()
        self.owner = MemoryOwner(self.clock, self.config)
        self.service = ShellSwitchService(self.config, self.owner, clock=self.clock, background=False)
        self.addCleanup(self.service.close)

    def start(self):
        return self.service.start('project-test', self.service.generation)

    def run_to(self, end, *, sample=True, profile=1):
        for at in range(self.clock.ms+100, end+1, 100):
            self.owner.advance(at, sample=sample, profile=profile)
            self.service.tick()

    def send_once(self):
        self.start()
        for at in range(100, 6000, 100):
            self.owner.advance(at)
            self.service.tick()
            if self.owner.activations: return
        self.fail('stable original samples should produce one activation')

    def test_defaults_stopped_status_reads_do_not_schedule_or_prepare(self):
        for _ in range(3):
            state = self.service.state()
            self.assertEqual(state['phase'], 'stopped')
            self.assertEqual(state['ticks'], 0)
        self.run_to(2000)
        self.assertEqual(self.config.prepares, 0)
        self.assertFalse(self.owner.activations)

    def test_original_samples_production_protocol_first_frame_then_save(self):
        self.send_once()
        submitted = self.service.state()['runtime']['adapter']
        self.assertEqual(submitted['attempt']['status'], 'submitted')
        self.run_to(self.clock.ms+1500)
        adapter = self.service.state()['runtime']['adapter']
        self.assertTrue(adapter['attempt']['first_frame'])
        self.assertNotEqual(adapter['decision']['saved'].get('role_id'), 'character-b')
        self.run_to(self.clock.ms+2500)
        adapter = self.service.state()['runtime']['adapter']
        self.assertEqual(adapter['decision']['saved']['role_id'], 'character-b')
        self.assertEqual(adapter['attempt']['status'], 'saved')
        self.assertEqual(len(self.owner.activations), 1)

    def test_pause_before_handoff_cancels_unsent_choice(self):
        self.start(); self.run_to(1000)
        self.service.stop(self.service.generation)
        self.run_to(6000)
        self.assertEqual(self.service.state()['phase'], 'stopped')
        self.assertFalse(self.owner.activations)

    def test_pause_drains_handoff_without_activating_new_shell(self):
        self.send_once()
        state = self.service.stop(self.service.generation)
        self.assertEqual(state['phase'], 'finishing')
        self.run_to(self.clock.ms+4000, profile=0)
        state = self.service.state()
        self.assertEqual(state['phase'], 'stopped')
        self.assertEqual(state['runtime']['adapter']['attempt']['status'], 'saved')
        self.assertEqual(len(self.owner.activations), 1)

    def test_start_stop_idempotent_and_old_generations_conflict(self):
        state = self.start()
        self.assertEqual(self.start()['generation'], state['generation'])
        with self.assertRaises(RuntimeConflict): self.service.stop(0)
        state = self.service.stop(state['generation'])
        self.assertEqual(self.service.stop(state['generation']), state)
        with self.assertRaises(RuntimeConflict): self.service.retry(True)

    def test_start_does_not_replace_inflight_or_uncertain_attempt(self):
        self.owner.lose_handoff_reply = True
        self.send_once()
        state = self.service.stop(self.service.generation)
        attempt = deepcopy(state['runtime']['adapter']['attempt'])
        self.assertEqual(attempt['status'], 'uncertain')
        with self.assertRaises(ValueError): self.start()
        self.assertEqual(self.service.state()['runtime']['adapter']['attempt'], attempt)
        self.run_to(self.clock.ms+4000)
        self.assertEqual(self.service.state()['runtime']['adapter']['attempt']['status'], 'saved')
        self.assertEqual(len(self.owner.activations), 1)

    def test_motion_unknown_and_end_require_fresh_post_boundary_samples(self):
        self.owner.motion = None
        self.start(); self.run_to(3000)
        self.assertEqual(self.service.state()['reason'], 'motion_unknown')
        self.assertFalse(self.owner.activations)
        self.owner.motion = False
        self.run_to(5000, sample=False)
        self.assertFalse(self.owner.activations)
        self.run_to(9000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_motion_blocks_activation_and_drops_queued_moving_samples(self):
        self.start(); self.run_to(1200)
        self.owner.motion = True; self.run_to(3000)
        self.owner.motion = False; self.run_to(5000, sample=False)
        self.assertFalse(self.owner.activations)
        self.run_to(9000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_repeated_old_buffer_never_triggers_delayed_activation(self):
        self.start(); self.run_to(1000)
        self.run_to(6000, sample=False)
        self.assertFalse(self.owner.activations)
        self.run_to(10000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_config_change_blocks_new_activation_in_same_cycle(self):
        self.start(); self.run_to(1000)
        with self.config.transaction(): self.config.version += 1
        self.run_to(6000)
        state = self.service.state()
        self.assertFalse(state['enabled'])
        self.assertEqual(state['reason'], 'config_updated')
        self.assertFalse(self.owner.activations)
        self.start(); self.run_to(10000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_config_change_preserves_request_receipt_until_saved(self):
        self.send_once()
        self.config.version += 1
        self.run_to(self.clock.ms+4000, profile=0)
        state = self.service.state()
        self.assertEqual(state['reason'], 'config_updated')
        self.assertEqual(state['runtime']['adapter']['attempt']['status'], 'saved')
        self.assertEqual(len(self.owner.activations), 1)

    def test_restart_uses_only_post_start_samples(self):
        self.start(); self.run_to(1000)
        self.service.stop(self.service.generation)
        self.run_to(3000)
        self.start(); self.run_to(6000, sample=False)
        self.assertFalse(self.owner.activations)
        self.run_to(10000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_reconnect_queries_current_device_and_discards_pre_connection_samples(self):
        self.start(); self.run_to(1200)
        self.owner.lab.reconnect(1200)
        self.run_to(5000, sample=False)
        self.assertFalse(self.owner.activations)
        self.run_to(10000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_stop_serializes_with_handoff_and_then_drains_one_request(self):
        self.start(); self.run_to(1700)
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        original = self.owner.command
        def blocked(value):
            entered.set()
            if not release.wait(3): raise OSError('test handoff deadline')
            return original(value)
        self.owner.command = blocked
        # Keep ticking until next activation, then hold its handoff inside the transaction.
        def drive():
            for at in range(1800, 4000, 100):
                self.owner.advance(at); self.service.tick()
                if entered.is_set(): break
        driver = threading.Thread(target=drive)
        stopper = threading.Thread(target=lambda: (self.service.stop(self.service.generation), stopped.set()))
        try:
            driver.start(); self.assertTrue(entered.wait(3))
            stopper.start(); self.assertFalse(stopped.wait(.05))
        finally:
            release.set(); driver.join(3)
            if stopper.ident: stopper.join(3)
        self.assertTrue(stopped.is_set())
        self.assertFalse(driver.is_alive()); self.assertFalse(stopper.is_alive())
        self.run_to(self.clock.ms+5000)
        self.assertEqual(len(self.owner.activations), 1)
        self.assertEqual(self.service.state()['runtime']['adapter']['attempt']['status'], 'saved')

    def test_speaking_defers_and_stale_sample_does_not_switch_after_sentence(self):
        self.owner.lab.device.app = 'speaking'
        self.start(); self.run_to(3500)
        self.assertFalse(self.owner.activations)
        self.run_to(6000, sample=False)
        self.owner.lab.device.app = 'idle'
        self.run_to(9000, sample=False)
        self.assertFalse(self.owner.activations)
        self.run_to(13000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_save_failure_waits_for_explicit_retry(self):
        self.owner.lab.device.fail_save = True
        self.start(); self.run_to(8000)
        state = self.service.state()
        self.assertTrue(state['can_retry'])
        self.assertEqual(len(self.owner.activations), 1)
        self.service.retry(state['generation']); self.run_to(14000)
        self.assertEqual(len(self.owner.activations), 2)
        self.assertEqual(self.service.state()['runtime']['adapter']['attempt']['status'], 'saved')

    def test_missing_handoff_response_queries_then_resolves_without_duplicate(self):
        self.owner.lose_handoff_reply = True
        self.send_once()
        self.assertEqual(self.service.state()['runtime']['adapter']['attempt']['status'], 'uncertain')
        with self.assertRaises(ValueError): self.service.retry(self.service.generation)
        self.run_to(self.clock.ms+5000)
        self.assertEqual(self.service.state()['runtime']['adapter']['attempt']['status'], 'saved')
        self.assertEqual(len(self.owner.activations), 1)

    def test_failed_owner_handoff_recovers_queries_without_silent_reactivation(self):
        self.owner.fail_handoff = True
        self.send_once_failure()
        self.owner.fail_handoff = False
        self.run_to(self.clock.ms+6000)
        state = self.service.state()
        self.assertFalse(self.owner.activations)
        self.assertTrue(state['can_retry'])
        self.service.retry(state['generation']); self.run_to(self.clock.ms+6000)
        self.assertEqual(len(self.owner.activations), 1)

    def send_once_failure(self):
        self.start(); self.run_to(5000)
        self.assertEqual(self.service.state()['runtime']['adapter']['error'], 'retry_required')

    def test_rejected_directory_queries_back_off_and_recover(self):
        self.owner.lab.read_requested = True  # Let this service, rather than the lab, initiate directory reads.
        self.owner.reject_queries = 2
        self.start(); self.run_to(6000)
        reads = [c for c in self.owner.lab.commands if c['operation'] == 'read_roles']
        self.assertEqual(len(reads), 3)
        self.assertGreaterEqual(reads[1]['at_ms']-reads[0]['at_ms'], 1000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_lost_directory_query_response_is_retried_after_bounded_wait(self):
        self.owner.lab.read_requested = True
        self.owner.drop_queries = True
        self.start(); self.run_to(21000)
        reads = [c for c in self.owner.lab.commands if c['operation'] == 'read_roles']
        self.assertEqual(len(reads), 2)
        self.assertFalse(self.owner.activations)
        self.owner.drop_queries = False
        self.run_to(44000)
        self.assertEqual(len(self.owner.activations), 1)

    def test_background_runs_without_state_poll_and_close_joins_worker(self):
        self.service.close()
        self.service = ShellSwitchService(self.config, self.owner, clock=self.clock, interval=.005)
        self.start()
        self.owner.advance(100)
        deadline = time.monotonic()+2
        while self.service.ticks < 3 and time.monotonic() < deadline: time.sleep(.005)
        self.assertGreaterEqual(self.service.ticks, 3)
        self.service.close()
        self.assertFalse(self.service.thread.is_alive())
        ticks = self.service.ticks
        self.service.tick()
        self.assertEqual(self.service.ticks, ticks)
        with self.assertRaises(ValueError): self.start()

    def test_worker_error_is_reported_and_thread_can_accept_fresh_start(self):
        self.service.close()
        self.service = ShellSwitchService(self.config, self.owner, clock=self.clock, interval=.005)
        self.owner.fail_read = True
        self.start()
        deadline = time.monotonic()+2
        while self.service.reason != 'service_error' and time.monotonic() < deadline: time.sleep(.005)
        self.assertFalse(self.service.state()['enabled'])
        self.assertIn('owner unavailable', self.service.state()['error'])
        self.owner.fail_read = False
        self.start()
        self.assertTrue(self.service.state()['enabled'])

    def test_reopening_service_defaults_stopped(self):
        self.start(); self.run_to(1000)
        self.service.close()
        reopened = ShellSwitchService(self.config, self.owner, clock=self.clock, background=False)
        self.addCleanup(reopened.close)
        self.assertFalse(reopened.state()['enabled'])
        self.assertIsNone(reopened.state()['pin'])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.fixture = registry_fixture.RegistryTests(methodName='runTest')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.config = ShellRuntimeConfig(self.fixture.store)

    def complete(self):
        f = self.fixture
        f.doc = f.sessions.create(name='synthetic-complete', device_id=DEVICE,
            context=registry_fixture.CONTEXT, shell_names=['A','B'])['session']
        f.sequence = 0
        a, b = [s['shell_id'] for s in f.doc['shells']]
        for task in list(f.doc['tasks']):
            x = 100 if task['shell_id'] == a else 500 if task['shell_id'] == b else 1000 if task['scenario'] == 'unknown_layout' else 0
            f.window(task, x)
        f.doc = f.sessions.evaluate(f.doc['id'], f.doc['revision'])['session']
        sources = f.sessions.registration_sources()
        f.add(); f.confirm(source=sources[0])
        f.add('shell-b', 'B'); f.confirm('shell-b', role=1, source=sources[1])
        return f

    def test_full_registration_pins_two_current_associations_and_verified_source_paths(self):
        f = self.complete()
        with self.config.transaction():
            lease = self.config.prepare(f.doc['id'])
            self.assertEqual(lease['pin']['associations'], 2)
            self.assertEqual(lease['pin']['device_id'], DEVICE)
            self.assertEqual(len(lease['pin']['characters']), 2)
            self.assertTrue(all(b['ready'] for b in lease['bindings'].values()))
            self.assertEqual(lease['token'], self.config.token(lease['paths']))
            log = next(p for p in lease['paths'] if p.suffix == '.log')
            log.write_bytes(log.read_bytes()+b'\n')
            self.assertNotEqual(lease['token'], self.config.token(lease['paths']))
            with self.assertRaisesRegex(ValueError, 'digest changed'): self.config.prepare(f.doc['id'])

    def test_edited_character_requires_reconfirmation_before_runtime_preparation(self):
        f = self.complete()
        p = f.characters.snapshot()['profiles'][0]
        p['persona'] = 'new personality'; f.characters.save(p, p['profile_revision'])
        lease = self.config.prepare(f.doc['id'])
        self.assertEqual(set(lease['bindings']), {'shell-b'})
        self.assertTrue(all(b['role_id'] != p['role_id'] for b in lease['bindings'].values()))

    def test_unregistered_incomplete_and_historical_sources_stay_unready(self):
        f = self.fixture
        self.assertEqual(self.config.describe(), [])
        sources = f.trained(); f.add(); f.confirm(source=sources[0])
        projects = self.config.describe()
        self.assertFalse(projects[0]['ready'])
        with self.assertRaises(ValueError): self.config.prepare(f.doc['id'])
        f.sessions.example()
        self.assertTrue(all(not p['ready'] for p in self.config.describe()))

    def test_config_token_changes_for_profile_and_log_edits_not_rehearsal(self):
        f = self.fixture
        f.trained(); f.add(); f.confirm()
        paths = [f.sessions._folder(f.doc['id'])/'session.json']
        token = self.config.token(paths)
        f.store.run(f.store.state()['revision'], scenario='speaking', shell_a='shell-a', shell_b='shell-a')
        self.assertEqual(token, self.config.token(paths))
        profile = f.characters.snapshot()['profiles'][0]
        profile['persona'] = 'changed'
        f.characters.save(profile, profile['profile_revision'])
        self.assertNotEqual(token, self.config.token(paths))
        token = self.config.token(paths)
        path = paths[0]; path.write_bytes(path.read_bytes()+b'\n')
        self.assertNotEqual(token, self.config.token(paths))


class HTTPTests(character_fixture.HTTPTests):
    def runtime_request(self, action, value=None, headers=None, raw=None):
        if value is not None: raw = json.dumps(value).encode('utf-8')
        if raw is not None and headers is None:
            headers = {'Content-Type':'application/json', 'X-DeskBot-Studio':'shell-runtime-v1'}
        try:
            with self.opener.open(Request(self.origin+'/api/shell-runtime/'+action, data=raw, headers=headers or {}), timeout=10) as r:
                return r.status, json.load(r)
        except HTTPError as error: return error.code, json.load(error)

    def test_runtime_state_and_generation_conflicts_leave_other_modules_usable(self):
        code, state = self.runtime_request('state')
        self.assertEqual(code, 200)
        self.assertFalse(state['enabled'])
        self.assertEqual(self.runtime_request('start', dict(session_id='unknown', expected_generation=0))[0], 422)
        code, conflict = self.runtime_request('stop', dict(expected_generation=99))
        self.assertEqual(code, 409)
        self.assertEqual(conflict['state']['generation'], 0)
        for route in ('/api/characters/state', '/api/shells/state', '/api/shell-characters/state'):
            with self.opener.open(self.origin+route) as r: self.assertEqual(r.status, 200)

    def test_runtime_body_headers_size_duplicates_and_origin(self):
        value = dict(expected_generation=0)
        self.assertEqual(self.runtime_request('stop', value, headers={'Content-Type':'application/json'})[0], 415)
        self.assertEqual(self.runtime_request('stop', value, headers={'Content-Type':'application/json','X-DeskBot-Studio':'shell-runtime-v1','Origin':'https://example.test'})[0], 403)
        self.assertEqual(self.runtime_request('stop', raw=b' '*8193)[0], 413)
        self.assertEqual(self.runtime_request('stop', raw=b'{"expected_generation":0,"expected_generation":1}')[0], 422)
        self.assertEqual(self.runtime_request('stop', {})[0], 422)
        self.assertEqual(self.runtime_request('stop', dict(expected_generation=True))[0], 409)

    def test_server_close_stops_scheduler_before_releasing_stores(self):
        events = []
        for obj, name in [(self.server.shell_runtime, 'runtime'), (self.server.shell_characters, 'registry'),
                          (self.server.characters, 'characters'), (self.server.shells, 'sessions')]:
            original = obj.close
            def record(original=original, name=name):
                events.append(name); return original()
            obj.close = record
        self.server.shutdown(); self.thread.join(3)
        self.server.server_close()
        self.assertEqual(events[:4], ['runtime', 'registry', 'characters', 'sessions'])
        self.assertFalse(self.server.shell_runtime.thread.is_alive())


class BridgeTests(unittest.TestCase):
    def test_single_owner_atomic_snapshot_keeps_motion_unknown_and_original_time(self):
        bridge = ControlBridge()
        self.addCleanup(bridge.close)
        inputs = bridge.shell_inputs()
        self.assertEqual(set(inputs), {'control', 'magnetic', 'motion'})
        self.assertIsNone(inputs['motion'])
        self.assertEqual(inputs['control']['connection_id'], inputs['magnetic']['identity']['connection_id'])
        self.assertIsNone(bridge.process)


if __name__ == '__main__': unittest.main()
