#!/usr/bin/env python3
"""Read-only E1.3h preflight for the frozen O2f2 flash layout.

This checks the existing accepted build and rollback artifacts against the
E1.3g in-memory resource-package result. It does not build, emit a candidate
asset package, open a serial port, or flash a device.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_E1_3G = ROOT / "tmp/expression-e1-3g-20261021-run2/e1-3g-report.json"
DEFAULT_BUILD = ROOT / "tmp/o2f2-o2d-readonly-20261018/build-mapped"
DEFAULT_APP_BACKUP = ROOT / "backups/o2f2-accepted-20261018/body-app-0x10000.bin"
DEFAULT_OUTPUT = ROOT / "tmp/expression-e1-3h-20261021-readonly/segment-boundary-report.json"
FLASH_BYTES = 16 * 1024 * 1024

EXPECTED_PARTITIONS = {
    "nvs": (0x9000, 0x4000),
    "otadata": (0xD000, 0x2000),
    "ota_0": (0x10000, 0x5DC000),
    "assets": (0x5EC000, 0x5DC000),
    "model": (0xBC8000, 0xC8000),
}

EXPECTED_FLASH_FILES = {
    0x0: "bootloader/bootloader.bin",
    0x8000: "partition_table/partition-table.bin",
    0xD000: "ota_data_initial.bin",
    0x10000: "xiaozhi.bin",
    0x5EC000: "expression_assets.bin",
    0xBC8000: "srmodels/srmodels.bin",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_partition_table(raw: bytes) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for offset in range(0, len(raw) - 31, 32):
        if raw[offset:offset + 2] != b"\xAA\x50":
            break
        part_type, subtype = raw[offset + 2], raw[offset + 3]
        part_offset, size = struct.unpack_from("<II", raw, offset + 4)
        name = raw[offset + 12:offset + 28].split(b"\0", 1)[0].decode("ascii")
        entries.append({
            "name": name,
            "type": part_type,
            "subtype": subtype,
            "offset": part_offset,
            "size": size,
            "end": part_offset + size,
        })
    if not entries:
        raise ValueError("partition table contains no valid 0xAA50 entries")
    return entries


def intervals_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def inspect(build: Path, app_backup: Path, e1_3g_path: Path) -> dict[str, Any]:
    e1_3g = json.loads(e1_3g_path.read_text(encoding="utf-8"))
    if e1_3g.get("schema") != "deskbot.expression_e1_3g_package_layout_audit/v1":
        raise ValueError("E1.3g report schema is not recognized")
    if e1_3g.get("result") != "offline_actual_4bit_layout_audit_pass_no_flash_candidate":
        raise ValueError("E1.3g renderer/package gate did not pass")
    if e1_3g.get("device_package_eligible") is not False:
        raise ValueError("E1.3g device eligibility must remain false")

    required_paths = {
        "partition_table": build / "partition_table/partition-table.bin",
        "app": build / "xiaozhi.bin",
        "assets": build / "expression_assets.bin",
        "models": build / "srmodels/srmodels.bin",
        "otadata": build / "ota_data_initial.bin",
        "flasher_args": build / "flasher_args.json",
    }
    for name, path in required_paths.items():
        if not path.is_file():
            raise ValueError(f"missing frozen O2f2 artifact {name}: {path}")
    if not app_backup.is_file():
        raise ValueError(f"missing accepted O2f2 App rollback: {app_backup}")

    partition_raw = required_paths["partition_table"].read_bytes()
    partitions = parse_partition_table(partition_raw)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in partitions:
        by_name.setdefault(entry["name"], []).append(entry)

    layout_checks: dict[str, Any] = {}
    for name, expected in EXPECTED_PARTITIONS.items():
        found = by_name.get(name, [])
        actual = (found[0]["offset"], found[0]["size"]) if len(found) == 1 else None
        layout_checks[name] = {
            "expected_offset_size": list(expected),
            "actual_offset_size": list(actual) if actual is not None else None,
            "pass": actual == expected,
        }
    unique_names = all(len(items) == 1 for items in by_name.values())
    within_flash = all(0 <= item["offset"] < item["end"] <= FLASH_BYTES for item in partitions)
    ordered = sorted(partitions, key=lambda item: item["offset"])
    no_partition_overlap = all(
        left["end"] <= right["offset"] for left, right in zip(ordered, ordered[1:])
    )

    flasher = json.loads(required_paths["flasher_args"].read_text(encoding="utf-8"))
    flash_files = {int(offset, 16): filename for offset, filename in flasher["flash_files"].items()}
    flash_mapping_pass = flash_files == EXPECTED_FLASH_FILES
    segments: list[dict[str, Any]] = []
    for offset, relative in sorted(flash_files.items()):
        segment = build / relative
        if not segment.is_file():
            raise ValueError(f"missing file in frozen O2f2 flash mapping: {segment}")
        size = segment.stat().st_size
        end = offset + size
        segments.append({
            "offset": offset,
            "file": relative,
            "size_bytes": size,
            "end": end,
            "within_16m_flash": 0 <= offset < end <= FLASH_BYTES,
            "overlaps_nvs": intervals_overlap((offset, end), (0x9000, 0xD000)),
        })

    app_hash = sha256_file(required_paths["app"])
    app_backup_hash = sha256_file(app_backup)
    official_assets_hash = sha256_file(required_paths["assets"])
    partition_map = by_name
    app_partition = partition_map.get("ota_0", [{}])[0]
    asset_partition = partition_map.get("assets", [{}])[0]
    candidate_size = e1_3g["package"]["candidate_package_size_bytes"]
    candidate_hash = e1_3g["package"]["candidate_package_sha256"]
    package_checks = {
        "official_asset_hash_matches_e1_3g_anchor": (
            official_assets_hash == e1_3g["package"]["official_sha256"]
        ),
        "accepted_app_hash_matches_o2f2_build": app_hash == app_backup_hash,
        "app_fits_partition": required_paths["app"].stat().st_size <= app_partition.get("size", 0),
        "candidate_package_fits_assets_partition": candidate_size <= asset_partition.get("size", 0),
        "candidate_package_remaining_bytes": asset_partition.get("size", 0) - candidate_size,
        "candidate_package_hash_in_e1_3g_report": candidate_hash,
        "candidate_package_materialized_to_disk": False,
    }
    partition_checks_pass = (
        unique_names and within_flash and no_partition_overlap and
        all(item["pass"] for item in layout_checks.values())
    )
    segments_pass = (
        flash_mapping_pass and all(item["within_16m_flash"] and not item["overlaps_nvs"]
                                   for item in segments)
    )
    assets_mapping_matches = (
        flash_files.get(0x5EC000) == EXPECTED_FLASH_FILES[0x5EC000] and
        asset_partition.get("offset") == 0x5EC000
    )
    static_pass = (
        partition_checks_pass and segments_pass and assets_mapping_matches and
        package_checks["official_asset_hash_matches_e1_3g_anchor"] and
        package_checks["accepted_app_hash_matches_o2f2_build"] and
        package_checks["app_fits_partition"] and
        package_checks["candidate_package_fits_assets_partition"]
    )
    return {
        "schema": "deskbot.expression_e1_3h_boundary_preflight/v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "result": "static_partition_boundary_preflight_pass_candidate_build_pending" if static_pass
                  else "static_partition_boundary_preflight_failed",
        "build_performed": False,
        "candidate_package_generated": False,
        "flash_performed": False,
        "hardware_accessed": False,
        "paths": {"build": str(build), "app_backup": str(app_backup), "e1_3g_report": str(e1_3g_path)},
        "partition_table_sha256": hashlib.sha256(partition_raw).hexdigest(),
        "partition_table": partitions,
        "partition_layout_checks": layout_checks,
        "partition_unique_names": unique_names,
        "partitions_within_16m_flash": within_flash,
        "partition_non_overlap": no_partition_overlap,
        "flash_mapping_matches_o2f2": flash_mapping_pass,
        "flash_segments": segments,
        "assets_offset_matches_partition": assets_mapping_matches,
        "package_checks": package_checks,
        "gate": {
            "partition_layout_pass": partition_checks_pass,
            "flash_segments_pass": segments_pass,
            "baseline_hashes_pass": (
                package_checks["official_asset_hash_matches_e1_3g_anchor"] and
                package_checks["accepted_app_hash_matches_o2f2_build"]
            ),
            "candidate_size_pass": package_checks["candidate_package_fits_assets_partition"],
            "static_preflight_pass": static_pass,
            "candidate_firmware_build_complete": False,
            "device_package_eligible": False,
        },
        "limitations": [
            "This verifies existing O2f2 artifacts and the E1.3g in-memory package report; it is not a candidate build.",
            "The candidate package has not been written to disk or integrated into flasher_args.json.",
            "The E1.3g physical LCD circle mask and target playback timing remain unverified.",
        ],
    }


def safe_output(path: Path) -> Path:
    resolved = path.resolve()
    tmp_root = (ROOT / "tmp").resolve()
    if resolved == tmp_root or not resolved.is_relative_to(tmp_root):
        raise ValueError("E1.3h report output must be inside the project tmp directory")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--app-backup", type=Path, default=DEFAULT_APP_BACKUP)
    parser.add_argument("--e1-3g-report", type=Path, default=DEFAULT_E1_3G)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        output = safe_output(args.output)
        report = inspect(args.build.resolve(), args.app_backup.resolve(), args.e1_3g_report.resolve())
        encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if output.exists() and output.read_bytes() != encoded:
            raise ValueError(f"refusing to overwrite different evidence: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            output.write_bytes(encoded)
        print(json.dumps({"result": report["result"], "static_preflight_pass": report["gate"]["static_preflight_pass"],
                          "candidate_firmware_build_complete": False, "report": str(output)},
                         ensure_ascii=False, indent=2))
        return 0 if report["gate"]["static_preflight_pass"] else 2
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"result": "rejected", "error": str(error)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
