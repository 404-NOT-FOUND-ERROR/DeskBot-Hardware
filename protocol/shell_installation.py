"""Owner-confirmed physical installations and device expression adoption."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

from protocol.character_profiles import CharacterConflict, text, SHA
from protocol.role_activation import StateOwner, atomic_save, RoleStorageError, MAX_REVISION
from protocol.shell_characters import binding_hash
from protocol.shell_profile_archive import canonical, digest
from protocol.shell_switch_adapter import ShellSwitchAdapter, IDENTITY
from protocol.studio_timing import IPC_TIMEOUT_MS, REQUEST_TIMEOUT_MS
from protocol.studio_usb_session import DEVICE, _unique

SCHEMA = 'deskbot.shell_installation/v1'
TERMINAL = ('saved', 'superseded', 'needs_confirmation', 'failed', 'retry_required')


class ShellInstallation:
    def __init__(self, path, registry, bridge, automatic, *, character_id=None, writer=atomic_save,
                 clock=time.monotonic, background=True):
        self.path, self.registry, self.bridge, self.automatic = Path(path), registry, bridge, automatic
        self.character_id, self.writer, self.clock = character_id, writer, clock
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.owner = StateOwner(self.path)
        self.closed, self.thread = False, None
        self.adapter = self.running_id = self.identity = self.query = None
        self.next_query = 0
        self.recovering, self.service_error = False, None
        self.recovery_after, self.recovery_command = -1, None
        self.doc = dict(schema=SCHEMA, revision=0, current=None, records=[])
        try:
            if self.path.exists():
                if self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('安装记录超过大小限制')
                self.doc = json.loads(self.path.read_bytes(), object_pairs_hook=_unique)
                self._validate(self.doc)
            automatic.installation_busy = self.busy
            if background:
                self.thread = threading.Thread(target=self._run, name='deskbot-installation', daemon=True)
                self.thread.start()
        except Exception:
            self.owner.close()
            raise

    @staticmethod
    def _validate(doc):
        if (type(doc) is not dict or set(doc) != {'schema', 'revision', 'current', 'records'}
                or doc['schema'] != SCHEMA or type(doc['revision']) is not int
                or not 0 <= doc['revision'] <= MAX_REVISION or type(doc['records']) is not list
                or len(doc['records']) > 128):
            raise ValueError('安装记录格式无效')
        ids = set()
        for row in doc['records']:
            if (type(row) is not dict or row.get('installation_id') in ids
                    or row.get('phase') not in TERMINAL + ('confirmed', 'waiting', 'submitting', 'first_frame', 'reconciling')):
                raise ValueError('安装记录内容无效')
            for key in ('installation_id', 'character_id', 'device_id', 'shell_id', 'binding_sha256'):
                text(row.get(key), key, 128)
            if not DEVICE.fullmatch(row['device_id']) or not SHA.fullmatch(row['binding_sha256']):
                raise ValueError('安装设备或关联摘要无效')
            if type(row.get('binding')) is not dict or binding_hash(row['binding']) != row['binding_sha256']:
                raise ValueError('安装关联内容与摘要不同')
            if not isinstance(row.get('profile'), dict) or digest(canonical(row['profile'])) != row['binding']['profile_sha256']:
                raise ValueError('安装角色档案与摘要不同')
            ids.add(row['installation_id'])
        if doc['current'] is not None and doc['current'] not in ids:
            raise ValueError('当前安装记录不存在')

    @contextmanager
    def transaction(self):
        r = self.registry
        with r.sessions.lock, r.characters.lock, r.lock, self.automatic.lock, self.lock:
            r._open()
            if self.closed:
                raise ValueError('安装服务已关闭')
            yield

    def busy(self):
        with self.lock:
            return bool(self.running_id or any(row['phase'] not in TERMINAL for row in self.doc['records']))

    def _record(self, ident):
        return next((row for row in self.doc['records'] if row['installation_id'] == ident), None)

    def _commit(self, doc):
        if doc == self.doc:
            return
        doc['revision'] = self.doc['revision'] + 1
        self._validate(doc)
        if doc['revision'] > MAX_REVISION or len(json.dumps(doc, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
            raise ValueError('安装记录已满，请先归档')
        try:
            self.writer(self.path, doc)
        except OSError as error:
            raise RoleStorageError('安装进度暂未保存，请重新读取后接续') from error
        self.doc = doc

    def _change(self, ident, **values):
        doc = deepcopy(self.doc)
        next(row for row in doc['records'] if row['installation_id'] == ident).update(deepcopy(values))
        self._commit(doc)

    def _expect(self, revision):
        if type(revision) is not int or revision != self.doc['revision']:
            raise CharacterConflict('安装进度已更新，请读取后确认')

    def _choices(self):
        return [dict(shell_id=row['shell_id'], name=row['name'], role_name=row['role_name'],
                     ready=row['role_current'], binding_sha256=binding_hash(row['binding']),
                     expression_role_id=row['binding']['expression_role_id'])
                for row in self._shells() if row['binding']]

    def _shells(self):
        # Manual installation reads saved choices only; magnetic logs are not on this path.
        characters = self.registry.characters.snapshot()
        profiles = {row['role_id']: row for row in characters['profiles']}
        rows = []
        for shell in self.registry.doc['shells']:
            binding = shell['binding']
            profile = profiles.get(binding['role_id']) if binding else None
            current = bool(profile and binding['profile_revision'] == profile['profile_revision']
                and binding['profile_sha256'] == digest(canonical(profile))
                and characters['readiness'][profile['role_id']]['expression_reference'] == 'current')
            rows.append(dict(**deepcopy(shell), role_current=current,
                             role_name=profile['display_name'] if profile else None))
        return rows

    @staticmethod
    def _device(control):
        roles = control.get('roles') or {}
        row = roles.get('reply') or {}
        if (roles.get('status') != 'current' or type(roles.get('age_ms')) is not int
                or roles['age_ms'] >= 3000 or not row.get('loaded')
                or any(row.get(k) != control.get(k) for k in ('target_id', 'boot_id', 'session_id'))):
            return None
        names = row['roles']
        name = lambda key: names[row[key]] if 0 <= row[key] < len(names) else None
        return dict(device_id=control['target_id'], connection_id=control['connection_id'],
                    boot_id=control['boot_id'], session_id=control['session_id'], sequence=row['sequence'],
                    graph_sha256=row['graph_sha256'], current=name('current_index'),
                    actual=name('active_index'), target=name('target_index'), saved=name('saved_index'), save_state=row['save_state'],
                    activation=deepcopy(row['activation']))

    def state(self):
        with self.transaction(), self.bridge.lock:
            return dict(**deepcopy(self.doc), choices=self._choices(), character_id=self.character_id,
                        device=self._device(self.bridge.control()), error=self.service_error,
                        scope='device_expression', client_role_adoption='pending',
                        automatic_enabled=self.automatic.enabled)

    def confirm(self, installation_id, character_id, device_id, shell_id, binding_sha256, plan_id, expected_revision):
        for value, label in ((installation_id, '安装编号'), (character_id, '持续个体编号')):
            text(value, label, 128)
        if type(device_id) is not str or not DEVICE.fullmatch(device_id):
            raise ValueError('请选择当前桌宠设备')
        if plan_id is not None:
            text(plan_id, '方案编号', 128)
        request = dict(installation_id=installation_id, character_id=character_id, device_id=device_id,
                       shell_id=shell_id, binding_sha256=binding_sha256, plan_id=plan_id)
        with self.transaction():
            old = self._record(installation_id)
            if old:
                if any(old.get(key) != value for key, value in request.items()):
                    raise CharacterConflict('这个安装编号已有不同内容，请重新确认')
                return self.state()
            self._expect(expected_revision)
            if self.character_id and character_id != self.character_id:
                raise CharacterConflict('请使用当前客户端关联的持续个体')
            shell = next((row for row in self._shells() if row['shell_id'] == shell_id), None)
            if (not shell or not shell['binding'] or not shell['role_current']
                    or binding_hash(shell['binding']) != binding_sha256):
                raise CharacterConflict('外壳角色已有更新，请重新确认当前关联')
            if len(self.doc['records']) >= 128:
                raise ValueError('安装记录已满，请先归档')
            doc = deepcopy(self.doc)
            prior = self._record(doc['current'])
            if prior and not prior['command'] and prior['phase'] not in TERMINAL:
                next(row for row in doc['records'] if row['installation_id'] == prior['installation_id']).update(phase='superseded')
            doc['current'] = installation_id
            profile = next(row for row in self.registry.characters.snapshot()['profiles'] if row['role_id'] == shell['binding']['role_id'])
            doc['records'].append(dict(**request, binding=deepcopy(shell['binding']),
                profile=deepcopy(profile), automatic_was_enabled=self.automatic.enabled,
                shell_name=shell['name'], role_name=shell['role_name'], phase='confirmed', reason='owner_confirmed',
                confirmed_at=datetime.now(timezone.utc).isoformat(), source='owner_manual', command=None,
                attempts=[], first_frame=False, saved=False, scope='device_expression'))
            self._commit(doc)
            if self.automatic.enabled:
                self.automatic.stop(self.automatic.generation)
            return self.state()

    def retry(self, installation_id, expected_revision):
        with self.transaction():
            self._expect(expected_revision)
            record = self._record(installation_id)
            if not record or installation_id != self.doc['current'] or record['phase'] not in ('failed', 'retry_required'):
                raise ValueError('请读取当前安装进度后选择可接续的记录')
            self._change(installation_id, phase='confirmed', reason='retry_requested', command=None)
            self.adapter = self.running_id = None
            return self.state()

    def _valid(self, record):
        return any(row['shell_id'] == record['shell_id'] and row['role_current'] and row['binding']
                   and binding_hash(row['binding']) == record['binding_sha256']
                   for row in self._shells())

    @staticmethod
    def _target(record):
        binding = record['binding']
        return dict(shell_id=record['shell_id'], shell_name=record['shell_name'], role_name=record['role_name'],
                    role_id=binding['role_id'], profile_revision=binding['profile_revision'],
                    expression_role_id=binding['expression_role_id'],
                    expression_graph_sha256=binding['expression_graph_sha256'],
                    binding_sha256=record['binding_sha256'], ready=True, source=None)

    def tick(self):
        with self.transaction(), self.bridge.lock:
            current = self._record(self.doc['current'])
            if current and current['phase'] == 'saved' and not self._valid(current):
                # Saved is a historical device fact; a revised binding needs owner confirmation.
                self._change(current['installation_id'], phase='needs_confirmation', reason='binding_updated')
            unfinished = next((row['installation_id'] for row in self.doc['records']
                               if row['command'] and row['phase'] not in TERMINAL), None)
            record = self._record(self.running_id or unfinished or self.doc['current'])
            if not record or not self.running_id and record['phase'] in TERMINAL:
                return
            ident, now = record['installation_id'], int(self.clock() * 1000)
            if ident != self.doc['current'] and not record['command']:
                self._change(ident, phase='superseded', reason='new_installation_confirmed')
                self.adapter = self.running_id = None
                return
            if self.automatic.enabled:
                self.automatic.stop(self.automatic.generation)
            if self.automatic._outstanding():
                self._change(ident, phase='waiting', reason='waiting_previous')
                return
            control = self.bridge.control()
            key = tuple(control.get(k) for k in IDENTITY)
            if self.adapter is None or key != self.identity:
                self.adapter = ShellSwitchAdapter({}, device_id=record['device_id'])
                self.running_id, self.identity = ident, key
                self.query, self.next_query = None, 0
                self.recovering = bool(record['command'])
                self.recovery_command = None
                self.recovery_after = (control.get('roles', {}).get('reply') or {}).get('sequence', -1)
                if self.recovering:
                    self.adapter.query_required = True
                    self.adapter.query_after = self.recovery_after
                    self._change(ident, phase='reconciling', reason='read_device_state')
            adapter = self.adapter
            adapter.update(control, now)
            if self.recovering and self.recovery_command is None:
                adapter.query_required = True
                adapter.query_after = self.recovery_after
            self.service_error = None
            device = self._device(control)
            valid = self._valid(record)
            if not valid and not record['command'] and not adapter._inflight() and not self.recovering:
                self._change(ident, phase='needs_confirmation', reason='binding_updated')
                self.adapter = self.running_id = None
                return
            last = control.get('last_command') or {}
            recovery_fresh = (self.recovery_command and last.get('command_id') == self.recovery_command
                and last.get('status') == 'processed' and device and device['sequence'] > self.recovery_after
                and not control.get('host_command') and not control.get('pending')
                and not control.get('roles', {}).get('pending'))
            if self.recovering and recovery_fresh and device['device_id'] == record['device_id']:
                target = self._target(record)
                matched = (device['graph_sha256'] == target['expression_graph_sha256']
                           and device['actual'] == device['target'] == device['current'] == target['expression_role_id']
                           and device['activation']['status'] not in ('queued', 'switching'))
                if matched and device['saved'] == target['expression_role_id'] and device['save_state'] in ('saved', 'restored'):
                    self._change(ident, phase='saved' if valid else 'needs_confirmation', reason='state_reconciled',
                                 first_frame=True, saved=True, completion_source='device_snapshot', device_result=device)
                    self.adapter = self.running_id = None
                    return
                if matched and device['save_state'] == 'pending':
                    self._change(ident, phase='reconciling', reason='save_pending', first_frame=True)
                elif matched and device['save_state'] == 'failed':
                    self._change(ident, phase='failed', reason='save_failed', first_frame=True, device_result=device)
                    self.adapter = self.running_id = None
                    return
                elif device['activation']['status'] not in ('queued', 'switching'):
                    self._change(ident, phase='retry_required' if valid else 'needs_confirmation', reason='device_state_read')
                    self.adapter = self.running_id = None
                    return
            if valid and not self.recovering:
                adapter.owner_target(self._target(record), now)
            attempt = adapter.attempt
            if attempt:
                if attempt['status'] == 'saved':
                    self._change(ident, phase='saved' if valid else 'needs_confirmation', reason='expression_saved',
                                 first_frame=True, saved=True, completion_source='correlated_device_reply',
                                 device_result=device, request_result=attempt)
                    self.adapter = self.running_id = None
                    return
                if adapter.error and adapter.error not in ('result_unknown', 'submission_unknown'):
                    self._change(ident, phase='failed', reason=adapter.error, first_frame=attempt['first_frame'],
                                 device_result=device, request_result=attempt)
                    self.adapter = self.running_id = None
                    return
                self._change(ident, phase='reconciling' if adapter.error else 'first_frame' if attempt['first_frame'] else 'submitting',
                             reason=adapter.reason, first_frame=attempt['first_frame'])
            if self.query:
                last = control.get('last_command') or {}
                if ((last.get('command_id') == self.query['command_id'] and last.get('status') == 'rejected')
                        or now - self.query['at_ms'] >= IPC_TIMEOUT_MS + REQUEST_TIMEOUT_MS):
                    adapter.read_requested = False
                    self.query, self.next_query = None, now + 1000
                elif not adapter.read_requested:
                    self.query = None
            if now >= self.next_query:
                command = adapter.next_command(now, allow_activation=valid and not self.recovering)
                if command:
                    if command['operation'].startswith('role.activate.'):
                        # Persist intent before I/O under the same owner lock as all presentations.
                        try:
                            history = deepcopy(self._record(ident)['attempts'])
                            history.append(dict(command=command, at=datetime.now(timezone.utc).isoformat()))
                            self._change(ident, phase='submitting', reason='activate_expression', command=command, attempts=history)
                        except RoleStorageError:
                            self.adapter = self.running_id = None
                            raise
                    try:
                        self.bridge.command(command)
                    except (ValueError, OSError) as error:
                        adapter.submission_failed(command['command_id'], now)
                        adapter.read_requested = False
                        self.next_query = now + 1000
                        self.service_error = str(error)
                    else:
                        self.service_error = None
                        if command['operation'] == 'read_roles':
                            self.query = dict(command_id=command['command_id'], at_ms=now)
                            if self.recovering:
                                self.recovery_command = command['command_id']
                                self.recovery_after = device['sequence'] if device else -1
            if not adapter.attempt and not self.recovering:
                self._change(ident, phase='waiting', reason=adapter.reason)

    def _run(self):
        while not self.stop.wait(.1):
            try:
                self.tick()
            except Exception as error:
                with self.lock:
                    self.service_error = str(error)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
        with self.lock:
            self.closed = True
            self.owner.close()
            if self.automatic.installation_busy == self.busy:
                self.automatic.installation_busy = lambda: False
