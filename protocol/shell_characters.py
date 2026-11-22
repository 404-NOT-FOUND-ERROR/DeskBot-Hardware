"""Local shell choices with pinned full-character versions and offline rehearsal."""
from copy import deepcopy
import json
from pathlib import Path
import threading

from protocol.character_profiles import CharacterConflict, SHA
from protocol.character_switch import SCENARIOS, simulate
from protocol.deskbot_role_profile import ROLE_ID_RE
from protocol.role_activation import StateOwner, atomic_save, RoleStorageError, MAX_REVISION
from protocol.shell_profile_archive import canonical, digest
from protocol.shell_workbench import text
from protocol.studio_usb_session import _unique

SCHEMA = 'deskbot.shell_characters/v1'
MAX_SHELLS = 64


def binding_hash(binding):
    return digest(canonical(binding))


def validate_document(value):
    if (type(value) is not dict or set(value) != {'schema', 'revision', 'shells', 'last_run'}
            or value['schema'] != SCHEMA or type(value['revision']) is not int
            or not 0 <= value['revision'] <= MAX_REVISION or type(value['shells']) is not list
            or len(value['shells']) > MAX_SHELLS):
        raise ValueError('外壳角色库格式无效')
    ids, sources = set(), set()
    for row in value['shells']:
        if type(row) is not dict or set(row) != {'shell_id', 'name', 'binding'}:
            raise ValueError('外壳字段无效')
        ident = row['shell_id']
        if type(ident) is not str or not ROLE_ID_RE.fullmatch(ident) or ident in ids:
            raise ValueError('外壳编号无效或重复')
        ids.add(ident)
        text(row['name'], '外壳名称', 40)
        binding = row['binding']
        if binding is None:
            continue
        if (type(binding) is not dict or set(binding) != {'role_id', 'profile_revision', 'profile_sha256',
                'expression_role_id', 'expression_graph_sha256', 'preview_catalog_id', 'source', 'confirmation'}
                or binding['confirmation'] != 'owner_choice_local'):
            raise ValueError('外壳关联字段无效')
        for field in ('role_id', 'expression_role_id'):
            if type(binding[field]) is not str or not ROLE_ID_RE.fullmatch(binding[field]):
                raise ValueError('关联角色编号无效')
        if type(binding['profile_revision']) is not int or not 1 <= binding['profile_revision'] <= MAX_REVISION:
            raise ValueError('关联角色修订无效')
        for field in ('profile_sha256', 'expression_graph_sha256', 'preview_catalog_id'):
            if type(binding[field]) is not str or not SHA.fullmatch(binding[field]):
                raise ValueError('关联版本摘要无效')
        source = binding['source']
        if source is not None:
            if type(source) is not dict or set(source) != {'key', 'session_id', 'capture_shell_id', 'device_id',
                                                          'magnet_profile_id', 'archive_sha256'}:
                raise ValueError('采样来源字段无效')
            for field in ('key', 'session_id', 'capture_shell_id', 'device_id', 'magnet_profile_id'):
                text(source[field], '采样来源', 256)
            if source['archive_sha256'] is not None and (type(source['archive_sha256']) is not str or not SHA.fullmatch(source['archive_sha256'])):
                raise ValueError('采样来源版本无效')
            identity = (source['device_id'], source['magnet_profile_id'])
            if identity in sources:
                raise ValueError('同一磁档案已有外壳关联')
            sources.add(identity)
    run = value['last_run']
    if run is not None and (type(run) is not dict or run.get('scope') != 'offline_only'
                            or type(run.get('inputs_sha256')) is not str or not SHA.fullmatch(run['inputs_sha256'])):
        raise ValueError('离线演练记录无效')
    return value


class ShellCharacters:
    def __init__(self, path, *, characters, sessions, writer=atomic_save):
        self.path, self.characters, self.sessions = Path(path), characters, sessions
        self.writer, self.lock, self.closed = writer, threading.RLock(), False
        self.owner = StateOwner(self.path)
        try:
            self.doc = dict(schema=SCHEMA, revision=0, shells=[], last_run=None)
            if self.path.exists():
                if self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('外壳角色库超过大小限制')
                self.doc = validate_document(json.loads(self.path.read_bytes(), object_pairs_hook=_unique))
        except Exception:
            self.owner.close()
            raise

    def _open(self):
        if self.closed:
            raise RoleStorageError('外壳服务已关闭，请重新打开')

    def _expect(self, revision):
        self._open()
        if type(revision) is not int or revision != self.doc['revision']:
            raise CharacterConflict('外壳配置已更新，请重新读取后确认')

    def _save(self, doc):
        doc['revision'] += 1
        if doc['revision'] > MAX_REVISION or len(canonical(doc)) + 1 > 2 * 1024 * 1024:
            raise ValueError('外壳角色库已满，请先归档')
        validate_document(doc)
        try:
            self.writer(self.path, doc)
        except OSError as error:
            raise RoleStorageError('外壳配置暂未保存，请保留选择后重试') from error
        self.doc = doc

    def _inputs(self, characters, sources):
        return digest(canonical(dict(shells=self.doc['shells'], profiles=characters['profiles'],
                                     catalog=characters['expression_catalog'], sources=sources)))

    def _state(self, characters, sources):
        from protocol.shell_switch_lab import SCENARIOS as SESSION_SCENARIOS
        profiles = {p['role_id']: p for p in characters['profiles']}
        source_map = {s['key']: s for s in sources}
        rows = []
        for shell in self.doc['shells']:
            binding = shell['binding']
            profile = profiles.get(binding['role_id']) if binding else None
            current_role = bool(profile and binding['profile_revision'] == profile['profile_revision']
                                and binding['profile_sha256'] == digest(canonical(profile))
                                and characters['readiness'][profile['role_id']]['expression_reference'] == 'current')
            reference = source_map.get(binding['source']['key']) if binding and binding['source'] else None
            source_current = bool(reference and binding['source'] == self._source_ref(reference))
            recognition = ('not_registered' if not binding or not binding['source'] else 'source_updated' if not source_current
                           else reference['recognition'])
            rows.append(dict(**deepcopy(shell), role_name=profile['display_name'] if profile else None,
                role_current=current_role, recognition=recognition, sync_status='pending_device',
                next_action='choose_role' if not binding else 'confirm_role_update' if not current_role
                    else 'confirm_source_update' if recognition == 'source_updated' else 'continue_registration'
                    if recognition in ('not_registered', 'collecting', 'needs_review', 'historical_only') else 'ready_for_integration'))
        inputs = self._inputs(characters, sources)
        run = deepcopy(self.doc['last_run'])
        if run:
            run['current'] = run['inputs_sha256'] == inputs
        return deepcopy(dict(schema=SCHEMA, revision=self.doc['revision'], shells=rows,
            characters=[dict(role_id=p['role_id'], display_name=p['display_name'], profile_revision=p['profile_revision'],
                expression_role_id=p['expression_role_id'], current=characters['readiness'][p['role_id']]['expression_reference']=='current')
                for p in characters['profiles']], sources=sources, last_run=run, scenarios=SCENARIOS,
                session_scenarios=SESSION_SCENARIOS,
                device_written=False, hardware_accessed=False, scope='local_configuration'))

    @staticmethod
    def _source_ref(source):
        return {key: source[key] for key in ('key', 'session_id', 'capture_shell_id', 'device_id', 'magnet_profile_id', 'archive_sha256')}

    def state(self):
        with self.sessions.lock, self.characters.lock, self.lock:
            self._open()
            return self._state(self.characters.snapshot(), self.sessions.registration_sources())

    def save_shell(self, shell_id, name, expected_revision):
        if type(shell_id) is not str or not ROLE_ID_RE.fullmatch(shell_id):
            raise ValueError('外壳编号无效')
        text(name, '外壳名称', 40)
        with self.sessions.lock, self.characters.lock, self.lock:
            self._open()
            old = next((s for s in self.doc['shells'] if s['shell_id'] == shell_id), None)
            if old and old['name'] == name:
                if type(expected_revision) is not int or not 0 <= expected_revision <= self.doc['revision']:
                    raise CharacterConflict('外壳修订无效')
                return self.state()
            self._expect(expected_revision)
            if any(s['name'].casefold() == name.casefold() and s['shell_id'] != shell_id for s in self.doc['shells']):
                raise ValueError('这个外壳名称已使用，请换一个名称')
            doc = deepcopy(self.doc)
            if old:
                next(s for s in doc['shells'] if s['shell_id'] == shell_id)['name'] = name
            elif len(doc['shells']) < MAX_SHELLS:
                doc['shells'].append(dict(shell_id=shell_id, name=name, binding=None))
            else:
                raise ValueError('当前最多保存 64 个外壳')
            self._save(doc)
            return self.state()

    def confirm(self, shell_id, role_id, profile_revision, source_key, source_sha256, expected_revision):
        if type(role_id) is not str or type(profile_revision) is not int or profile_revision < 1:
            raise ValueError('请选择已保存的完整角色')
        with self.sessions.lock, self.characters.lock, self.lock:
            self._open()
            characters, sources = self.characters.snapshot(), self.sessions.registration_sources()
            profile = next((p for p in characters['profiles'] if p['role_id'] == role_id), None)
            if (not profile or profile_revision != profile['profile_revision']
                    or characters['readiness'][role_id]['expression_reference'] != 'current'):
                raise CharacterConflict('角色已有新版，请重新读取并选择')
            if source_key is not None and type(source_key) is not str:
                raise ValueError('采样来源编号无效')
            source = next((s for s in sources if s['key'] == source_key), None)
            if source_key is not None and (not source or source['archive_sha256'] != source_sha256):
                raise CharacterConflict('登记档案已更新，请重新选择来源')
            if source_key is None and source_sha256 is not None:
                raise ValueError('未选择采样来源时摘要应为空')
            source_ref = self._source_ref(source) if source else None
            binding = dict(role_id=role_id, profile_revision=profile_revision, profile_sha256=digest(canonical(profile)),
                expression_role_id=profile['expression_role_id'], expression_graph_sha256=profile['expression_graph_sha256'],
                preview_catalog_id=profile['preview_catalog_id'], source=source_ref, confirmation='owner_choice_local')
            old = next((s for s in self.doc['shells'] if s['shell_id'] == shell_id), None)
            if old is None:
                raise ValueError('请先添加外壳')
            if old['binding'] == binding:
                if type(expected_revision) is not int or not 0 <= expected_revision <= self.doc['revision']:
                    raise CharacterConflict('外壳修订无效')
                return self.state()
            self._expect(expected_revision)
            for other in self.doc['shells']:
                previous = other['binding'] and other['binding']['source']
                if other['shell_id'] != shell_id and previous and source_ref and (
                        previous['device_id'], previous['magnet_profile_id']) == (source_ref['device_id'], source_ref['magnet_profile_id']):
                    raise ValueError('这个磁档案已关联另一个外壳，请选择其他来源')
            doc = deepcopy(self.doc)
            next(s for s in doc['shells'] if s['shell_id'] == shell_id)['binding'] = binding
            self._save(doc)
            return self.state()

    def _bindings(self, state):
        return {s['shell_id']: dict(shell_id=s['shell_id'], shell_name=s['name'], role_name=s['role_name'],
            role_id=s['binding']['role_id'], profile_revision=s['binding']['profile_revision'],
            expression_role_id=s['binding']['expression_role_id'], binding_sha256=binding_hash(s['binding']),
            expression_graph_sha256=s['binding']['expression_graph_sha256'],
            ready=s['role_current']) for s in state['shells'] if s['binding']}

    def run_session(self, expected_revision, *, scenario, shell_a, shell_b):
        from protocol.shell_switch_lab import run_session_lab
        with self.sessions.lock, self.characters.lock, self.lock:
            self._expect(expected_revision)
            characters, sources = self.characters.snapshot(), self.sessions.registration_sources()
            state = self._state(characters, sources)
            # Only this private lab substitutes simulated recognition identities.
            result = run_session_lab(self._bindings(state), self.characters.graph, scenario, shell_a, shell_b)
            result.update(scope='offline_only', inputs_sha256=self._inputs(characters, sources))
            doc = deepcopy(self.doc); doc['last_run'] = result
            self._save(doc)
            return self.state()

    def replay_session(self, expected_revision, *, session_id):
        from protocol.shell_runtime_replay import replay_recordings
        from protocol.shell_switch_adapter import project_bindings
        with self.sessions.lock, self.characters.lock, self.lock:
            self._expect(expected_revision)
            characters, sources = self.characters.snapshot(), self.sessions.registration_sources()
            state = self._state(characters,sources)
            capture = self.sessions.runtime_recordings(session_id)
            result = replay_recordings(capture,project_bindings(state),self.characters.graph)
            result.update(scope='offline_only',inputs_sha256=self._inputs(characters,sources))
            doc = deepcopy(self.doc);doc['last_run']=result
            self._save(doc)
            return self.state()

    def run(self, expected_revision, *, scenario=None, shell_a=None, shell_b=None, session_id=None):
        with self.sessions.lock, self.characters.lock, self.lock:
            self._expect(expected_revision)
            characters, sources = self.characters.snapshot(), self.sessions.registration_sources()
            state = self._state(characters, sources)
            if session_id is None:
                result = simulate(self._bindings(state), scenario, shell_a, shell_b)
                result['kind'] = 'scenario'
            else:
                replay = self.sessions.replay_windows(session_id)
                rows = []
                for window in replay['windows']:
                    observation = window['final']
                    profile_id = observation.get('profile_id') if observation['status'] == 'known' else None
                    shell = next((s for s in state['shells'] if s['binding'] and s['binding']['source']
                        and s['binding']['source']['session_id'] == session_id
                        and s['binding']['source']['magnet_profile_id'] == profile_id), None)
                    ready = bool(shell and shell['role_current'] and shell['recognition'] != 'source_updated')
                    rows.append(dict(recording_id=window['id'], outcome=window['outcome'], observation=observation,
                        shell_id=shell['shell_id'] if shell else None, shell_name=shell['name'] if shell else None,
                        role_name=shell['role_name'] if shell else None, role_id=shell['binding']['role_id'] if ready else None,
                        expression_role_id=shell['binding']['expression_role_id'] if ready else None,
                        decision='mapped_window' if ready else 'keep_current'))
                result = dict(kind='log_windows', input_kind='stored_log_reidentification', session_id=session_id,
                    archive_sha256=replay['archive_sha256'], rows=rows, device_commands=0)
            result.update(scope='offline_only', inputs_sha256=self._inputs(characters, sources))
            doc = deepcopy(self.doc); doc['last_run'] = result
            self._save(doc)
            return self.state()

    def close(self):
        with self.lock:
            self.closed = True
            self.owner.close()
