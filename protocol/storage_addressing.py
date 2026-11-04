"""Offline Flash addressing evidence and sector-exact rollback checks. No device I/O."""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import re
import struct
from typing import Any

from protocol.storage_capacity import image_facts
from protocol.storage_n32r16 import CAPTURE_SHA256, LEGACY_FLASH_BYTES, PHYSICAL_FLASH_BYTES, PSRAM_BYTES

MIB = 1024 * 1024
SECTOR = 4096
SAFETY = {key: False for key in ("serial_accessed", "device_written", "device_reset", "c61_accessed",
                                "nvs_written", "formal_firmware_changed")}
LIVE_BOOT_SHA = "2e9dceaa1affbc6e38826dbc320a08fbc46b5e55be21be043fc060cf13da8a4d"
LIVE_APP_SHA = "f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2"
CONFIG_EXPECTED = {
    "ESPTOOLPY_FLASHSIZE": "32MB", "ESPTOOLPY_FLASH_MODE_AUTO_DETECT": True,
    "ESPTOOLPY_OCT_FLASH": False, "ESPTOOLPY_FLASHMODE": "dio", "ESPTOOLPY_FLASHMODE_QIO": True,
    "ESPTOOLPY_FLASH_SAMPLE_MODE_STR": True, "ESPTOOLPY_FLASHFREQ": "80m",
    "BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH": False, "BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH": False,
    "SPI_FLASH_SIZE_OVERRIDE": False, "SPI_FLASH_SUPPORT_MXIC_OPI_CHIP": True,
    "SPI_FLASH_ROM_IMPL": False, "IDF_EXPERIMENTAL_FEATURES": False,
}
PROTECTED_RANGES = (
    ("bootloader_reserved", 0, 0x8000), ("partition_table", 0x8000, 0x1000),
    ("nvs", 0x9000, 0x4000), ("otadata", 0xD000, 0x2000), ("phy_init", 0xF000, 0x1000),
    ("app_partition", 0x10000, 0x5DC000), ("assets", 0x5EC000, 0x5DC000),
    ("model", 0xBC8000, 0xC8000), ("inplace_migration_tail", 0xC90000, 0x224000),
    ("remaining_lower_half", 0xEB4000, 0x14C000), ("upper_half", 0x1000000, 0x1000000),
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def flash_evidence(flash_log: str, boot_log: str) -> dict[str, Any]:
    """Require consistent independent identity, eFuse and normal-startup evidence."""
    required_flash = (r"Manufacturer:\s*c2\b", r"Device:\s*8039\b", r"Detected flash size:\s*32MB\b",
                      r"Flash type set in eFuse:\s*octal \(8 data lines\)",
                      r"Flash voltage set by eFuse to\s*3\.3V")
    if any(len(re.findall(pattern, flash_log, re.I)) != 1 for pattern in required_flash):
        raise ValueError("Flash identity/eFuse evidence does not match reviewed MXIC Octal hardware")
    flat = re.sub(r"\s+", " ", boot_log)
    flat = re.sub(r"(?<=\()\s+(?=\d)", "", flat)
    required_boot = ("Octal Flash Mode Enabled", "For OPI Flash, Use Default Flash Boot Mode",
                     "Found 16MB PSRAM device", "detected chip: mxic (opi)", "flash io: opi_str",
                     "Octal flash chip is using but qio mode is selected, will automatically switch to Octal mode",
                     "Detected size(32768k) larger than the size in the binary image header(16384k)")
    if any(flat.count(value) != 1 for value in required_boot):
        raise ValueError("normal boot evidence is absent, ambiguous or inconsistent")
    if re.search(r"flash io:\s*(?!opi_str\b)\w+", flat):
        raise ValueError("conflicting runtime Flash mode evidence")
    return {"manufacturer": "Macronix (MXIC)", "jedec_manufacturer": "0xC2", "jedec_device": "0x8039",
            "physical_flash_bytes": PHYSICAL_FLASH_BYTES, "psram_bytes": PSRAM_BYTES,
            "efuse_flash_type": "octal", "efuse_flash_voltage_v": 3.3,
            "commercial_part_number": None, "runtime_driver": "mxic (opi)", "runtime_io": "opi_str",
            "runtime_configured_flash_bytes": LEGACY_FLASH_BYTES, "auto_detect_switched_to_opi": True}


def effective_config(config: dict) -> dict:
    """Absent invisible Kconfig booleans are false; do not infer them from runtime OPI."""
    result = {}
    for key, expected in CONFIG_EXPECTED.items():
        value = config.get(key, False if type(expected) is bool else None)
        if type(value) is not type(expected):
            raise ValueError("invalid effective configuration type: " + key)
        result[key] = value
    return result


def capacity_case(boot_bytes: int, app_bytes: int, override: bool) -> dict:
    if (type(boot_bytes) is not int or type(app_bytes) is not int
            or boot_bytes not in (16 * MIB, 32 * MIB) or app_bytes not in (16 * MIB, 32 * MIB)
            or type(override) is not bool):
        raise ValueError("invalid capacity/header inputs")
    return {"bootloader_header_bytes": boot_bytes, "app_header_bytes": app_bytes,
            "size_override": override, "expected_driver_limit_bytes": app_bytes if override else boot_bytes,
            "capacity_source": "app_header" if override else "bootloader_header", "runtime_verified": False}


def mmap_guard(start: int, size: int, config: dict) -> dict:
    if (type(start) is not int or type(size) is not int or start < 0 or size <= 0
            or start + size > PHYSICAL_FLASH_BYTES):
        raise ValueError("mmap request outside physical Flash")
    if config.get("SPI_FLASH_ROM_IMPL", False) is not False:
        raise ValueError("ROM mmap implementation is outside this source audit")
    quad = config.get("BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH", False)
    octal = config.get("BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH", False)
    if type(quad) is not bool or type(octal) is not bool:
        raise ValueError("invalid cache configuration")
    rejected = not (quad or octal) and (start >= 16 * MIB or size > 16 * MIB or start > 16 * MIB - size)
    return {"start": start, "bytes": size, "end_exclusive": start + size,
            "rejected_by_24bit_guard": rejected,
            "expected_guard_result": "ESP_ERR_INVALID_ARG" if rejected else "passes_24bit_guard_only",
            "runtime_mmap_verified": False}


def bounded_image(raw: bytes, start: int, bound: int) -> bytes:
    """Extract an unsigned ESP image without accepting erased partition padding as image data."""
    if (type(start) is not int or type(bound) is not int or start < 0 or bound < 24
            or start + bound > len(raw)):
        raise ValueError("image bound exceeds capture")
    head = raw[start:start + 24]
    if head[0] != 0xE9 or not 1 <= head[1] <= 16 or head[23] != 1:
        raise ValueError("invalid bounded image header")
    cursor = start + 24
    for _ in range(head[1]):
        if cursor + 8 > start + bound:
            raise ValueError("segment header exceeds image bound")
        size = struct.unpack_from("<I", raw, cursor + 4)[0]
        cursor += 8 + size
        if cursor > start + bound:
            raise ValueError("segment payload exceeds image bound")
    end = start + ((cursor - start) | 15) + 33
    if end > start + bound:
        raise ValueError("image checksum/digest exceeds bound")
    result = raw[start:end]
    image_facts(result)
    return result


def compare_boot_metadata(live: bytes, built: bytes) -> dict:
    left, right = image_facts(live), image_facts(built)
    if (len(live) != len(built) or left["checksum_offset"] != right["checksum_offset"]
            or [(s["file_offset"], s["load_address"], s["bytes"]) for s in left["segments"]]
            != [(s["file_offset"], s["load_address"], s["bytes"]) for s in right["segments"]]):
        raise ValueError("bootloader image structure changed")
    first = left["segments"][0]
    desc = first["file_offset"]
    if first["bytes"] < 80 or live[desc] != 80 or built[desc] != 80:
        raise ValueError("ESP bootloader descriptor missing")
    date_start, date_end = desc + 40, desc + 64
    for raw in (live, built):
        field = raw[date_start:date_end]
        value, separator, tail = field.partition(b"\0")
        if (not separator or any(tail) or (value and not re.fullmatch(
                rb"[A-Z][a-z]{2} [ 0-9][0-9] [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}", value))):
            raise ValueError("bootloader date field is not empty or a compile timestamp")
    changed = [i for i, pair in enumerate(zip(live, built)) if pair[0] != pair[1]]
    allowed = {3, left["checksum_offset"], *range(date_start, date_end), *range(len(live) - 32, len(live))}
    other = [i for i in changed if i not in allowed]
    # The low nibble is frequency, not capacity. The rest of the descriptor is protected.
    if (live[3] & 15) != (built[3] & 15):
        other.append(3)
    return {"live": {"bytes": len(live), "sha256": sha(live)},
            "built": {"bytes": len(built), "sha256": sha(built)},
            "byte_identical": live == built, "different_bytes": len(changed),
            "date_field": {"start": date_start, "bytes": 24},
            "timestamp_changed_offsets": [i for i in changed if date_start <= i < date_end],
            "capacity_header_changed": live[3] != built[3], "checksum_changed": live[-33] != built[-33],
            "digest_changed_bytes": sum(a != b for a, b in zip(live[-32:], built[-32:])),
            "other_changed_offsets": sorted(set(other)), "only_reviewed_metadata_changed": not other,
            "build_is_exact_rollback": live == built}


def sector_range(offset: int, payload_bytes: int) -> dict:
    if (type(offset) is not int or type(payload_bytes) is not int or offset < 0 or payload_bytes <= 0
            or offset + payload_bytes > PHYSICAL_FLASH_BYTES):
        raise ValueError("invalid Flash write range")
    start = offset // SECTOR * SECTOR
    end = (offset + payload_bytes + SECTOR - 1) // SECTOR * SECTOR
    return {"offset": start, "bytes": end - start, "end_exclusive": end}


def rollback_ranges(raw: bytes, images: list[dict]) -> list[dict]:
    if len(raw) != PHYSICAL_FLASH_BYTES or not images:
        raise ValueError("complete physical capture and planned images are required")
    ranges = []
    for row in images:
        span = sector_range(row["offset"], row["payload_bytes"])
        if any(span["offset"] < r["end_exclusive"] and r["offset"] < span["end_exclusive"] for r in ranges):
            raise ValueError("overlapping sector erase ranges")
        payload = raw[span["offset"]:span["end_exclusive"]]
        ranges.append({"id": row["id"], **span, "sha256": sha(payload)})
    return ranges


def validate_rollback_files(raw: bytes, plan: dict, files: dict[str, bytes]) -> None:
    expected = rollback_ranges(raw, plan["images"])
    recorded = [{key: row.get(key) for key in expected[0]} for row in plan.get("ranges", [])]
    if recorded != expected or set(files) != {r["id"] for r in expected}:
        raise ValueError("rollback range coverage differs from actual sector erases")
    for row in expected:
        if files[row["id"]] != raw[row["offset"]:row["end_exclusive"]]:
            raise ValueError("rollback is not the exact live sector bytes")


def validate_report(report: dict) -> None:
    """Public metadata consistency only; local audit separately verifies private source bytes."""
    if (report.get("schema") != "deskbot.n32r16.addressing_audit.v1" or report.get("edition") != "E1.38"
            or report.get("result") != "offline_preflight_complete_addressing_candidate_blocked"):
        raise ValueError("invalid addressing report identity")
    if report.get("safety") != SAFETY or any(report.get(k) is not False for k in (
            "deployment_eligible", "flash_authorized", "runtime_verified", "hardware_accepted", "live_snapshot_current")):
        raise ValueError("offline evidence cannot authorize writes or assert current device/hardware state")
    facts = report.get("hardware", {})
    # A selected JEDEC family is not sufficient to invent a commercial part number.
    if facts != {"manufacturer": "Macronix (MXIC)", "jedec_manufacturer": "0xC2", "jedec_device": "0x8039",
                 "physical_flash_bytes": PHYSICAL_FLASH_BYTES, "psram_bytes": PSRAM_BYTES,
                 "efuse_flash_type": "octal", "efuse_flash_voltage_v": 3.3,
                 "commercial_part_number": None, "runtime_driver": "mxic (opi)", "runtime_io": "opi_str",
                 "runtime_configured_flash_bytes": LEGACY_FLASH_BYTES, "auto_detect_switched_to_opi": True}:
        raise ValueError("hardware, runtime mode or part-number claim changed")
    if report.get("image_header_mode") != "dio" or report.get("build_mode_selection") != "qio_with_runtime_auto_detection":
        raise ValueError("image-header, compile-time and runtime modes must remain distinct")
    evidence = report.get("evidence", {})
    if (evidence.get("capture_sha256") != CAPTURE_SHA256 or evidence.get("capture_bytes") != PHYSICAL_FLASH_BYTES
            or evidence.get("capture_chunks_verified") != 32 or evidence.get("independent_verify_passed") is not True):
        raise ValueError("capture provenance missing")
    configs = report.get("candidate_configs", [])
    if [r.get("target") for r in configs] != ["app", "bootloader"] or any(r.get("effective") != CONFIG_EXPECTED for r in configs):
        raise ValueError("effective app/bootloader configuration changed")
    expected_cases = [dict(id=name, **capacity_case(boot, app, False)) for name, boot, app in (
        ("accepted_snapshot", 16 * MIB, 16 * MIB), ("app_only_candidate", 16 * MIB, 32 * MIB),
        ("paired_candidate", 32 * MIB, 32 * MIB))]
    if report.get("capacity_cases") != expected_cases:
        raise ValueError("App-only capacity claim does not follow bootloader/override configuration")
    expected_mmap = [dict(id=name, **mmap_guard(start, size, CONFIG_EXPECTED)) for name, start, size in (
        ("legacy_assets", 0x5EC000, 0x5DC000), ("cross_16m_boundary", 0xFF0000, 0x20000),
        ("upper_assets_8m", 0x1000000, 8 * MIB))]
    if report.get("mmap_checks") != expected_mmap or report.get("upper_assets_deployment_ready") is not False:
        raise ValueError("runtime auto-detection cannot bypass the compiled mmap guard")
    comparisons = report.get("live_boot_comparison", [])
    if [r.get("build") for r in comparisons] != ["16m", "32m"]:
        raise ValueError("live bootloader comparison missing")
    for index, row in enumerate(comparisons):
        if (row.get("live") != {"bytes": 16240, "sha256": LIVE_BOOT_SHA}
                or row.get("byte_identical") is not False or row.get("build_is_exact_rollback") is not False
                or row.get("only_reviewed_metadata_changed") is not True or row.get("other_changed_offsets") != []
                or row.get("different_bytes") != 53 + index or row.get("capacity_header_changed") is not bool(index)
                or row.get("date_field") != {"start": 72, "bytes": 24}
                or row.get("timestamp_changed_offsets") != list(range(72, 92))):
            raise ValueError("live bootloader differences were hidden or a build was mislabeled as exact rollback")
    plan = report.get("rollback", {})
    images = plan.get("images", [])
    if ([{k: r.get(k) for k in ("id", "offset", "payload_bytes")} for r in images]
            != [{"id": "bootloader", "offset": 0, "payload_bytes": 16240},
                {"id": "app", "offset": 0x10000, "payload_bytes": 4477280}]
            or plan.get("source_capture_sha256") != CAPTURE_SHA256
            or plan.get("scope") != "conditional_e137_paired_capacity_only"
            or plan.get("files_verified_against_capture") is not True
            or plan.get("prewrite_identity_and_digest_refresh_required") is not True
            or plan.get("executable_flash_script_generated") is not False):
        raise ValueError("rollback source/scope or prewrite gate changed")
    ranges = plan.get("ranges", [])
    if len(ranges) != len(images):
        raise ValueError("rollback ranges missing")
    for row, image in zip(ranges, images):
        if row.get("id") != image["id"] or any(row.get(k) != v for k, v in sector_range(image["offset"], image["payload_bytes"]).items()):
            raise ValueError("rollback omits a sector touched by the planned write")
        path_text = row.get("private_file", "")
        path = PurePosixPath(path_text)
        if (path.is_absolute() or ".." in path.parts or path.parts[:2] != ("tmp", "e138-addressing")
                or "\\" in path_text or ":" in path_text):
            raise ValueError("rollback path escaped private project workspace")
    protected = report.get("protected_ranges", [])
    if [(r.get("id"), r.get("offset"), r.get("bytes")) for r in protected] != list(PROTECTED_RANGES):
        raise ValueError("protected live ranges changed")
    for row in protected:
        if row.get("end_exclusive") != row["offset"] + row["bytes"]:
            raise ValueError("invalid protected range end")
    sources = report.get("source_evidence", [])
    anchors = {anchor.get("id") for row in sources for anchor in row.get("anchors", [])}
    if (len(sources) != 10 or len({row.get("path") for row in sources}) != 10
            or anchors != {"cache32_kconfig", "mmap_24bit_guard", "runtime_auto_detect", "driver_size_limit",
                           "size_override_default", "conditional_app_override", "bootloader_header_size",
                           "mxic_opi_probe_size_caps", "boot_descriptor_layout", "boot_compile_timestamp", "emote_requires_mmap"}):
        raise ValueError("required source evidence is missing")
    for row in sources:
        path_text = row.get("path", "")
        path = PurePosixPath(path_text)
        if (path.is_absolute() or ".." in path.parts or row.get("origin") not in ("idf", "repo")
                or "\\" in path_text or ":" in path_text
                or path.parts[:1] != (("components",) if row["origin"] == "idf" else ("firmware",))
                or any(type(a.get("start_line")) is not int or a["start_line"] < 1
                       or type(a.get("end_line")) is not int
                       or a["end_line"] - a["start_line"] + 1 != len(a.get("excerpt", "").split("\n"))
                       for a in row.get("anchors", []))):
            raise ValueError("invalid source anchors")
    for container in (*configs, *ranges, *protected, *sources):
        if not isinstance(container.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", container["sha256"]):
            raise ValueError("required evidence digest missing")
    for container in (evidence, *configs, *images, *ranges, *protected, *sources):
        for key, value in container.items():
            if key.endswith("sha256") and (not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value)):
                raise ValueError("invalid evidence digest")
    if report.get("next_step") != "E1.39_isolated_explicit_octal_candidate_and_capacity_preflight":
        raise ValueError("unreviewed next stage")
