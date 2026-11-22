"""Resource-only transport that keeps the accepted E3d-2b App identity."""
import hashlib
import re

from protocol.device_link import decode_hello
from protocol.studio_e3d2b_deployment import APP_ELF_SHA
from protocol.studio_resource_esptool import ResourceDevice as BaseDevice
from protocol.storage_upper_experiment_esptool import _NORMAL_BOOT_MARKERS, _PANIC_MARKERS
from protocol.storage_upper_migration_esptool import ASSET_ERRORS


def inspect_boot(log, mac, *, expected_elf=APP_ELF_SHA):
    """Check three periodic identity frames from one normal, complete boot."""
    if (not all(marker in log for marker in _NORMAL_BOOT_MARKERS)
            or log.count("Calling app_main()") != 1
            or any(marker in log for marker in _PANIC_MARKERS + ASSET_ERRORS)):
        return False
    expected_id = "device-" + hashlib.sha256(
        b"deskbot-link-v1\0" + bytes.fromhex(mac.replace(":", ""))).digest()[:16].hex()
    messages = []
    for raw in log.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw.split("\t", 1)[-1])
        match = re.fullmatch(r"I \([0-9]+\) DeskBotLink: (\{.*\})", line)
        if match is None:
            continue
        try:
            hello = decode_hello(match[1].encode("utf-8"))
        except ValueError:
            return False
        if (hello["device_id"] != expected_id or hello["firmware"]["app_elf_sha256"] != expected_elf
                or hello["firmware"]["version"] != "2.0.5"
                or hello["hardware"]["flash_bytes"] != 0x2000000
                or hello["hardware"]["psram_bytes"] != 0x1000000):
            return False
        if messages and (hello["boot_id"] != messages[-1]["boot_id"]
                or hello["sequence"] != messages[-1]["sequence"] + 1
                or not 9000 <= hello["uptime_ms"] - messages[-1]["uptime_ms"] <= 12000):
            return False
        messages.append(hello)
    return len(messages) >= 3


class ResourceDevice(BaseDevice):
    BASELINE = "E3d-2b"

    def boot(self):
        # The serial helper checks fresh chip identity before USB reset.
        log = self._capture_serial("e3d2b-resource-boot", 45)
        return inspect_boot(log, self._identity.mac)
