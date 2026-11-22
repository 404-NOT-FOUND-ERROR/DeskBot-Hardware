"""Resource-only COM4 adapter retaining the accepted E3f-4c layout App."""
from pathlib import Path

from protocol import studio_resource_installation as resources
from protocol.studio_resource_esptool import ResourceDevice as BaseDevice
from protocol.studio_resource_e3d2b_esptool import inspect_boot
from protocol.studio_resource_e3f4c_reference import APP_ELF_SHA, load_reference
from protocol.storage_upper_experiment_esptool import EsptoolDevice, _MAC


class ResourceDevice(BaseDevice):
    BASELINE = "E3f-4c"

    def __init__(self, folder, *, reference=None, **kwargs):
        super().__init__(folder, **kwargs)
        self.reference = None
        if self.plan is None:
            self.reference = load_reference()["snapshot"] if reference is None else reference
            resources.validate_snapshot(self.reference, baseline=self.BASELINE)

    def _invoke(self, label, args, *, timeout=180, log_path=None):
        tail = tuple(args)
        if tail and tail[0] == "verify_flash":
            if tail[:-1] != ("verify_flash", "--flash_mode", "keep", "0x0"):
                raise ValueError("complete_resource_reference_verification_required")
            path = resources._safe(Path(tail[-1]).parent, Path(tail[-1]).name)
            if path.parent != self.run_dir / "payloads" or path.stat().st_size != resources.FLASH:
                raise ValueError("complete_resource_reference_verification_required")
            output = EsptoolDevice._invoke(self, label, tail, timeout=timeout, log_path=log_path)
            if (self._identity is None
                    or {m.lower() for m in _MAC.findall(output)} != {self._identity.mac}
                    or output.count("-- verify OK (digest matched)") != 1):
                raise ValueError("resource_reference_digest_unconfirmed")
            return output
        return super()._invoke(label, tail, timeout=timeout, log_path=log_path)

    def read_full(self):
        self._require_plan_identity()
        original = self.plan.snapshot if self.plan is not None else self.reference
        prefix = self._capture("resource-current-prefix", 0, 0x10000)
        raw = prefix + original[0x10000:]
        state = resources.Journal(self.run_dir, self.plan).load() if self.plan else None
        if state is not None:
            # After intent, including an interrupted write or later recovery,
            # read every resource byte. Never assume candidate/rollback content.
            assets = self._capture("resource-assets-readback", resources.START, resources.SIZE)
            raw = raw[:resources.START] + assets + raw[resources.START + resources.SIZE:]
        path = self._new_path("payloads", "resource-reference-32m.bin")
        resources.write_new(path, raw)
        self._invoke("verify-resource-reference", ("verify_flash", "--flash_mode", "keep", "0x0", str(path)))
        self._require_plan_identity()
        resources.write_new(self._new_path("captures", "resource-verification.json"), resources.canonical(dict(
            method="resource_byte_readback_and_full_device_md5" if state else "fresh_prefix_and_full_device_md5",
            prefix_bytes=0x10000, resource_bytes_read=resources.SIZE if state else 0,
            verified_bytes=resources.FLASH, sha256=resources.sha(raw))))
        return raw

    def boot(self):
        log = self._capture_serial("e3f4c-resource-boot", 45)
        return ("USB input ready: /dev/secondary" in log
                and not any(marker in log for marker in (
                    "USB input unavailable", "USB input configuration unavailable", "USB input owner changed"))
                and inspect_boot(log, self._identity.mac, expected_elf=APP_ELF_SHA))
