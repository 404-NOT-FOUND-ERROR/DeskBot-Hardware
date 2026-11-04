"""Read-only E1.40 paired-capacity boot preflight helpers.

This module consumes a fresh full Flash readback and the already reviewed
E1.39 32 MiB bootloader/App candidates.  It never opens a serial port and it
never authorizes a write.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any

from protocol.storage_addressing import bounded_image
from protocol.storage_capacity import image_facts

MIB = 1024 * 1024
PHYSICAL_FLASH_BYTES = 32 * MIB
LEGACY_CONFIGURED_FLASH_BYTES = 16 * MIB
APP_OFFSET = 0x10000
APP_PARTITION_BYTES = 0x5DC000
APP_PARTITION_END = APP_OFFSET + APP_PARTITION_BYTES
PARTITION_TABLE_PAYLOAD_BYTES = 0xC00
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_TABLE_SECTOR_BYTES = 0x1000
ASSETS_OFFSET = 0x5EC000
ASSETS_BYTES = 0x5DC000
MODEL_OFFSET = 0xBC8000
MODEL_BYTES = 0xC8000
UPPER_HALF_OFFSET = 0x1000000

ACCEPTED_APP_BYTES = 4_477_280
ACCEPTED_APP_SHA256 = "f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2"
ACCEPTED_BOOTLOADER_BYTES = 16_240
ACCEPTED_BOOTLOADER_SHA256 = "2e9dceaa1affbc6e38826dbc320a08fbc46b5e55be21be043fc060cf13da8a4d"
ACCEPTED_ASSETS_SHA256 = "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6"
ACCEPTED_MODEL_PAYLOAD_BYTES = 582_107
ACCEPTED_MODEL_PAYLOAD_SHA256 = "cec059b2f1f574523e7e53d34166b224f6ae2f393652515ae7a1ef94ed44d7ba"
ACCEPTED_MODEL_SHA256 = "37a572cda25d8381b733f8ce48c1532488c87cf3359e1c78b13b1262f58eed39"
PARTITION_TABLE_PAYLOAD_SHA256 = "f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c"
CANDIDATE_BOOTLOADER_SHA256 = "1b21ce721cae5f456511e4611d0a2fb61b9809108f39eb488d4eea314c0d0485"
CANDIDATE_BOOTLOADER_BYTES = 15_504
CANDIDATE_APP_SHA256 = "fa57fac6056be8b779a52e97cd1f7410c69d5af4c1798a52560f631ce352e4e5"
CANDIDATE_APP_BYTES = 4_476_944
MAC = "9c:cc:01:fb:a8:78"

NO_WRITE_SAFETY = {
    "serial_read_only": True,
    "device_written": False,
    "device_reset": True,
    "c61_accessed": False,
    "nvs_written": False,
    "formal_source_changed": False,
    "formal_partition_changed": False,
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sector_span(offset: int, payload_bytes: int, sector_bytes: int = 0x1000) -> dict[str, int]:
    if type(offset) is not int or type(payload_bytes) is not int or offset < 0 or payload_bytes <= 0:
        raise ValueError("invalid Flash payload range")
    start = offset // sector_bytes * sector_bytes
    end = (offset + payload_bytes + sector_bytes - 1) // sector_bytes * sector_bytes
    return {"offset": start, "bytes": end - start, "end_exclusive": end}


def range_record(raw: bytes, identifier: str, offset: int, size: int) -> dict[str, Any]:
    if offset < 0 or size <= 0 or offset + size > len(raw):
        raise ValueError("range exceeds readback")
    payload = raw[offset:offset + size]
    return {"id": identifier, "offset": offset, "bytes": size,
            "end_exclusive": offset + size, "sha256": sha(payload),
            "all_ff": payload.count(0xFF) == size}


def parse_identity(flash_log: str, chip_log: str) -> dict[str, Any]:
    flat = re.sub(r"\s+", " ", flash_log)
    chip_flat = re.sub(r"\s+", " ", chip_log)
    required = (
        "Manufacturer: c2", "Device: 8039", "Detected flash size: 32MB",
        "Flash type set in eFuse: octal (8 data lines)",
        "Flash voltage set by eFuse to 3.3V",
    )
    if any(flat.count(value) != 1 for value in required):
        raise ValueError("fresh Flash identity evidence is incomplete")
    if chip_flat.count("MAC: " + MAC) < 1 or flat.count("MAC: " + MAC) < 1:
        raise ValueError("fresh MAC evidence is missing")
    return {
        "chip": "esp32s3",
        "chip_revision": "v0.2",
        "mac": MAC,
        "psram_bytes": 16 * MIB,
        "jedec_manufacturer": "0xC2",
        "jedec_device": "0x8039",
        "physical_flash_bytes": PHYSICAL_FLASH_BYTES,
        "efuse_flash_type": "octal",
        "efuse_flash_voltage_v": 3.3,
        "transport": "USB-Serial/JTAG",
    }


def read_log_is_safe(log: str, expected_path: str) -> bool:
    flat = re.sub(r"\s+", " ", log)
    forbidden = ("write_flash", "erase_flash", "erase_region", "Chip erase")
    return ("Read 33554432 bytes at 0x00000000" in flat
            and "Staying in bootloader." in flat
            and not any(item.lower() in log.lower() for item in forbidden))


def candidate_facts(bootloader: bytes, app: bytes) -> dict[str, Any]:
    boot = image_facts(bootloader)
    application = image_facts(app)
    if (len(bootloader) != CANDIDATE_BOOTLOADER_BYTES or sha(bootloader) != CANDIDATE_BOOTLOADER_SHA256
            or len(app) != CANDIDATE_APP_BYTES or sha(app) != CANDIDATE_APP_SHA256):
        raise ValueError("E1.39 candidate bytes do not match the reviewed E1.40 input")
    if boot["configured_flash_bytes"] != PHYSICAL_FLASH_BYTES or application["configured_flash_bytes"] != PHYSICAL_FLASH_BYTES:
        raise ValueError("paired candidate is not configured for 32 MiB")
    if not (len(bootloader) <= 0x8000 and len(app) < APP_PARTITION_BYTES):
        raise ValueError("candidate crosses a protected partition boundary")
    return {"bootloader": boot, "app": application}


def current_facts(raw: bytes) -> dict[str, Any]:
    if len(raw) != PHYSICAL_FLASH_BYTES:
        raise ValueError("fresh readback must cover exactly 32 MiB")
    boot = bounded_image(raw, 0, 0x8000)
    app = bounded_image(raw, APP_OFFSET, APP_PARTITION_BYTES)
    app_facts = image_facts(app)
    checks = {
        "bootloader_header_is_legacy_16m": (raw[3] >> 4) == 4,
        "app_header_is_legacy_16m": (raw[APP_OFFSET + 3] >> 4) == 4,
        "accepted_bootloader_matches": len(boot) == ACCEPTED_BOOTLOADER_BYTES and sha(boot) == ACCEPTED_BOOTLOADER_SHA256,
        "accepted_e126_app_matches": len(app) == ACCEPTED_APP_BYTES and sha(app) == ACCEPTED_APP_SHA256,
        "partition_table_payload_matches": sha(raw[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + PARTITION_TABLE_PAYLOAD_BYTES]) == PARTITION_TABLE_PAYLOAD_SHA256,
        "e118_assets_matches": sha(raw[ASSETS_OFFSET:ASSETS_OFFSET + ASSETS_BYTES]) == ACCEPTED_ASSETS_SHA256,
        "speech_model_payload_matches": len(raw[MODEL_OFFSET:MODEL_OFFSET + ACCEPTED_MODEL_PAYLOAD_BYTES]) == ACCEPTED_MODEL_PAYLOAD_BYTES
        and sha(raw[MODEL_OFFSET:MODEL_OFFSET + ACCEPTED_MODEL_PAYLOAD_BYTES]) == ACCEPTED_MODEL_PAYLOAD_SHA256,
        "speech_model_partition_matches": sha(raw[MODEL_OFFSET:MODEL_OFFSET + MODEL_BYTES]) == ACCEPTED_MODEL_SHA256,
        "upper_half_is_erased": raw[UPPER_HALF_OFFSET:] == b"\xff" * (PHYSICAL_FLASH_BYTES - UPPER_HALF_OFFSET),
    }
    if not all(checks.values()):
        raise ValueError("fresh readback does not match the accepted protected baseline")
    return {
        "bytes": len(raw), "sha256": sha(raw),
        "bootloader": {"bytes": len(boot), "sha256": sha(boot), "facts": image_facts(boot)},
        "app": {"bytes": len(app), "sha256": sha(app), "facts": app_facts},
        "checks": checks,
        "ranges": [
            range_record(raw, "bootloader_reserved", 0x000000, 0x8000),
            range_record(raw, "partition_table", 0x8000, 0x1000),
            range_record(raw, "nvs", 0x9000, 0x4000),
            range_record(raw, "otadata", 0xD000, 0x2000),
            range_record(raw, "phy_init", 0xF000, 0x1000),
            range_record(raw, "app_partition", APP_OFFSET, APP_PARTITION_BYTES),
            range_record(raw, "assets", ASSETS_OFFSET, ASSETS_BYTES),
            range_record(raw, "model", MODEL_OFFSET, MODEL_BYTES),
            range_record(raw, "upper_half", UPPER_HALF_OFFSET, PHYSICAL_FLASH_BYTES - UPPER_HALF_OFFSET),
        ],
    }


def write_rollback_files(raw: bytes, root: Path, work_dir: str, *, create_missing: bool = True) -> list[dict[str, Any]]:
    plan = [
        ("bootloader", 0x000000, CANDIDATE_BOOTLOADER_BYTES),
        ("app", APP_OFFSET, CANDIDATE_APP_BYTES),
    ]
    result = []
    rollback_dir = root / work_dir / "rollback"
    rollback_dir.mkdir(parents=True, exist_ok=True)
    for identifier, offset, payload_bytes in plan:
        span = sector_span(offset, payload_bytes)
        target = rollback_dir / f"{identifier}-live-sectors.bin"
        expected = raw[span["offset"]:span["end_exclusive"]]
        if target.exists():
            existing = target.read_bytes()
            if existing != expected:
                raise ValueError("existing rollback differs from the fresh readback: " + target.name)
        elif create_missing:
            target.write_bytes(expected)
        else:
            raise ValueError("rollback file is missing: " + target.name)
        actual = target.read_bytes()
        result.append({"id": identifier, **span, "sha256": sha(actual),
                       "bytes_verified_against_readback": actual == expected,
                       "private_file": f"{work_dir}/rollback/{target.name}"})
    return result


__all__ = [
    "ACCEPTED_APP_SHA256", "ACCEPTED_ASSETS_SHA256", "ACCEPTED_BOOTLOADER_SHA256", "ACCEPTED_MODEL_SHA256",
    "APP_OFFSET", "APP_PARTITION_BYTES", "CANDIDATE_APP_BYTES", "CANDIDATE_APP_SHA256",
    "CANDIDATE_BOOTLOADER_BYTES", "CANDIDATE_BOOTLOADER_SHA256", "LEGACY_CONFIGURED_FLASH_BYTES",
    "MAC", "MODEL_OFFSET", "MODEL_BYTES", "NO_WRITE_SAFETY", "PARTITION_TABLE_PAYLOAD_SHA256",
    "PHYSICAL_FLASH_BYTES", "candidate_facts", "current_facts", "parse_identity", "read_log_is_safe",
    "sector_span", "sha", "write_rollback_files",
]
