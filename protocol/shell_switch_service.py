"""Server-owned shell scheduler. The existing bridge alone owns device I/O."""
from contextlib import contextmanager
from copy import deepcopy
import threading
import time

from protocol.shell_profile_archive import canonical, digest
from protocol.shell_switch_adapter import project_bindings
from protocol.shell_switch_runtime import ShellSwitchRuntime
from protocol.studio_timing import IPC_TIMEOUT_MS, REQUEST_TIMEOUT_MS


class RuntimeConflict(ValueError):
    pass


class ShellRuntimeConfig:
    """Pin validated inputs once; check versions under the same locks as command handoff."""
    def __init__(self, registry):
        self.registry = registry

    @contextmanager
    def transaction(self):
        r = self.registry
        with r.sessions.lock, r.characters.lock, r.lock:
            r._open()
            yield self

    def describe(self):
        r = self.registry
        state = r.state()
        bindings = project_bindings(state)
        projects = []
        for s in r.sessions.state()['sessions']:
            sources = [v for v in state['sources'] if v['session_id'] == s['id']]
            ready = [b for b in bindings.values() if b['ready'] and b['source']['session_id'] == s['id']]
            reason = ('registration_required' if not sources or any(v['recognition'] != 'validated_offline' for v in sources)
                      else 'association_required' if not ready else 'ready')
            projects.append(dict(session_id=s['id'], name=s['name'], ready=reason == 'ready',
                                 reason=reason, associations=len(ready)))
        return projects

    def token(self, paths):
        r = self.registry
        # Metadata checks catch project/log replacements without reparsing all samples every tick.
        stamps = []
        for p in paths:
            stat = p.stat()
            stamps.append((str(p), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return digest(canonical(dict(shells=r.doc['shells'], characters=r.characters.snapshot(), files=stamps)))

    def prepare(self, session_id):
        r = self.registry
        choice = next((p for p in self.describe() if p['session_id'] == session_id), None)
        if not choice or not choice['ready']:
            raise ValueError('请先完成这个项目的采样验证和角色关联')
        folder = r.sessions._folder(session_id)
        doc = r.sessions._load(session_id)
        paths = tuple(sorted({folder / 'session.json',
            r.sessions.project_root / doc['assessment']['folder'] / 'suite.json',
            *(r.sessions.project_root / t['recording']['log'] for t in doc['tasks'] if t['recording'])}))
        token = self.token(paths)
        capture = r.sessions.runtime_recordings(session_id)  # Verifies suite and original log hashes.
        state = r.state()
        bindings = {k: b for k, b in project_bindings(state).items()
                    if b['ready'] and b['source']['session_id'] == session_id
                    and b['source']['device_id'] == capture['device_id']
                    and b['source']['archive_sha256'] == capture['archive']['payload_sha256']}
        if not bindings:
            raise ValueError('请确认当前登记档案对应的角色关联')
        if token != self.token(paths):
            raise ValueError('采样文件已有更新，请重新开始')
        pin = dict(session_id=session_id, name=choice['name'], device_id=capture['device_id'],
                   archive_sha256=capture['archive']['payload_sha256'], associations=len(bindings),
                   characters=[{k: b[k] for k in ('shell_id', 'role_id', 'profile_revision', 'binding_sha256')}
                               for b in bindings.values()], inputs_sha256=token)
        return dict(pin=pin, bindings=bindings, archive=capture['archive'], context=capture['context'],
                    paths=paths, token=token)


class ShellSwitchService:
    def __init__(self, config, bridge, *, clock=time.monotonic, interval=.1, background=True):
        self.config, self.bridge, self.clock = config, bridge, clock
        self.lock = threading.RLock()
        self.closed = self.enabled = False
        self.generation = 0
        self.runtime = self.lease = None
        self.reason, self.error = 'stopped', None
        self.motion = None
        self.identity = None
        self.next_query_at = 0
        self.query = None
        self.ticks = 0
        self.wake = threading.Event()
        self.thread = None
        self.interval = interval
        self.installation_busy = lambda: False
        if background:
            self.thread = threading.Thread(target=self._run, name='deskbot-shell-switch', daemon=True)
            self.thread.start()

    def _now(self):
        return int(self.clock()*1000)

    def _expect(self, expected_generation):
        if self.closed:
            raise ValueError('自动换角服务已关闭')
        if type(expected_generation) is not int or expected_generation != self.generation:
            raise RuntimeConflict('运行状态已更新，请读取后重试')

    def _outstanding(self):
        attempt = self.runtime and self.runtime.adapter.attempt
        return bool(attempt and attempt['status'] in ('submitted', 'queued', 'switching', 'visible', 'uncertain'))

    def _snapshot(self, projects):
        runtime = self.runtime.snapshot() if self.runtime else None
        outstanding = self._outstanding()
        return deepcopy(dict(schema='deskbot.shell_switch_service/v1', generation=self.generation,
            enabled=self.enabled, closed=self.closed, phase='closed' if self.closed else
                'running' if self.enabled else 'finishing' if outstanding else 'stopped',
            reason=self.reason, error=self.error, pin=self.lease['pin'] if self.lease else None,
            projects=projects, motion=self.motion, runtime=runtime, ticks=self.ticks,
            can_start=not self.closed and not outstanding and not self.installation_busy() and any(p['ready'] for p in projects),
            can_retry=bool(self.enabled and self.runtime and self.runtime.adapter.error
                           and self.runtime.adapter.error not in ('result_unknown', 'submission_unknown')),
            voice_service='pending'))

    def state(self):
        # Reading status neither schedules work nor renews sample age.
        with self.config.transaction():
            projects = self.config.describe()
            with self.lock:
                return self._snapshot(projects)

    def start(self, session_id, expected_generation):
        with self.config.transaction(), self.lock:
            self._expect(expected_generation)
            if self.installation_busy():
                raise RuntimeConflict('正在完成主人确认的安装，请在保存完成后开始自动模式')
            if (self.enabled and self.lease['pin']['session_id'] == session_id
                    and self.config.token(self.lease['paths']) == self.lease['token']):
                return self._snapshot(self.config.describe())
            if self._outstanding():
                raise ValueError('正在核对上次请求，完成后可以开始')
            lease = self.config.prepare(session_id)
            runtime = ShellSwitchRuntime(lease['bindings'], device_id=lease['pin']['device_id'],
                                         archive=lease['archive'], context=lease['context'])
            runtime.reset_segment(self._now())
            self.runtime, self.lease = runtime, lease
            self.enabled, self.motion, self.identity = True, None, None
            self.reason, self.error = 'await_connection', None
            self.generation += 1
            self.next_query_at = 0
            self.query = None
            return self._snapshot(self.config.describe())

    def stop(self, expected_generation):
        with self.config.transaction(), self.lock:
            self._expect(expected_generation)
            if self.enabled:
                self.enabled = False
                self.runtime.reset_segment(self._now())
                self.generation += 1
            self.reason = 'finishing' if self._outstanding() else 'stopped'
            return self._snapshot(self.config.describe())

    def retry(self, expected_generation):
        with self.config.transaction(), self.lock:
            self._expect(expected_generation)
            if not self.enabled or not self.runtime:
                raise ValueError('请先开始自动换角')
            if self.config.token(self.lease['paths']) != self.lease['token']:
                raise RuntimeConflict('配置已更新，请重新开始')
            if self.runtime.adapter.error in ('result_unknown', 'submission_unknown'):
                raise ValueError('正在核对上次请求，请等待查询结果')
            if not self.runtime.adapter.error:
                raise ValueError('当前请求正常，无需重试')
            self.runtime.adapter.retry(self._now())
            self.generation += 1
            return self._snapshot(self.config.describe())

    def tick(self):
        # Lock order: sessions -> characters -> associations -> service -> bridge.
        with self.config.transaction(), self.lock:
            if self.closed or not self.runtime:
                return
            now = self._now()
            if self.enabled and self.config.token(self.lease['paths']) != self.lease['token']:
                self.enabled = False
                self.runtime.reset_segment(now)
                self.reason = 'config_updated'
                self.generation += 1
            with self.bridge.lock:
                inputs = self.bridge.shell_inputs()
                if type(inputs) is not dict or set(inputs) != {'control', 'magnetic', 'motion'} or (
                        inputs['motion'] is not None and type(inputs['motion']) is not bool):
                    raise ValueError('连接状态格式需要更新')
                self.motion = inputs['motion']
                control = inputs['control']
                identity = tuple(control[k] for k in ('connection_id','target_id','boot_id','session_id'))
                if identity != self.identity:
                    self.runtime.reset_segment(now)
                    self.identity = identity
                if self.enabled:
                    # Unknown movement is held; unknown -> still starts a fresh recognition window.
                    self.runtime.motion(self.motion is not False, now)
                    self.runtime.update(control, inputs['magnetic'], now)
                else:
                    self.runtime.adapter.update(control, now)
                if self.query:
                    ack = control.get('last_command')
                    rejected = ack and ack['command_id'] == self.query['command_id'] and ack['status'] == 'rejected'
                    expired = now-self.query['at_ms'] >= IPC_TIMEOUT_MS+REQUEST_TIMEOUT_MS
                    if rejected or expired:
                        # A rejected/lost directory query must not leave read_requested latched forever.
                        self.runtime.adapter.read_requested = False
                        self.query = None
                        self.next_query_at = now+1000
                    elif not self.runtime.adapter.read_requested:
                        self.query = None
                if (self.enabled or self._outstanding()) and now >= self.next_query_at:
                    command = self.runtime.next_command(now, allow_activation=self.enabled and self.motion is False)
                    if command:
                        try:
                            self.bridge.command(command)
                        except (ValueError, OSError) as error:
                            self.runtime.adapter.submission_failed(command['command_id'], now)
                            self.runtime.adapter.read_requested = False
                            self.error = str(error)
                            self.next_query_at = now+1000
                        else:
                            self.error = None
                            if command['operation'] == 'read_roles':
                                self.query = dict(command_id=command['command_id'], at_ms=now)
                self.ticks += 1
                if self.enabled:
                    self.reason = 'motion_unknown' if self.motion is None else self.runtime.adapter.reason
                elif self.reason != 'config_updated':
                    self.reason = 'finishing' if self._outstanding() else 'stopped'

    def _run(self):
        while not self.wake.wait(self.interval):
            try:
                self.tick()
            except Exception as error:
                # Surface a failed scheduling cycle and require a fresh start; keep the worker alive.
                with self.lock:
                    if self.enabled:
                        self.enabled = False
                        self.generation += 1
                    self.reason, self.error = 'service_error', str(error)

    def close(self):
        # Join outside every lock used by tick, before the server closes stores or the bridge.
        self.wake.set()
        if self.thread:
            self.thread.join()
        with self.lock:
            self.closed = True
            self.enabled = False
