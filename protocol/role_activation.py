"""Persistent expression-role selection, independent of USB and authoring drafts."""
from copy import deepcopy
import hashlib
import contextlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from protocol.studio_resource_package import canonical, load_catalog as authoring_catalog
from protocol.studio_installed_catalog import validate_manifest
from protocol.studio_role_layout import load_requirements, playback
from protocol.studio_usb_session import _unique

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "protocol/deskbot-expression-roles-v1.json"
STATE_PATH = ROOT / "tmp/deskbot-studio-state/active-role.json"
STATE_SCHEMA = "deskbot.role_activation/v1"
MAX_REVISION = 2**53 - 1  # Shared with the browser's safe integer range.
SEMANTICS = ("neutral", "happy", "sad", "embarrassed", "confused", "sleepy")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes(), object_pairs_hook=_unique)


def local_file(base, name):
    value = (base / name).resolve()
    if not value.is_relative_to(base.resolve()) or not value.is_file():
        raise ValueError("角色资源路径无效")
    return value


def load_roles():
    """Load six stable daily bindings from the selected full-face library."""
    registry = read_json(REGISTRY)
    if (registry["schema"] != "deskbot.expression_roles/v1"
            or tuple(registry["semantics"]) != SEMANTICS):
        raise ValueError("角色目录版本不匹配")
    old = registry["official"]
    reference_path = local_file(ROOT, old["reference"])
    reference = validate_manifest(read_json(reference_path))
    assets = authoring_catalog()
    requirements = load_requirements(assets)
    entries = {row["id"]: row for row in reference["entries"] if row["kind"] == "expression"}
    official = {}
    for semantic in SEMANTICS:
        row = entries[semantic]
        asset = assets["assets"][row["resource_id"]]
        expected = playback(assets, semantic, row["resource_id"], requirements)
        if (row["resource_sha256"] != asset["deployed_sha256"] or row["bytes"] != asset["bytes"]
                or any(row[key] != value for key, value in expected.items())
                or row["implicit_action_id"] != assets["semantics"][semantic]["action_id"]):
            raise ValueError("原有角色与资源快照不同")
        official[semantic] = dict(semantic=semantic, label=assets["semantics"][semantic]["label"],
            mode="single", resource_id=row["resource_id"], resource_sha256=row["resource_sha256"],
            bytes=row["bytes"], frames=asset["frame_count"], fps=row["fps"], loop=row["loop"],
            layout_id=row["layout_id"], mirror=row["layout_id"] == "eye_anim",
            implicit_action_id=row["implicit_action_id"])

    new = registry["composable"]
    manifest_path = local_file(ROOT, new["manifest"])
    manifest = read_json(manifest_path)
    if (manifest["schema"] != "deskbot.composable_role_clips/v1" or manifest["role_id"] != new["id"]
            or manifest["fps"] != 20 or manifest["display"] != dict(
                width=360, height=360, shape="circle", layout="full_face", mirror=False)):
        raise ValueError("角色格式或布局不同")
    composable, media = {}, {}
    for semantic, state in registry["semantics"].items():
        expression = manifest["expressions"][state]
        clips = deepcopy(expression["clips"])
        for phase in ("entry", "loop", "exit"):
            clip = clips[phase]
            raw = local_file(manifest_path.parent, clip["file"]).read_bytes()
            if digest(raw) != clip["sha256"] or len(raw) != clip["bytes"]:
                raise ValueError("角色片段内容不同")
            if type(clip["frames"]) is not int or not 1 <= clip["frames"] <= 192:
                raise ValueError("角色片段帧数无效")
        if (clips["entry"]["last_rgba_sha256"] != clips["loop"]["first_rgba_sha256"]
                or clips["loop"]["last_rgba_sha256"] != clips["exit"]["first_rgba_sha256"]):
            raise ValueError("角色片段接点不同")
        atlas_path = local_file(manifest_path.parent, expression["atlas"])
        media_name = f"role-media/{state}.png"
        media[media_name] = atlas_path
        composable[semantic] = dict(semantic=semantic, label=official[semantic]["label"],
            mode="composable", state=state, fps=20, loop=True, layout_id="full_frame", mirror=False,
            implicit_action_id=None, clips=clips, atlas=media_name,
            atlas_sha256=digest(atlas_path.read_bytes()), atlas_format=manifest["atlas"])
    home = {binding["clips"][phase][edge] for binding in composable.values()
            for phase, edge in (("entry", "first_rgba_sha256"), ("exit", "last_rgba_sha256"))}
    if len(home) != 1:
        raise ValueError("角色共享姿态不同")
    catalog = dict(schema="deskbot.runtime_roles/v1", default_role_id=registry["default_role_id"],
        semantics=list(SEMANTICS), roles=[
            dict(id=old["id"], name=old["name"], version=old["version"],
                 resource_stage="reference_e145", expressions=official),
            dict(id=new["id"], name=new["name"], version=new["version"],
                 resource_stage="computer_preview", expressions=composable)],
        sources=dict(registry=digest(REGISTRY.read_bytes()), reference=digest(reference_path.read_bytes()),
                     manifest=digest(manifest_path.read_bytes())))
    catalog["catalog_id"] = digest(canonical(catalog))
    return catalog, media


class RoleConflict(ValueError):
    """The request belongs to an earlier role selection or catalog."""


class RoleStorageError(OSError):
    """The saved selection remains available for inspection and recovery."""


def atomic_save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".role-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RoleActivation:
    def __init__(self, catalog, path=STATE_PATH, *, writer=atomic_save, clock=time.time):
        self.catalog = deepcopy(catalog)
        self.path, self.writer, self.clock = Path(path), writer, clock
        self._lock = threading.RLock()
        self._closed = False
        self.roles = {row["id"]: row for row in self.catalog["roles"]}
        self.error = None
        self._state = dict(schema=STATE_SCHEMA, catalog_id=self.catalog["catalog_id"],
            role_id=self.catalog["default_role_id"], revision=0, source="initial", saved_at_ms=None)
        try:
            if self.path.exists():
                if self.path.stat().st_size > 4096:
                    raise ValueError("保存文件大小无效")
                self._state = self.validate(read_json(self.path))
        except (ValueError, TypeError, KeyError, RecursionError, OSError):
            self._state = None
            self.error = "保存的角色选择需要检查，请保留文件并查看本机记录。"

    def validate(self, value):
        if (type(value) is not dict or set(value) != {"schema", "catalog_id", "role_id", "revision", "source", "saved_at_ms"}
                or value["schema"] != STATE_SCHEMA or value["catalog_id"] != self.catalog["catalog_id"]
                or type(value["role_id"]) is not str or value["role_id"] not in self.roles
                or type(value["revision"]) is not int or not 1 <= value["revision"] <= MAX_REVISION
                or value["source"] != "manual" or type(value["saved_at_ms"]) is not int
                or not 0 <= value["saved_at_ms"] <= MAX_REVISION):
            raise ValueError("保存的角色选择格式无效")
        return deepcopy(value)

    def snapshot(self):
        with self._lock:
            return dict(schema="deskbot.role_runtime/v1", catalog_id=self.catalog["catalog_id"],
                activation=deepcopy(self._state), catalog=deepcopy(self.catalog),
                storage_status="unavailable" if self.error else "saved" if self._state["revision"] else "initial",
                error=self.error)

    def _expect(self, expected_revision, catalog_id):
        if self._closed:
            raise RoleStorageError("角色服务正在重新启动，请刷新工作台。")
        if self.error:
            raise RoleStorageError(self.error)
        if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION:
            raise ValueError("角色修订号无效")
        if type(catalog_id) is not str or catalog_id != self.catalog["catalog_id"]:
            raise RoleConflict("角色目录已更新，请刷新工作台。")
        if expected_revision != self._state["revision"]:
            raise RoleConflict("角色已在其他页面切换，已读取最新选择。")

    def activate(self, role_id, expected_revision, catalog_id, *, source="manual"):
        with self._lock:
            self._expect(expected_revision, catalog_id)
            if type(role_id) is not str or role_id not in self.roles or source != "manual":
                raise ValueError("请选择目录中的角色")
            if self._state["role_id"] == role_id and self._state["revision"]:
                return self.snapshot()
            if self._state["revision"] == MAX_REVISION:
                raise ValueError("角色修订已满，请归档后创建新版本")
            next_state = dict(schema=STATE_SCHEMA, catalog_id=self.catalog["catalog_id"], role_id=role_id,
                revision=self._state["revision"] + 1, source=source, saved_at_ms=int(self.clock() * 1000))
            self.validate(next_state)
            # Commit durable bytes before exposing the new active role to readers.
            try:
                self.writer(self.path, next_state)
            except OSError as error:
                raise RoleStorageError("角色选择暂未保存，请重试。") from error
            self._state = next_state
            return self.snapshot()

    def resolve(self, semantic, expected_revision, catalog_id):
        with self._lock:
            self._expect(expected_revision, catalog_id)
            if type(semantic) is not str or semantic not in SEMANTICS:
                raise ValueError("请选择六种常用表情之一")
            role = self.roles[self._state["role_id"]]
            return dict(schema="deskbot.active_role_expression/v1", catalog_id=self.catalog["catalog_id"],
                activation=deepcopy(self._state), role_name=role["name"], role_version=role["version"],
                expression=deepcopy(role["expressions"][semantic]), scope="computer_preview",
                resource_stage=role["resource_stage"])

    def recover(self, role_id, expected_revision, catalog_id):
        """Explicitly start a new selection, retaining the original saved bytes."""
        with self._lock:
            if self._closed:
                raise RoleStorageError("角色服务正在重新启动，请刷新工作台。")
            if not self.error:
                raise RoleConflict("已有保存的选择，请刷新后切换角色。")
            if (type(expected_revision) is not int or expected_revision != 0
                    or catalog_id != self.catalog["catalog_id"]):
                raise RoleConflict("角色目录已更新，请刷新工作台。")
            if type(role_id) is not str or role_id not in self.roles:
                raise ValueError("请选择目录中的角色")
            try:
                raw = self.path.read_bytes()
                backup = self.path.with_name(self.path.stem + ".previous-" + digest(raw)[:16] + ".json")
                if backup.exists():
                    if backup.read_bytes() != raw:
                        raise OSError("保存记录摘要不同")
                else:
                    with backup.open("xb") as handle:
                        handle.write(raw)
                        handle.flush()
                        os.fsync(handle.fileno())
                value = dict(schema=STATE_SCHEMA, catalog_id=self.catalog["catalog_id"], role_id=role_id,
                    revision=max(1, int(self.clock()*1000)), source="manual", saved_at_ms=int(self.clock()*1000))
                self.validate(value)
                self.writer(self.path, value)
            except OSError as error:
                raise RoleStorageError("角色选择暂未保存，请重试。") from error
            self._state, self.error = value, None
            return self.snapshot()

    def close(self):
        # Wait for the current atomic commit before releasing the process-wide owner.
        # Daemon HTTP handlers already queued behind it cannot write after this point.
        with self._lock:
            self._closed = True


class StateOwner:
    """Hold one OS lock for the service lifetime; released on exit or crash."""
    def __init__(self, path):
        path = Path(path).with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if not self.handle.tell():
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            raise RoleStorageError("角色服务已运行，请使用已打开的工作台。") from error

    def close(self):
        if self.handle.closed:
            return
        with contextlib.suppress(OSError):
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
