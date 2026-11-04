"""Strict offline checks for a capacity-only ESP32 image/build comparison."""
from __future__ import annotations

import hashlib
import struct
from typing import Any

from protocol.storage_live_backup import configured_flash_bytes
from protocol.storage_n32r16 import APP_BYTES, OLD_TABLE_SHA256

CAPACITY_CONFIG_DELTA = {
    "CONFIG_ESPTOOLPY_FLASHSIZE": {"old": '"16MB"', "candidate": '"32MB"'},
    "CONFIG_ESPTOOLPY_FLASHSIZE_16MB": {"old": "y", "candidate": "n"},
    "CONFIG_ESPTOOLPY_FLASHSIZE_32MB": {"old": "n", "candidate": "y"},
    # Invisible helpers derived by ESP-IDF bootloader/Kconfig.projbuild.
    "CONFIG_BOOTLOADER_FLASH_32BIT_ADDR": {"old": None, "candidate": "y"},
    "CONFIG_BOOTLOADER_FLASH_NEEDS_32BIT_FEAT": {"old": None, "candidate": "y"},
    "CONFIG_BOOTLOADER_FLASH_NEEDS_32BIT_ADDR_QUAD_FLASH": {"old": None, "candidate": "y"},
}
NO_WRITE_SAFETY = {"serial_accessed": False, "device_written": False, "c61_accessed": False,
                   "nvs_written": False, "formal_partition_changed": False}


def image_facts(raw: bytes) -> dict[str, Any]:
    """Validate unsigned ESP image segments, XOR checksum and appended SHA-256."""
    if not isinstance(raw, bytes) or len(raw) < 24 or raw[0] != 0xE9 or not 1 <= raw[1] <= 16:
        raise ValueError("invalid ESP image header")
    if raw[23] != 1:
        raise ValueError("reviewed build images must include an appended SHA-256")
    configured = configured_flash_bytes(raw[:4])
    offset, checksum, segments = 24, 0xEF, []
    for index in range(raw[1]):
        if offset + 8 > len(raw):
            raise ValueError("truncated segment header")
        address, size = struct.unpack_from("<II", raw, offset)
        offset += 8
        if size > len(raw) - offset:
            raise ValueError("truncated image segment")
        payload = raw[offset:offset + size]
        for value in payload:
            checksum ^= value
        segments.append({"index": index, "load_address": address, "file_offset": offset,
                         "bytes": size, "sha256": hashlib.sha256(payload).hexdigest()})
        offset += size
    checksum_offset = offset | 15
    if checksum_offset + 33 != len(raw):
        raise ValueError("image is truncated or contains an unreviewed signature/trailer")
    if raw[checksum_offset] != checksum:
        raise ValueError("ESP image checksum mismatch")
    digest_offset = checksum_offset + 1
    if hashlib.sha256(raw[:digest_offset]).digest() != raw[digest_offset:]:
        raise ValueError("appended image SHA-256 mismatch")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "configured_flash_bytes": configured, "flash_mode": raw[2],
            "flash_frequency_code": raw[3] & 15, "segments": segments,
            "checksum_offset": checksum_offset, "checksum_verified": True,
            "appended_sha256_verified": True}


def compare_images(old: bytes, candidate: bytes) -> dict[str, Any]:
    before, after = image_facts(old), image_facts(candidate)
    if before["configured_flash_bytes"] != 16 * 1024 * 1024 or after["configured_flash_bytes"] != 32 * 1024 * 1024:
        raise ValueError("capacity comparison requires a 16 MiB control and a 32 MiB candidate")
    if (before["flash_mode"], before["flash_frequency_code"]) != (after["flash_mode"], after["flash_frequency_code"]):
        raise ValueError("capacity-only build changed Flash mode or frequency")
    def normalized(raw: bytes) -> bytes:
        # High nibble of header byte 3 is capacity. The final digest covers it.
        return raw[:3] + bytes((raw[3] & 15,)) + raw[4:-32]
    identical_payload = normalized(old) == normalized(candidate)
    changed = [index for index, (left, right) in enumerate(zip(old, candidate)) if left != right]
    return {"control": before, "candidate": after,
            "only_header_capacity_and_digest_changed": identical_payload,
            "differing_bytes_in_shared_range": len(changed),
            "first_differing_offset": changed[0] if changed else None,
            "length_delta_bytes": len(candidate) - len(old),
            "segment_payloads_equal": before["segments"] == after["segments"]}


def validate_build_report(report: dict[str, Any]) -> None:
    if (not isinstance(report, dict) or report.get("schema") != "deskbot.n32r16.capacity_build.v1"
            or report.get("edition") != "E1.37"
            or report.get("result") != "isolated_capacity_builds_passed_runtime_pending"
            or report.get("candidate") != "capacity-only-32m"):
        raise ValueError("unexpected capacity-build report identity")
    if report.get("physical_flash_bytes") != 32 * 1024 * 1024 or report.get("psram_bytes") != 16 * 1024 * 1024:
        raise ValueError("physical Flash and PSRAM identity mismatch")
    if report.get("safety") != NO_WRITE_SAFETY or report.get("formal_source_unchanged") is not True:
        raise ValueError("capacity build crossed its offline boundary")
    if any(report.get(key) is not False for key in ("deployment_eligible", "flash_authorized", "runtime_verified", "hardware_accepted")):
        raise ValueError("offline builds cannot authorize deployment or claim hardware acceptance")
    builds = report.get("builds", [])
    if (not isinstance(builds, list) or len(builds) != 2
            or [row.get("configured_flash_bytes") for row in builds] != [16 * 1024 * 1024, 32 * 1024 * 1024]):
        raise ValueError("both capacity builds are required in control/candidate order")
    for row in builds:
        if row.get("observer_enabled") is not True or row.get("observer_source_members") != 1:
            raise ValueError("observer configuration changed")
        if row.get("artifacts", {}).get("partition_table/partition-table.bin", {}).get("sha256") != OLD_TABLE_SHA256:
            raise ValueError("legacy partition table changed")
        if row.get("artifacts", {}).get("xiaozhi.bin", {}).get("bytes", APP_BYTES) >= APP_BYTES:
            raise ValueError("App does not fit the protected partition")
    comparison = report.get("comparison", {})
    if comparison.get("config_changes") != CAPACITY_CONFIG_DELTA:
        raise ValueError("configuration differs beyond the Flash capacity choices")
    if comparison.get("control_app_matches_accepted_e126") is not True:
        raise ValueError("control App must reproduce accepted E1.26")
    same = comparison.get("unchanged_outputs", {})
    protected_outputs = {"partition_table/partition-table.bin", "expression_assets.bin", "srmodels/srmodels.bin", "ota_data_initial.bin"}
    if set(same) != protected_outputs or any(value is not True for value in same.values()):
        raise ValueError("protected non-capacity build output changed")
    for name in protected_outputs:
        if builds[0]["artifacts"].get(name) != builds[1]["artifacts"].get(name):
            raise ValueError("protected artifact hashes differ")
    expected_room = APP_BYTES - builds[1]["artifacts"]["xiaozhi.bin"]["bytes"]
    if comparison.get("candidate_app_partition_free_bytes") != expected_room:
        raise ValueError("App capacity arithmetic changed")
    cache = report.get("upper_half_cache", {})
    if (cache.get("quad_cache_enabled") is not False
            or cache.get("octal_cache_enabled") is not False
            or cache.get("experimental_features_enabled") is not False
            or cache.get("upper_assets_deployment_ready") is not False):
        raise ValueError("capacity-only build does not establish upper-half cached access")
