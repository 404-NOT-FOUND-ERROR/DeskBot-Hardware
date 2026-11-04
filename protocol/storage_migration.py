"""Versioned offline storage migration manifests for DeskBot E1.35b.

This module deliberately distinguishes accepted build artifacts from bytes read
from a physical device.  It never opens a serial port and never writes flash.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
from typing import Any


FLASH_BYTES = 16 * 1024 * 1024
SECTOR_BYTES = 4096
OLD_ASSETS_START = 0x5EC000
OLD_ASSETS_BYTES = 6000 * 1024
OLD_MODEL_START = 0xBC8000
MODEL_BYTES = 800 * 1024
NEW_ASSETS_BYTES = 8 * 1024 * 1024
NEW_MODEL_START = OLD_ASSETS_START + NEW_ASSETS_BYTES
NEW_MODEL_END = NEW_MODEL_START + MODEL_BYTES
OLD_MODEL_END = OLD_MODEL_START + MODEL_BYTES
OLD_TAIL_OVERWRITTEN_START = OLD_MODEL_END
# The candidate write reaches through the relocated model.  The live range
# that can be destroyed therefore ends at the end of the new model, not at
# the model's start address.
OLD_TAIL_OVERWRITTEN_END = NEW_MODEL_END
OLD_TABLE_SHA256 = "f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c"
NEW_TABLE_SHA256 = "0a55d50b8b2fb845c205c475e1cd19aad6801b81320a5ebb4f9b3f31269945fa"
E118_PACKAGE_BYTES = 5_538_351
E118_PACKAGE_SHA256 = "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1"
E118_OLD_ASSETS_SHA256 = "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6"
MODEL_PAYLOAD_BYTES = 582_107
MODEL_PAYLOAD_SHA256 = "cec059b2f1f574523e7e53d34166b224f6ae2f393652515ae7a1ef94ed44d7ba"
EMBEDDED_SPEECH_MODEL_SHA256 = "4e0b56d3c987b060078d7ab1e79baf814b473ce0a4230444a7a6185e301fa632"
TABLE_PAYLOAD_BYTES = 3072
E126_APP_PAYLOAD_BYTES = 4_477_280
E126_APP_PAYLOAD_SHA256 = "f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2"
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
MEMBER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

KNOWN_PARTITIONS = {
    "bootloader": (0x0, 0x8000),
    "partition_table": (0x8000, 0x1000),
    "nvs": (0x9000, 0x4000),
    "otadata": (0xD000, 0x2000),
    "phy_init": (0xF000, 0x1000),
    "app": (0x10000, 6000 * 1024),
    "assets": (OLD_ASSETS_START, OLD_ASSETS_BYTES),
    "model": (OLD_MODEL_START, MODEL_BYTES),
}


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def padded_partition(payload: bytes, capacity: int, fill: int = 0xFF) -> bytes:
    if type(capacity) is not int or capacity <= 0 or capacity % SECTOR_BYTES:
        raise ValueError("partition capacity must be a positive sector-aligned integer")
    if type(fill) is not int or not 0 <= fill <= 0xFF:
        raise ValueError("fill must be one byte")
    if len(payload) > capacity:
        raise ValueError("payload exceeds partition capacity")
    return payload + bytes([fill]) * (capacity - len(payload))


def safe_relative_path(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or relative.startswith("/")
            or "\\" in relative or ":" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or Path(relative).is_absolute()):
        raise ValueError("manifest paths must be relative")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("manifest path escapes repository root") from exc
    unresolved = root / relative
    if any(parent.is_symlink() for parent in (unresolved, *unresolved.parents) if parent.exists()):
        raise ValueError("manifest path must not traverse symlinks")
    return path


def validate_member_inventory(records: list[dict[str, Any]]) -> str:
    if not isinstance(records, list) or not records:
        raise ValueError("asset member inventory is missing")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("asset member record must be an object")
        name, size, digest = row.get("name"), row.get("bytes"), row.get("sha256")
        if (not isinstance(name, str) or not MEMBER_NAME_PATTERN.fullmatch(name)
                or name in seen or type(size) is not int or size <= 0
                or not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest)):
            raise ValueError("invalid or duplicate asset member")
        seen.add(name)
        normalized.append({"name": name, "bytes": size, "sha256": digest})
    return sha256_bytes(json_canonical(normalized))


def json_canonical(value: Any) -> bytes:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def validate_member_records(records: list[dict[str, Any]], actual: dict[str, bytes]) -> None:
    if not isinstance(actual, dict):
        raise ValueError("asset member payload inventory is missing")
    validate_member_inventory(records)
    normalized = {row["name"]: row for row in records}
    if set(normalized) != set(actual):
        raise ValueError("asset member set changed")
    for name, raw in actual.items():
        row = normalized[name]
        if len(raw) != row["bytes"] or sha256_bytes(raw) != row["sha256"]:
            raise ValueError("asset member size/hash mismatch: " + name)


def validate_live_backup_requirements(live: dict[str, Any]) -> None:
    if not isinstance(live, dict) or live.get("complete_device_backup") is not False:
        raise ValueError("offline evidence cannot claim complete device backup")
    required = live.get("required_before_write")
    expected = {
        "bootloader": (0x0, 0x8000),
        "current_partition_table": (0x8000, 0x1000),
        "nvs": (0x9000, 0x4000),
        "otadata": (0xD000, 0x2000),
        "phy_init": (0xF000, 0x1000),
        "current_app": (0x10000, 6000 * 1024),
        "current_assets": (OLD_ASSETS_START, OLD_ASSETS_BYTES),
        "current_model": (OLD_MODEL_START, MODEL_BYTES),
        "old_tail_extension": (OLD_MODEL_END, NEW_MODEL_END - OLD_MODEL_END),
    }
    if not isinstance(required, list):
        raise ValueError("live backup range list is missing")
    indexed = {}
    for row in required:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row.get("id") in indexed:
            raise ValueError("invalid or duplicate live backup range")
        indexed[row["id"]] = row
    if set(indexed) != set(expected):
        raise ValueError("live backup range set is incomplete or unexpected")
    for name, (offset, length) in expected.items():
        if (type(indexed[name].get("offset")) is not int or type(indexed[name].get("bytes")) is not int
                or indexed[name].get("offset") != offset or indexed[name].get("bytes") != length):
            raise ValueError("live backup range is incomplete or misaligned: " + name)


def validate_model_identity(payload: bytes, expected_sha256: str = MODEL_PAYLOAD_SHA256) -> None:
    if len(payload) != MODEL_PAYLOAD_BYTES or sha256_bytes(payload) != expected_sha256:
        raise ValueError("model payload size/hash mismatch")


def validate_layout_segments(layout: dict[str, Any], root: Path, *, require_files: bool = True) -> None:
    if not isinstance(layout, dict) or layout.get("id") not in {"old-6m", "candidate-8m"}:
        raise ValueError("unknown migration layout")
    expected_assets = OLD_ASSETS_BYTES if layout["id"] == "old-6m" else NEW_ASSETS_BYTES
    expected_model_start = OLD_MODEL_START if layout["id"] == "old-6m" else NEW_MODEL_START
    expected_table_sha = OLD_TABLE_SHA256 if layout["id"] == "old-6m" else NEW_TABLE_SHA256
    if layout.get("partition_table_payload_sha256") != expected_table_sha:
        raise ValueError("partition table hash does not match layout")
    segments = layout.get("segments")
    required = {"bootloader", "partition_table", "app", "otadata", "assets", "model"}
    if not isinstance(segments, dict) or set(segments) != required:
        raise ValueError("migration layout must cover all known writable artifacts")
    ranges: list[tuple[int, int, str]] = []
    expected_actions = ({name: "rollback_reference_only" for name in required}
                        if layout["id"] == "old-6m" else {
                            "bootloader": "reference_only_do_not_write",
                            "partition_table": "commit_last",
                            "app": "reference_only_do_not_write",
                            "otadata": "reference_only_do_not_write",
                            "assets": "write_after_model",
                            "model": "write_first",
                        })
    for name, row in segments.items():
        if not isinstance(row, dict):
            raise ValueError("segment record must be an object")
        start, size = row.get("offset"), row.get("capacity_bytes")
        if type(start) is not int or type(size) is not int or start < 0 or size <= 0:
            raise ValueError("segment offset/capacity is invalid")
        if start % SECTOR_BYTES or size % SECTOR_BYTES or start + size > FLASH_BYTES:
            raise ValueError("segment is not aligned or exceeds flash")
        if (type(row.get("bytes")) is not int or row.get("bytes") != size
                or not isinstance(row.get("sha256"), str) or not SHA256_PATTERN.fullmatch(row["sha256"])
                or row.get("write_action") != expected_actions[name]):
            raise ValueError("segment must describe a complete image and hash")
        if name == "assets" and (start, size) != (OLD_ASSETS_START, expected_assets):
            raise ValueError("assets boundary does not match layout")
        if name == "model" and (start, size) != (expected_model_start, MODEL_BYTES):
            raise ValueError("model boundary does not match layout")
        if name == "bootloader" and (start, size) != (0x0, 0x8000):
            raise ValueError("bootloader boundary changed")
        if name == "partition_table" and (start, size) != (0x8000, 0x1000):
            raise ValueError("partition table sector boundary changed")
        if name == "app" and (start, size) != KNOWN_PARTITIONS["app"]:
            raise ValueError("App boundary changed")
        if name == "otadata" and (start, size) != (0xD000, 0x2000):
            raise ValueError("OTA data boundary changed")
        expected_source = ("accepted_e118_package_padded_0xff" if name == "assets"
                           else "e135a_model_build_reference_padded_0xff" if name == "model"
                           else "accepted_build_artifact_padded_0xff")
        if row.get("source_kind") != expected_source:
            raise ValueError("offline image source provenance is missing")
        path = safe_relative_path(root, row.get("path", ""))
        payload_bytes = row.get("payload_bytes")
        if (type(payload_bytes) is not int or not 0 < payload_bytes <= size
                or not isinstance(row.get("payload_sha256"), str)
                or not SHA256_PATTERN.fullmatch(row["payload_sha256"])):
            raise ValueError("image payload length/hash is invalid")
        if require_files:
            if not path.is_file() or path.stat().st_size != size or sha256_file(path) != row["sha256"]:
                raise ValueError(f"migration image is missing or changed: {name}")
            raw = path.read_bytes()
            if (sha256_bytes(raw[:payload_bytes]) != row.get("payload_sha256")
                    or any(byte != 0xFF for byte in raw[payload_bytes:])):
                raise ValueError("image payload or 0xFF padding mismatch")
        if name == "partition_table" and (
                row.get("payload_bytes") != TABLE_PAYLOAD_BYTES or row.get("payload_sha256") != expected_table_sha):
            raise ValueError("partition table payload hash does not match layout")
        if name == "model" and (row.get("payload_bytes") != MODEL_PAYLOAD_BYTES
                                  or row.get("payload_sha256") != MODEL_PAYLOAD_SHA256):
            raise ValueError("standalone model payload identity changed")
        if name == "app" and (row["payload_bytes"] != E126_APP_PAYLOAD_BYTES
                               or row["payload_sha256"] != E126_APP_PAYLOAD_SHA256):
            raise ValueError("accepted E1.26 App identity changed")
        ranges.append((start, start + size, name))
    for left, right in zip(sorted(ranges), sorted(ranges)[1:]):
        if left[1] > right[0]:
            raise ValueError(f"migration segments overlap: {left[2]} / {right[2]}")


def validate_manifest(manifest: dict[str, Any], root: Path, *, require_files: bool = True) -> None:
    """Check cross-field claims; require_files=False is metadata audit only."""
    if manifest.get("schema") != "deskbot.expression.storage_migration.v1" or manifest.get("edition") != "E1.35b":
        raise ValueError("unexpected migration manifest identity")
    if manifest.get("deployment_eligible") is not False or manifest.get("flash_authorized") is not False:
        raise ValueError("offline migration must remain non-deployable")
    safety = manifest.get("safety", {})
    expected_safety = {"hardware_accessed": False, "serial_accessed": False,
                       "device_written": False, "nvs_written": False}
    if safety != expected_safety:
        raise ValueError("migration safety boundary is not offline")
    inputs = manifest.get("source_inputs", {})
    for key in ("e135a_report_sha256", "e135a_build_report_sha256", "e135a_source_manifest_sha256",
                "asset_member_manifest_sha256", "accepted_package_sha256", "model_payload_sha256",
                "embedded_speech_model_sha256"):
        if not isinstance(inputs.get(key), str) or not SHA256_PATTERN.fullmatch(inputs[key]):
            raise ValueError("migration source-input identity is missing: " + key)
    if (inputs.get("accepted_package_sha256") != E118_PACKAGE_SHA256
            or inputs.get("model_payload_sha256") != MODEL_PAYLOAD_SHA256
            or inputs.get("embedded_speech_model_sha256") != EMBEDDED_SPEECH_MODEL_SHA256):
        raise ValueError("migration accepted source identity changed")
    inventory = inputs.get("asset_members")
    if (type(inputs.get("asset_member_count")) is not int
            or inputs["asset_member_count"] != len(inventory or [])
            or validate_member_inventory(inventory) != inputs["asset_member_manifest_sha256"]):
        raise ValueError("migration asset member manifest is incomplete or changed")
    layouts = manifest.get("layouts", {})
    if set(layouts) != {"old", "candidate"}:
        raise ValueError("old and candidate layouts are required")
    if layouts["old"].get("id") != "old-6m" or layouts["candidate"].get("id") != "candidate-8m":
        raise ValueError("migration layouts have been swapped")
    validate_layout_segments(layouts["old"], root, require_files=require_files)
    validate_layout_segments(layouts["candidate"], root, require_files=require_files)
    old = layouts["old"]["segments"]
    candidate = layouts["candidate"]["segments"]
    if manifest.get("accepted_assets", {}).get("old_full_partition_sha256") != old["assets"]["sha256"]:
        raise ValueError("old assets image is not the accepted E1.18 image")
    assets = manifest.get("accepted_assets", {})
    model = manifest.get("model", {})
    if assets.get("package_bytes") != E118_PACKAGE_BYTES or assets.get("package_sha256") != E118_PACKAGE_SHA256:
        raise ValueError("accepted E1.18 package identity changed")
    if (assets.get("old_full_partition_bytes") != OLD_ASSETS_BYTES
            or assets.get("old_full_partition_sha256") != E118_OLD_ASSETS_SHA256
            or assets.get("candidate_full_partition_bytes") != NEW_ASSETS_BYTES
            or assets.get("candidate_full_partition_sha256") != candidate["assets"]["sha256"]):
        raise ValueError("assets partition image boundary or hash changed")
    if (old["assets"].get("payload_bytes") != E118_PACKAGE_BYTES
            or candidate["assets"].get("payload_bytes") != E118_PACKAGE_BYTES
            or old["assets"].get("payload_sha256") != E118_PACKAGE_SHA256
            or candidate["assets"].get("payload_sha256") != E118_PACKAGE_SHA256):
        raise ValueError("E1.18 package payload changed")
    if require_files:
        # Reuse the audited format reader rather than reimplementing its parser.
        parser_path = root / "scripts/audit-expression-resources.py"
        spec = importlib.util.spec_from_file_location("storage_migration_resource_reader", parser_path)
        if spec is None or spec.loader is None:
            raise ValueError("asset package reader is unavailable")
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        payload = safe_relative_path(root, old["assets"]["path"]).read_bytes()[:E118_PACKAGE_BYTES]
        members = reader.parse_package_bytes(payload, modern=False)["members"]
        validate_member_records(inputs["asset_members"], members)
    if (assets.get("padding_byte") != "0xFF"
            or model.get("payload_bytes") != MODEL_PAYLOAD_BYTES
            or model.get("payload_sha256") != MODEL_PAYLOAD_SHA256
            or model.get("candidate_payload_sha256") != MODEL_PAYLOAD_SHA256):
        raise ValueError("model payload changed during relocation")
    if (old["model"]["sha256"] != candidate["model"]["sha256"]
            or model.get("old_full_partition_sha256") != old["model"]["sha256"]
            or model.get("candidate_full_partition_sha256") != candidate["model"]["sha256"]):
        raise ValueError("old and candidate model image bytes differ")
    for name in ("bootloader", "app", "otadata"):
        if old[name]["sha256"] != candidate[name]["sha256"]:
            raise ValueError(f"non-layout artifact changed across layouts: {name}")
    live = manifest.get("live_backup_requirements", {})
    validate_live_backup_requirements(live)
    rollback = manifest.get("rollback", {})
    required_live_items = {
        "bootloader", "current_partition_table", "otadata", "phy_init", "current_app",
        "current_assets", "current_model", "nvs", "old_tail_extension", "current_device_identity",
    }
    if (rollback.get("offline_build_reference_complete") is not True
            or rollback.get("device_complete") is not False
            or rollback.get("model_source_kind") != "E1.35a build artifact, not device readback"
            or set(rollback.get("missing_live_items", [])) != required_live_items):
        raise ValueError("rollback completeness boundary is missing")
    order = manifest.get("write_order")
    if order != ["new_model", "new_assets", "partition_table_last"]:
        raise ValueError("unsafe or ambiguous write order")
    plan = manifest.get("write_plan")
    expected_plan = [
        {"id": "new_model", "segment": "model", "offset": NEW_MODEL_START, "bytes": MODEL_BYTES,
         "sha256": candidate["model"]["sha256"]},
        {"id": "new_assets", "segment": "assets", "offset": OLD_ASSETS_START, "bytes": NEW_ASSETS_BYTES,
         "sha256": candidate["assets"]["sha256"]},
        {"id": "partition_table_last", "segment": "partition_table", "offset": 0x8000, "bytes": 0x1000,
         "sha256": candidate["partition_table"]["sha256"]},
    ]
    if plan != expected_plan:
        raise ValueError("write plan must contain only model, assets, and final partition table")
    protected = manifest.get("untouched_partitions", [])
    expected_protected = {name: KNOWN_PARTITIONS[name] for name in ("bootloader", "app", "nvs", "phy_init", "otadata")}
    if (not isinstance(protected, list) or len(protected) != len(expected_protected)
            or any(not isinstance(row, dict) for row in protected)
            or {row.get("name") for row in protected} != set(expected_protected)):
        raise ValueError("protected partition set is incomplete")
    for row in protected:
        if ((row.get("offset"), row.get("bytes")) != expected_protected[row["name"]]
                or row.get("action") != "preserve_and_back_up_live"):
            raise ValueError("protected partition boundary or policy changed")
    flash_maps = manifest.get("flash_maps", {})
    if flash_maps.get("candidate_layout_id") != "candidate-8m" or flash_maps.get("legacy_map_allowed_for_candidate") is not False:
        raise ValueError("legacy flash maps must be rejected for candidate layout")
    validate_flash_map("candidate-8m", flash_maps)


def validate_flash_map(layout_id: str, flash_map: dict[str, Any]) -> None:
    """Reject a generic/old map when a caller is validating an 8 MiB layout."""
    if layout_id not in {"old-6m", "candidate-8m"}:
        raise ValueError("unknown flash-map target layout")
    expected_table = OLD_TABLE_SHA256 if layout_id == "old-6m" else NEW_TABLE_SHA256
    expected_model = OLD_MODEL_START if layout_id == "old-6m" else NEW_MODEL_START
    if (flash_map.get("layout_id") != layout_id
            or flash_map.get("partition_table_sha256") != expected_table
            or flash_map.get("model_offset") != expected_model):
        raise ValueError("flash map does not match selected partition layout")


def validate_schema(manifest: dict[str, Any]) -> None:
    """Use the public schema without retrieving remote schema resources."""
    import json
    import jsonschema

    schema = json.loads(Path(__file__).with_name("expression-storage-migration-e1-35b.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(manifest))
    if errors:
        raise ValueError("migration schema mismatch: " + errors[0].message)
