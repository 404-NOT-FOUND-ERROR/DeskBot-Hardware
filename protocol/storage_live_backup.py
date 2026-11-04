"""Offline validation of E1.35c readbacks; no serial or flash operations."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any

from protocol.storage_migration import (
    E118_OLD_ASSETS_SHA256, E126_APP_PAYLOAD_BYTES, E126_APP_PAYLOAD_SHA256,
    FLASH_BYTES, MODEL_BYTES, MODEL_PAYLOAD_BYTES, MODEL_PAYLOAD_SHA256,
    NEW_MODEL_END, OLD_ASSETS_BYTES, OLD_ASSETS_START, OLD_MODEL_START,
    OLD_TABLE_SHA256, safe_relative_path, validate_manifest,
)

MIB = 1024 * 1024


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def configured_flash_bytes(header: bytes) -> int:
    if len(header) < 4 or header[0] != 0xE9 or header[3] >> 4 not in range(8):
        raise ValueError("invalid ESP image flash-size header")
    return MIB << (header[3] >> 4)


def verify_capture_files(directory: Path, index: dict[str, Any]) -> bytes:
    """Check all chunk files, their successful read logs and assembled image."""
    if (index.get("edition") != "E1.35c" or index.get("chip") != "esp32s3"
            or index.get("flash_written") is not False or index.get("capture_complete") is not True):
        raise ValueError("capture is incomplete or claims flash writes")
    size = index.get("physical_flash_bytes")
    mac = index.get("mac")
    if type(size) is not int or size not in {16 * MIB, 32 * MIB}:
        raise ValueError("unsupported physical Flash size")
    if not isinstance(mac, str) or not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac):
        raise ValueError("device identity is missing")
    records = index.get("chunks")
    if not isinstance(records, list) or not records or any(not isinstance(r, dict) for r in records):
        raise ValueError("capture chunks are missing")
    attempts = index.get("attempts", [])
    if not isinstance(attempts, list) or any(not isinstance(a, dict) for a in attempts):
        raise ValueError("capture attempts are invalid")
    actual: list[bytes] = []
    cursor = 0
    for row in sorted(records, key=lambda r: r.get("offset", -1)):
        if (type(row.get("offset")) is not int or row["offset"] != cursor
                or type(row.get("bytes")) is not int or row["bytes"] != MIB
                or row.get("transport_digest_verified") is not True):
            raise ValueError("capture chunk coverage or digest evidence is invalid")
        path = safe_relative_path(directory, row.get("path", ""))
        raw = path.read_bytes()
        if len(raw) != row["bytes"] or sha(raw) != row.get("sha256"):
            raise ValueError("capture chunk length/hash mismatch")
        log = safe_relative_path(directory, row.get("log", "")).read_text(encoding="utf-8")
        entries = [a for a in attempts if a.get("log") == row["log"]]
        if (len(entries) != 1 or type(entries[0].get("returncode")) is not int
                or entries[0]["returncode"] != 0 or entries[0].get("offset") != cursor
                or entries[0].get("bytes") != MIB
                or set(re.findall(r"MAC: ([0-9a-f:]{17})", log)) != {mac}
                or f"Read {MIB} bytes at {cursor:#010x}" not in log
                or "esptool.py v4.12.0" not in log):
            raise ValueError("successful device read provenance is missing")
        argv = entries[0].get("argv", [])
        suffix = ["--before", "usb_reset", "--after", "no_reset", "read_flash",
                  "--no-progress", "--flash_size", f"{size // MIB}MB", hex(cursor), hex(MIB), str(path)]
        if (not isinstance(argv, list) or any(not isinstance(v, str) for v in argv)
                or len(argv) < len(suffix) or argv[-len(suffix):] != suffix
                or argv.count("read_flash") != 1
                or any(v in argv for v in ("write_flash", "erase_flash", "erase_region"))):
            raise ValueError("capture command is not the expected read-only operation")
        actual.append(raw)
        cursor += MIB
    if cursor != size:
        raise ValueError("capture does not cover the whole detected device")
    full = index.get("full_image", {})
    raw = safe_relative_path(directory, full.get("path", "")).read_bytes()
    if (full.get("bytes") != size or len(raw) != size
            or sha(raw) != full.get("sha256") or raw != b"".join(actual)):
        raise ValueError("assembled readback differs from verified chunks")
    return raw


def require_independent_verify(log: str, mac: str, physical_bytes: int, filename: str) -> None:
    expected = f"Verifying 0x{physical_bytes:x} ({physical_bytes}) bytes @ 0x00000000 in flash against "
    if (set(re.findall(r"MAC: ([0-9a-f:]{17})", log)) != {mac}
            or expected not in log or filename not in log
            or log.count("-- verify OK (digest matched)") != 1
            or "FAILED" in log or "fatal error" in log.lower()
            or "Flash params set" in log or "SHA digest in image updated" in log):
        raise ValueError("independent whole-device verification is missing or failed")


def audit_backup(raw: bytes, manifest: dict[str, Any], repository: Path) -> dict[str, Any]:
    validate_manifest(manifest, repository, require_files=False)
    if len(raw) not in {16 * MIB, 32 * MIB}:
        raise ValueError("readback must cover a supported physical Flash")
    boot_configured = configured_flash_bytes(raw[:4])
    app_configured = configured_flash_bytes(raw[0x10000:0x10004])
    old = manifest["layouts"]["old"]["segments"]
    model = raw[OLD_MODEL_START:OLD_MODEL_START + MODEL_BYTES]
    checks = {
        "old_partition_payload_matches": sha(raw[0x8000:0x8C00]) == OLD_TABLE_SHA256,
        "old_partition_sector_matches": sha(raw[0x8000:0x9000]) == old["partition_table"]["sha256"],
        "accepted_app_payload_matches": sha(raw[0x10000:0x10000 + E126_APP_PAYLOAD_BYTES]) == E126_APP_PAYLOAD_SHA256,
        "accepted_e118_assets_matches": sha(raw[OLD_ASSETS_START:OLD_ASSETS_START + OLD_ASSETS_BYTES]) == E118_OLD_ASSETS_SHA256,
        "model_payload_matches_build_reference": sha(model[:MODEL_PAYLOAD_BYTES]) == MODEL_PAYLOAD_SHA256,
        "model_full_partition_matches_build_reference": sha(model) == old["model"]["sha256"],
        "bootloader_matches_build_reference": sha(raw[:0x8000]) == old["bootloader"]["sha256"],
        "configured_capacity_matches_preflight": boot_configured == app_configured == FLASH_BYTES,
        "physical_capacity_covers_candidate": len(raw) >= FLASH_BYTES >= NEW_MODEL_END,
    }
    ranges = []
    for row in sorted(manifest["live_backup_requirements"]["required_before_write"], key=lambda r: r["offset"]):
        start, length = row["offset"], row["bytes"]
        payload = raw[start:start + length]
        ranges.append({"id": row["id"], "offset": start, "bytes": length,
                       "end_exclusive": start + length, "sha256": sha(payload),
                       "all_erased": payload.count(0xFF) == length})
    baseline_ok = all(checks[k] for k in (
        "old_partition_payload_matches", "old_partition_sector_matches",
        "accepted_app_payload_matches", "accepted_e118_assets_matches",
        "configured_capacity_matches_preflight", "physical_capacity_covers_candidate"))
    return {"checks": checks, "baseline_matches": baseline_ok,
            "required_ranges": ranges, "required_ranges_complete": True,
            "physical_flash_bytes": len(raw), "configured_flash_bytes": app_configured,
            "additional_unconfigured_bytes": len(raw) - app_configured,
            "additional_unconfigured_all_erased": all(b == 0xFF for b in raw[app_configured:]),
            "live_model_restage_required": not checks["model_full_partition_matches_build_reference"],
            "whole_device_sha256": sha(raw),
            "runtime_mmap_verified": False, "physical_interaction_accepted": False,
            "flash_authorized": False, "device_written": False}
