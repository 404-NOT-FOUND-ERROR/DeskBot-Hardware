"""E3f-1 App installer; device transport is loaded only for live operations."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from protocol import studio_app_deployment as app
from protocol import studio_e3d2b_deployment as baseline
from protocol import studio_e3f1_deployment as release
from protocol import studio_resource_installation as resources

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "deskbot.studio_shy_installation/v1"
LABEL = re.compile(r"e3f1-app-[0-9]{8}-[a-z0-9-]{1,28}\Z")
RESOURCE_LABEL = re.compile(r"e145-assets-[0-9]{8}-[a-z0-9-]{1,30}\Z")


def folder_for(label: str) -> Path:
    if not isinstance(label, str) or not LABEL.fullmatch(label):
        raise ValueError("shy_run_name_invalid")
    return safe_folder(ROOT / "tmp" / label)


def safe_folder(folder: Path) -> Path:
    folder = resources._safe(Path(folder).absolute(), ".")
    if folder.parent != ROOT / "tmp" or not LABEL.fullmatch(folder.name):
        raise ValueError("shy_run_must_be_child_of_tmp")
    return folder


def _saved_paths(folder):
    safe_folder(folder)
    for name in ("plan.json", "snapshot-32m.bin", "rollback-app-union.bin", "candidate-app.bin",
                 "installation.json", "deployment-state.json", "deployment-state.json.tmp",
                 "logs", "captures", "payloads"):
        resources._safe(folder, name)


class Journal(app.Journal):
    def load(self):
        _saved_paths(self.path.parent)
        return super().load()

    def save(self, phase, **details):
        _saved_paths(self.path.parent)
        return super().save(phase, **details)


def _resource_source(folder):
    folder = resources._safe(Path(folder).absolute(), ".")
    if folder.parent != ROOT / "tmp" or not RESOURCE_LABEL.fullmatch(folder.name):
        raise ValueError("shy_resource_source_path_invalid")
    plan = resources.load(folder)
    state = resources.Journal(folder, plan).load()
    expected = plan.snapshot[:resources.START] + plan.image + plan.snapshot[resources.START + resources.SIZE:]
    if (plan.metadata["mode"] != "live" or resources.plan_baseline(plan) != "E3d-2b"
            or plan.metadata["identity_sha256"] != baseline.IDENTITY_SHA
            or not state or state["phase"] != "installed"
            or state.get("readback_sha256") != app.sha(expected)):
        raise ValueError("installed_live_E1.45_resource_source_required")
    source = dict(run=folder.name, plan_id=plan.id, snapshot_sha256=plan.metadata["snapshot_sha256"],
                  installed_sha256=app.sha(expected))
    return plan, source


def _receipt(plan, mode, source):
    return dict(schema=SCHEMA, edition="E3f-1", mode=mode, plan_id=plan.id,
        rollback_app="E3d-2b", resource_sha256=resources.CANDIDATE_SHA,
        build_report_sha256=release.BUILD_REPORT_SHA, test_report_sha256=release.TEST_REPORT_SHA,
        source=source, hardware_accessed=mode == "live", device_written=False)


def _save(folder, plan, mode, source):
    _saved_paths(folder)
    app.persist(folder, plan)
    app.write_new(resources._safe(folder, "installation.json"), app.canonical(_receipt(plan, mode, source)))


def load_checked(folder, *, recovery=False):
    folder = safe_folder(folder)
    _saved_paths(folder)
    plan = (app.load_recovery if recovery else app.load)(folder)
    (release.check_recovery_plan if recovery else release.check_app_plan)(plan)
    receipt = json.loads(resources._safe(folder, "installation.json").read_bytes(), object_pairs_hook=app._unique)
    mode, source = receipt.get("mode"), receipt.get("source")
    if mode not in ("offline", "live") or not isinstance(source, dict):
        raise ValueError("shy_installation_receipt_invalid")
    if receipt != _receipt(plan, mode, source):
        raise ValueError("shy_installation_receipt_changed")
    if mode == "offline":
        if source != dict(kind="offline_rehearsal", plan_id=release.RESOURCE_PLAN_ID):
            raise ValueError("shy_offline_source_changed")
    elif (set(source) != {"run", "plan_id", "snapshot_sha256", "installed_sha256"}
            or not RESOURCE_LABEL.fullmatch(source.get("run", ""))
            or any(not resources._hash(source.get(key))
                   for key in ("plan_id", "snapshot_sha256", "installed_sha256"))):
        raise ValueError("shy_live_source_changed")
    # Recovery uses only the original bytes in this run, including after the
    # external resource run or candidate build has been moved to an archive.
    if not recovery:
        _, _, candidate = release.inputs()
        release.check_app_plan(plan, candidate=candidate)
        if mode == "live":
            resource_plan, current_source = _resource_source(ROOT / "tmp" / source["run"])
            if source != current_source:
                raise ValueError("shy_resource_source_changed")
            resources.compare_outside(resource_plan, plan.snapshot, allow_runtime_nvs=True)
    return plan, receipt


def _factory():
    from protocol.studio_shy_esptool import ShyDevice
    return ShyDevice


def _summary(plan, mode, result, accessed=False):
    return dict(result=result, edition="E3f-1", mode=mode, plan_id=plan.id,
                offset=app.START, erase_bytes=plan.metadata["erase_bytes"],
                end_exclusive=release.APP_ERASE_END, rollback_app="E3d-2b",
                resources="E1.45", hardware_accessed=accessed, device_written=False)


def prepare(folder, *, resource_folder=None, device_factory=None):
    folder = safe_folder(folder)
    if folder.exists():
        raise FileExistsError("choose_new_shy_run_name")
    _, _, candidate = release.inputs()
    if resource_folder is None:
        resource_plan = release.resources_ready()
        snapshot = release.simulate_resource_install(resource_plan)
        plan = release.make_app_plan(snapshot, candidate, resource_plan.metadata["identity_sha256"])
        folder.mkdir()
        _save(folder, plan, "offline", dict(kind="offline_rehearsal", plan_id=resource_plan.id))
        return _summary(plan, "offline", "prepared_offline")
    resource_plan, source = _resource_source(resource_folder)
    device_factory = device_factory or _factory()
    folder.mkdir()
    original = resource_plan.snapshot[app.START:app.START + baseline.APP_SIZE]
    device = device_factory(folder, baseline.IDENTITY_SHA, original_image=original)
    confirmed = False
    try:
        if device.identity() != baseline.IDENTITY_SHA:
            raise ValueError("shy_device_identity_changed")
        confirmed = True
        snapshot = device.read_full()
        resources.compare_outside(resource_plan, snapshot, allow_runtime_nvs=True)
        plan = release.make_app_plan(snapshot, candidate, baseline.IDENTITY_SHA)
        _save(folder, plan, "live", source)
    except BaseException:
        if confirmed:
            device.boot(False)
        raise
    # Keep the controller in download mode until install or resume-original;
    # running the original App here could update NVS after the fresh backup.
    return _summary(plan, "live", "prepared_live", True)


def _result_path(folder, phase):
    numbers = [int(match[1]) for path in folder.iterdir()
               if (match := re.fullmatch(re.escape(phase) + r"-result-([0-9]+)\.json", path.name))]
    return resources._safe(folder, f"{phase}-result-{max(numbers, default=0) + 1:03d}.json")


def run(phase, folder, *, resource_folder=None, confirm_plan=None, device_factory=None):
    if phase not in {"prepare-offline", "prepare-live", "check", "install", "recover", "resume-original"}:
        raise ValueError("unknown_shy_installation_phase")
    if (phase == "prepare-live") != (resource_folder is not None):
        raise ValueError("resource_source_only_required_for_live_preparation")
    if phase.startswith("prepare-"):
        return prepare(folder, resource_folder=resource_folder, device_factory=device_factory)
    folder = safe_folder(folder)
    plan, receipt = load_checked(folder, recovery=phase in {"recover", "resume-original"})
    if phase == "check":
        return _summary(plan, receipt["mode"], "checked")
    if receipt["mode"] != "live" or confirm_plan != plan.id:
        raise ValueError("matching_live_shy_plan_required")
    journal = Journal(folder, plan)
    state = journal.load()
    if phase == "recover" and (not state or state["phase"] not in app.RECOVERY_PHASES):
        raise ValueError("shy_recovery_state_required")
    if phase == "install" and state is not None:
        raise ValueError("existing_shy_transaction_use_recovery")
    if phase == "resume-original" and state is not None and state["phase"] not in {"prepared", "restored"}:
        raise ValueError("recover_shy_transaction_before_resume")
    # Reserve the result before device access; an existing result stays intact.
    with _result_path(folder, phase).open("xb") as stream:
        device = None
        try:
            device = (device_factory or _factory())(folder, baseline.IDENTITY_SHA, plan=plan,
                                                   write_enabled=phase != "resume-original")
            if phase == "resume-original":
                device.identity()
                device.verify_image(plan.rollback, allow_runtime_nvs=True)
                result = dict(result="original_started", boot=device.boot(False), device_written=False)
            else:
                result = (app.install if phase == "install" else app.recover)(plan, device, journal)
        except BaseException as error:
            current = journal.load()
            try:
                if device is not None and phase == "install" and (current is None or current["phase"] == "prepared"):
                    device.boot(False)
            finally:
                stream.write(app.canonical(dict(result="failed", error_type=type(error).__name__,
                    phase=phase, plan_id=plan.id, state=current)))
                stream.flush()
                os.fsync(stream.fileno())
            raise
        stream.write(app.canonical(result))
        stream.flush()
        os.fsync(stream.fileno())
    return result
