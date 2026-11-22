"""Deterministic character-switch decisions. Adapters own I/O and supply receipts."""
from copy import deepcopy
from protocol.studio_timing import monotonic_ms

STABLE_MS = 800
UNKNOWN_MS = 2000
OBSERVATION_MS = 1500
MODES = ('idle', 'speaking', 'moving', 'updating')
STATUSES = ('known', 'unknown', 'removed', 'unstable', 'missing', 'stale')


class SwitchCoordinator:
    def __init__(self, bindings):
        self.bindings = deepcopy(bindings)
        self.at = 0
        self.epoch = 1
        self.serial = 0
        self.connected = True
        self.synced = True
        self.mode = 'idle'
        self.resources_ready = True
        self.observation = dict(status='removed', shell_id=None)
        self.candidate = None
        self.since = 0
        self.last_observation = 0
        self.unknown_since = None
        self.prompted = False
        self.target = self.pending = self.actual = self.saved = None
        self.reason = 'keep_current'
        self.trace = []

    def snapshot(self):
        return deepcopy(dict(at_ms=self.at, epoch=self.epoch, connected=self.connected, synced=self.synced, mode=self.mode,
            target=self.target, pending=self.pending, actual=self.actual, saved=self.saved,
            reason=self.reason, registration_prompt=self.prompted, scope='offline_simulation', voice_service='pending'))

    def _note(self, kind, reason, **values):
        self.reason = reason
        self.trace.append(dict(at_ms=self.at, event=kind, reason=reason, **deepcopy(values)))

    @staticmethod
    def _identity(value):
        return (value['role_id'], value['profile_revision'], value['expression_role_id']) if value and value.get('role_id') else None

    def _expire_observation(self):
        if self.observation['status'] in ('known', 'unknown') and self.at - self.last_observation > OBSERVATION_MS:
            self.candidate = self.target = None
            self.observation = dict(status='stale', shell_id=None)
            self.unknown_since = None
            self.prompted = False
            self._note('observe', 'observation_expired')

    def _decide(self):
        if self.observation['status'] == 'unknown' and self.unknown_since is not None:
            if not self.prompted and self.at - self.unknown_since >= UNKNOWN_MS:
                self.prompted = True
                self._note('prompt', 'register_shell')
        ident = self.observation['shell_id']
        if (self.connected and self.observation['status'] == 'known' and ident == self.candidate
                and self.at - self.since >= STABLE_MS):
            binding = self.bindings.get(ident)
            if not binding or not binding.get('ready'):
                self.target = None
                self._note('hold', 'binding_required' if not binding else 'binding_updated')
            elif (not self.target or self.target['shell_id'] != ident
                    or self.target['binding_sha256'] != binding['binding_sha256']):
                self.target = deepcopy(binding)
                self._note('target', 'stable_shell', target=self.target)
        if not self.target:
            return
        if not self.connected:
            self._note('hold', 'disconnected')
        elif not self.synced:
            self._note('hold', 'read_device_state')
        elif not self.resources_ready:
            self._note('hold', 'prepare_content')
        elif self.mode != 'idle':
            self._note('hold', self.mode)
        elif self.pending:
            self._note('hold', 'waiting_receipt')
        elif self._identity(self.target) == self._identity(self.actual):
            self._note('hold', 'already_current' if self._identity(self.saved) == self._identity(self.actual) else 'save_pending')
        else:
            self.serial += 1
            self.pending = dict(request_id=f'{self.epoch}-{self.serial}', epoch=self.epoch,
                                target=deepcopy(self.target), first_frame=False)
            self._note('request', 'activate_expression', request=self.pending)

    def feed(self, event):
        if type(event) is not dict or not monotonic_ms(event.get('at_ms')) or event['at_ms'] < self.at:
            raise ValueError('事件时间应为单调递增的毫秒整数')
        kind = event.get('type')
        fields = {'observe': {'status', 'shell_id'}, 'tick': set(), 'mode': {'mode'},
                  'connection': {'connected'}, 'availability': {'ready'},
                  'device_snapshot': {'epoch', 'expression_role_id', 'saved_expression_role_id'},
                  'receipt': {'request_id', 'epoch', 'phase'}}
        if type(kind) is not str or kind not in fields or set(event) != fields[kind] | {'at_ms', 'type'}:
            raise ValueError('换角事件字段无效')
        if kind == 'observe' and (event['status'] not in STATUSES or
                (event['status'] == 'known' and type(event['shell_id']) is not str) or
                (event['status'] != 'known' and event['shell_id'] is not None)):
            raise ValueError('外壳观察格式无效')
        if kind == 'mode' and event['mode'] not in MODES:
            raise ValueError('交互状态无效')
        if kind in ('connection', 'availability') and type(event['connected' if kind == 'connection' else 'ready']) is not bool:
            raise ValueError('连接或内容状态应为勾选值')
        if kind == 'receipt' and (type(event['request_id']) is not str or type(event['epoch']) is not int
                                  or event['phase'] not in ('first_frame', 'saved')):
            raise ValueError('回执格式无效')
        if kind == 'device_snapshot' and (type(event['epoch']) is not int or any(
                v is not None and (type(v) is not str or len(v)>64)
                for v in (event['expression_role_id'], event['saved_expression_role_id']))):
            raise ValueError('设备表达快照格式无效')
        self.at = event['at_ms']
        # Expire before replacing a packet: even the same shell must settle again after a gap.
        self._expire_observation()
        if kind == 'observe':
            status, ident = event['status'], event['shell_id']
            self.last_observation = self.at
            if self.mode == 'moving':
                status, ident = 'unstable', None
            if status == 'unknown':
                if self.observation['status'] != 'unknown':
                    self.unknown_since = self.at
            else:
                self.unknown_since = None
                self.prompted = False
            self.observation = dict(status=status, shell_id=ident)
            if status != 'known':
                self.candidate = self.target = None
                self._note('observe', 'keep_current', status=status)
            elif ident != self.candidate:
                self.candidate, self.since = ident, self.at
                self.target = None  # An unsettled new shell replaces every unsent old choice.
                self._note('observe', 'wait_stable', shell_id=ident)
        elif kind == 'mode':
            self.mode = event['mode']
            if self.mode == 'moving':
                self.candidate = self.target = None
                self.observation = dict(status='unstable', shell_id=None)
            self._note('mode', self.mode)
        elif kind == 'availability':
            self.resources_ready = event['ready']
            self._note('content', 'content_ready' if self.resources_ready else 'prepare_content')
        elif kind == 'connection':
            if self.connected != event['connected']:
                self.epoch += 1
                self.connected = event['connected']
                self.synced = False
                self.pending = self.actual = self.saved = self.target = self.candidate = None
                self.observation = dict(status='removed', shell_id=None)
                self.unknown_since = None
                self.prompted = False
            self._note('connection', 'reobserve' if self.connected else 'disconnected')
        elif kind == 'device_snapshot':
            if not self.connected or event['epoch'] != self.epoch:
                self._note('discard', 'old_snapshot')
            else:
                # Device expression alone cannot establish a full persona/voice version.
                self.actual = dict(expression_role_id=event['expression_role_id'], binding_sha256=None)
                self.saved = dict(expression_role_id=event['saved_expression_role_id'], binding_sha256=None)
                self.synced = True
                self._note('snapshot', 'device_state_read', actual=self.actual, saved=self.saved)
        elif kind == 'receipt':
            pending = self.pending
            if (not self.connected or not pending or event['epoch'] != self.epoch
                    or event['request_id'] != pending['request_id']):
                self._note('discard', 'old_receipt')
            elif (not self.bindings.get(pending['target']['shell_id'], {}).get('ready') or
                    self.bindings[pending['target']['shell_id']]['binding_sha256'] != pending['target']['binding_sha256']):
                self.pending = None
                self._note('discard', 'binding_updated')
            elif event['phase'] == 'first_frame':
                self.actual = deepcopy(pending['target'])
                pending['first_frame'] = True
                self._note('first_frame', 'expression_visible', actual=self.actual)
            elif not pending['first_frame']:
                self._note('discard', 'first_frame_required')
            else:
                self.saved = deepcopy(pending['target'])
                self.pending = None
                self._note('saved', 'expression_saved', saved=self.saved)
        self._decide()
        return self.snapshot()


SCENARIOS = {
    'switch': '换上外壳', 'speaking': '说话时换壳', 'rapid': '连续换壳',
    'removed': '取下外壳', 'unknown': '遇到未知外壳', 'moving': '转动后重新确认',
    'updating': '准备内容后切换', 'reconnect': '断开后重新连接', 'old_receipt': '迟到回执',
}


def simulate(bindings, scenario, shell_a, shell_b):
    if type(scenario) is not str or scenario not in SCENARIOS:
        raise ValueError('请选择已有演练场景')
    if shell_a not in bindings or shell_b not in bindings:
        raise ValueError('请先为演练外壳确认角色')
    coordinator = SwitchCoordinator(bindings)
    def send(at, kind, **value):
        coordinator.feed(dict(at_ms=at, type=kind, **value))
    def observe(at, ident): send(at, 'observe', status='known', shell_id=ident)
    def settle(at): send(at, 'tick')
    def ack(at):
        # Only the memory adapter emits these receipts; no USB or network client is available here.
        for _ in range(4):
            pending = deepcopy(coordinator.pending)
            if not pending: return
            for phase in ('first_frame', 'saved'):
                send(at, 'receipt', request_id=pending['request_id'], epoch=pending['epoch'], phase=phase)

    observe(0, shell_a); settle(800); ack(850)
    if scenario == 'switch':
        observe(1000, shell_b); settle(1800); ack(1850)
    elif scenario in ('speaking', 'rapid'):
        send(1000, 'mode', mode='speaking'); observe(1100, shell_b); settle(1900)
        if scenario == 'rapid': observe(2000, shell_a); settle(2800)
        else: observe(2600, shell_b)
        send(3000, 'mode', mode='idle'); ack(3050)
    elif scenario in ('removed', 'unknown'):
        send(1000, 'mode', mode='speaking'); observe(1100, shell_b); settle(1900)
        send(2000, 'observe', status=scenario, shell_id=None)
        send(3000, 'observe', status=scenario, shell_id=None)
        send(4100, 'observe', status=scenario, shell_id=None); settle(5000)
        send(5100, 'mode', mode='idle'); ack(5150)
    elif scenario == 'moving':
        send(1000, 'mode', mode='moving'); observe(1100, shell_b); settle(1900)
        send(2000, 'mode', mode='idle'); observe(2100, shell_b); settle(2900); ack(2950)
    elif scenario == 'updating':
        send(1000, 'availability', ready=False); observe(1100, shell_b); settle(1900)
        send(2000, 'availability', ready=True); ack(2050)
    elif scenario in ('reconnect', 'old_receipt'):
        observe(1000, shell_b); settle(1800); old = deepcopy(coordinator.pending)
        send(1850, 'connection', connected=False); send(2000, 'connection', connected=True)
        send(2010, 'device_snapshot', epoch=coordinator.epoch, expression_role_id=bindings[shell_b]['expression_role_id'],
             saved_expression_role_id=bindings[shell_a]['expression_role_id'])
        if scenario == 'old_receipt' and old:
            send(2050, 'receipt', request_id=old['request_id'], epoch=old['epoch'], phase='saved')
        observe(2100, shell_b); settle(2900); ack(2950)
    return dict(scenario=scenario, label=SCENARIOS[scenario], state=coordinator.snapshot(), timeline=coordinator.trace,
                input_kind='simulated_signals', device_commands=0, voice_service='pending')
