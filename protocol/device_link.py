"""Device-origin hello validation, independent of USB and future LAN framing."""
from __future__ import annotations

from copy import deepcopy
import json
import re

SCHEMA = "deskbot.device_link/v1"
MAX_FRAME_BYTES = 2048
HELLO_INTERVAL_MS = 10_000
FRESH_MS = 30_000


def _integer(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def validate_hello(value):
    fields = {"schema", "message_type", "device_id", "boot_id", "sequence", "uptime_ms", "firmware",
              "hardware", "capabilities", "transport", "controls_available", "catalog_id", "limits"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("hello_fields")
    if value["schema"] != SCHEMA or value["message_type"] != "hello":
        raise ValueError("hello_version")
    for field, prefix in (("device_id", "device"), ("boot_id", "boot")):
        if not isinstance(value[field], str) or not re.fullmatch(prefix + r"-[a-f0-9]{32}", value[field]):
            raise ValueError("hello_identity")
    if not _integer(value["sequence"], 0xFFFFFFFF) or not _integer(value["uptime_ms"], 9_007_199_254_740_991):
        raise ValueError("hello_counter")
    firmware, hardware = value["firmware"], value["hardware"]
    if (not isinstance(firmware, dict) or set(firmware) != {"edition", "version", "app_elf_sha256"}
            or firmware["edition"] != "E3b" or not isinstance(firmware["version"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,31}", firmware["version"])
            or not isinstance(firmware["app_elf_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", firmware["app_elf_sha256"])):
        raise ValueError("hello_firmware")
    if (not isinstance(hardware, dict) or set(hardware) != {"chip", "board", "flash_bytes", "psram_bytes"}
            or hardware["chip"] != "esp32s3" or hardware["board"] != "esp_vocat"
            or not _integer(hardware["flash_bytes"], 0xFFFFFFFF)
            or not _integer(hardware["psram_bytes"], 0xFFFFFFFF)):
        raise ValueError("hello_hardware")
    if (value["capabilities"] != ["device_identity"] or value["transport"] != "usb_console"
            or value["controls_available"] is not False or value["catalog_id"] is not None
            or value["limits"] != {"max_frame_bytes": MAX_FRAME_BYTES, "hello_interval_ms": HELLO_INTERVAL_MS}
            or any(type(n) is not int for n in value["limits"].values())):
        raise ValueError("hello_capabilities")
    return deepcopy(value)


def decode_hello(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_FRAME_BYTES:
        raise ValueError("hello_size")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("hello_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("hello_number")))
        return validate_hello(value)
    except (UnicodeError, RecursionError, TypeError) as error:
        raise ValueError("hello_json") from error
