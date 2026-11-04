"""E1.43a deterministic address-test material. No serial or device operations."""
from __future__ import annotations

import hashlib
import struct

from protocol.storage_budget import validate_layout
from protocol.storage_n32r16 import OLD_TABLE_SHA256, PHYSICAL_FLASH_BYTES, PSRAM_BYTES, legacy_rows

PAGE_START = 0x01000000
PAGE_BYTES = 0x10000
SECTOR_BYTES = 0x1000
HEADER = struct.Struct("<8s8I24s")
MAGIC = b"DBUP43A\0"
DOMAIN = b"deskbot.e1.43a.pattern.v1\0"
REFERENCE_SHA256 = "7597eb8e56ae08180f472085ba392bd0d48443f7ef79a281f46403a1fe2ee58e"
PATTERN_SHA256 = "63cd7bb2d9acd763bec3d74f3561ac68572f3f45fdfc8c4fc6513d7f6048df22"
MANIFEST_SCHEMA = "deskbot.upper_pattern.manifest.v1"
REPORT_SCHEMA = "deskbot.upper_pattern.offline.v1"
SAFETY = {name: False for name in (
    "serial_accessed", "device_written", "device_reset", "c61_accessed",
    "nvs_targeted", "firmware_source_changed", "deployment_eligible", "hardware_accepted",
)}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_pattern() -> bytes:
    """Sixteen independently marked sectors; SHA256 counter stream, no randomness."""
    blocks = []
    for index in range(PAGE_BYTES // SECTOR_BYTES):
        relative = index * SECTOR_BYTES
        header = HEADER.pack(MAGIC, 1, PAGE_START, PAGE_BYTES, index,
                             PAGE_START + relative, relative, SECTOR_BYTES,
                             SECTOR_BYTES - HEADER.size, hashlib.sha256(DOMAIN).digest()[:24])
        payload = b"".join(hashlib.sha256(DOMAIN + struct.pack("<III", PAGE_START, index, counter)).digest()
                           for counter in range((SECTOR_BYTES - HEADER.size) // 32))
        blocks.append(header + payload)
    return b"".join(blocks)


def validate_pattern(raw: bytes) -> None:
    if (not isinstance(raw, bytes) or raw != build_pattern() or sha(raw) != PATTERN_SHA256):
        raise ValueError("pattern length, version, marker or deterministic content mismatch")


def manifest_for(raw: bytes) -> dict:
    validate_pattern(raw)
    blocks = []
    for index, relative in enumerate(range(0, PAGE_BYTES, SECTOR_BYTES)):
        block = raw[relative:relative + SECTOR_BYTES]
        blocks.append({"index": index, "relative_offset": relative,
                       "absolute_offset": PAGE_START + relative, "bytes": SECTOR_BYTES,
                       "sha256": sha(block), "header_hex": block[:HEADER.size].hex()})
    return {"schema": MANIFEST_SCHEMA, "edition": "E1.43a", "format_version": 1,
            "purpose": "address_test_pattern_not_eaf", "start": PAGE_START,
            "bytes": PAGE_BYTES, "end_exclusive": PAGE_START + PAGE_BYTES,
            "sector_bytes": SECTOR_BYTES, "header_bytes": HEADER.size,
            "sha256": sha(raw), "algorithm": "sha256_counter_per_sector_v1",
            "blocks": blocks, "deployment_eligible": False}


def validate_manifest(manifest: dict, raw: bytes) -> None:
    # Exact equality also rejects extra deployment flags, altered markers and stale digests.
    if manifest != manifest_for(raw):
        raise ValueError("manifest differs from regenerated deterministic pattern")
    # Python regards True == 1: enforce all JSON numeric field types separately.
    integer_keys = ("format_version", "start", "bytes", "end_exclusive", "sector_bytes", "header_bytes")
    if any(type(manifest[key]) is not int for key in integer_keys):
        raise ValueError("manifest integer fields must not be booleans")
    if manifest["deployment_eligible"] is not False:
        raise ValueError("test material cannot authorize deployment")
    for row in manifest["blocks"]:
        if any(type(row[key]) is not int for key in ("index", "relative_offset", "absolute_offset", "bytes")):
            raise ValueError("block integer fields must not be booleans")


def validate_span(start: int, size: int, capacity: int, rows: list[dict]) -> None:
    if any(type(value) is not int for value in (start, size, capacity)):
        raise ValueError("integer span and capacity required")
    if (capacity != PHYSICAL_FLASH_BYTES or start < 0x9000 or size <= 0
            or start % SECTOR_BYTES or size % SECTOR_BYTES or start + size > capacity):
        raise ValueError("unaligned, reserved or out-of-capacity span")
    validate_layout(rows, capacity)
    if any(start < row["end_exclusive"] and row["start"] < start + size for row in rows):
        raise ValueError("test span overlaps a partition")


def validate_target_context(context: dict, rows: list[dict], *, start: int = PAGE_START,
                            size: int = PAGE_BYTES) -> None:
    """Checks an intended target tuple, NOT current device identity or permission."""
    expected = {"port": "COM4", "chip": "esp32s3", "physical_flash_bytes": PHYSICAL_FLASH_BYTES,
                "psram_bytes": PSRAM_BYTES, "bootloader_header_bytes": PHYSICAL_FLASH_BYTES,
                "driver_flash_bytes": PHYSICAL_FLASH_BYTES}
    if context != expected or any(type(context[key]) is not int for key in expected if key.endswith("bytes")):
        raise ValueError("wrong intended body, capacity or boot/runtime context")
    fields = ("name", "type", "subtype", "start", "size_bytes", "end_exclusive", "flags")
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or any(type(row.get(key)) is not int for row in rows for key in fields if key != "name")):
        raise ValueError("partition fields must use exact integer types")
    validate_span(start, size, PHYSICAL_FLASH_BYTES, rows)
    if start != PAGE_START or size != PAGE_BYTES or rows != legacy_rows():
        raise ValueError("only the fixed page and reviewed legacy layout are allowed")


def parse_legacy_table(sector: bytes) -> list[dict]:
    """Check locked IDF table bytes, MD5 record, sector padding and decoded geometry."""
    if len(sector) != SECTOR_BYTES or sha(sector[:0xC00]) != OLD_TABLE_SHA256:
        raise ValueError("partition-table reference digest mismatch")
    result = []
    entry = struct.Struct("<HBBII16sI")
    cursor = 0
    for expected in legacy_rows():
        magic, ptype, subtype, start, size, label, flags = entry.unpack_from(sector, cursor)
        row = {"name": label.rstrip(b"\0").decode("ascii"), "type": ptype, "subtype": subtype,
               "start": start, "size_bytes": size, "end_exclusive": start + size, "flags": flags}
        if magic != 0x50AA or row != expected:
            raise ValueError("partition entries differ from reviewed layout")
        result.append(row)
        cursor += entry.size
    md5_record = b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(sector[:cursor]).digest()
    if sector[cursor:cursor + 32] != md5_record or any(value != 0xFF for value in sector[cursor + 32:]):
        raise ValueError("partition MD5 record or sector padding mismatch")
    return result


def offline_report(raw: bytes, manifest: dict, reference: bytes) -> dict:
    validate_manifest(manifest, raw)
    if len(reference) != PHYSICAL_FLASH_BYTES or sha(reference) != REFERENCE_SHA256:
        raise ValueError("historical E1.40 full reference missing or changed; not a fresh backup")
    table = reference[0x8000:0x9000]
    rows = parse_legacy_table(table)
    context = {"port": "COM4", "chip": "esp32s3", "physical_flash_bytes": PHYSICAL_FLASH_BYTES,
               "psram_bytes": PSRAM_BYTES, "bootloader_header_bytes": PHYSICAL_FLASH_BYTES,
               "driver_flash_bytes": PHYSICAL_FLASH_BYTES}
    validate_target_context(context, rows)
    low = reference[:PAGE_BYTES]
    upper = reference[PAGE_START:PAGE_START + PAGE_BYTES]
    digests = [row["sha256"] for row in manifest["blocks"]]
    low_different = all(raw[offset:offset + SECTOR_BYTES] != low[offset:offset + SECTOR_BYTES]
                        for offset in range(0, PAGE_BYTES, SECTOR_BYTES))
    if len(set(digests)) != 16 or not low_different or raw == b"\xff" * PAGE_BYTES or raw == upper:
        raise ValueError("pattern is not distinctive from sectors, low alias, erased or reference upper page")
    return {"schema": REPORT_SCHEMA, "edition": "E1.43a", "result": "offline_material_ready_device_gates_pending",
            "pattern": manifest, "safety": dict(SAFETY), "intended_target": context,
            "reference": {"edition": "E1.40", "role": "historical_only_not_current_backup",
                          "bytes": len(reference), "sha256": sha(reference),
                          "partition_table_sha256": sha(table[:0xC00]),
                          "low_alias_start": 0, "low_alias_bytes": PAGE_BYTES, "low_alias_sha256": sha(low),
                          "target_original_sha256": sha(upper)},
            "checks": {"fixed_page": True, "sector_aligned": True, "partition_overlap_absent": True,
                       "distinct_sector_count": len(set(digests)), "each_sector_differs_from_low_alias": low_different,
                       "differs_from_erased_page": True, "differs_from_reference_upper_page": True},
            "pending_gates": ["restored_body_physical_regression", "fresh_private_usb_identity",
                              "fresh_full_32m_backup_and_independent_verify", "fresh_protected_hashes_and_table",
                              "exact_page_and_app_rollback", "dedicated_readonly_cpu_probe_build",
                              "bounded_page_write_and_readback", "cpu_sha256_markers_and_alias_check",
                              "exact_restore_and_physical_regression"]}
