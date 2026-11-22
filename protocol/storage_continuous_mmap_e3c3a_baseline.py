"""Verify the current E3c-3a App against the migrated 8 MiB layout."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from protocol import storage_continuous_mmap_baseline as migrated
from protocol import storage_upper_migration_transaction as migration
from protocol.storage_continuous_mmap_experiment import (
    APP_BYTES, APP_START, FLASH_BYTES, NVS_END, NVS_START,
)
from protocol.storage_upper_migration_preflight import (
    IMAGE_SHA256, LIVE_MODEL_SHA256, UPPER_BYTES, UPPER_START,
)

ROOT = Path(__file__).resolve().parents[1]
BUILD_REPORT = Path("artifacts/ui-menu-regression-20261106/e3c3a-build.json")
BUILD_REPORT_SHA256 = "0839fb2d252145b2f264715bf81b12d6d335127270c8ea3279aab3b13415eb10"
BUILD_BASE = Path("tmp/e3c3a-usb-path-20261106")
APP_BYTES_EXPECTED = 4_489_232
APP_SHA256_EXPECTED = "e34d29e96bfb68eadda974b1f0a48350551a9b4d669cbf52e58db54b205d6dc3"
APP_ELF_SHA256_EXPECTED = "cfe8c4b4700e0b0a51c0c7a881988af11d0d7084cfa05931a0af5f97c9618ca5"
MINIMUM_APP_HEADROOM = 1024 * 1024
SOURCE_DELTA = ["main/boards/esp_vocat/studio_usb_diagnostic.cc"]
BUILD_ARTIFACTS = {
    "xiaozhi.bin", "bootloader/bootloader.bin", "partition_table/partition-table.bin",
    "srmodels/srmodels.bin", "ota_data_initial.bin", "sdkconfig", "flasher_args.json",
    "config/sdkconfig.json", "bootloader/config/sdkconfig.json",
}
PROTECTED_ARTIFACTS = {
    "bootloader/bootloader.bin", "partition_table/partition-table.bin",
    "srmodels/srmodels.bin", "ota_data_initial.bin",
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _read_repo_file(root: Path, relative: Path) -> bytes:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("build evidence path is outside the repository")
    base = root.resolve(strict=True)
    path = base
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("build evidence must not use symlinks")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ValueError("build evidence file is missing or outside the repository")
    return resolved.read_bytes()


def _record_matches(record: object, raw: bytes, label: str) -> None:
    if (not isinstance(record, dict) or type(record.get("bytes")) is not int
            or record["bytes"] != len(raw) or not _valid_sha(record.get("sha256"))
            or record["sha256"] != sha256(raw)):
        raise ValueError("E3c-3a build evidence differs: " + label)


def load_e3c3a_build_reference(root: Path = ROOT) -> dict:
    """Verify the retained E3c-3a query-on report and its local build outputs."""
    root = Path(root)
    report_raw = _read_repo_file(root, BUILD_REPORT)
    if sha256(report_raw) != BUILD_REPORT_SHA256:
        raise ValueError("E3c-3a build report digest differs")
    try:
        report = json.loads(report_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("E3c-3a build report is invalid JSON") from error
    if not isinstance(report, dict):
        raise ValueError("E3c-3a build report must be a JSON object")
    target = report.get("target")
    if not isinstance(target, dict):
        raise ValueError("E3c-3a build target must be an object")
    assets = target.get("assets")
    if not isinstance(assets, dict):
        raise ValueError("E3c-3a asset target must be an object")
    if (report.get("schema") != "deskbot.usb_path_build/v1"
            or report.get("edition") != "E3c-3a"
            or report.get("result") != "off_on_builds_verified"
            or report.get("reference") != "retained_E3c-3"
            or report.get("source_delta") != SOURCE_DELTA
            or target.get("chip") != "esp32s3"
            or target.get("flash_bytes") != FLASH_BYTES
            or target.get("layout") != "upper-assets-8m"
            or target.get("board") != "esp_vocat"
            or target.get("psram_bytes") != 16 * 1024 * 1024
            or target.get("flash_memory_interface") != "opi"
            or assets.get("offset") != UPPER_START
            or assets.get("bytes") != UPPER_BYTES):
        raise ValueError("E3c-3a build report identity or layout differs")

    rows = report.get("builds")
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("E3c-3a default-off and query-on builds are required")
    by_query = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get("query_enabled")) is not bool:
            raise ValueError("invalid E3c-3a build row")
        enabled = row["query_enabled"]
        if enabled in by_query:
            raise ValueError("duplicate E3c-3a build configuration")
        by_query[enabled] = row
    if set(by_query) != {False, True}:
        raise ValueError("E3c-3a default-off and query-on builds are required")

    inspected = {}
    for enabled, label in ((False, "off"), (True, "on")):
        row = by_query[enabled]
        expected_dir = (BUILD_BASE / label).as_posix()
        artifacts = row.get("artifacts")
        protected = row.get("protected_equal")
        app = artifacts.get("xiaozhi.bin") if isinstance(artifacts, dict) else None
        if (row.get("build_directory") != expected_dir
                or row.get("config_equal_reference") is not True
                or not isinstance(protected, dict)
                or set(protected) != PROTECTED_ARTIFACTS
                or not all(value is True for value in protected.values())
                or not isinstance(artifacts, dict) or set(artifacts) != BUILD_ARTIFACTS
                or type(row.get("app_headroom_bytes")) is not int
                or row["app_headroom_bytes"] < MINIMUM_APP_HEADROOM
                or not isinstance(app, dict)):
            raise ValueError("E3c-3a build configuration or protected outputs differ")

        output_records = {}
        for name, record in artifacts.items():
            try:
                raw = _read_repo_file(root, Path(expected_dir) / name)
            except (OSError, ValueError) as error:
                raise ValueError("E3c-3a build output is unavailable: " + name) from error
            _record_matches(record, raw, label + "/" + name)
            output_records[name] = record
            if name == "xiaozhi.bin":
                app_raw = raw
        calculated_headroom = APP_BYTES - len(app_raw)
        if row["app_headroom_bytes"] != calculated_headroom:
            raise ValueError("E3c-3a App headroom report differs")
        inspected[enabled] = {"artifacts": output_records, "app_raw": app_raw, "row": row}

    off = inspected[False]
    on = inspected[True]
    for name in PROTECTED_ARTIFACTS:
        if off["artifacts"][name] != on["artifacts"][name]:
            raise ValueError("E3c-3a protected build outputs differ: " + name)
    query_row = on["row"]
    app_raw = on["app_raw"]
    app_record = on["artifacts"]["xiaozhi.bin"]
    elf_sha = query_row.get("app_elf_sha256")
    if (app_record["bytes"] != APP_BYTES_EXPECTED
            or app_record["sha256"] != APP_SHA256_EXPECTED
            or not _valid_sha(elf_sha) or elf_sha != APP_ELF_SHA256_EXPECTED
            or app_raw[176:208].hex() != elf_sha):
        raise ValueError("E3c-3a query-on App or embedded ELF identity differs")
    target_table_sha = target.get("partition_table_sha256")
    if (not _valid_sha(target_table_sha)
            or on["artifacts"]["partition_table/partition-table.bin"]["sha256"] != target_table_sha):
        raise ValueError("E3c-3a partition table build reference differs")

    return {
        "layout": "upper-assets-8m",
        "report": {"path": BUILD_REPORT.as_posix(), "sha256": BUILD_REPORT_SHA256},
        "build_directory": (BUILD_BASE / "on").as_posix(),
        "app": {"offset": APP_START, "bytes": app_record["bytes"],
                "sha256": app_record["sha256"], "elf_sha256": elf_sha},
        "partition_table_sha256": target_table_sha,
        "asset_range": {"offset": UPPER_START, "bytes": UPPER_BYTES,
                         "sha256": IMAGE_SHA256},
        "model_sha256": LIVE_MODEL_SHA256,
    }


def _validate_migration_reference(reference: dict) -> dict[str, dict]:
    if not isinstance(reference, dict) or reference.get("layout") != "upper-assets-8m":
        raise ValueError("E1.43c-3 upper-assets-8m migration reference required")
    identity = reference.get("identity_sha256")
    if not _valid_sha(identity):
        raise ValueError("invalid migrated device identity")
    expected = {name: (offset, size) for name, offset, size in migration.STATIC_GEOMETRY}
    expected.update({
        "partition_table": (migration.TABLE_START, migration.TABLE_BYTES),
        "upper_assets": (UPPER_START, UPPER_BYTES),
    })
    rows = reference.get("protected_ranges")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("complete migrated static reference required")
    by_name = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"name", "offset", "bytes", "sha256"}:
            raise ValueError("invalid migrated protected range")
        name = row["name"]
        if name in by_name or name not in expected:
            raise ValueError("duplicate or unknown migrated protected range")
        if ((row["offset"], row["bytes"]) != expected[name]
                or not _valid_sha(row["sha256"])):
            raise ValueError("migrated protected range geometry differs: " + str(name))
        by_name[name] = row
    if set(by_name) != set(expected):
        raise ValueError("migrated protected ranges are incomplete")
    return by_name


def _validate_e3c3a_snapshot_with_references(
    snapshot: bytes,
    identity_sha256: str,
    migration_reference: dict,
    build_reference: dict,
) -> dict:
    """Validate a snapshot using already verified references."""
    if not isinstance(snapshot, bytes) or len(snapshot) != FLASH_BYTES:
        raise ValueError("complete 32 MiB device snapshot required")
    if not _valid_sha(identity_sha256):
        raise ValueError("invalid observed device identity")
    protected = _validate_migration_reference(migration_reference)
    if identity_sha256 != migration_reference["identity_sha256"]:
        raise ValueError("device identity differs from E1.43c-3 migration reference")

    if not isinstance(build_reference, dict) or build_reference.get("layout") != "upper-assets-8m":
        raise ValueError("verified E3c-3a query-on build reference required")
    app = build_reference.get("app")
    if (not isinstance(app, dict) or set(app) != {"offset", "bytes", "sha256", "elf_sha256"}
            or app.get("offset") != APP_START or type(app.get("bytes")) is not int
            or not 0 < app["bytes"] <= APP_BYTES - MINIMUM_APP_HEADROOM
            or not _valid_sha(app.get("sha256")) or not _valid_sha(app.get("elf_sha256"))):
        raise ValueError("invalid E3c-3a App build reference")
    if (app["bytes"] != APP_BYTES_EXPECTED or app["sha256"] != APP_SHA256_EXPECTED
            or app["elf_sha256"] != APP_ELF_SHA256_EXPECTED):
        raise ValueError("E3c-3a App build reference differs from the accepted version")

    app_end = APP_START + app["bytes"]
    if sha256(snapshot[APP_START:app_end]) != app["sha256"]:
        raise ValueError("current App BIN differs from E3c-3a")
    for name, row in protected.items():
        if name == "app":
            continue
        start, end = row["offset"], row["offset"] + row["bytes"]
        if sha256(snapshot[start:end]) != row["sha256"]:
            raise ValueError("migrated static baseline differs: " + name)

    fixed_digests = {
        "partition_table": migration.TABLE_SECTOR_SHA256,
        "upper_assets": IMAGE_SHA256,
        "model": LIVE_MODEL_SHA256,
    }
    for name, expected in fixed_digests.items():
        if protected[name]["sha256"] != expected:
            raise ValueError("migration reference has unexpected " + name + " digest")

    return {
        "schema": "deskbot.continuous_mmap.e3c3a_baseline.v1",
        "layout": "upper-assets-8m",
        "identity_sha256": identity_sha256,
        "migration_reference": migration_reference.get("report"),
        "migration_plan_id": migration_reference.get("migration_plan_id"),
        "build_reference": build_reference.get("report"),
        "original_app": app,
        "protected_ranges": [dict(row) for name, row in protected.items() if name != "app"],
        "historical_app_range_skipped": True,
        "nvs": {"offset": NVS_START, "bytes": NVS_END - NVS_START,
                "sha256": sha256(snapshot[NVS_START:NVS_END])},
    }


def validate_e3c3a_snapshot(snapshot: bytes, identity_sha256: str) -> dict:
    """Validate one snapshot against the sealed E1.43c-3 and E3c-3a records."""
    if not isinstance(snapshot, bytes) or len(snapshot) != FLASH_BYTES:
        raise ValueError("complete 32 MiB device snapshot required")
    if not _valid_sha(identity_sha256):
        raise ValueError("invalid observed device identity")
    migration_reference = migrated.load_migrated_baseline()
    build_reference = load_e3c3a_build_reference()
    return _validate_e3c3a_snapshot_with_references(
        snapshot, identity_sha256, migration_reference, build_reference)


__all__ = ["BUILD_REPORT", "BUILD_REPORT_SHA256", "load_e3c3a_build_reference",
           "sha256", "validate_e3c3a_snapshot"]
