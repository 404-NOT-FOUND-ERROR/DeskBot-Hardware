"""Bounded COM4 transport for the resource transaction; import has no I/O."""
from pathlib import Path
import re

from protocol import studio_resource_installation as resources
from protocol.storage_upper_experiment_esptool import (
    CAPTURE_SCRIPT, CAPTURE_SCRIPT_SHA256, ESPYTHON, EsptoolDevice, Runner,
    _MAC, _NORMAL_BOOT_MARKERS, _PANIC_MARKERS,
)
from protocol.storage_upper_migration_esptool import ASSET_ERRORS

ROOT = Path(__file__).resolve().parents[1]


class ResourceDevice(EsptoolDevice):
    simulation_only = False
    BASELINE = "E3c-3b"

    def __init__(self, folder, *, plan=None, expected_identity=None, write_enabled=False, runner=None):
        # This adapter has its own resource plan. The App/test-page adapter's
        # ranges and recovery intent are never reused to authorize a write.
        folder = resources._safe(Path(folder), ".").resolve()
        if (folder.parent != (ROOT / "tmp").resolve() or not folder.is_dir()
                or not ESPYTHON.is_file() or resources.sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256):
            raise ValueError("resource_adapter_inputs_changed")
        if plan is not None:
            plan.validate()
            if resources.plan_baseline(plan) != self.BASELINE:
                raise ValueError("resource_adapter_baseline_changed")
        identity = plan.metadata["identity_sha256"] if plan else expected_identity
        if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
            raise ValueError("resource_identity_required")
        if type(write_enabled) is not bool or write_enabled and (plan is None or plan.metadata["mode"] != "live"):
            raise ValueError("live_resource_plan_required")
        self.port, self.run_dir, self.plan = "COM4", folder, plan
        self.expected_identity, self.python, self.runner = identity, ESPYTHON, runner or Runner()
        self._identity, self._reserved_logs, self._write_armed = None, set(), write_enabled
        self._sequence = self._last_evidence_sequence()

    def _require_plan_identity(self):
        value = self._fresh_identity()
        if value.digest != self.expected_identity:
            raise ValueError("resource_device_identity_changed")
        return value

    def identity(self):
        return self._require_plan_identity().digest

    def _invoke(self, label, args, *, timeout=180, log_path=None):
        tail = tuple(args)
        if tail[0] not in {"flash_id", "get_security_info", "read_flash", "write_flash"}:
            raise ValueError("resource_command_not_supported")
        if tail[0] == "write_flash" and not self._write_armed:
            raise PermissionError("resource_writes_disabled")
        output = super()._invoke(label, tail, timeout=timeout, log_path=log_path)
        if tail[0] in {"read_flash", "write_flash"} and (
                self._identity is None or {m.lower() for m in _MAC.findall(output)} != {self._identity.mac}):
            raise ValueError("resource_transfer_identity_changed")
        return output

    def read_full(self):
        self._require_plan_identity()
        raw = self._capture("resource-full-readback", 0, resources.FLASH)
        self._require_plan_identity()
        return raw

    def write_assets(self, raw):
        self._write_authorized()
        if type(raw) is not bytes or len(raw) != resources.SIZE:
            raise ValueError("exact_8m_resource_payload_required")
        saved = resources.load(self.run_dir, recovery=True)
        journal = resources.Journal(self.run_dir, saved)
        state = journal.load()
        if saved.id != self.plan.id or not state:
            raise ValueError("resource_write_intent_required")
        if state["phase"] == "write_intent":
            payload = resources.load(self.run_dir).image
        elif state["phase"] == "restore_intent":
            payload = saved.rollback
        else:
            raise ValueError("resource_write_intent_required")
        if raw != payload:
            raise ValueError("resource_payload_changed")
        path = self._new_path("payloads", "assets-8m.bin")
        resources.write_new(path, raw)
        log = self._reserve_log("write-resources")
        self._require_plan_identity()
        if resources.load(self.run_dir, recovery=True).id != saved.id or journal.load() != state:
            raise ValueError("resource_intent_changed")
        output = self._invoke("write-resources", ("write_flash", "--verify", "--no-progress",
            "--flash_mode", "keep", "--flash_freq", "keep", "--flash_size", "keep",
            hex(resources.START), str(path)), log_path=log)
        if "Hash of data verified" not in output:
            raise ValueError("resource_transport_verification_missing")

    def boot(self):
        log = self._capture_serial("resource-boot", 45)
        return (all(marker in log for marker in _NORMAL_BOOT_MARKERS)
                and "e0941ae3c" in log and log.count("Calling app_main()") == 1
                and not any(marker in log for marker in _PANIC_MARKERS + ASSET_ERRORS))

    def read_flash(self, *_):
        raise ValueError("use_complete_resource_snapshot")

    def write_flash(self, *_):
        raise ValueError("use_exact_resource_transaction")

    def snapshot(self, *_):
        raise ValueError("use_complete_resource_snapshot")

    def capture_probe(self, *_):
        raise ValueError("resource_installation_uses_normal_boot")

    def boot_original(self):
        return self.boot()
