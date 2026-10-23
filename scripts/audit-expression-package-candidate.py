#!/usr/bin/env python3
"""Build and audit an offline E1.3 resource package; never access hardware."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIT_MODULE_PATH = ROOT / "scripts/audit-expression-resources.py"
OFFICIAL_PACKAGE = ROOT / "tmp/remote-esp-vocat-official-build/b-events-v1/expression_assets.bin"
OFFICIAL_SOURCE = ROOT / "tmp/remote-esp-vocat/examples/xiaozhi-esp32"
SHY_EAF = ROOT / "tmp/expression-e1-2-20261020/shy_legacy.eaf"
SHY_REPORT = ROOT / "tmp/expression-e1-2-20261020/candidate-report.json"
OUTPUT = ROOT / "tmp/expression-e1-3-20261020"
EXPECTED_OFFICIAL_PACKAGE_SHA256 = "56b39e7fdd14b9dd0de9b22d3aedc9b04383376dbdef70925675f639d763cd5b"
EXPECTED_SHY_EAF_SHA256 = "4dd57777598dc1517fcf770fd859894567fbaa434373ef604e4d080e9331a5b1"
EXPECTED_SOURCE_COMMIT = "46d427255756fe2a148777ea3f256b615b52e160"
PARTITION_BYTES = 6000 * 1024
NAME_SLOT_BYTES = 32


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("deskbot_expression_resource_audit", AUDIT_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the anchored resource parser")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AUDIT = _load_audit_module()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sort_key(name: str) -> tuple[str, str]:
    path = Path(name)
    return path.suffix, path.stem


def build_legacy_package(members: dict[str, bytes], name_slot_bytes: int = NAME_SLOT_BYTES) -> bytes:
    """Reproduce the official v0.1 legacy packer byte-for-byte."""
    if not 1 <= name_slot_bytes <= 256 or not 1 <= len(members) <= 4096:
        raise ValueError("package name slot or member count is invalid")
    table = bytearray()
    merged = bytearray()
    for name in sorted(members, key=_sort_key):
        encoded_name = name.encode("utf-8")
        if not AUDIT._safe_name(name) or len(encoded_name) > name_slot_bytes:
            raise ValueError(f"unsafe or oversized package member name: {name!r}")
        data = members[name]
        if not data or len(data) > 0xFFFFFFFF or len(merged) > 0xFFFFFFFF:
            raise ValueError(f"invalid or oversized package member: {name}")
        table.extend(encoded_name + bytes(name_slot_bytes - len(encoded_name)))
        table.extend(struct.pack("<IIHH", len(data), len(merged), 0, 0))
        merged.extend(b"ZZ")
        merged.extend(data)

    payload = table + merged
    if len(payload) > 0xFFFFFFFF:
        raise ValueError("package payload exceeds the legacy format limit")
    return struct.pack("<III", len(members), sum(payload) & 0xFFFF, len(payload)) + payload


def deduplicate_eaf_frames(raw: bytes) -> bytes:
    """Losslessly share byte-identical EAF frame payloads through the frame table."""
    parsed = AUDIT.parse_eaf_bytes(raw)
    frame_count = parsed["stored_frame_count"]
    frame_table_end = 16 + frame_count * 8
    payload_end = 16 + struct.unpack_from("<I", raw, 12)[0]
    shared_data = bytearray()
    frame_table = bytearray()
    offsets: dict[bytes, tuple[int, int]] = {}

    for index in range(frame_count):
        size, offset = struct.unpack_from("<II", raw, 16 + index * 8)
        frame = raw[frame_table_end + offset:frame_table_end + offset + size]
        if len(frame) != size or frame_table_end + offset + size > payload_end:
            raise ValueError(f"EAF frame {index} exceeds the declared payload")
        location = offsets.get(frame)
        if location is None:
            location = (len(frame), len(shared_data))
            offsets[frame] = location
            shared_data.extend(frame)
        frame_table.extend(struct.pack("<II", location[0], location[1]))

    payload = frame_table + shared_data
    result = raw[:4] + struct.pack("<III", frame_count, sum(payload) & 0xFFFFFFFF, len(payload)) + payload
    AUDIT.parse_eaf_bytes(result)
    return result


def _eaf_frame_payloads(raw: bytes) -> tuple[bytes, ...]:
    frame_count = struct.unpack_from("<I", raw, 4)[0]
    table_end = 16 + frame_count * 8
    return tuple(
        raw[table_end + offset:table_end + offset + size]
        for size, offset in (struct.unpack_from("<II", raw, 16 + index * 8) for index in range(frame_count))
    )


def _index_semantics(index: dict[str, Any]) -> dict[str, str]:
    entries = index.get("emoji_collection")
    if not isinstance(entries, list):
        raise ValueError("official index.json has no emoji_collection array")
    result: dict[str, str] = {}
    for item in entries:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or not isinstance(item.get("file"), str) or item["name"] in result):
            raise ValueError("official index.json has an invalid or duplicate animation")
        result[item["name"]] = item["file"]
    return result


def _write_immutable(path: Path, raw: bytes) -> None:
    """Allow repeatable identical runs, but never overwrite different evidence."""
    if path.exists():
        if not path.is_file() or sha256_bytes(path.read_bytes()) != sha256_bytes(raw):
            raise ValueError(f"refusing to overwrite different existing evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _replacement_analysis(members: dict[str, bytes], index: dict[str, Any], candidate_size: int,
                          baseline_size: int) -> list[dict[str, Any]]:
    semantics = _index_semantics(index)
    usages: dict[str, list[str]] = {}
    for semantic, file_name in semantics.items():
        usages.setdefault(file_name, []).append(semantic)
    result = []
    for name, data in sorted(members.items(), key=lambda item: item[0].casefold()):
        if not name.endswith(".eaf"):
            continue
        parsed = AUDIT.parse_eaf_bytes(data)
        resulting_size = baseline_size - len(data) + candidate_size
        frees_enough = resulting_size <= PARTITION_BYTES
        used_by = usages.get(name, [])
        if name == "insert.eaf":
            disposition = "reject_protected_base_connection_animation"
        elif name == "eat.eaf":
            disposition = "reject_changes_magnetic_accessory_feedback"
        elif len(used_by) > 1:
            disposition = "reject_shared_semantics"
        elif not frees_enough:
            disposition = "reject_insufficient_capacity"
        else:
            disposition = "capacity_only_candidate_requires_owner_approval"
        result.append({
            "member": name,
            "current_size_bytes": len(data),
            "semantic_ids": used_by,
            "dimensions": parsed["dimensions"],
            "candidate_package_size_bytes": resulting_size,
            "remaining_partition_bytes": PARTITION_BYTES - resulting_size,
            "fits_partition_if_replaced": frees_enough,
            "disposition": disposition,
        })
    return result


def audit_candidate(output: Path = OUTPUT, *, write_outputs: bool = True) -> dict[str, Any]:
    output = output.resolve()
    output.relative_to((ROOT / "tmp").resolve())

    official_raw = OFFICIAL_PACKAGE.read_bytes()
    if sha256_bytes(official_raw) != EXPECTED_OFFICIAL_PACKAGE_SHA256:
        raise ValueError("official package differs from the E1.1 immutable anchor")
    official = AUDIT.parse_package_bytes(official_raw, modern=False)
    if official["header"]["name_slot_bytes"] != NAME_SLOT_BYTES:
        raise ValueError("official package name-slot size changed")
    if build_legacy_package(official["members"]) != official_raw:
        raise ValueError("local packer does not reproduce the official package byte-for-byte")

    source_commit = AUDIT.git_commit(OFFICIAL_SOURCE)
    if source_commit != EXPECTED_SOURCE_COMMIT:
        raise ValueError("official source commit differs from the E1.1 anchor")
    index_original = json.loads(official["members"]["index.json"])
    if not isinstance(index_original, dict):
        raise ValueError("official index.json root must be an object")
    source_semantics = AUDIT.read_json(OFFICIAL_SOURCE / "main/boards/esp_vocat/assets/360_360/emote.json")
    if not isinstance(source_semantics, list):
        raise ValueError("official emote.json must be an array")
    source_map = {item["emote"]: item["src"] for item in source_semantics}
    official_index_map = _index_semantics(index_original)
    if official_index_map != source_map:
        raise ValueError("official package index semantic map differs from the anchored emote.json")

    shy_raw = SHY_EAF.read_bytes()
    if sha256_bytes(shy_raw) != EXPECTED_SHY_EAF_SHA256:
        raise ValueError("shy candidate differs from the visually accepted E1.2 asset")
    shy_report = json.loads(SHY_REPORT.read_text(encoding="utf-8"))
    if shy_report.get("eaf", {}).get("size_bytes") != len(shy_raw):
        raise ValueError("E1.2 report size does not match the EAF candidate")
    shy_info = AUDIT.parse_eaf_bytes(shy_raw, fps=25)
    if (shy_info["image_frame_count"] != 188 or shy_info["dimensions"] != [{"width": 360, "height": 360}]
            or shy_info["encodings"] != [0] or shy_info["preview"]["result"] != "format_supported"):
        raise ValueError("shy candidate no longer meets the audited E1.2 EAF profile")
    if "shy_candidate" in official_index_map or "shy_candidate.eaf" in official["members"]:
        raise ValueError("candidate identity already exists in the official package")

    index_candidate = json.loads(json.dumps(index_original))
    index_candidate["emoji_collection"].append({
        "name": "shy_candidate",
        "file": "shy_candidate.eaf",
        "eaf": {"loop": True, "fps": 25},
    })
    candidate_members = dict(official["members"])
    candidate_members["index.json"] = (json.dumps(index_candidate, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    candidate_members["shy_candidate.eaf"] = shy_raw
    candidate_package = build_legacy_package(candidate_members)
    parsed_candidate = AUDIT.parse_package_bytes(candidate_package, modern=False)
    if parsed_candidate["header"]["member_count"] != official["header"]["member_count"] + 1:
        raise ValueError("candidate package member count did not increase by exactly one")
    if parsed_candidate["members"] != candidate_members:
        raise ValueError("candidate package round-trip changed one or more members")
    for name, raw in official["members"].items():
        if name != "index.json" and parsed_candidate["members"].get(name) != raw:
            raise ValueError(f"official package member changed: {name}")
    if _index_semantics(index_candidate) != official_index_map | {"shy_candidate": "shy_candidate.eaf"}:
        raise ValueError("candidate changed or lost an official semantic binding")

    dedup_raw = deduplicate_eaf_frames(shy_raw)
    if _eaf_frame_payloads(dedup_raw) != _eaf_frame_payloads(shy_raw):
        raise ValueError("lossless EAF optimization changed one or more decoded frame payloads")
    dedup_members = dict(candidate_members)
    dedup_members["shy_candidate.eaf"] = dedup_raw
    dedup_package = build_legacy_package(dedup_members)
    parsed_dedup_package = AUDIT.parse_package_bytes(dedup_package, modern=False)
    if parsed_dedup_package["members"] != dedup_members:
        raise ValueError("lossless candidate package round-trip changed one or more members")
    for name, raw in official["members"].items():
        if name != "index.json" and parsed_dedup_package["members"].get(name) != raw:
            raise ValueError(f"lossless candidate changed official package member: {name}")

    baseline_size = len(official_raw)
    candidate_size = len(candidate_package)
    remaining = PARTITION_BYTES - candidate_size
    dedup_package_size = len(dedup_package)
    dedup_remaining = PARTITION_BYTES - dedup_package_size
    max_candidate_size = PARTITION_BYTES - baseline_size
    replacement = _replacement_analysis(official["members"], index_original, len(shy_raw), baseline_size)
    fitting_replacements = [item for item in replacement if item["fits_partition_if_replaced"]]
    safe_replacements = [item for item in fitting_replacements
                         if item["disposition"] == "capacity_only_candidate_requires_owner_approval"]
    fallback_path = output / "rollback" / "official-expression_assets.bin"
    package_path = output / "candidate" / "append-shy-overflow-do-not-flash.bin"
    dedup_eaf_path = output / "candidate" / "shy-lossless-frame-deduplicated.eaf"
    dedup_package_path = output / "candidate" / "append-shy-deduplicated-overflow-do-not-flash.bin"
    report_path = output / "package-candidate-audit.json"
    compression_report_path = output / "package-compression-audit.json"

    report = {
        "schema": "deskbot.expression_package_candidate_audit/v1",
        "result": "rejected_capacity_no_safe_replacement",
        "hardware_accessed": False,
        "firmware_changed": False,
        "source_tree_modified": False,
        "device_partition_read": False,
        "device_package_eligible": False,
        "official_baseline": {
            "source_commit": source_commit,
            "package_path": str(OFFICIAL_PACKAGE.relative_to(ROOT)).replace("\\", "/"),
            "package_sha256": sha256_bytes(official_raw),
            "package_size_bytes": baseline_size,
            "member_count": official["header"]["member_count"],
            "semantic_count": len(official_index_map),
            "complete_rollback_copy": str(fallback_path.relative_to(ROOT)).replace("\\", "/"),
            "rollback_sha256": sha256_bytes(official_raw),
        },
        "candidate": {
            "id": "shy_candidate",
            "resource_name": "shy_candidate.eaf",
            "source_eaf_sha256": sha256_bytes(shy_raw),
            "source_eaf_size_bytes": len(shy_raw),
            "index_entry": {"name": "shy_candidate", "file": "shy_candidate.eaf", "loop": True, "fps": 25},
            "official_semantics_replaced": [],
            "official_emote_json_changed": False,
            "candidate_package_path": str(package_path.relative_to(ROOT)).replace("\\", "/"),
            "candidate_package_sha256": sha256_bytes(candidate_package),
            "candidate_package_size_bytes": candidate_size,
            "member_count": parsed_candidate["header"]["member_count"],
            "header_checksum_valid": parsed_candidate["header"]["checksum_valid"],
        },
        "capacity": {
            "partition_bytes": PARTITION_BYTES,
            "official_package_bytes": baseline_size,
            "official_remaining_bytes": PARTITION_BYTES - baseline_size,
            "candidate_package_bytes": candidate_size,
            "candidate_remaining_bytes": remaining,
            "candidate_overflow_bytes": max(0, -remaining),
            "largest_new_file_bytes_for_append": max_candidate_size,
            "candidate_exceeds_append_budget_bytes": max(0, len(shy_raw) - max_candidate_size),
            "status": "overflow" if remaining < 0 else "fits",
        },
        "layout_and_runtime": {
            "candidate_layout": "full_screen_360x360_non_mirrored",
            "compatible_entry": "existing emote_set_dialog_anim / InsertAnimDialog alias lookup, pending device validation",
            "not_compatible_entry": "eye_anim auto-mirrored sprite path",
            "index_entry_required": True,
            "segments_supported_by_current_index": False,
            "current_loop_behavior": "whole 188-frame animation loops; enter/loop/exit ranges exist only in the offline workbench",
            "runtime_display_verified": False,
        },
        "member_comparison": [
            {"name": name, "official_size_bytes": len(raw), "official_sha256": sha256_bytes(raw),
             "candidate_size_bytes": len(parsed_candidate["members"][name]),
             "candidate_sha256": sha256_bytes(parsed_candidate["members"][name]),
             "byte_identical": parsed_candidate["members"][name] == raw,
             "change": "index_additive_candidate_alias" if name == "index.json" else "unchanged"}
            for name, raw in sorted(official["members"].items(), key=lambda item: item[0].casefold())
        ] + [{"name": "shy_candidate.eaf", "official_size_bytes": None, "official_sha256": None,
              "candidate_size_bytes": len(shy_raw), "candidate_sha256": sha256_bytes(shy_raw),
              "byte_identical": False, "change": "new_candidate"}],
        "replacement_feasibility": replacement,
        "safe_replacement_count": len(safe_replacements),
        "capacity_fitting_replacement_count": len(fitting_replacements),
        "validation": {
            "baseline_package_reproduced_byte_for_byte": True,
            "all_original_non_index_members_unchanged": True,
            "all_official_index_semantics_unchanged": True,
            "candidate_eaf_decoded_and_structure_checked": True,
            "candidate_package_round_trip_checked": True,
            "full_candidate_package_fits_partition": remaining >= 0,
        },
        "next_gate": "E1.3b evaluate a smaller EAF encoding or a separately approved partition plan; do not replace eat/insert or flash this overflow package",
    }

    compression_report = {
        "schema": "deskbot.expression_package_compression_audit/v1",
        "result": "capacity_rejected_even_after_lossless_frame_dedup",
        "hardware_accessed": False,
        "firmware_changed": False,
        "device_package_eligible": False,
        "baseline_package_sha256": sha256_bytes(official_raw),
        "partition_bytes": PARTITION_BYTES,
        "official_remaining_bytes": PARTITION_BYTES - baseline_size,
        "source_candidate": {
            "path": str(SHY_EAF.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_bytes(shy_raw),
            "size_bytes": len(shy_raw),
            "frame_count": shy_info["stored_frame_count"],
        },
        "lossless_frame_deduplication": {
            "method": "identical full-frame EAF payloads share one byte range; frame table order/timing remains unchanged",
            "path": str(dedup_eaf_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_bytes(dedup_raw),
            "size_bytes": len(dedup_raw),
            "source_frame_count": shy_info["stored_frame_count"],
            "unique_frame_payload_count": len(set(_eaf_frame_payloads(shy_raw))),
            "bytes_saved": len(shy_raw) - len(dedup_raw),
            "decoded_frame_payload_sequence_identical": True,
            "eaf_structure_valid": True,
        },
        "full_package": {
            "path": str(dedup_package_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_bytes(dedup_package),
            "size_bytes": dedup_package_size,
            "remaining_partition_bytes": dedup_remaining,
            "overflow_bytes": max(0, -dedup_remaining),
            "package_round_trip_valid": True,
            "official_semantics_unchanged": True,
            "official_non_index_members_unchanged": True,
            "fits_partition": dedup_remaining >= 0,
        },
        "comparison": {
            "uncompressed_package_bytes": candidate_size,
            "uncompressed_package_overflow_bytes": max(0, -remaining),
            "lossless_dedup_package_bytes": dedup_package_size,
            "lossless_dedup_overflow_bytes": max(0, -dedup_remaining),
            "additional_bytes_needed_after_dedup": max(0, -dedup_remaining),
        },
        "next_gate": "test player-supported EAF compression/4-bit palette as a separate offline candidate; if quality or size gate fails, discuss a partition-layout change without editing the table",
    }

    if write_outputs:
        _write_immutable(fallback_path, official_raw)
        _write_immutable(package_path, candidate_package)
        _write_immutable(dedup_eaf_path, dedup_raw)
        _write_immutable(dedup_package_path, dedup_package)
        _write_immutable(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        _write_immutable(compression_report_path,
                         (json.dumps(compression_report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return report | {"compression_variant": compression_report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    try:
        report = audit_candidate(args.output)
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        print(json.dumps({"result": "rejected", "hardware_accessed": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"result": report["result"], "device_package_eligible": report["device_package_eligible"],
                      "capacity": report["capacity"], "safe_replacement_count": report["safe_replacement_count"],
                      "lossless_dedup": report["compression_variant"]["lossless_frame_deduplication"],
                      "dedup_package": report["compression_variant"]["full_package"],
                      "output": str((args.output / "package-candidate-audit.json").resolve())}, ensure_ascii=False, indent=2))
    # A valid audit may correctly conclude that no device-eligible package fits.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
