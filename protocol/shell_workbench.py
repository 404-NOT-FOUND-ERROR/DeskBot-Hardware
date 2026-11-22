"""Persistent capture tasks and shell-to-expression-role rehearsal projects."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import uuid

from protocol.magnetic_recognition import RecognitionConfig
from protocol.role_activation import atomic_save, StateOwner
from protocol.shell_profile_archive import (
    build_archive, canonical, digest, restore_archive, save_archive, validate_context,
    validate_archive,
)
from protocol.shell_repeatability import (
    SUITE_SCHEMA, MAX_LOG_BYTES, MAX_TOTAL_BYTES, MAX_RECORDINGS, MAX_TOTAL_SAMPLES,
    TARGET_MOUNTS, _identifier, _read_samples, _time, _load_suite, evaluate_suite,
)
from protocol.shell_role_assignment import activation_preview, resolve_assignment
from protocol.shell_capture_readiness import build_field_guide, empty_setup, validate_setup
from protocol.studio_usb_session import _unique

SCHEMA = 'deskbot.shell_workbench/v1'
MAX_SESSIONS = 32
MAX_DOCUMENT_BYTES = 1024 * 1024


class SessionConflict(ValueError):
    pass


def text(value, label, limit=80):
    if (not isinstance(value, str) or not 1 <= len(value) <= limit
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ValueError(label + '应为完整的可显示文字')
    return value


def identifier(value):
    try:
        return _identifier(value)
    except ValueError:
        raise ValueError('项目或采样编号无效') from None


class SessionStore:
    def __init__(self, root, *, project_root, graph, writer=atomic_save):
        self.root = Path(root).resolve()
        self.project_root = Path(project_root).resolve(strict=True)
        if not self.root.is_relative_to(self.project_root):
            raise ValueError('采集项目应保存在本项目目录')
        self.root.mkdir(parents=True, exist_ok=True)
        self.graph = deepcopy(graph)
        if (set(graph) != {'sha256', 'roles', 'names', 'source'}
                or not isinstance(graph['sha256'], str) or len(graph['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in graph['sha256'])
                or not graph['roles'] or len(graph['roles']) > 4
                or len(set(graph['roles'])) != len(graph['roles'])
                or any(r not in graph['names'] for r in graph['roles'])):
            raise ValueError('角色图格式不完整')
        self.writer = writer
        self.lock = threading.RLock()
        self.owner = StateOwner(self.root / 'owner.json')
        self.closed = False

    def close(self):
        with self.lock:
            self.closed = True
            self.owner.close()

    def _folder(self, ident):
        identifier(ident)
        path = (self.root / ident).resolve()
        if path.parent != self.root:
            raise ValueError('采集项目路径无效')
        return path

    def _load(self, ident):
        if self.closed:
            raise ValueError('工作台正在重新启动，请刷新')
        path = self._folder(ident) / 'session.json'
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError('项目记录过大，请归档后重新建立')
        value = json.loads(path.read_bytes(), object_pairs_hook=_unique)
        if (not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('id') != ident
                or type(value.get('revision')) is not int or value['revision'] < 0):
            raise ValueError('采集项目格式无效')
        return value

    def _expect(self, ident, revision):
        doc = self._load(ident)
        if type(revision) is not int or revision != doc['revision']:
            raise SessionConflict('项目已由其他页面更新，请重新读取进度')
        return doc

    def _save(self, doc):
        doc['revision'] += 1
        if len(canonical(doc)) > MAX_DOCUMENT_BYTES:
            raise ValueError('项目记录过大，请归档后重新建立')
        self.writer(self._folder(doc['id']) / 'session.json', doc)

    def state(self, ident=None):
        with self.lock:
            if self.closed:
                raise ValueError('工作台正在重新启动，请刷新')
            summaries = []
            for path in sorted(self.root.glob('*/session.json')):
                doc = self._load(path.parent.name)
                summaries.append({k: doc[k] for k in ('id', 'name', 'revision', 'source', 'created_at')})
            if len(summaries) > MAX_SESSIONS:
                raise ValueError('项目数量已满，请归档旧项目')
            summaries.sort(key=lambda row: (row['created_at'], row['id']))
            selected = self._load(ident) if ident else None
            return dict(schema=SCHEMA, sessions=summaries, graph=deepcopy(self.graph),
                        session=selected,
                        field_guide=build_field_guide(selected, self.graph) if selected else None,
                        device_written=False, firmware_changed=False)

    def registration_sources(self):
        """Compact product references; detailed task and sensor data stay in the capture tool."""
        with self.lock:
            rows = []
            for summary in self.state()['sessions']:
                doc = self._load(summary['id'])
                assessment = doc['assessment']
                archive = validate_archive(assessment['archive']) if assessment else None
                passed = assessment and assessment['report'].get('coverage_complete') is True and assessment['report'].get('all_evaluated_mounts_matched') is True
                status = ('historical_only' if doc['source'] == 'historical_rehearsal' else
                          'collecting' if not assessment else 'validated_offline' if passed else 'needs_review')
                for shell in doc['shells']:
                    rows.append(dict(key=doc['id'] + '/' + shell['shell_id'], session_id=doc['id'],
                        capture_shell_id=shell['shell_id'], name=shell['name'], project_name=doc['name'],
                        device_id=doc['device_id'], magnet_profile_id=shell['magnet_profile_id'],
                        archive_sha256=archive['payload_sha256'] if archive else None, recognition=status))
            return rows

    def replay_windows(self, ident):
        """Read and reidentify existing logs without adding projects or recording new samples."""
        with self.lock:
            doc = self._load(ident)
            if not doc['assessment']:
                raise ValueError('先在采样工具中评估已有日志')
            archive = validate_archive(doc['assessment']['archive'])
            suite_path = (self.project_root / doc['assessment']['folder'] / 'suite.json').resolve()
            if not suite_path.is_relative_to(self._folder(ident)) or json.loads(suite_path.read_bytes()) != self._suite(doc):
                raise ValueError('项目日志与评估版本不同，请重新评估')
            restored = restore_archive(archive, doc['context'])
            report = evaluate_suite(suite_path, self.project_root, saved_profiles=restored.profiles)
            return dict(archive_sha256=archive['payload_sha256'], windows=report['windows'])

    def runtime_recordings(self, ident):
        """Validated original samples for independent segment replay; never writes the project."""
        with self.lock:
            doc = self._load(ident)
            if not doc['assessment']:
                raise ValueError('先在采样工具中评估已有日志')
            archive = validate_archive(doc['assessment']['archive'])
            suite = (self.project_root / doc['assessment']['folder'] / 'suite.json').resolve()
            if not suite.is_relative_to(self._folder(ident)) or json.loads(suite.read_bytes()) != self._suite(doc):
                raise ValueError('项目日志与评估版本不同，请重新评估')
            _, _, recordings, _, _ = _load_suite(suite,self.project_root)
            return dict(session_id=ident,device_id=doc['device_id'],source=doc['source'],context=doc['context'],
                        archive=archive,recordings=[r for r in recordings if r['use'] != 'train'])

    @staticmethod
    def _tasks(shells):
        tasks = []
        for shell in shells:
            tasks.append(dict(id='train-' + shell['shell_id'], use='train',
                              shell_id=shell['shell_id'], profile_id=shell['magnet_profile_id'],
                              scenario='registration', number=1, recording=None))
            for number in range(1, TARGET_MOUNTS + 1):
                tasks.append(dict(id=f"eval-{shell['shell_id']}-{number}", use='evaluate',
                                  shell_id=shell['shell_id'], profile_id=shell['magnet_profile_id'],
                                  scenario='remount', number=number, recording=None))
        for scenario in ('removed', 'unknown_layout'):
            for number in range(1, TARGET_MOUNTS + 1):
                tasks.append(dict(id=f'{scenario}-{number}', use='evaluate', shell_id=None,
                                  profile_id=None, scenario=scenario, number=number, recording=None))
        return tasks

    def create(self, *, name, device_id, context, shell_names):
        with self.lock:
            if len(self.state()['sessions']) >= MAX_SESSIONS:
                raise ValueError('项目数量已满，请归档旧项目')
            text(name, '项目名称')
            text(device_id, '设备编号', 128)
            context = validate_context(context)
            if (not isinstance(shell_names, list) or not 1 <= len(shell_names) <= 7
                    or any(not isinstance(n, str) for n in shell_names)
                    or len(set(shell_names)) != len(shell_names)):
                raise ValueError('填写 1–7 个不同的外壳名称')
            shells = []
            for name_value in shell_names:
                text(name_value, '外壳名称', 40)
                token = uuid.uuid4().hex[:16]
                shells.append(dict(shell_id='shell-' + token, name=name_value,
                                   magnet_profile_id='magnet-' + token))
            ident = 'capture-' + uuid.uuid4().hex
            folder = self._folder(ident)
            folder.mkdir(exist_ok=False)
            doc = dict(schema=SCHEMA, id=ident, name=name, device_id=device_id,
                       context=context, source='field_project', revision=0,
                       created_at=datetime.now(timezone.utc).isoformat(), shells=shells,
                       tasks=self._tasks(shells), assignments=[], assessment=None,
                       rehearsal=None, manual_acceptance='pending', field_setup=empty_setup())
            self.writer(folder / 'session.json', doc)
            return self.state(ident)

    def prepare(self, ident, revision, *, layout_fixed, frame_confirmed, unknown_reference):
        with self.lock:
            doc = self._expect(ident, revision)
            if doc['source'] != 'field_project':
                raise ValueError('请在正式项目中保存采样准备项')
            setup = dict(schema='deskbot.shell_field_setup/v1', layout_fixed=layout_fixed,
                         frame_confirmed=frame_confirmed, unknown_reference=unknown_reference)
            setup = validate_setup(setup)
            if setup['unknown_reference'].casefold() in {s['name'].casefold() for s in doc['shells']}:
                raise ValueError('未登记对照请使用与已登记外壳不同的名称')
            previous = validate_setup(doc.get('field_setup', empty_setup()))
            # Keep the physical reference attached to its first recorded data.
            # Renaming it must not relabel existing evidence as a new layout.
            if (setup['unknown_reference'] != previous['unknown_reference']
                    and any(t['scenario'] == 'unknown_layout' and t['recording']
                            for t in doc['tasks'])):
                raise ValueError('已有未知布局采样，对照名称已固定；测试新对照请新建项目')
            doc['field_setup'] = setup
            self._save(doc)
            return self.state(ident)

    def _suite(self, doc):
        rows = []
        for task in doc['tasks']:
            if task['recording'] is None:
                continue
            rec = task['recording']
            row = {k: rec[k] for k in ('capture_id', 'mount_id', 'log', 'sha256')}
            row.update(id=task['id'], use=task['use'])
            if task['use'] == 'train':
                row['profile_id'] = task['profile_id']
            else:
                row.update(expected_profile_id=task['profile_id'], scenario=task['scenario'])
            rows.append(row)
        return dict(schema=SUITE_SCHEMA, title=doc['name'], config={}, recordings=rows)

    def import_window(self, ident, revision, *, task_id, capture_id, mount_id, log_text):
        with self.lock:
            doc = self._expect(ident, revision)
            if doc['source'] != 'field_project':
                raise ValueError('历史演练保留原始记录，请新建采集项目')
            task = next((r for r in doc['tasks'] if r['id'] == task_id), None)
            if task is None or task['recording'] is not None:
                raise ValueError('请选择尚未采集的任务')
            identifier(capture_id)
            identifier(mount_id)
            if not isinstance(log_text, str):
                raise ValueError('请导入带时间的本体原日志')
            raw = log_text.encode('utf-8')
            if not 0 < len(raw) <= MAX_LOG_BYTES:
                raise ValueError('每份原日志应小于 8 MiB')
            existing = [r['recording'] for r in doc['tasks'] if r['recording']]
            if (len(existing) >= MAX_RECORDINGS
                    or sum(r['bytes'] for r in existing) + len(raw) > MAX_TOTAL_BYTES):
                raise ValueError('本项目已到采集容量上限')
            samples = _read_samples(raw)
            config = RecognitionConfig()
            start, end = _time(samples[0].host_received_at), _time(samples[-1].host_received_at)
            if (end - start).total_seconds() < 10 or len(samples) < config.window_size:
                raise ValueError('静止窗口至少需要 10 秒与 8 条磁场样本')
            if samples[0].kind != doc['context']['sensor_kind']:
                raise ValueError('日志传感器与本项目安装基准不同')
            if any((_time(b.host_received_at) - _time(a.host_received_at)).total_seconds() > config.max_gap_s
                   for a, b in zip(samples, samples[1:])):
                raise ValueError('采样有间断，请重新采集这一段')
            if sum(r['sample_count'] for r in existing) + len(samples) > MAX_TOTAL_SAMPLES:
                raise ValueError('本项目磁场样本数已满')
            keys = [[s.kind, s.host_received_at, s.x, s.y, s.z] for s in samples]
            samples_sha = digest(canonical(keys))
            for rec in existing:
                if rec['mount_id'] == mount_id:
                    raise ValueError('每个任务使用独立拆装编号；重复录制沿用原编号另行归档')
                if rec['sha256'] == digest(raw) or rec['samples_sha256'] == samples_sha:
                    raise ValueError('这份样本已用于其他任务')
                # Reject intersections across sessions too: one sample cannot
                # count as a second independent physical mounting.
                other_start, other_end = _time(rec['start']), _time(rec['end'])
                if max(start, other_start) <= min(end, other_end):
                    raise ValueError('采样时间与已有任务重叠')
            if task['use'] == 'train':
                from protocol.magnetic_recognition import MagneticRecognizer
                recognizer = MagneticRecognizer(config)
                for sample in samples:
                    recognizer.feed(sample)
                recognizer.register(task['profile_id'], profile_id=task['profile_id'],
                                    as_of=samples[-1].host_received_at)
            folder = self._folder(ident) / 'logs'
            folder.mkdir(exist_ok=True)
            log = folder / (uuid.uuid4().hex + '.log')
            with log.open('xb') as output:
                output.write(raw)
            task['recording'] = dict(capture_id=capture_id, mount_id=mount_id,
                log=log.relative_to(self.project_root).as_posix(), sha256=digest(raw),
                samples_sha256=samples_sha, bytes=len(raw), sample_count=len(samples),
                start=start.isoformat(), end=end.isoformat())
            # New evidence invalidates the cached assessment. Bindings keep
            # their archive version and are rechecked after evaluating again.
            doc['assessment'] = doc['rehearsal'] = None
            self._save(doc)
            return self.state(ident)

    def evaluate(self, ident, revision):
        with self.lock:
            doc = self._expect(ident, revision)
            if any(t['recording'] is None for t in doc['tasks'] if t['use'] == 'train'):
                raise ValueError('先完成每个外壳的一次登记采样')
            suite = self._suite(doc)
            folder = self._folder(ident) / ('assessment-' + digest(canonical(suite)))
            folder.mkdir(exist_ok=True)
            suite_path = folder / 'suite.json'
            self.writer(suite_path, suite)
            report = evaluate_suite(suite_path, self.project_root)
            archive = build_archive(report, doc['context'])
            archive_path = folder / 'profiles.json'
            if archive_path.exists():
                if validate_archive(json.loads(archive_path.read_bytes())) != archive:
                    raise ValueError('本次评估与已保存档案不同，请核对记录')
            else:
                save_archive(archive_path, archive)
            self.writer(folder / 'report.json', report)
            doc['assessment'] = dict(report=report, archive=archive,
                folder=folder.relative_to(self.project_root).as_posix())
            doc['rehearsal'] = None
            self._save(doc)
            return self.state(ident)

    def bind(self, ident, revision, *, shell_id, role_id):
        with self.lock:
            doc = self._expect(ident, revision)
            shell = next((s for s in doc['shells'] if s['shell_id'] == shell_id), None)
            if shell is None or role_id is not None and role_id not in self.graph['roles']:
                raise ValueError('请选择本项目外壳和角色包中的角色')
            if not doc['assessment']:
                raise ValueError('先评估采样并保存登记档案')
            archive = validate_archive(doc['assessment']['archive'])
            doc['assignments'] = [a for a in doc['assignments'] if a['shell_id'] != shell_id]
            if role_id is not None:
                doc['assignments'].append(dict(device_id=doc['device_id'], shell_id=shell_id,
                    magnet_profile_id=shell['magnet_profile_id'], role_id=role_id,
                    archive_sha256=archive['payload_sha256'], graph_sha256=self.graph['sha256'],
                    binding_state='draft', scope='installed_expression_role'))
            doc['rehearsal'] = None
            self._save(doc)
            return self.state(ident)

    def rehearse(self, ident, revision, *, control=None):
        with self.lock:
            doc = self._expect(ident, revision)
            if not doc['assessment']:
                raise ValueError('先评估采样并保存登记档案')
            archive = validate_archive(doc['assessment']['archive'])
            suite_path = self._folder(ident) / ('replay-' + uuid.uuid4().hex + '.json')
            self.writer(suite_path, self._suite(doc))
            restored = restore_archive(archive, doc['context'])
            report = evaluate_suite(suite_path, self.project_root, saved_profiles=restored.profiles)
            rows = []
            for window in report['windows']:
                resolution = resolve_assignment(archive, doc['assignments'], window['final'], self.graph)
                rows.append(dict(recording_id=window['id'], scenario=window['scenario'],
                    outcome=window['outcome'], observation=window['final'], resolution=resolution,
                    interface=activation_preview(resolution, control or {}, expected_device_id=doc['device_id'])))
            doc['rehearsal'] = dict(scope='offline_rehearsal', rows=rows, device_activated=False,
                                   initial_status=restored.last_result.status)
            self._save(doc)
            return self.state(ident)

    def example(self):
        """Copy the actual 2026-11-07 suite; keep physical labels unchanged."""
        source_path = self.project_root / 'protocol/shell-repeatability-s1a-history.example.json'
        source = json.loads(source_path.read_bytes(), object_pairs_hook=_unique)
        state = self.create(name='历史滑块 · 流程演练', device_id='history:20261107',
            context=dict(sensor_id='history-bmm150-20261107', frame_id='repair-fixed-base-20261107',
                         sensor_kind=1, units='transport_counts'), shell_names=['上移位置', '下移位置'])
        with self.lock:
            doc = state['session']
            doc['source'] = 'historical_rehearsal'
            for shell, row in zip(doc['shells'], [r for r in source['recordings'] if r['use'] == 'train']):
                shell['magnet_profile_id'] = row['profile_id']
            doc['tasks'] = self._tasks(doc['shells'])
            for row in source['recordings']:
                if row['use'] == 'observe':
                    continue
                profile = row.get('profile_id', row.get('expected_profile_id'))
                task = next(t for t in doc['tasks'] if t['use'] == row['use'] and t['profile_id'] == profile
                    and (row['use'] == 'train' or t['scenario'] == row['scenario']) and t['recording'] is None)
                raw = (self.project_root / row['log']).read_bytes()
                if digest(raw) != row['sha256']:
                    raise ValueError('历史记录摘要已改变')
                samples = _read_samples(raw)
                task['recording'] = {k: row[k] for k in ('log', 'sha256', 'capture_id', 'mount_id')}
                task['recording'].update(bytes=len(raw), sample_count=len(samples),
                    samples_sha256=digest(canonical([[s.kind, s.host_received_at, s.x, s.y, s.z] for s in samples])),
                    start=samples[0].host_received_at, end=samples[-1].host_received_at)
            self._save(doc)
            return self.evaluate(doc['id'], doc['revision'])
