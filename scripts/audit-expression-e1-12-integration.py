#!/usr/bin/env python3
"""Audit the isolated E1.12 asset integration and build without touching hardware."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "tmp/expression-e1-12-integration-20261023-run1"
BASELINE = ROOT / "tmp/o2f2-o2d-readonly-20261018/source"
BASELINE_BUILD = ROOT / "tmp/o2f2-o2d-readonly-20261018/build-mapped"
BUILD = STAGE / "build-correct"
ROLLBACK_APP = ROOT / "backups/o2f2-accepted-20261018/body-app-0x10000.bin"
E1_11_REPORT = ROOT / "tmp/expression-e1-11-player-20261023-run1/player-audit-e1-11.json"
E1_10_PACKAGE = ROOT / "artifacts/expression-e1-10-review-20261022/packages/combined-lossless-offline-do-not-flash.bin"
EXPECTED_SOURCE_CHANGES = {
    "main/boards/esp_vocat/assets/emoji_large/insert.eaf": {
        "original": "1d3d2babc0770594f8fdf0701614638e3e03647b18c32674beae3fc3d6459ef1",
        "candidate": "2eb63094840a657a7e6bc5841dbacbfdda809151f344c403dee4255c9743f744",
    },
    "main/boards/esp_vocat/assets/emoji_large/eat.eaf": {
        "original": "df8d5e00c5e5dd9abd618162d19a9bffbb50d6769c4cc09f88c6977b9d8485da",
        "candidate": "2abe739070ca8e3ae0bd437360f94b24a74d2bf0be6a86eb3c33ba223fef5030",
    },
}
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
ROOT_EXCLUDED = {
    "build", "build-correct", "build-temp", "build-temp-correct",
    "sdkconfig.o2f2-build-anchor", "staging-copy.log",
}
PACKER_OUTPUT = Path(
    "managed_components/espressif2022__esp_emote_assets/scripts/spiffs_assets/build/face"
)
ASSET_SIZE_BYTES = 0x5DC000
FLASH_SIZE_BYTES = 16 * 1024 * 1024


def load_script(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load audit helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RESOURCES = load_script(ROOT / "scripts/audit-expression-resources.py", "e1_12_resource_parser")
PACKER = load_script(ROOT / "scripts/audit-expression-package-candidate.py", "e1_12_legacy_packer")
BOUNDARIES = load_script(ROOT / "scripts/audit-expression-e1-3h-boundaries.py", "e1_12_partition_parser")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excluded(rel: Path) -> bool:
    posix = rel.as_posix()
    if len(rel.parts) == 1 and rel.name in ROOT_EXCLUDED:
        return True
    return posix == PACKER_OUTPUT.as_posix() or posix.startswith(PACKER_OUTPUT.as_posix() + "/")


def tree_manifest(root: Path) -> dict[str, str]:
    """Hash logical paths while following source-root junctions and nested links."""
    if not root.is_dir():
        raise ValueError(f"source tree is missing: {root}")
    manifest: dict[str, str] = {}
    stack: list[tuple[Path, Path, frozenset[str]]] = [(root, Path(), frozenset({str(root.resolve()).casefold()}))]
    while stack:
        physical_dir, logical_dir, ancestors = stack.pop()
        try:
            children = list(os.scandir(physical_dir))
        except OSError as error:
            raise ValueError(f"cannot enumerate {physical_dir}: {error}") from error
        for child in children:
            rel = logical_dir / child.name
            if excluded(rel):
                continue
            if child.is_dir(follow_symlinks=True):
                resolved = Path(child.path).resolve()
                key = str(resolved).casefold()
                if key in ancestors:
                    raise ValueError(f"directory link cycle detected at {rel.as_posix()}")
                stack.append((resolved, rel, ancestors | {key}))
            elif child.is_file(follow_symlinks=True):
                manifest[rel.as_posix()] = sha256_file(Path(child.path))
    return manifest


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"missing {label}: {path}")


def partition_checks(build: Path) -> dict[str, Any]:
    table_path = build / "partition_table/partition-table.bin"
    flasher_path = build / "flasher_args.json"
    require_file(table_path, "built partition table")
    require_file(flasher_path, "built flash map")
    raw = table_path.read_bytes()
    partitions = BOUNDARIES.parse_partition_table(raw)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in partitions:
        by_name.setdefault(entry["name"], []).append(entry)
    layout = {
        name: {
            "expected_offset_size": list(expected),
            "actual_offset_size": ([found[0]["offset"], found[0]["size"]]
                                    if len(found := by_name.get(name, [])) == 1 else None),
        }
        for name, expected in EXPECTED_PARTITIONS.items()
    }
    for item in layout.values():
        item["pass"] = item["actual_offset_size"] == item["expected_offset_size"]
    unique_names = all(len(values) == 1 for values in by_name.values())
    ordered = sorted(partitions, key=lambda item: item["offset"])
    non_overlapping = all(left["end"] <= right["offset"] for left, right in zip(ordered, ordered[1:]))
    within_flash = all(0 <= item["offset"] < item["end"] <= FLASH_SIZE_BYTES for item in partitions)
    flash_map_raw = json.loads(flasher_path.read_text(encoding="utf-8"))
    flash_map = {int(offset, 16): filename for offset, filename in flash_map_raw["flash_files"].items()}
    segments: list[dict[str, Any]] = []
    for offset, relative in sorted(flash_map.items()):
        path = build / relative
        require_file(path, f"flash segment {relative}")
        size = path.stat().st_size
        end = offset + size
        segments.append({"offset": offset, "file": relative, "size_bytes": size, "end": end,
                         "within_flash": 0 <= offset < end <= FLASH_SIZE_BYTES,
                         "overlaps_nvs": BOUNDARIES.intervals_overlap((offset, end), (0x9000, 0xD000))})
    assets = by_name.get("assets", [{}])[0]
    app = by_name.get("ota_0", [{}])[0]
    assets_size = (build / "expression_assets.bin").stat().st_size
    app_size = (build / "xiaozhi.bin").stat().st_size
    return {
        "partition_table_sha256": hashlib.sha256(raw).hexdigest(),
        "partition_table": partitions,
        "partition_layout_checks": layout,
        "unique_partition_names": unique_names,
        "partitions_within_16m": within_flash,
        "partitions_non_overlapping": non_overlapping,
        "flash_map_matches_o2f2": flash_map == EXPECTED_FLASH_FILES,
        "flash_segments": segments,
        "app_partition_bytes": app.get("size"),
        "app_size_bytes": app_size,
        "app_fits_partition": app_size <= app.get("size", 0),
        "assets_partition_bytes": assets.get("size"),
        "assets_size_bytes": assets_size,
        "assets_remaining_bytes": assets.get("size", 0) - assets_size,
        "assets_fits_partition": assets_size <= assets.get("size", 0),
        "pass": (unique_names and non_overlapping and within_flash and
                 all(item["pass"] for item in layout.values()) and
                 flash_map == EXPECTED_FLASH_FILES and
                 all(item["within_flash"] and not item["overlaps_nvs"] for item in segments) and
                 app_size <= app.get("size", 0) and assets_size <= assets.get("size", 0)),
    }


def compare_build_artifact(baseline_path: Path, candidate_path: Path, metadata_spans: list[tuple[int, int]]) -> dict[str, Any]:
    """Compare an ESP image while allowing only generated build timestamp/hash fields."""
    baseline_raw, candidate_raw = baseline_path.read_bytes(), candidate_path.read_bytes()
    if len(baseline_raw) != len(candidate_raw):
        return {"baseline_size_bytes": len(baseline_raw), "candidate_size_bytes": len(candidate_raw),
                "unchanged_outside_build_metadata": False, "changed_runs": ["size_changed"]}
    allowed = bytearray(len(baseline_raw))
    for start, end in metadata_spans:
        if not 0 <= start <= end <= len(allowed):
            raise ValueError(f"invalid metadata span {(start, end)} for {baseline_path.name}")
        allowed[start:end] = b"\x01" * (end - start)
    changed = [index for index, (left, right) in enumerate(zip(baseline_raw, candidate_raw))
               if left != right]
    unexpected = [index for index in changed if not allowed[index]]
    return {
        "baseline_size_bytes": len(baseline_raw),
        "candidate_size_bytes": len(candidate_raw),
        "sha256_baseline": hashlib.sha256(baseline_raw).hexdigest(),
        "sha256_candidate": hashlib.sha256(candidate_raw).hexdigest(),
        "changed_byte_count": len(changed),
        "unexpected_changed_byte_count": len(unexpected),
        "unexpected_changed_offsets": unexpected[:32],
        "allowed_metadata_spans": [[start, end] for start, end in metadata_spans],
        "unchanged_outside_build_metadata": not unexpected,
    }


def inspect(stage: Path, baseline: Path, build: Path, baseline_build: Path,
            rollback_app: Path, e1_11_report: Path, e1_10_package: Path) -> dict[str, Any]:
    expected_outputs = [
        "xiaozhi.bin", "xiaozhi.elf", "xiaozhi.map", "expression_assets.bin",
        "partition_table/partition-table.bin", "bootloader/bootloader.bin",
        "ota_data_initial.bin", "srmodels/srmodels.bin", "flasher_args.json", "sdkconfig",
    ]
    for relative in expected_outputs:
        require_file(build / relative, f"build output {relative}")

    original_manifest = tree_manifest(baseline)
    candidate_manifest = tree_manifest(stage)
    paths = sorted(set(original_manifest) | set(candidate_manifest))
    source_changes = [
        {"path": name, "baseline_sha256": original_manifest.get(name),
         "candidate_sha256": candidate_manifest.get(name)}
        for name in paths if original_manifest.get(name) != candidate_manifest.get(name)
    ]
    expected_paths = set(EXPECTED_SOURCE_CHANGES)
    actual_paths = {item["path"] for item in source_changes}
    source_diff_pass = actual_paths == expected_paths
    for change in source_changes:
        expected = EXPECTED_SOURCE_CHANGES.get(change["path"])
        if expected is not None:
            change["expected_baseline_sha256"] = expected["original"]
            change["expected_candidate_sha256"] = expected["candidate"]
            change["expected_hashes_match"] = (
                change["baseline_sha256"] == expected["original"] and
                change["candidate_sha256"] == expected["candidate"]
            )
    if source_diff_pass and not all(item.get("expected_hashes_match") for item in source_changes):
        source_diff_pass = False

    e1_11 = json.loads(e1_11_report.read_text(encoding="utf-8"))
    if e1_11.get("audit_pass") is not True or e1_11.get("device_package_eligible") is not False:
        raise ValueError("E1.11 player result or deployment hold has changed")
    player_pins: list[dict[str, Any]] = []
    source_prefix = "tmp/o2f2-o2d-readonly-20261018/source/"
    for label, anchor in e1_11["player_anchor"]["files"].items():
        anchor_path = anchor["path"].replace("\\", "/")
        if not anchor_path.startswith(source_prefix):
            raise ValueError(f"unexpected E1.11 player path: {anchor_path}")
        relative = anchor_path[len(source_prefix):]
        staged_path = stage / Path(relative)
        require_file(staged_path, f"staged E1.11 player input {label}")
        actual_hash = sha256_file(staged_path)
        player_pins.append({"name": label, "path": relative, "expected_sha256": anchor["sha256"],
                            "staged_sha256": actual_hash, "pass": actual_hash == anchor["sha256"]})
    player_pins_pass = all(item["pass"] for item in player_pins)

    baseline_package_path = baseline_build / "expression_assets.bin"
    candidate_package_path = build / "expression_assets.bin"
    require_file(baseline_package_path, "accepted O2f2 expression package")
    baseline_raw, candidate_raw = baseline_package_path.read_bytes(), candidate_package_path.read_bytes()
    baseline_package = RESOURCES.parse_package_bytes(baseline_raw, modern=False)
    candidate_package = RESOURCES.parse_package_bytes(candidate_raw, modern=False)
    baseline_members, candidate_members = baseline_package["members"], candidate_package["members"]
    candidate_member_changes = sorted(
        name for name in set(baseline_members) | set(candidate_members)
        if baseline_members.get(name) != candidate_members.get(name)
    )
    expected_asset_replacements = {
        "insert.eaf": (stage / "main/boards/esp_vocat/assets/emoji_large/insert.eaf").read_bytes(),
        "eat.eaf": (stage / "main/boards/esp_vocat/assets/emoji_large/eat.eaf").read_bytes(),
    }
    expected_eaf_profiles = {
        "insert.eaf": {"stored_frame_count": 51, "image_frame_count": 50},
        "eat.eaf": {"stored_frame_count": 76, "image_frame_count": 75},
    }
    replacement_hashes = {
        name: {"size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
               "eaf_valid": (
                   (info := RESOURCES.parse_eaf_bytes(raw, fps=20))["dimensions"] == [{"width": 360, "height": 360}] and
                   info["stored_frame_count"] == expected_eaf_profiles[name]["stored_frame_count"] and
                   info["image_frame_count"] == expected_eaf_profiles[name]["image_frame_count"] and
                   info["encodings"] == [0, 1]
               ),
               "package_matches_candidate": candidate_members.get(name) == raw}
        for name, raw in expected_asset_replacements.items()
    }
    package_members_pass = (
        set(baseline_members) == set(candidate_members) and
        candidate_member_changes == ["eat.eaf", "insert.eaf"] and
        all(item["eaf_valid"] and item["package_matches_candidate"] for item in replacement_hashes.values())
    )
    reproduced_candidate = PACKER.build_legacy_package(candidate_members)
    reproducible = reproduced_candidate == candidate_raw
    e1_10_raw = e1_10_package.read_bytes()
    e1_10_match = candidate_raw == e1_10_raw

    partitions = partition_checks(build)
    build_partition_raw = (build / "partition_table/partition-table.bin").read_bytes()
    baseline_artifacts: dict[str, Any] = {}
    for label, relative in {
        "partition_table": "partition_table/partition-table.bin",
        "bootloader": "bootloader/bootloader.bin",
        "otadata": "ota_data_initial.bin",
        "models": "srmodels/srmodels.bin",
        "app": "xiaozhi.bin",
    }.items():
        path = baseline_build / relative
        require_file(path, f"accepted O2f2 {label}")
        candidate_path = build / relative
        if label == "bootloader":
            comparison = compare_build_artifact(path, candidate_path,
                                                [(76, 92), (candidate_path.stat().st_size - 33, candidate_path.stat().st_size)])
        elif label == "app":
            comparison = compare_build_artifact(path, candidate_path,
                                                [(112, 144), (176, 208),
                                                 (candidate_path.stat().st_size - 33, candidate_path.stat().st_size)])
        else:
            comparison = compare_build_artifact(path, candidate_path, [])
        baseline_artifacts[label] = comparison
        baseline_artifacts[label]["unchanged"] = comparison["unchanged_outside_build_metadata"]
    app_path = build / "xiaozhi.bin"
    baseline_app = baseline_build / "xiaozhi.bin"
    require_file(baseline_app, "accepted O2f2 App")
    rollback_hash, baseline_app_hash, candidate_app_hash = (
        sha256_file(rollback_app), sha256_file(baseline_app), sha256_file(app_path)
    )
    baseline_artifacts["app"]["accepted_baseline_sha256"] = baseline_app_hash
    baseline_artifacts["app"]["rollback_sha256"] = rollback_hash
    baseline_artifacts["app"]["candidate_sha256"] = candidate_app_hash
    baseline_artifacts["app"]["rollback_matches_baseline"] = rollback_hash == baseline_app_hash
    baseline_artifacts["app"]["candidate_matches_baseline"] = candidate_app_hash == baseline_app_hash
    baseline_artifacts["partition_table_matches_expected"] = (
        hashlib.sha256(build_partition_raw).hexdigest() ==
        "f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c"
    )
    baseline_artifacts_pass = (
        all(item["unchanged"] for item in baseline_artifacts.values() if isinstance(item, dict) and "unchanged" in item) and
        baseline_artifacts["app"]["rollback_matches_baseline"] and
        baseline_artifacts["partition_table_matches_expected"]
    )
    package_size_pass = len(candidate_raw) <= ASSET_SIZE_BYTES
    app_size_pass = app_path.stat().st_size <= partitions["app_partition_bytes"]

    checks = {
        "expected_source_diff_only": source_diff_pass,
        "pinned_e1_11_player_sources_unchanged": player_pins_pass,
        "asset_package_checksum_and_ranges_valid": True,
        "only_insert_and_eat_members_replaced": package_members_pass,
        "resource_package_reproducible": reproducible,
        "resource_package_matches_e1_10_reviewed_candidate": e1_10_match,
        "resource_package_fits_assets_partition": package_size_pass,
        "partition_and_flash_boundaries_valid": partitions["pass"],
        "rollback_and_protected_segments_match_o2f2": baseline_artifacts_pass,
        "app_fits_app_partition": app_size_pass,
    }
    audit_pass = all(checks.values())
    return {
        "schema": "deskbot.expression_e1_12_integration_build_audit/v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "result": "isolated_asset_integration_build_audit_pass_no_flash" if audit_pass
                  else "isolated_asset_integration_build_audit_hold",
        "audit_pass": audit_pass,
        "device_package_eligible": False,
        "firmware_build_completed": True,
        "hardware_accessed": False,
        "serial_accessed": False,
        "flash_performed": False,
        "erase_performed": False,
        "c61_changed": False,
        "nvs_changed": False,
        "source_trees": {"baseline": str(baseline), "stage": str(stage),
                         "baseline_file_count": len(original_manifest), "stage_file_count": len(candidate_manifest),
                         "excluded_generated_output": PACKER_OUTPUT.as_posix(),
                         "excluded_build_and_stage_metadata": sorted(ROOT_EXCLUDED)},
        "source_changes": source_changes,
        "player_pins": player_pins,
        "package": {
            "baseline_sha256": hashlib.sha256(baseline_raw).hexdigest(),
            "baseline_size_bytes": len(baseline_raw),
            "candidate_sha256": hashlib.sha256(candidate_raw).hexdigest(),
            "candidate_size_bytes": len(candidate_raw),
            "member_count": candidate_package["header"]["member_count"],
            "changed_members": candidate_member_changes,
            "candidate_replacements": replacement_hashes,
            "e1_10_candidate_sha256": hashlib.sha256(e1_10_raw).hexdigest(),
            "matches_e1_10_candidate_byte_for_byte": e1_10_match,
            "rebuilds_byte_for_byte_with_official_packer": reproducible,
            "bytes_saved": len(baseline_raw) - len(candidate_raw),
            "partition_capacity_bytes": ASSET_SIZE_BYTES,
            "partition_remaining_bytes": ASSET_SIZE_BYTES - len(candidate_raw),
            "checksum_valid": candidate_package["header"]["checksum_valid"],
        },
        "partitions": partitions,
        "protected_build_artifacts": baseline_artifacts,
        "build_artifacts": {
            relative: {"size_bytes": (build / relative).stat().st_size,
                       "sha256": sha256_file(build / relative)}
            for relative in expected_outputs if relative not in ("flasher_args.json", "sdkconfig")
        },
        "checks": checks,
        "limits": [
            "This is a host build and byte-level integration audit only; no COM port was opened.",
            "LCD playback, round-circle mask, speech, DOA, petting, UI stability, and C61 behavior remain unverified for this candidate.",
            "device_package_eligible remains false until a separate release lock and user-approved device acceptance gate.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=STAGE)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--build", type=Path, default=BUILD)
    parser.add_argument("--baseline-build", type=Path, default=BASELINE_BUILD)
    parser.add_argument("--rollback-app", type=Path, default=ROLLBACK_APP)
    parser.add_argument("--e1-11-report", type=Path, default=E1_11_REPORT)
    parser.add_argument("--e1-10-package", type=Path, default=E1_10_PACKAGE)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "logs/expression-e1-12-integration-20261023-run1/audit-final.json")
    args = parser.parse_args(argv)
    try:
        output = args.output.resolve()
        if not output.is_relative_to(ROOT.resolve()):
            raise ValueError("audit output must stay inside this project workspace")
        report = inspect(*(path.resolve() for path in (
            args.stage, args.baseline, args.build, args.baseline_build, args.rollback_app,
            args.e1_11_report, args.e1_10_package,
        )))
        encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if output.exists() and output.read_bytes() != encoded:
            raise ValueError(f"refusing to overwrite existing different audit evidence: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            output.write_bytes(encoded)
        print(f"E1.12 audit: {report['result']}")
        print(f"source files: {report['source_trees']['baseline_file_count']} baseline / "
              f"{report['source_trees']['stage_file_count']} staged")
        print(f"candidate package: {report['package']['candidate_size_bytes']} bytes, "
              f"{report['package']['partition_remaining_bytes']} bytes free")
        print(f"report: {output}")
        return 0 if report["audit_pass"] else 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"E1.12 audit failed closed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
