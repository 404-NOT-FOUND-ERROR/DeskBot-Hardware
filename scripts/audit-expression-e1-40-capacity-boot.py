#!/usr/bin/env python3
"""Prepare the E1.40 paired-capacity boot preflight from a fresh readback.

The entrypoint is intentionally read-only with respect to the device.  It
consumes a 32 MiB COM4 readback already made with ``read_flash`` and the
reviewed E1.39 32 MiB bootloader/App pair, then emits a report, a minimal
two-image manifest, and exact sector rollback files.  It never invokes
``write_flash``, erase commands, or COM8.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_capacity_boot import (  # noqa: E402
    APP_OFFSET,
    APP_PARTITION_BYTES,
    CANDIDATE_APP_BYTES,
    CANDIDATE_APP_SHA256,
    CANDIDATE_BOOTLOADER_BYTES,
    CANDIDATE_BOOTLOADER_SHA256,
    LEGACY_CONFIGURED_FLASH_BYTES,
    NO_WRITE_SAFETY,
    PHYSICAL_FLASH_BYTES,
    candidate_facts,
    current_facts,
    parse_identity,
    read_log_is_safe,
    sha,
    write_rollback_files,
)

EDITION = "E1.40"
DATE = "2026-10-30"
WORK_DIR = "tmp/e140-capacity-boot-20261030"
PUBLIC_DIR = ROOT / "artifacts/expression-capacity-boot-e1-40-20261030"
WORK = ROOT / WORK_DIR
CURRENT = WORK / "current-full-32MiB.bin"
READ_LOG = WORK / "read-current.log"
FLASH_ID_LOG = WORK / "flash-id.log"
CHIP_ID_LOG = WORK / "chip-id.log"
CANDIDATE_ROOT = ROOT / "tmp/e139-octal-20261030/outputs/32m"
CANDIDATE_BOOT = CANDIDATE_ROOT / "bootloader/bootloader.bin"
CANDIDATE_APP = CANDIDATE_ROOT / "xiaozhi.bin"


def file_hash(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": path.as_posix().replace(ROOT.as_posix() + "/", ""),
            "bytes": len(raw), "sha256": sha(raw)}


def log_hash(path: Path) -> dict[str, Any]:
    return file_hash(path)


def image_summary(facts: dict[str, Any]) -> dict[str, Any]:
    return {key: facts[key] for key in (
        "bytes", "sha256", "configured_flash_bytes", "flash_mode",
        "flash_frequency_code", "segments", "checksum_offset",
        "checksum_verified", "appended_sha256_verified")}


def relative(path: Path) -> str:
    return path.as_posix().replace(ROOT.as_posix() + "/", "")


def build_report(*, create_rollback: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    required = (CURRENT, READ_LOG, FLASH_ID_LOG, CHIP_ID_LOG, CANDIDATE_BOOT, CANDIDATE_APP)
    missing = [relative(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing E1.40 preflight input: " + ", ".join(missing))

    raw = CURRENT.read_bytes()
    read_log = READ_LOG.read_text(encoding="utf-8", errors="replace")
    flash_log = FLASH_ID_LOG.read_text(encoding="utf-8", errors="replace")
    chip_log = CHIP_ID_LOG.read_text(encoding="utf-8", errors="replace")
    if not read_log_is_safe(read_log, CURRENT.name):
        raise ValueError("current readback log is not a successful read-only 32 MiB operation")
    identity = parse_identity(flash_log, chip_log)
    public_identity = {key: value for key, value in identity.items() if key != "mac"}
    public_identity["device_identity_verified_locally"] = True
    public_identity["device_identity_value_published"] = False
    current = current_facts(raw)
    boot_bytes, app_bytes = CANDIDATE_BOOT.read_bytes(), CANDIDATE_APP.read_bytes()
    candidate = candidate_facts(boot_bytes, app_bytes)
    rollback = write_rollback_files(raw, ROOT, WORK_DIR, create_missing=create_rollback)

    if (candidate["bootloader"]["bytes"] != CANDIDATE_BOOTLOADER_BYTES
            or candidate["app"]["bytes"] != CANDIDATE_APP_BYTES):
        raise ValueError("candidate size changed after validation")

    current_ranges = {row["id"]: row for row in current["ranges"]}
    protected_ids = ("partition_table", "nvs", "otadata", "phy_init", "assets", "model", "upper_half")
    protected = [current_ranges[item] for item in protected_ids]
    erase_spans = {row["id"]: {key: row[key] for key in ("offset", "bytes", "end_exclusive")}
                   for row in rollback}

    report = {
        "schema": "deskbot.n32r16.capacity_boot_preflight.v1",
        "edition": EDITION,
        "date": DATE,
        "result": "fresh_readback_and_paired_capacity_preflight_complete_flash_pending",
        "hardware": public_identity,
        "current_snapshot": {
            "path": relative(CURRENT),
            "read_log": relative(READ_LOG),
            "read_log_sha256": sha(READ_LOG.read_bytes()),
            "whole_device_bytes": current["bytes"],
            "whole_device_sha256": current["sha256"],
            "headers": {
                "bootloader_configured_flash_bytes": LEGACY_CONFIGURED_FLASH_BYTES,
                "app_configured_flash_bytes": LEGACY_CONFIGURED_FLASH_BYTES,
            },
            "bootloader": current["bootloader"],
            "app": current["app"],
            "baseline_checks": current["checks"],
            "ranges": current["ranges"],
        },
        "identity_logs": {
            "flash_id": log_hash(FLASH_ID_LOG),
            "chip_id": log_hash(CHIP_ID_LOG),
            "flash_id_read_only": True,
            "chip_id_read_only": True,
        },
        "candidate": {
            "source": "E1.39 explicit Octal 32 MiB build",
            "bootloader": {"path": relative(CANDIDATE_BOOT), **image_summary(candidate["bootloader"])},
            "app": {"path": relative(CANDIDATE_APP), **image_summary(candidate["app"])},
            "formal_partition_unchanged": True,
            "resources_unchanged": True,
            "model_unchanged": True,
        },
        "write_plan": {
            "requires_explicit_user_confirmation": True,
            "no_full_flash_map": True,
            "images": [
                {"id": "bootloader", "offset": 0, "payload_bytes": CANDIDATE_BOOTLOADER_BYTES,
                 "erase_span": erase_spans["bootloader"], "file": relative(CANDIDATE_BOOT)},
                {"id": "app", "offset": APP_OFFSET, "payload_bytes": CANDIDATE_APP_BYTES,
                 "erase_span": erase_spans["app"], "file": relative(CANDIDATE_APP)},
            ],
            "esptool_flash_args": ["--chip", "esp32s3", "--before", "default_reset",
                                    "--after", "hard_reset", "write_flash", "--flash_mode", "dout",
                                    "--flash_size", "32MB", "--flash_freq", "80m", "0x0",
                                    relative(CANDIDATE_BOOT), "0x10000", relative(CANDIDATE_APP)],
            "excluded": ["0x8000 partition table", "0x9000 NVS", "0xD000 OTA data",
                         "0x5EC000 assets", "0xBC8000 speech model", "COM8/C61"],
        },
        "rollback": {
            "source_snapshot_sha256": current["sha256"],
            "files_verified_against_current_snapshot": all(row["bytes_verified_against_readback"] for row in rollback),
            "ranges": rollback,
            "prewrite_identity_refresh_required": True,
            "flash_script_generated": False,
        },
        "post_preflight_state": {
            "rom_download_mode_reset_requested": True,
            "reset_method": "esptool hard_reset via RTS after read-only chip_id",
            "flash_write_performed": False,
            "normal_boot_log_captured": False,
        },
        "protected_ranges": protected,
        "safety": NO_WRITE_SAFETY,
        "deployment_eligible": False,
        "flash_authorized": False,
        "runtime_verified": False,
        "hardware_accepted": False,
        "next_gate": "explicit_user_confirmation_then_paired_write_readback_and_boot_log",
    }

    manifest = {
        "schema": "deskbot.n32r16.capacity_boot_manifest.v1",
        "edition": EDITION,
        "date": DATE,
        "result": "read_only_paired_capacity_write_plan_ready",
        "deployment_eligible": False,
        "flash_authorized": False,
        "device_written": False,
        "target": {"chip": "esp32s3", "port": "COM4", "device_identity_verified_locally": True,
                   "base_target": "C61 unchanged", "physical_flash_bytes": PHYSICAL_FLASH_BYTES},
        "current_snapshot": {"path": relative(CURRENT), "bytes": current["bytes"],
                              "sha256": current["sha256"], "app_sha256": current["app"]["sha256"],
                              "assets_sha256": current_ranges["assets"]["sha256"],
                              "model_sha256": current_ranges["model"]["sha256"]},
        "images": report["write_plan"]["images"],
        "protected_ranges": protected,
        "rollback": report["rollback"],
        "acceptance": [
            "read back both actual erase spans and compare hashes",
            "verify partition table, NVS, OTA data, assets, model and upper-half protection",
            "collect normal boot log and confirm 32 MiB OPI_STR runtime initialization",
            "run display/menu, conversation, touch, sleep/wake, DOA and resource/model regression",
            "only then mark deployment accepted",
        ],
    }
    return report, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify existing E1.40 JSON without rewriting it")
    args = parser.parse_args()
    expected_report, expected_manifest = build_report(create_rollback=not args.check)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    report_path = PUBLIC_DIR / "report.json"
    manifest_path = PUBLIC_DIR / "deployment-manifest.json"
    if args.check:
        if json.loads(report_path.read_text(encoding="utf-8")) != expected_report:
            raise ValueError("E1.40 report is stale")
        if json.loads(manifest_path.read_text(encoding="utf-8")) != expected_manifest:
            raise ValueError("E1.40 manifest is stale")
    else:
        report_path.write_text(json.dumps(expected_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_path.write_text(json.dumps(expected_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("E1.40 read-only paired-capacity preflight passed; no Flash write or reset was performed by this script.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print("E1.40 preflight blocked: " + str(error), file=sys.stderr)
        raise SystemExit(2)
