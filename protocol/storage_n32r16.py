"""Offline layout proposals for the confirmed N32R16 body. No device I/O."""
from __future__ import annotations

import re
from typing import Any

from protocol.storage_budget import page_span, validate_layout

MIB = 1024 * 1024
PHYSICAL_FLASH_BYTES = 32 * MIB
PSRAM_BYTES = 16 * MIB
LEGACY_FLASH_BYTES = 16 * MIB
ASSETS_BYTES = 6000 * 1024
APP_BYTES = 6000 * 1024
MODEL_BYTES = 800 * 1024
PACKAGE_BYTES = 5538351
PACKAGE_SHA256 = "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1"
APP_PAYLOAD_BYTES = 4477280
OLD_TABLE_SHA256 = "f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c"
CAPTURE_SHA256 = "21e68e726db1e6c4a9b50c05f2dc1505a049feda5d0f6dbc872a67c2bddb1f6e"
FORMAL_CSV_SHA256 = "75b9ca2d57fc645c91e5281e5deb6ffd101ab9e120e6770f523c5438d94f3dd6"
# Locked outputs of the ESP-IDF 5.5.5 generator, independently round-tripped.
TABLE_SHA256 = {
    "current-16m": OLD_TABLE_SHA256,
    "capacity-only-32m": OLD_TABLE_SHA256,
    "upper-assets-8m": "245506195778fcca47a93c90b89c5031bdd7c7b727cb9f674febfd67317a0c8d",
    "inplace-assets-8m": "0a55d50b8b2fb845c205c475e1cd19aad6801b81320a5ebb4f9b3f31269945fa",
    "inplace-assets-12m": "ee0795d748d0777788f13d040c8c0e3ffac8bfd2699160a4580d40781b89d390",
    "inplace-assets-16m": "b29cc6d2d52fd0f7247aeea627b753c556a6532a1817e76b4d3fa1641e60b2b7",
}


def partition(name: str, start: int, size: int, subtype: int = 0x82, ptype: int = 1) -> dict:
    return {"name": name, "type": ptype, "subtype": subtype, "start": start,
            "size_bytes": size, "end_exclusive": start + size, "flags": 0}


def legacy_rows() -> list[dict]:
    return [partition("nvs", 0x9000, 0x4000, 2), partition("otadata", 0xD000, 0x2000, 0),
            partition("phy_init", 0xF000, 0x1000, 1), partition("ota_0", 0x10000, APP_BYTES, 0x10, 0),
            partition("assets", 0x5EC000, ASSETS_BYTES), partition("model", 0xBC8000, MODEL_BYTES)]


def proposals() -> list[dict]:
    baseline = legacy_rows()
    upper = legacy_rows()
    upper[4]["name"] = "assets_prev"
    upper.append(partition("assets", 0x1000000, 8 * MIB))
    plans = [
        {"id": "current-16m", "label": "当前已验收基线", "configured_flash_bytes": LEGACY_FLASH_BYTES,
         "rows": baseline, "purpose": "baseline", "next_build": False},
        {"id": "capacity-only-32m", "label": "先验证 32 MiB 配置，分区字节不变", "configured_flash_bytes": PHYSICAL_FLASH_BYTES,
         "rows": legacy_rows(), "purpose": "capacity_validation", "next_build": True},
        {"id": "upper-assets-8m", "label": "优先资源方案：上半区 8 MiB，保留旧资源和模型", "configured_flash_bytes": PHYSICAL_FLASH_BYTES,
         "rows": upper, "purpose": "resource_candidate", "next_build": False},
    ]
    for size in (8, 12, 16):
        rows = legacy_rows()
        rows[4] = partition("assets", 0x5EC000, size * MIB)
        rows[5] = partition("model", 0x5EC000 + size * MIB, MODEL_BYTES)
        plans.append({"id": f"inplace-assets-{size}m", "label": f"对照方案：原位 {size} MiB，移动模型",
                      "configured_flash_bytes": PHYSICAL_FLASH_BYTES, "rows": rows,
                      "purpose": "comparison_only", "next_build": False})
    return plans


def validate_proposal(plan: dict[str, Any]) -> dict[str, dict]:
    known = {item["id"]: item for item in proposals()}
    if not isinstance(plan, dict) or plan.get("id") not in known:
        raise ValueError("unknown layout proposal")
    capacity = plan.get("configured_flash_bytes")
    if type(capacity) is not int or capacity not in {LEGACY_FLASH_BYTES, PHYSICAL_FLASH_BYTES}:
        raise ValueError("invalid image capacity")
    rows = plan.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid partition rows")
    current = validate_layout(rows, capacity)
    old = {row["name"]: row for row in legacy_rows()}
    for name in ("nvs", "otadata", "phy_init", "ota_0"):
        if current.get(name) != old[name]:
            raise ValueError("protected partition changed: " + name)
    if plan["id"] == "upper-assets-8m":
        preserved = dict(old["assets"], name="assets_prev")
        if current.get("model") != old["model"] or current.get("assets_prev") != preserved:
            raise ValueError("upper layout must preserve the whole old model and assets")
    expected = known[plan["id"]]
    if capacity != expected["configured_flash_bytes"] or rows != expected["rows"]:
        raise ValueError("layout differs from its versioned proposal")
    if (type(plan.get("next_build")) is not bool
            or any(plan.get(key) != expected[key] for key in ("label", "purpose", "next_build"))):
        raise ValueError("layout stage or next-build selection changed")
    return current


def to_csv(rows: list[dict]) -> str:
    validate_layout(rows, PHYSICAL_FLASH_BYTES)
    lines = ["# ESP-IDF Partition Table", "# Name, Type, SubType, Offset, Size, Flags"]
    for row in rows:
        lines.append(f"{row['name']}, 0x{row['type']:X}, 0x{row['subtype']:X}, "
                     f"0x{row['start']:X}, 0x{row['size_bytes']:X},")
    return "\n".join(lines) + "\n"


def free_ranges(rows: list[dict], capacity: int) -> list[dict]:
    validate_layout(rows, capacity)
    cursor, result = 0x9000, []  # bootloader and partition-table sector are protected
    for row in rows:
        if row["start"] > cursor:
            result.append({"start": cursor, "end_exclusive": row["start"], "bytes": row["start"] - cursor})
        cursor = row["end_exclusive"]
    if cursor < capacity:
        result.append({"start": cursor, "end_exclusive": capacity, "bytes": capacity - cursor})
    return result


def summarize(plan: dict) -> dict:
    current = validate_proposal(plan)
    old = {row["name"]: row for row in legacy_rows()}
    assets, model = current["assets"], current["model"]
    resources_changed = assets != old["assets"]
    modified = [dict(row, purpose="future_resource_destination") for row in (assets,) if resources_changed]
    if model != old["model"]:
        modified.append(dict(model, purpose="future_model_destination"))
    span = page_span(assets["start"], assets["size_bytes"])
    return {
        **plan, "physical_flash_bytes": PHYSICAL_FLASH_BYTES, "psram_bytes": PSRAM_BYTES,
        "table_changes": plan["rows"] != legacy_rows(),
        "model_moves": model != old["model"], "old_assets_preserved_in_place": not resources_changed or "assets_prev" in current,
        "protected_partitions_unchanged": True,
        "assets": {**assets, "package_bytes": PACKAGE_BYTES, "free_bytes": assets["size_bytes"] - PACKAGE_BYTES,
                   "mmap_span_pages": span, "component_min_reported_free_pages": assets["size_bytes"] // 65536 + 1,
                   "release_headroom_bytes": MIB, "soft_limit_bytes": assets["size_bytes"] - MIB,
                   "meets_proposed_headroom": PACKAGE_BYTES <= assets["size_bytes"] - MIB},
        "model": {**model, "mmap_span_pages_if_mapped": page_span(model["start"], model["size_bytes"])},
        "app": {**current["ota_0"], "payload_bytes": APP_PAYLOAD_BYTES,
                "free_bytes": APP_BYTES - APP_PAYLOAD_BYTES},
        "unallocated_ranges": free_ranges(plan["rows"], plan["configured_flash_bytes"]),
        "unconfigured_ranges": ([{"start": LEGACY_FLASH_BYTES, "end_exclusive": PHYSICAL_FLASH_BYTES,
                                  "bytes": PHYSICAL_FLASH_BYTES - LEGACY_FLASH_BYTES}]
                                if plan["configured_flash_bytes"] == LEGACY_FLASH_BYTES else []),
        "migration_destination_geometry": modified,
        "deployment_eligible": False, "runtime_mmap_verified": False,
        "automatic_rollback_implemented": False,
    }


def validate_capacity_evidence(report: dict) -> None:
    expected = {"physical_flash_bytes": PHYSICAL_FLASH_BYTES, "configured_flash_bytes": LEGACY_FLASH_BYTES,
                "psram_bytes": PSRAM_BYTES}
    if not isinstance(report, dict) or not isinstance(report.get("device"), dict):
        raise ValueError("capacity evidence must be an object with a device identity")
    if any(type(report["device"].get(k)) is not int or report["device"][k] != v for k, v in expected.items()):
        raise ValueError("physical Flash, configured Flash and PSRAM evidence must be distinct")
    if (report.get("whole_device_sha256") != CAPTURE_SHA256 or report.get("baseline_matches") is not True
            or report.get("capture", {}).get("independent_verify_passed") is not True
            or report.get("device_written") is not False or report.get("flash_authorized") is not False):
        raise ValueError("verified read-only baseline evidence is missing")


def validate_report(report: dict) -> None:
    if not isinstance(report, dict):
        raise ValueError("audit report must be an object")
    if (report.get("schema") != "deskbot.n32r16.storage_audit.v1" or report.get("edition") != "E1.36"
            or report.get("result") != "offline_layout_audit_passed_runtime_pending"):
        raise ValueError("invalid audit identity")
    if report.get("safety") != {"serial_accessed": False, "device_written": False, "nvs_written": False,
                                 "formal_firmware_changed": False, "c61_accessed": False}:
        raise ValueError("audit crossed the offline boundary")
    if report.get("deployment_eligible") is not False or report.get("flash_authorized") is not False:
        raise ValueError("offline layout audit cannot authorize deployment")
    validate_capacity_evidence({**report, "device_written": report["safety"]["device_written"]})
    if report.get("device", {}).get("chip") != "esp32s3" or report["device"].get("model") != "N32R16":
        raise ValueError("unexpected hardware identity")
    if (report.get("capture", {}).get("upper_16m_all_ff") is not True
            or report.get("formal_csv_sha256") != FORMAL_CSV_SHA256):
        raise ValueError("captured upper-half or formal layout evidence changed")
    records = report.get("candidates", [])
    if (not isinstance(records, list) or any(not isinstance(row, dict) for row in records)
            or [row.get("id") for row in records] != [p["id"] for p in proposals()]):
        raise ValueError("missing, duplicate or unexpected proposal")
    for record in records:
        plan = {key: record.get(key) for key in ("id", "label", "configured_flash_bytes", "rows", "purpose", "next_build")}
        summary = summarize(plan)
        if any(record.get(key) != value for key, value in summary.items()):
            raise ValueError("proposal geometry, budget or runtime claim changed")
        if record.get("table_sha256") != TABLE_SHA256[record["id"]]:
            raise ValueError("partition table identity changed")
        if record.get("idf_roundtrip_verified") is not True:
            raise ValueError("ESP-IDF round trip evidence missing")
        if record.get("id") == "capacity-only-32m":
            baseline = next(item for item in records if item.get("id") == "current-16m")
            if record.get("rows") != baseline.get("rows") or record.get("table_sha256") != baseline.get("table_sha256"):
                raise ValueError("capacity-only candidate must preserve the legacy table byte-for-byte")
    if report.get("recommendation") != {"next_build": "capacity-only-32m", "resource_candidate": "upper-assets-8m"}:
        raise ValueError("unexpected rollout sequence")
