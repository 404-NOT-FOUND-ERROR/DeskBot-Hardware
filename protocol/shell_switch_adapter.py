"""Automatic shell-to-expression adapter; the existing connection owner performs I/O."""
from collections import deque
from copy import deepcopy
import uuid

from protocol.character_switch import SwitchCoordinator
from protocol.shell_characters import binding_hash
from protocol.studio_control_bridge import validate_control
from protocol.studio_control_link import aged_control, validate_command
from protocol.studio_timing import IPC_TIMEOUT_MS, REQUEST_TIMEOUT_MS
from protocol.studio_timing import monotonic_ms
from protocol import studio_usb_session as session

IDENTITY = ('connection_id', 'target_id', 'boot_id', 'session_id')
FACT_STATUSES = ('known', 'unknown', 'removed', 'unstable', 'warming_up', 'ambiguous', 'missing', 'stale')
MAX_TRACE = 160
ATTEMPT_TIMEOUT_MS = IPC_TIMEOUT_MS + REQUEST_TIMEOUT_MS


def project_bindings(state):
    """Full characters keep their identity; only validated registrations can drive the adapter."""
    return {s['shell_id']: dict(shell_id=s['shell_id'], shell_name=s['name'], role_name=s['role_name'],
        role_id=s['binding']['role_id'], profile_revision=s['binding']['profile_revision'],
        expression_role_id=s['binding']['expression_role_id'],
        expression_graph_sha256=s['binding']['expression_graph_sha256'],
        binding_sha256=binding_hash(s['binding']), source=deepcopy(s['binding']['source']),
        ready=s['role_current'] and s['recognition'] == 'validated_offline')
        for s in state['shells'] if s['binding']}


class ShellSwitchAdapter:
    def __init__(self, bindings, *, device_id):
        if type(device_id) is not str or not session.DEVICE.fullmatch(device_id):
            raise ValueError('需要明确的桌宠设备编号')
        self.device_id = device_id
        self.coordinator = SwitchCoordinator(bindings)
        self.coordinator.connected = self.coordinator.synced = False
        self.coordinator.resources_ready = False
        self.at = self.received = 0
        self.control = None
        self.identity = None
        self.retired = deque(maxlen=32)
        self.attempt = None
        self.error = None
        self.reason = 'session_required'
        self.motion_active = False
        self.read_requested = False
        self.trace = deque(maxlen=MAX_TRACE)
        self.command_serial = 0
        self.token = uuid.uuid4().hex
        self.last_fact = None
        self.fact_sequence = -1
        self.query_required = False
        self.query_after = -1
        self.observed_key = self.target_key = self.gate_key = None

    def _note(self, event, reason, **values):
        self.reason = reason
        row = dict(at_ms=self.at, event=event, reason=reason, **deepcopy(values))
        if not self.trace or {k:v for k,v in self.trace[-1].items() if k!='at_ms'} != {k:v for k,v in row.items() if k!='at_ms'}:
            self.trace.append(row)

    def _event(self, kind, **values):
        result = self.coordinator.feed(dict(at_ms=self.at, type=kind, **values))
        # This runtime adapter retains a bounded recent journal.
        del self.coordinator.trace[:-MAX_TRACE]
        return result

    def _begin(self, at_ms):
        if not monotonic_ms(at_ms) or at_ms < self.at:
            raise ValueError('会话时间应为递增的毫秒整数')
        self.at = at_ms
        # Apply each input atomically before opening the decision gate again.
        self.coordinator.resources_ready = False
        self._event('tick')

    def _current(self):
        return aged_control(self.control, self.at-self.received) if self.control else None

    def _roles(self):
        control = self._current()
        return control['roles']['reply'] if control and control.get('roles', {}).get('status') == 'current' else None

    def _gate(self):
        c, row = self._current(), self._roles()
        if not c or not c['enabled'] or not c['session_id'] or c['status'] in ('off', 'disconnected'):
            return 'session_required'
        if c['target_id'] != self.device_id:
            return 'device_changed'
        if not row or not row['loaded']:
            return 'read_roles'
        display = c['display']
        if not display or c['age_ms'] is None or c['age_ms'] >= 2000:
            return 'fresh_display_required'
        if display['application']['phase'] != 'settled' or display['page']['phase'] != 'settled':
            return 'wait_ui'
        app = display['application']['value']
        if app == 'speaking':
            return 'speaking'
        if app in ('upgrading', 'activating'):
            return 'updating'
        if app != 'idle' or display['page']['value'] != 'home':
            return 'wait_home'
        if self.motion_active:
            return 'moving'
        if display['player']['state'] != 'initialized' or row['blocked'] or row['sleep'] == 'sleeping':
            return 'player_busy'
        if (c.get('host_command') or c['pending'] or c['roles']['pending']
                or c['catalog']['status'] == 'reading' or c['status'] != 'ready'):
            return 'owner_busy'
        if row['activation']['status'] in ('queued', 'switching') and not self._inflight():
            return 'player_busy'
        return None

    def _inflight(self):
        return self.attempt and self.attempt['status'] in ('submitted', 'queued', 'switching', 'visible')

    def _finish(self):
        gate = self._gate()
        mode = 'moving' if self.motion_active else gate if gate in ('speaking', 'updating') else 'idle'
        if mode != self.coordinator.mode:
            self._event('mode', mode=mode)
        self._event('availability', ready=gate is None)
        pending, target = self.coordinator.pending, self.coordinator.target
        target_key = target and (target['shell_id'], target['binding_sha256'])
        if target_key != self.target_key:
            self.target_key = target_key
            if target:
                self._note('target', 'stable_shell', role_name=target['role_name'])
        if gate != self.gate_key:
            self.gate_key = gate
            if gate:
                self._note('hold', gate)
        # Before handoff, the latest stable observation can replace an unsent decision.
        if pending and not self._inflight() and not self.error and (
                not target or pending['target']['binding_sha256'] != target['binding_sha256']):
            self.coordinator.pending = None
            self._event('tick')
        if self._inflight() and self.at-self.attempt['progress_at_ms'] >= ATTEMPT_TIMEOUT_MS:
            self.error = 'result_unknown'
            self.attempt['status'] = 'uncertain'
            self.query_required = True
            self.read_requested = False
            self.query_after = self._roles()['sequence'] if self._roles() else -1
            self._note('query', 'result_unknown')
        if self.error:
            self.reason = self.error
        elif gate:
            self.reason = gate
        elif self._inflight():
            self.reason = 'await_result'
        elif self.coordinator.pending:
            self.reason = 'ready_to_submit'
        else:
            self.reason = self.coordinator.reason
        return self.snapshot()

    def observe(self, fact, at_ms):
        if type(fact) is not dict or set(fact) != {'identity', 'sequence', 'archive_sha256', 'observation'}:
            raise ValueError('识别事实需要设备、档案版本与观察结果')
        observation = fact['observation']
        if (type(observation) is not dict or observation.get('status') not in FACT_STATUSES
                or observation.get('status') == 'known' and type(observation.get('profile_id')) is not str):
            raise ValueError('外壳识别观察格式无效')
        self._begin(at_ms)
        if (type(fact['identity']) is not dict or set(fact['identity']) != set(IDENTITY)
                or type(fact['sequence']) is not int or fact['sequence'] < 0):
            raise ValueError('识别事实需要当前连接身份和递增采样序号')
        if tuple(fact['identity'][k] for k in IDENTITY) != self.identity or fact['sequence'] <= self.fact_sequence:
            self._note('discard', 'old_observation')
            return self._finish()
        self.fact_sequence = fact['sequence']
        self.last_fact = deepcopy(fact)
        status, ident = observation['status'], None
        if status == 'known':
            matches = [b for b in self.coordinator.bindings.values() if b.get('ready') and b.get('source')
                and b['source']['device_id'] == fact['identity']['target_id'] == self.device_id
                and b['source']['archive_sha256'] == fact['archive_sha256']
                and b['source']['magnet_profile_id'] == observation['profile_id']]
            if len(matches) == 1:
                ident = matches[0]['shell_id']
            else:
                status = 'missing'
                self._note('hold', 'association_required')
        if status in ('warming_up', 'ambiguous'):
            status = 'unstable'
        if fact['identity']['target_id'] != self.device_id:
            status, ident = 'missing', None
        if (status, ident) != self.observed_key:
            self.observed_key = (status, ident)
            self._note('recognition', 'wait_stable' if ident else 'keep_current',
                       shell_id=ident, role_name=self.coordinator.bindings[ident]['role_name'] if ident else None)
        self._event('observe', status=status, shell_id=ident)
        return self._finish()

    def motion(self, active, at_ms):
        if type(active) is not bool:
            raise ValueError('转动状态应为勾选值')
        self._begin(at_ms)
        self.motion_active = active
        return self._finish()

    def owner_target(self, target, at_ms):
        """An explicit installation is durable intent, independent of sensor observations."""
        self._begin(at_ms)
        self.coordinator.bindings = {target['shell_id']: deepcopy(target)}
        self.coordinator.observation = dict(status='removed', shell_id=None)
        self.coordinator.target = deepcopy(target)
        return self._finish()

    def replace_bindings(self, bindings, at_ms):
        self._begin(at_ms)
        self.coordinator.bindings = deepcopy(bindings)
        self._event('observe', status='missing', shell_id=None)
        return self._finish()

    def invalidate_observation(self, at_ms):
        """Discard unsent recognition after a stream boundary, retaining an existing handoff."""
        self._begin(at_ms)
        self.observed_key = None
        self._event('observe', status='missing', shell_id=None)
        return self._finish()

    def update(self, control, at_ms):
        value = deepcopy(control)
        host_command = value.pop('host_command', None) if type(value) is dict else None
        if not validate_control(value):
            raise ValueError('设备状态与现有会话契约不同')
        self._begin(at_ms)
        key = tuple(value[k] for k in IDENTITY) if value['enabled'] and value['session_id'] else None
        old_inactive = (not key and any(old[0] == value['connection_id'] for old in self.retired)
                        and (not self.identity or value['connection_id'] != self.identity[0]))
        if key and key in self.retired or old_inactive:
            self._note('discard', 'old_connection')
            return self._finish()
        if key != self.identity:
            if self.identity:
                self.retired.append(self.identity)
            self.identity = key
            self._event('connection', connected=False)
            if key:
                self._event('connection', connected=True)
            self.attempt = None
            self.error = None
            self.read_requested = False
            self.fact_sequence = -1
            self.observed_key = self.target_key = self.gate_key = None
            self.query_required = False
            self.control = None
            self._note('connection', 'read_device_state' if key else 'session_required')
        if self.control:
            # Repeated snapshots cannot make an old reply younger, nor move its sequence back.
            previous = self._current()
            for name in ('display', 'roles'):
                old = self.control.get(name)
                new = value.get(name)
                old_row = old.get('reply') if name == 'roles' and old else old
                new_row = new.get('reply') if name == 'roles' and new else new
                if old_row and new_row:
                    if new_row['sequence'] < old_row['sequence']:
                        self._note('discard', 'old_state')
                        return self._finish()
                    if new_row['sequence'] == old_row['sequence']:
                        if name == 'roles': new['age_ms'] = max(new['age_ms'], old['age_ms']+self.at-self.received)
                        else: value['age_ms'] = max(value['age_ms'], previous['age_ms'])
        value['host_command'] = host_command
        self.control, self.received = value, self.at
        row = self._roles()
        if row and not self.coordinator.synced:
            roles = row['roles']
            self._event('device_snapshot', epoch=self.coordinator.epoch,
                expression_role_id=roles[row['active_index']] if row['active_index'] >= 0 else None,
                saved_expression_role_id=roles[row['saved_index']] if row['saved_index'] >= 0 else None)
        if row:
            if not value['roles']['pending']:
                self.read_requested = False
            self._accept_result(row, value)
        return self._finish()

    def _fail(self, reason):
        self.error = reason
        if self.attempt:
            self.attempt['status'] = 'failed'
        self._note('failed', reason)

    def _accept_result(self, row, control):
        attempt = self.attempt
        if not attempt or attempt['status'] not in ('submitted', 'queued', 'switching', 'visible', 'uncertain'):
            return
        target = attempt['target']
        current = self.coordinator.bindings.get(target['shell_id'])
        obsolete = not current or not current.get('ready') or current['binding_sha256'] != target['binding_sha256']
        ack = control.get('last_command')
        if ack and ack['command_id'] == attempt['command_id'] and ack['status'] == 'rejected':
            self._fail(ack['reason'])
            return
        wire = control.get('role_attempt')
        if not wire or wire['command_id'] != attempt['command_id'] or wire['operation'] != attempt['operation']:
            if self.query_required and row['sequence'] > self.query_after:
                self.query_required = False
                self._fail('retry_required')
            return
        attempt['request_id'] = wire['request_id']
        if row['graph_sha256'] != target['expression_graph_sha256']:
            self._fail('graph_changed')
            return
        activation = row['activation']
        # A direct rejection describes this request in the envelope; activation may be older.
        if row['request_id'] == wire['request_id'] and row['result'] == 'rejected':
            self._fail(row['reason'])
            return
        if activation['request_id'] != wire['request_id']:
            if self.query_required and row['sequence'] > self.query_after:
                self.query_required = False
                self._fail('retry_required')
            return
        if row['target_index'] != attempt['index']:
            self._fail('target_replaced')
            return
        if activation['status'] in ('failed', 'cancelled'):
            self._fail(row['reason'])
            return
        if attempt['status'] == 'uncertain':
            if row['sequence'] <= self.query_after:
                return
            self.error = None
            self.query_required = False
            attempt['progress_at_ms'] = self.at
        if activation['status'] in ('queued', 'switching'):
            if attempt['status'] != activation['status']:
                attempt['progress_at_ms'] = self.at
            attempt['status'] = activation['status']
            self._note('accepted', 'queued' if activation['status']=='queued' else 'switching')
            return
        if activation['status'] != 'applied' or row['active_index'] != attempt['index']:
            return
        if obsolete:
            if not attempt['first_frame']:
                attempt['progress_at_ms'] = self.at
            attempt['first_frame'] = True
            attempt['status'] = 'visible'
            self.error = None
            self.query_required = False
            self._note('device_fact', 'previous_binding_applied')
            if row['save_state'] == 'failed' or (row['saved_index'] == attempt['index']
                    and row['save_state'] in ('saved', 'restored')):
                attempt['status'] = 'superseded'
                self.coordinator.pending = None
                self._note('device_fact', 'previous_binding_save_failed' if row['save_state']=='failed'
                           else 'previous_binding_saved')
            return
        self.error = None
        self.query_required = False
        attempt['status'] = 'visible'
        if not attempt['first_frame']:
            self._event('receipt', request_id=attempt['decision_id'], epoch=attempt['epoch'], phase='first_frame')
            attempt['first_frame'] = True
            attempt['progress_at_ms'] = self.at
            self._note('first_frame', 'expression_visible', role_name=target['role_name'])
        if row['save_state'] == 'failed':
            self._fail('save_failed')
        elif row['saved_index'] == attempt['index'] and row['save_state'] in ('saved', 'restored'):
            self._event('receipt', request_id=attempt['decision_id'], epoch=attempt['epoch'], phase='saved')
            attempt['status'] = 'saved'
            self._note('saved', 'expression_saved', role_name=target['role_name'])

    def next_command(self, at_ms, *, allow_activation=True):
        self._begin(at_ms)
        self._finish()
        c, row = self._current(), self._roles()
        if not c or not self.identity or c['target_id'] != self.device_id:
            return None
        if not row or self.query_required:
            if (self.read_requested or c.get('host_command') or c['pending']
                    or c.get('roles', {}).get('pending') or c['catalog']['status']=='reading'):
                return None
            self.read_requested = True
            return self._command('read_roles', c)
        if not allow_activation or self.error or self._inflight():
            return None
        pending = self.coordinator.pending
        if self._gate() or not pending:
            return None
        target = pending['target']
        if target['expression_graph_sha256'] != row['graph_sha256'] or target['expression_role_id'] not in row['roles']:
            self._note('hold', 'content_required')
            return None
        index = row['roles'].index(target['expression_role_id'])
        operation = f"role.activate.{index}.{row['graph_sha256']}"
        command = self._command(operation, c)
        self.attempt = dict(command_id=command['command_id'], operation=operation, index=index,
            decision_id=pending['request_id'], epoch=pending['epoch'], at_ms=self.at, progress_at_ms=self.at,
            target=deepcopy(target), status='submitted', request_id=None, first_frame=False)
        self._note('request', 'activate_expression', role_name=target['role_name'])
        return command

    def _command(self, operation, control):
        self.command_serial += 1
        return validate_command(dict(operation=operation, command_id=f"shell-{self.token}-{self.command_serial}",
            **{k:control[k] for k in IDENTITY}))

    def submission_failed(self, command_id, at_ms):
        self._begin(at_ms)
        if self.attempt and self.attempt['command_id'] == command_id:
            # The owner may have accepted the handoff before its response was lost.
            self.error = 'submission_unknown'
            self.attempt['status'] = 'uncertain'
            self.query_required = True
            self.read_requested = False
            self.query_after = self._roles()['sequence'] if self._roles() else -1
            self._note('query', 'submission_unknown')
        return self._finish()

    def retry(self, at_ms):
        self._begin(at_ms)
        if self.error in ('result_unknown', 'submission_unknown'):
            self.query_required = True
            self.read_requested = False
            return self._finish()
        if self._inflight() or self._gate() not in (None, 'read_roles'):
            return self._finish()
        row = self._roles()
        self.attempt = None
        self.error = None
        self.read_requested = False
        self.coordinator.pending = None
        if row:
            # Reissue the same expression only after re-reading; device-side dedup preserves playback.
            self._event('device_snapshot', epoch=self.coordinator.epoch,
                expression_role_id=row['roles'][row['active_index']] if row['active_index']>=0 else None,
                saved_expression_role_id=row['roles'][row['saved_index']] if row['saved_index']>=0 else None)
        return self._finish()

    def snapshot(self):
        row = self._roles()
        return dict(schema='deskbot.shell_switch_adapter/v1', at_ms=self.at, reason=self.reason,
            decision=self.coordinator.snapshot(), attempt=deepcopy(self.attempt), error=self.error,
            device_expression=None if not row else dict(
                actual=row['roles'][row['active_index']] if row['active_index']>=0 else None,
                saved=row['roles'][row['saved_index']] if row['saved_index']>=0 else None),
            timeline=list(deepcopy(self.trace)), voice_service='pending')
