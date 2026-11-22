"""Host-owned full characters; expression roles and shell bindings keep their identities."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading

from protocol.deskbot_role_profile import ROLE_ID_RE, validate_role_profile
from protocol.role_activation import StateOwner, atomic_save, RoleStorageError, MAX_REVISION
from protocol.studio_resource_package import canonical
from protocol.studio_usb_session import _unique

SCHEMA = 'deskbot.role_profile.v2'
STATE_SCHEMA = 'deskbot.characters/v1'
STATE_PATH = Path(__file__).resolve().parents[1] / 'tmp/deskbot-character-profiles/profiles.json'
FIELDS = {'schema', 'role_id', 'display_name', 'persona', 'voice', 'expression_role_id',
          'expression_graph_sha256', 'preview_catalog_id', 'profile_revision'}
SHA = re.compile(r'^[0-9a-f]{64}$')
MAX_PROFILES = 64


class CharacterConflict(ValueError):
    pass


def text(value, label, limit, *, empty=False, multiline=False):
    if (type(value) is not str or len(value) > limit or (not empty and not value.strip())
            or any(ord(c) < 32 and (not multiline or c not in '\t\n\r') for c in value)):
        raise ValueError(f'{label}须为{"0" if empty else "1"}–{limit}字文本')


def validate(value):
    if type(value) is not dict or set(value) - {'legacy'} != FIELDS:
        raise ValueError('角色字段不完整')
    if value['schema'] != SCHEMA:
        raise ValueError('请选择 v2 角色档案')
    for field in ('role_id', 'expression_role_id'):
        if type(value[field]) is not str or not ROLE_ID_RE.fullmatch(value[field]):
            raise ValueError('角色编号格式无效')
    text(value['display_name'], '名字', 80)
    text(value['persona'], '人格', 2000, empty=True, multiline=True)
    for field in ('expression_graph_sha256', 'preview_catalog_id'):
        if type(value[field]) is not str or not SHA.fullmatch(value[field]):
            raise ValueError('表达版本格式无效')
    revision = value['profile_revision']
    if type(revision) is not int or not 0 <= revision <= MAX_REVISION:
        raise ValueError('角色修订无效')
    voice = value['voice']
    if type(voice) is not dict or set(voice) != {'provider', 'voice_id', 'speaking_style'}:
        raise ValueError('音色配置字段不完整')
    if type(voice['provider']) is not str or voice['provider'] not in ('unconfigured', 'xiaozhi', 'gateway'):
        raise ValueError('请选择音色服务类型')
    text(voice['voice_id'], '音色标识', 96, empty=True)
    text(voice['speaking_style'], '说话风格', 500, empty=True, multiline=True)
    if 'legacy' in value:
        legacy = value['legacy']
        if type(legacy) is not dict or set(legacy) != {'source_sha256', 'profile'}:
            raise ValueError('迁移来源字段不完整')
        validate_role_profile(legacy['profile'])
        if legacy['source_sha256'] != hashlib.sha256(canonical(legacy['profile'])).hexdigest():
            raise ValueError('迁移来源摘要不同')
    return value


class CharacterProfiles:
    def __init__(self, path, *, catalog, graph, writer=atomic_save):
        self.path, self.catalog, self.graph = Path(path), deepcopy(catalog), deepcopy(graph)
        self.writer, self.lock, self.closed = writer, threading.RLock(), False
        self.owner = StateOwner(self.path)
        try:
            self.profiles = []
            if self.path.exists():
                if self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('角色库超过大小限制')
                saved = json.loads(self.path.read_bytes(), object_pairs_hook=_unique)
                if (type(saved) is not dict or set(saved) != {'schema', 'profiles'}
                        or saved['schema'] != STATE_SCHEMA or type(saved['profiles']) is not list
                        or len(saved['profiles']) > MAX_PROFILES):
                    raise ValueError('角色库格式无效')
                ids = set()
                for row in saved['profiles']:
                    validate(row)
                    if row['profile_revision'] < 1 or row['role_id'] in ids:
                        raise ValueError('角色库编号或修订无效')
                    ids.add(row['role_id'])
                self.profiles = saved['profiles']
        except Exception:
            self.owner.close()
            raise

    def _reference(self, profile):
        return (profile['expression_role_id'] in self.graph['roles']
                and any(r['id'] == profile['expression_role_id'] for r in self.catalog['roles'])
                and profile['expression_graph_sha256'] == self.graph['sha256']
                and profile['preview_catalog_id'] == self.catalog['catalog_id'])

    def snapshot(self):
        with self.lock:
            return deepcopy(dict(schema=STATE_SCHEMA, profiles=self.profiles,
                expression_catalog=dict(graph_sha256=self.graph['sha256'], catalog_id=self.catalog['catalog_id'],
                    roles=[dict(id=r['id'], name=r['name']) for r in self.catalog['roles'] if r['id'] in self.graph['roles']]),
                readiness={p['role_id']: dict(profile_saved=True, persona_configured=bool(p['persona'].strip()),
                    voice_configured=p['voice']['provider'] != 'unconfigured' and bool(p['voice']['voice_id'].strip()),
                    expression_reference='current' if self._reference(p) else 'stale',
                    shell_binding='not_configured', voice_service='pending') for p in self.profiles}))

    def save(self, profile, expected_revision):
        value = deepcopy(validate(profile))
        if type(expected_revision) is not int or not 0 <= expected_revision < MAX_REVISION:
            raise ValueError('预期修订无效')
        with self.lock:
            if self.closed:
                raise RoleStorageError('角色服务已关闭，请重新打开')
            old = next((p for p in self.profiles if p['role_id'] == value['role_id']), None)
            if (old['profile_revision'] if old else 0) != expected_revision:
                raise CharacterConflict('角色已在另一页面保存，请载入新版或另存角色')
            if value['profile_revision'] != expected_revision:
                raise ValueError('表单修订与预期修订不同')
            if not self._reference(value):
                raise CharacterConflict('表情版本已变化，请重新关联当前版本')
            if old is None and len(self.profiles) >= MAX_PROFILES:
                raise ValueError('当前最多保存 64 个角色')
            # Imported source is immutable; editing a character never rewrites its migration evidence.
            if old and old.get('legacy') != value.get('legacy'):
                raise ValueError('迁移来源保留原值')
            value['profile_revision'] += 1
            next_profiles = [value if p['role_id'] == value['role_id'] else p for p in self.profiles]
            if old is None:
                next_profiles.append(value)
            document = dict(schema=STATE_SCHEMA, profiles=next_profiles)
            if len(canonical(document)) + 1 > 2 * 1024 * 1024:
                raise ValueError('角色库已满，请导出档案后整理')
            try:
                self.writer(self.path, document)
            except OSError as error:
                raise RoleStorageError('角色暂未保存，草稿可保留后重试') from error
            self.profiles = next_profiles
            return dict(profile=deepcopy(value), state=self.snapshot())

    def migrate(self, legacy_profile, expression_role_id):
        validate_role_profile(legacy_profile)
        source_sha = hashlib.sha256(canonical(legacy_profile)).hexdigest()
        # One source and expression version identify one import, so retrying a lost reply creates no duplicate.
        identity = dict(source=source_sha, expression_role_id=expression_role_id,
                        graph=self.graph['sha256'], catalog=self.catalog['catalog_id'])
        role_id = 'character-' + hashlib.sha256(canonical(identity)).hexdigest()[:32]
        profile = dict(schema=SCHEMA, role_id=role_id,
            display_name=legacy_profile['display_name'], persona='',
            voice=dict(provider='unconfigured', voice_id=legacy_profile['voice_id'], speaking_style=''),
            expression_role_id=expression_role_id, expression_graph_sha256=self.graph['sha256'],
            preview_catalog_id=self.catalog['catalog_id'], profile_revision=0,
            legacy=dict(source_sha256=source_sha,
                        profile=deepcopy(legacy_profile)))
        validate(profile)
        with self.lock:
            if self.closed:
                raise RoleStorageError('角色服务已关闭，请重新打开')
            old = next((p for p in self.profiles if p['role_id'] == role_id), None)
            if old:
                if old.get('legacy') != profile['legacy']:
                    raise CharacterConflict('导入编号已有其他档案，请核对角色列表')
                return dict(profile=deepcopy(old), state=self.snapshot())
            return self.save(profile, 0)

    def close(self):
        with self.lock:
            self.closed = True
            self.owner.close()
