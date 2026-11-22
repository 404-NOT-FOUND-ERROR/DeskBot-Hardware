"""E3f-1 COM4 transport using the existing App read/write and boot checks."""
import importlib.util
from pathlib import Path

from protocol import studio_app_deployment as app
from protocol import studio_e3d2b_deployment as baseline
from protocol import studio_e3f1_deployment as release
from protocol import studio_shy_installer as installer
from protocol.storage_upper_experiment_esptool import _MAC
from protocol.storage_upper_migration_esptool import ASSET_ERRORS

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shy_existing_app_transport", ROOT / "scripts/deploy-deskbot-studio-e3c.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class ShyDevice(entry.Device):
    simulation_only = False

    def __init__(self, folder, identity, *, plan=None, write_enabled=False, original_image=None, runner=None):
        installer.safe_folder(folder)
        installer._saved_paths(folder)
        if identity != baseline.IDENTITY_SHA or type(write_enabled) is not bool:
            raise ValueError("shy_transport_identity_or_mode_invalid")
        if plan is not None:
            release.check_recovery_plan(plan)
            original_image = plan.snapshot[app.START:app.START + baseline.APP_SIZE]
        if (not isinstance(original_image, bytes) or len(original_image) != baseline.APP_SIZE
                or app.sha(original_image) != baseline.APP_SHA
                or write_enabled and plan is None):
            raise ValueError("shy_transport_saved_App_required")
        super().__init__(folder, identity, plan)
        self._write_armed, self.original_image = write_enabled, original_image
        if runner is not None:
            self.runner = runner

    def _invoke(self, label, args, **kwargs):
        tail = tuple(args)
        if not tail or tail[0] not in {"flash_id", "get_security_info", "read_flash", "verify_flash", "write_flash"}:
            raise ValueError("shy_transport_command_invalid")
        if tail[0] == "write_flash":
            if not self._write_armed:
                raise PermissionError("shy_App_writes_disabled")
            expected = ("write_flash", "--verify", "--flash_mode", "keep", "--flash_size", "keep", "--flash_freq", "keep", hex(app.START))
            if tail[:-1] != expected:
                raise ValueError("shy_transport_App_range_changed")
        installer._saved_paths(self.run_dir)
        output = super()._invoke(label, tail, **kwargs)
        if tail[0] in {"read_flash", "verify_flash", "write_flash"} and (
                self._identity is None or {mac.lower() for mac in _MAC.findall(output)} != {self._identity.mac}):
            raise ValueError("shy_transfer_identity_changed")
        return output

    def verify_original(self):
        # NVS is captured afresh at prepare-live. Installation requires those
        # exact bytes; later recovery preserves the current runtime NVS.
        self.verify_image(self.deployment_plan.rollback, allow_runtime_nvs=False)
        return True

    def write_app(self, raw):
        if not self._write_armed:
            raise PermissionError("shy_App_writes_disabled")
        installer._saved_paths(self.run_dir)
        return super().write_app(raw)

    def boot(self, candidate):
        image = self.deployment_plan.image if candidate else self.original_image
        log = self._serial("shy-boot" if candidate else "restored-boot", 45)
        if (log.count("Calling app_main()") != 1 or "USB input ready: /dev/secondary" not in log
                or any(marker in log for marker in ASSET_ERRORS + ("USB input unavailable",
                    "USB input configuration unavailable", "USB input owner changed"))):
            raise ValueError("shy_App_boot_incomplete")
        return entry.parent.inspect_boot(log, image, self._identity.mac, identity_required=True)
