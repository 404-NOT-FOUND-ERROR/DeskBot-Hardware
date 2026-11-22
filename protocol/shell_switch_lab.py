"""In-memory device over the production host session and role codecs."""
from copy import deepcopy
import json

from protocol.shell_switch_adapter import ShellSwitchAdapter, IDENTITY
from protocol.studio_control_link import ControlLink, validate_command
from protocol.studio_device_roles import validate_reply
from protocol import studio_usb_session as session
from protocol.studio_display_query import reason as display_reason

DEVICE = 'device-'+'a'*32
BOOT = 'boot-'+'b'*32
SCENARIOS = {
    'switch':'换壳后切换', 'speaking':'说完再换角', 'rapid':'连续换壳',
    'removed':'取下后保持', 'unknown':'未知外壳提示', 'moving':'转动后重新确认',
    'menu':'菜单返回后切换', 'reconnect':'断线后重读',
    'rejected':'设备拒绝后重试', 'save_failure':'保存失败后接续',
    'lost_reply':'回复丢失后查询', 'stale_fact':'重复旧识别不触发切换',
}


class MemoryRoleDevice:
    """Simulates protocol replies only; no bridge, USB module, file or network I/O."""
    def __init__(self, roles, graph, device_id=DEVICE):
        self.roles, self.graph = list(roles), graph
        self.device_id = device_id
        self.sequence = 0
        self.session_number = 1
        self.target = self.active = self.saved = 0
        self.request_id, self.phase = '', 'idle'
        self.changed_at = None
        self.save_state = 'restored'
        self.app, self.page = 'idle', 'home'
        self.reject_next = self.fail_save = self.drop_next = False
        self.fail_consumed = False
        self.wires = []

    @property
    def session_id(self):
        return f"session-{'b'*32}-{self.session_number}"

    def hello(self):
        return dict(device_id=self.device_id, boot_id=BOOT)

    def advance(self, at):
        if self.changed_at is None: return
        if at-self.changed_at >= 300:
            self.active = self.target
            self.phase = 'applied'
            self.save_state = 'pending'
        if at-self.changed_at >= 1600:
            if self.fail_save and not self.fail_consumed:
                self.save_state = 'failed'
            else:
                self.saved = self.target
                self.save_state = 'saved'
            self.changed_at = None

    def reply(self, request, at):
        session.validate_request(request)
        self.advance(at)
        self.sequence += 1
        self.wires.append(dict(at_ms=at, operation=request['operation'], request_id=request['request_id']))
        common = dict(request_id=request['request_id'], target_id=self.device_id, boot_id=BOOT,
            session_id=self.session_id, sequence=self.sequence, at_ms=at, lease_ms=30000)
        op = request['operation']
        if op == 'open':
            return dict(common, schema=session.SCHEMA, message_type='session', state='ready',
                        reason='session_opened', controls_available=False)
        if op == 'display':
            row = dict(common, schema='deskbot.studio_display/v1', message_type='display',
                application=dict(revision=1,phase='settled',value=self.app),
                page=dict(revision=1,phase='settled',value=self.page),sleep='unknown',
                controls_available=False,player=dict(revision=1,state='initialized'))
            row['preview'] = dict(available=False,reason=display_reason(row))
            return row
        result, why = 'state', 'none'
        if op.startswith('role.activate.'):
            if self.reject_next:
                self.reject_next = False
                result, why = 'rejected', 'player_unavailable'
            else:
                self.target = int(op.split('.')[2])
                if self.save_state == 'failed':
                    self.fail_consumed = True
                self.request_id, self.phase, self.changed_at = request['request_id'], 'queued', at
                result, why = 'accepted', 'queued'
                if self.drop_next:
                    self.drop_next = False
                    return None
        elif op != 'role.query':
            raise ValueError('内存设备收到不支持的操作')
        return validate_reply(dict(common, schema='deskbot.studio_roles/v1',message_type='roles',
            result=result,reason=why,loaded=True,graph_sha256=self.graph,roles=self.roles,
            default_index=0,target_index=self.target,current_index=self.active,active_index=self.active,
            phase='held',blocked=0,sleep='unknown',saved_index=self.saved,save_state=self.save_state,
            activation=dict(request_id=self.request_id,status=self.phase)))


class SessionLab:
    def __init__(self, bindings, graph, device_id=DEVICE):
        self.device = MemoryRoleDevice(graph['roles'], graph['sha256'],device_id)
        self.link = ControlLink(nonce=lambda:'lab-client-'+str(self.device.session_number))
        self.adapter = ShellSwitchAdapter(bindings, device_id=device_id)
        self.commands, self.sequence, self.at = [], 0, 0
        self.link.observe(self.device.hello(), 0)
        self.command('enable', 0)
        self.read_requested = False

    def command(self, operation, at, command=None):
        identity = self.link.snapshot(at)
        value = command or dict(operation=operation,command_id='lab-'+str(len(self.commands)+1),
            **{k:identity[k] for k in IDENTITY})
        if value['target_id'] is None:
            value.update(target_id=self.device.device_id,boot_id=BOOT)
        validate_command(value)
        self.commands.append(dict(at_ms=at, **deepcopy(value)))
        self.link.command(value,at)

    def pump(self, at):
        self.at = at
        self.device.advance(at)
        wire = self.link.tick(at)
        if wire:
            reply = self.device.reply(json.loads(wire), at)
            if reply: self.link.accept(reply,at)
        control = self.link.snapshot(at)
        if control['session_id'] and not self.read_requested:
            self.command('read_roles',at)
            self.read_requested = True
        return self.link.snapshot(at)

    def step(self, at, *, shell=None, status='known', fresh=True):
        control = self.pump(at)
        self.adapter.update(control,at)
        if shell or status != 'known':
            if fresh: self.sequence += 1
            binding = self.adapter.coordinator.bindings.get(shell)
            source = binding and binding['source']
            self.adapter.observe(dict(identity={k:control[k] for k in IDENTITY},sequence=self.sequence,
                archive_sha256=source['archive_sha256'] if source else 'f'*64,
                observation=dict(status=status,profile_id=source['magnet_profile_id'] if source and status=='known' else None)),at)
        command = self.adapter.next_command(at)
        if command: self.command(command['operation'],at,command)

    def reconnect(self, at):
        self.link.disconnect(at)
        self.adapter.update(self.link.snapshot(at),at)
        self.device.session_number += 1
        self.link.observe(self.device.hello(),at)
        self.command('enable',at)
        self.read_requested = False
        self.sequence = 0


def run_session_lab(bindings, graph, scenario, shell_a, shell_b):
    if scenario not in SCENARIOS or shell_a not in bindings or shell_b not in bindings:
        raise ValueError('请选择已有外壳和会话联测场景')
    chosen = deepcopy(bindings)
    for index, binding in enumerate(chosen.values()):
        # A private simulation context substitutes explicitly synthetic registration identities.
        binding['ready'] = bool(binding['ready'])
        binding['source'] = dict(device_id=DEVICE,archive_sha256='f'*64,magnet_profile_id='lab-'+str(index))
    lab = SessionLab(chosen,graph)
    retried = False
    for at in range(0,24001,100):
        shell, status, fresh = (shell_a if at<5000 else shell_b), 'known', True
        lab.device.app = 'speaking' if scenario in ('speaking','rapid','removed','unknown','stale_fact') and 5000<=at<8500 else 'idle'
        lab.device.page = 'other' if scenario=='menu' and 5000<=at<8500 else 'home'
        if scenario=='rapid' and at>=6600: shell=shell_a
        if scenario=='removed' and at>=6600: shell,status=None,'removed'
        if scenario=='unknown' and at>=6600: shell,status=None,'unknown'
        if scenario=='moving':
            lab.adapter.motion(5000<=at<7500,at)
        if scenario=='stale_fact' and at>=5100:
            fresh=False
        if scenario=='reconnect' and at==6400:
            lab.reconnect(at)
        if scenario=='rejected' and at==5000:
            lab.device.reject_next=True
        if scenario=='lost_reply' and at==5000:
            lab.device.drop_next=True
        if scenario=='save_failure' and at==5000:
            lab.device.fail_save=True
        lab.step(at,shell=shell,status=status,fresh=fresh)
        if lab.adapter.error and scenario in ('rejected','save_failure') and at>=9000 and not retried:
            lab.adapter.retry(at)
            retried=True
    report = lab.adapter.snapshot()
    activation_commands = [c for c in lab.commands if c['operation'].startswith('role.activate.')]
    return dict(kind='session_lab',label=SCENARIOS[scenario],scenario=scenario,
        input_kind='simulated_recognition_and_memory_device',state=report['decision'],
        adapter=report, timeline=report['timeline'],commands=lab.commands,wire=lab.device.wires,
        simulated_activation_count=len(activation_commands),device_commands=0,
        hardware_accessed=False,voice_service='pending')
