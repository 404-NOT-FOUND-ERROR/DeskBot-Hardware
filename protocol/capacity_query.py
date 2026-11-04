"""Normalize read-only Flash capacity responses for E1.41.

This module accepts the user-only system-info response or the bounded
assistant-visible capacity response. It separates the driver-reported Flash
size from CPU mmap and resource-layout claims that require a later stage.
"""
from __future__ import annotations

import json
from typing import Any

MIB = 1024 * 1024
PHYSICAL_FLASH_BYTES = 32 * MIB
LEGACY_PARTITIONS = {
    "nvs": {"address": 0x9000, "size": 0x4000},
    "otadata": {"address": 0xD000, "size": 0x2000},
    "phy_init": {"address": 0xF000, "size": 0x1000},
    "ota_0": {"address": 0x10000, "size": 0x5DC000},
    "assets": {"address": 0x5EC000, "size": 0x5DC000},
    "model": {"address": 0xBC8000, "size": 0xC8000},
}
UPPER_ASSETS_8M_PARTITIONS = {
    "nvs": LEGACY_PARTITIONS["nvs"],
    "otadata": LEGACY_PARTITIONS["otadata"],
    "phy_init": LEGACY_PARTITIONS["phy_init"],
    "ota_0": LEGACY_PARTITIONS["ota_0"],
    "assets_prev": LEGACY_PARTITIONS["assets"],
    "model": LEGACY_PARTITIONS["model"],
    "assets": {"address": 0x1000000, "size": 0x800000},
}

SCHEMA = "deskbot.n32r16.capacity_query.e1_41/v1"
SOURCE_TOOLS = {"self.get_system_info", "self.get_flash_capacity"}


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _payload(value: str | bytes | bytearray | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("system-info response is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("system-info response must be a JSON object")
    return value


def parse_system_info(value: str | bytes | bytearray | dict[str, Any]) -> dict[str, Any]:
    """Return a bounded normalized view without claiming mmap support.

    A wrong capacity is kept as a failed match in the returned record so a
    caller can show the observed value.  ``validate_expected_capacity`` is the
    fail-closed gate used when the result is considered for E1.42.
    """
    payload = _payload(value)
    source_tool = payload.get("source_tool", "self.get_system_info")
    if source_tool not in SOURCE_TOOLS:
        raise ValueError("unrecognized capacity source tool")
    flash_size = _integer(payload.get("flash_size"), "flash_size")
    raw_partitions = payload.get("partition_table")
    if not isinstance(raw_partitions, list) or not raw_partitions:
        raise ValueError("partition_table must be a non-empty array")

    partitions: dict[str, dict[str, int]] = {}
    for row in raw_partitions:
        if not isinstance(row, dict):
            raise ValueError("partition_table entries must be objects")
        label = row.get("label")
        if not isinstance(label, str) or not label or label in partitions:
            raise ValueError("partition labels must be unique non-empty strings")
        address = _integer(row.get("address"), f"partition {label}.address")
        size = _integer(row.get("size"), f"partition {label}.size")
        if size == 0:
            raise ValueError(f"partition {label}.size must be positive")
        partitions[label] = {"address": address, "size": size}

    matches = all(partitions.get(label) == expected for label, expected in LEGACY_PARTITIONS.items())
    return {
        "schema": SCHEMA,
        "edition": "E1.41",
        "source_tool": source_tool,
        "claim_scope": "driver_reported_flash_size_and_partition_table_only",
        "flash_size_bytes": flash_size,
        "flash_size_mib": flash_size / MIB,
        "expected_physical_flash_bytes": PHYSICAL_FLASH_BYTES,
        "flash_size_matches_expected": flash_size == PHYSICAL_FLASH_BYTES,
        "legacy_partition_table_matches": matches,
        "partitions": [{"label": label, **partitions[label]}
                        for label in sorted(partitions, key=lambda name: partitions[name]["address"])],
        "mmap_verified": False,
        "upper_assets_verified": False,
    }


def validate_expected_capacity(value: str | bytes | bytearray | dict[str, Any], *,
                               layout: str = "legacy") -> dict[str, Any]:
    """Require 32 MiB and an exact supported partition layout."""
    result = parse_system_info(value)
    if not result["flash_size_matches_expected"]:
        raise ValueError("driver-reported Flash size is not 32 MiB")
    layouts = {"legacy": LEGACY_PARTITIONS, "upper-assets-8m": UPPER_ASSETS_8M_PARTITIONS}
    if layout not in layouts:
        raise ValueError("unsupported partition layout")
    observed = {row["label"]: {"address": row["address"], "size": row["size"]}
                for row in result["partitions"]}
    if observed != layouts[layout]:
        raise ValueError("partition table does not match the accepted " + layout + " layout")
    if layout != "legacy":
        result["expected_partition_layout"] = layout
        result["partition_table_matches_expected"] = True
    return result


__all__ = ["LEGACY_PARTITIONS", "UPPER_ASSETS_8M_PARTITIONS", "PHYSICAL_FLASH_BYTES", "SCHEMA", "SOURCE_TOOLS",
           "parse_system_info", "validate_expected_capacity"]
