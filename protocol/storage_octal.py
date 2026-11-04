"""Offline validation for the explicit-Octal N32R16 candidate (E1.39).

This module only consumes local build/capture evidence.  It never opens a
serial port and it never authorizes a Flash write.
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import re
from typing import Any

from protocol.storage_addressing import mmap_guard, rollback_ranges, sector_range
from protocol.storage_capacity import image_facts
from protocol.storage_n32r16 import APP_BYTES, CAPTURE_SHA256, OLD_TABLE_SHA256, PHYSICAL_FLASH_BYTES, PSRAM_BYTES

MIB = 1024 * 1024
EXPECTED_RESULT = "isolated_explicit_octal_builds_passed_capacity_preflight"
NO_WRITE_SAFETY = {
    "serial_accessed": False,
    "device_written": False,
    "device_reset": False,
    "c61_accessed": False,
    "nvs_written": False,
    "formal_source_changed": False,
    "formal_partition_changed": False,
}
BUILD_ARTIFACTS = {
    "xiaozhi.bin", "xiaozhi.elf", "xiaozhi.map", "bootloader/bootloader.bin",
    "bootloader/bootloader.elf", "bootloader/bootloader.map",
    "partition_table/partition-table.bin", "expression_assets.bin", "srmodels/srmodels.bin",
    "ota_data_initial.bin", "sdkconfig", "flasher_args.json", "config/sdkconfig.json",
    "bootloader/config/sdkconfig.json", "compile_commands.json",
}

OCTAL_KEYS = (
    "ESPTOOLPY_OCT_FLASH", "ESPTOOLPY_FLASH_MODE_AUTO_DETECT",
    "ESPTOOLPY_FLASHMODE", "ESPTOOLPY_FLASHMODE_OPI",
    "ESPTOOLPY_FLASHMODE_QIO", "ESPTOOLPY_FLASH_SAMPLE_MODE_STR",
    "ESPTOOLPY_FLASH_SAMPLE_MODE_DTR", "ESPTOOLPY_FLASHSIZE",
    "ESPTOOLPY_FLASHSIZE_16MB", "ESPTOOLPY_FLASHSIZE_32MB",
    "BOOTLOADER_FLASH_32BIT_ADDR", "BOOTLOADER_FLASH_NEEDS_32BIT_FEAT",
    "BOOTLOADER_FLASH_NEEDS_32BIT_ADDR_QUAD_FLASH",
    "BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH",
    "BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH",
    "SPI_FLASH_SIZE_OVERRIDE", "SPI_FLASH_SUPPORT_MXIC_OPI_CHIP",
    "SPI_FLASH_ROM_IMPL", "IDF_EXPERIMENTAL_FEATURES",
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def config_value(config: dict[str, Any], key: str, default: Any = False) -> Any:
    """Read a generated sdkconfig JSON key without inferring runtime OPI."""
    return config.get(key, default)


def expected_explicit_config(mib: int) -> dict[str, Any]:
    if mib not in (16, 32):
        raise ValueError("explicit-Octal candidates are limited to 16 and 32 MiB")
    is_32 = mib == 32
    return {
        "ESPTOOLPY_OCT_FLASH": True,
        "ESPTOOLPY_FLASH_MODE_AUTO_DETECT": False,
        "ESPTOOLPY_FLASHMODE": "dout",
        "ESPTOOLPY_FLASHMODE_OPI": True,
        "ESPTOOLPY_FLASHMODE_QIO": False,
        "ESPTOOLPY_FLASH_SAMPLE_MODE_STR": True,
        "ESPTOOLPY_FLASH_SAMPLE_MODE_DTR": False,
        "ESPTOOLPY_FLASHFREQ": "80m",
        "ESPTOOLPY_FLASHSIZE": f"{mib}MB",
        "ESPTOOLPY_FLASHSIZE_16MB": not is_32,
        "ESPTOOLPY_FLASHSIZE_32MB": is_32,
        "BOOTLOADER_FLASH_32BIT_ADDR": is_32,
        "BOOTLOADER_FLASH_NEEDS_32BIT_FEAT": False,
        "BOOTLOADER_FLASH_NEEDS_32BIT_ADDR_QUAD_FLASH": False,
        "BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH": False,
        "BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH": is_32,
        "SPI_FLASH_SIZE_OVERRIDE": False,
        "SPI_FLASH_SUPPORT_MXIC_OPI_CHIP": True,
        "SPI_FLASH_ROM_IMPL": False,
        "IDF_EXPERIMENTAL_FEATURES": False,
    }


def effective_octal_config(config: dict[str, Any], mib: int) -> dict[str, Any]:
    expected = expected_explicit_config(mib)
    actual = {key: config_value(config, key) for key in expected}
    if actual != expected or any(type(actual[key]) is not type(value) for key, value in expected.items()):
        raise ValueError("effective explicit-Octal configuration mismatch")
    return actual


def parse_config(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            result[line[2:-len(" is not set")]] = "n"
    return result


def config_diff(old: dict[str, str], new: dict[str, str]) -> dict[str, dict[str, str | None]]:
    return {key: {"old": old.get(key), "candidate": new.get(key)}
            for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)}


def compare_images(old: bytes, candidate: bytes) -> dict[str, Any]:
    before, after = image_facts(old), image_facts(candidate)
    shared = min(len(old), len(candidate))
    differing = 0
    first = None
    for index, (left, right) in enumerate(zip(old, candidate)):
        if left != right:
            differing += 1
            if first is None:
                first = index
    segments = []
    for index in range(max(len(before["segments"]), len(after["segments"]))):
        a = before["segments"][index] if index < len(before["segments"]) else None
        b = after["segments"][index] if index < len(after["segments"]) else None
        segments.append({"index": index, "control": a, "candidate": b,
                         "payload_equal": a is not None and b is not None
                         and (a["bytes"], a["sha256"]) == (b["bytes"], b["sha256"]),
                         "geometry_equal": a is not None and b is not None and all(
                             a[key] == b[key] for key in ("load_address", "file_offset", "bytes"))})
    return {
        "control": before,
        "candidate": after,
        "length_delta_bytes": len(candidate) - len(old),
        "shared_range_bytes": shared,
        "differing_bytes_in_shared_range": differing,
        "first_differing_offset": first,
        "header_changed_offsets": [i for i in range(24) if old[i] != candidate[i]],
        "segment_comparison": segments,
        "segments_equal": before["segments"] == after["segments"],
        "checksum_verified": before["checksum_verified"] and after["checksum_verified"],
        "appended_sha256_verified": before["appended_sha256_verified"] and after["appended_sha256_verified"],
    }


def validate_hash(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA-256 record")


def validate_record(record: dict) -> None:
    if (not isinstance(record, dict) or set(record) != {"bytes", "sha256"}
            or type(record["bytes"]) is not int or record["bytes"] <= 0):
        raise ValueError("invalid artifact record")
    validate_hash(record["sha256"])


def validate_image_metadata(facts: dict, artifact: dict, mib: int) -> None:
    """Check public metadata geometry; only local image_facts verifies bytes."""
    if (not isinstance(facts, dict)
            or {"bytes": facts.get("bytes"), "sha256": facts.get("sha256")} != artifact
            or facts.get("configured_flash_bytes") != mib * MIB
            or facts.get("flash_mode") != 3 or facts.get("flash_frequency_code") != 15
            or facts.get("checksum_verified") is not True
            or facts.get("appended_sha256_verified") is not True):
        raise ValueError("image metadata does not match the explicit-Octal artifact")
    segments = facts.get("segments", [])
    if not isinstance(segments, list) or not 1 <= len(segments) <= 16:
        raise ValueError("missing image segment metadata")
    offset = 24
    for index, segment in enumerate(segments):
        if (segment.get("index") != index or segment.get("file_offset") != offset + 8
                or type(segment.get("load_address")) is not int or not 0 <= segment["load_address"] < 2**32
                or type(segment.get("bytes")) is not int or segment["bytes"] < 0):
            raise ValueError("image segment geometry is inconsistent")
        validate_hash(segment.get("sha256"))
        offset += 8 + segment["bytes"]
    if facts.get("checksum_offset") != (offset | 15) or (offset | 15) + 33 != artifact["bytes"]:
        raise ValueError("image checksum/digest extent does not match its artifact")


def validate_relative(path: Any, prefix: str) -> None:
    if (not isinstance(path, str) or "\\" in path or ":" in path
            or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
            or not path.startswith(prefix) or str(PurePosixPath(path)) != path):
        raise ValueError("unsafe retained evidence path")


def validate_rollback(rollback: dict, candidate: dict, work_dir: str = "tmp/e139-octal") -> None:
    if (rollback.get("source_capture_sha256") != CAPTURE_SHA256
            or rollback.get("files_verified_against_capture") is not True
            or rollback.get("prewrite_identity_and_digest_refresh_required") is not True
            or rollback.get("executable_flash_script_generated") is not False
            or rollback.get("capture_date") != "2026-10-29"):
        raise ValueError("rollback provenance or prewrite boundary changed")
    images, ranges = rollback.get("images", []), rollback.get("ranges", [])
    if len(images) != 2 or len(ranges) != 2:
        raise ValueError("rollback must cover exactly the paired candidate")
    for image, row, (name, offset, artifact, bound) in zip(images, ranges, (
            ("bootloader", 0, "bootloader/bootloader.bin", 0x8000),
            ("app", 0x10000, "xiaozhi.bin", 0x5EC000))):
        if image != {"id": name, "offset": offset, "payload_bytes": candidate["artifacts"][artifact]["bytes"],
                      "candidate_sha256": candidate["artifacts"][artifact]["sha256"]}:
            raise ValueError("rollback does not describe the actual paired candidate images")
        span = sector_range(offset, image["payload_bytes"])
        if row.get("id") != name or any(row.get(key) != value for key, value in span.items()) or span["end_exclusive"] > bound:
            raise ValueError("rollback is partial or crosses protected partition boundaries")
        validate_hash(row.get("sha256"))
        if row.get("private_file") != f"{work_dir}/rollback/{name}-live-sectors.bin":
            raise ValueError("rollback path differs from the private retained evidence")


def validate_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("E1.39 report must be an object")
    if (report.get("schema") != "deskbot.n32r16.explicit_octal_build.v1"
            or report.get("edition") != "E1.39"
            or report.get("result") != EXPECTED_RESULT):
        raise ValueError("invalid E1.39 report identity")
    if report.get("physical_flash_bytes") != PHYSICAL_FLASH_BYTES or report.get("psram_bytes") != PSRAM_BYTES:
        raise ValueError("hardware capacity identity changed")
    if report.get("safety") != NO_WRITE_SAFETY:
        raise ValueError("E1.39 crossed the offline safety boundary")
    if (report.get("formal_source_unchanged") is not True or report.get("formal_partition_unchanged") is not True
            or report.get("components") != {"count": 69, "actual_contents_match_lock": True,
                                            "local_touch_path_is_only_lock_difference": True}):
        raise ValueError("formal source or pinned dependency boundary changed")
    validate_hash(report.get("source_manifest_sha256"))
    validate_hash(report.get("build_report_e137_sha256"))
    invocation = report.get("build_invocation", {})
    for key in ("original_script_sha256", "review_script_sha256", "inputs_sha256"):
        validate_hash(invocation.get(key))
    if set(invocation.get("checkpoint_sha256", {})) != {"16m", "32m"}:
        raise ValueError("both build checkpoint identities are required")
    for value in invocation["checkpoint_sha256"].values():
        validate_hash(value)
    if any(report.get(key) is not False for key in (
            "deployment_eligible", "flash_authorized", "runtime_verified", "hardware_accepted", "live_snapshot_current")):
        raise ValueError("offline E1.39 cannot authorize deployment or claim hardware acceptance")
    builds = report.get("builds", [])
    if (not isinstance(builds, list) or [row.get("configured_flash_bytes") for row in builds]
            != [16 * MIB, 32 * MIB]):
        raise ValueError("E1.39 requires 16 MiB control then 32 MiB candidate")
    for row, mib in zip(builds, (16, 32)):
        if row.get("observer_source_members") != 1 or row.get("formal_source_unchanged") is not True:
            raise ValueError("observer/source boundary changed")
        if row.get("effective_app_config") != expected_explicit_config(mib):
            raise ValueError("App effective configuration is not the reviewed explicit-Octal set")
        if row.get("effective_bootloader_config") != expected_explicit_config(mib):
            raise ValueError("bootloader effective configuration is not the reviewed explicit-Octal set")
        artifacts = row.get("artifacts", {})
        if set(artifacts) != BUILD_ARTIFACTS:
            raise ValueError("retained build artifact set is incomplete or unreviewed")
        for record in artifacts.values():
            validate_record(record)
        if artifacts.get("partition_table/partition-table.bin", {}).get("sha256") != OLD_TABLE_SHA256:
            raise ValueError("formal partition table changed")
        if not 0 < artifacts["xiaozhi.bin"]["bytes"] < APP_BYTES:
            raise ValueError("App does not fit the protected partition")
        if not 0 < artifacts["bootloader/bootloader.bin"]["bytes"] <= 0x8000:
            raise ValueError("bootloader crosses the partition-table boundary")
        if row.get("app_partition_free_bytes") != APP_BYTES - artifacts["xiaozhi.bin"]["bytes"]:
            raise ValueError("App partition headroom arithmetic changed")
        for name, key in (("xiaozhi.bin", "app_image"), ("bootloader/bootloader.bin", "bootloader_image")):
            validate_image_metadata(row.get(key), artifacts[name], mib)
    protected = report.get("protected_outputs", {})
    if set(protected) != {"partition_table/partition-table.bin", "expression_assets.bin",
                          "srmodels/srmodels.bin", "ota_data_initial.bin"} or any(value is not True for value in protected.values()):
        raise ValueError("protected outputs changed across explicit-Octal capacity pair")
    for name in protected:
        if builds[0]["artifacts"].get(name) != builds[1]["artifacts"].get(name):
            raise ValueError("protected artifact records disagree")
    comparisons = report.get("image_comparisons", {})
    if set(comparisons) != {"xiaozhi.bin", "bootloader/bootloader.bin"}:
        raise ValueError("both previous-mode image comparisons are required")
    for name, key in (("xiaozhi.bin", "app_image"), ("bootloader/bootloader.bin", "bootloader_image")):
        if comparisons[name].get("candidate") != builds[1][key]:
            raise ValueError("image comparison does not describe the actual Octal candidate")
    work_dir = report.get("work_dir", "tmp/e139-octal")
    validate_relative(work_dir, "tmp/")
    for mib, row in zip((16, 32), builds):
        if len(row.get("steps", [])) != 2:
            raise ValueError("both configure/build log records are required")
        for step, action in zip(row["steps"], ("reconfigure", "build")):
            if type(step.get("returncode")) is not int or step["returncode"] != 0 or step.get("log") != f"{work_dir}/{mib}m-{action}.log":
                raise ValueError("build log path or exit status is inconsistent")
            validate_hash(step.get("log_sha256"))
    validate_rollback(report.get("rollback", {}), builds[1], work_dir)
    cache = report.get("cache_preflight", {})
    if cache.get("16m_octal_cache32") is not False or cache.get("32m_octal_cache32") is not True:
        raise ValueError("Octal cache32 derivation is missing")
    if cache.get("quad_cache32") is not False or cache.get("experimental_features") is not False:
        raise ValueError("Quad/experimental cache path was enabled")
    if cache.get("upper_half_runtime_verified") is not False:
        raise ValueError("offline preflight cannot claim upper-half runtime verification")
    if cache.get("eaf_runtime_verified") is not False or any(
            cache.get(f"mmap_{mib}m_upper") != candidate_mmap(mib) for mib in (16, 32)):
        raise ValueError("mmap guard inference or runtime boundary changed")
    for row in builds:
        expected = row["configured_flash_bytes"] // MIB
        for key in ("effective_app_config", "effective_bootloader_config"):
            effective_octal_config(row[key], expected)
        validate_relative(row.get("archive"), f"{work_dir}/outputs/{expected}m")
        if row.get("archive") != f"{work_dir}/outputs/{expected}m":
            raise ValueError("unexpected candidate archive path")


def candidate_mmap(mib: int) -> dict[str, Any]:
    if type(mib) is not int or mib not in (16, 32):
        raise ValueError("unsupported mmap candidate capacity")
    config = {"BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH": False,
              "BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH": mib == 32,
              "SPI_FLASH_ROM_IMPL": False}
    result = mmap_guard(16 * MIB, 8 * MIB, config)
    result["candidate"] = f"{mib}m-explicit-octal"
    result["runtime_verified"] = False
    return result


__all__ = ["BUILD_ARTIFACTS", "EXPECTED_RESULT", "MIB", "NO_WRITE_SAFETY", "OCTAL_KEYS",
           "candidate_mmap", "compare_images", "config_diff", "effective_octal_config",
           "expected_explicit_config", "parse_config", "rollback_ranges", "sha",
           "validate_report"]
