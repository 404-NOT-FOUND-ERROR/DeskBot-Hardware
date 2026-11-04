"""E1.43a fixed-range CPU probe record parsing; never accesses hardware."""
from __future__ import annotations

import json
import re
from typing import Any

from protocol.capacity_query import LEGACY_PARTITIONS, validate_expected_capacity
from protocol.storage_upper_pattern import PAGE_BYTES, PAGE_START, SECTOR_BYTES, build_pattern, validate_manifest

MARKER = "[DESKBOT_E143A "
RECORD = re.compile(r"\[DESKBOT_E143A (capacity|begin|block|summary|aborted) bytes=([0-9]{1,4})\] (.*)$")
MEMORY_SUFFIXES = ("internal_free", "internal_largest", "internal_minimum",
                   "spiram_free", "spiram_largest", "spiram_minimum")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON value: " + value)


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(name + " must be a non-negative integer")
    return value


def extract_records(log: str) -> dict:
    records: dict[str, Any] = {"blocks": []}
    order = []
    for line in log.splitlines():
        if MARKER not in line:
            continue
        match = RECORD.search(line)
        if match is None:
            raise ValueError("malformed E1.43a diagnostic record")
        kind, length, payload = match.groups()
        order.append(kind)
        if not 0 < int(length) <= 4096 or len(payload.encode("utf-8")) != int(length):
            raise ValueError("E1.43a record length mismatch")
        try:
            value = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except json.JSONDecodeError as error:
            raise ValueError("invalid E1.43a JSON") from error
        if not isinstance(value, dict):
            raise ValueError("E1.43a record must contain a JSON object")
        if kind == "block":
            if type(value.get("index")) is not int or not 0 <= value["index"] < 16:
                raise ValueError("invalid block index")
            if any(row.get("index") == value["index"] for row in records["blocks"]):
                raise ValueError("duplicate block record")
            records["blocks"].append(value)
        else:
            key = "aborted" if kind == "aborted" else kind
            if key in records:
                raise ValueError("duplicate E1.43a record: " + key)
            records[key] = value
    if "capacity" not in records or "begin" not in records:
        raise ValueError("incomplete E1.43a capture")
    if "aborted" in records:
        if "summary" in records:
            raise ValueError("capture cannot contain both aborted and summary records")
        if order != ["capacity", "begin"] + ["block"] * len(records["blocks"]) + ["aborted"]:
            raise ValueError("E1.43a aborted record order is invalid")
        return records
    if "summary" not in records or len(records["blocks"]) != 16:
        raise ValueError("incomplete E1.43a block/summary capture")
    if order != ["capacity", "begin"] + ["block"] * 16 + ["summary"]:
        raise ValueError("E1.43a record order is invalid")
    return records


def validate_capture(records: dict, manifest: dict, expected_low_alias_sha256: str,
                     expected_boundary_hex: str, expected_low_protected_sha256: str) -> dict:
    raw_pattern = build_pattern()
    validate_manifest(manifest, raw_pattern)
    if not isinstance(records, dict) or set(records) - {"blocks", "capacity", "begin", "summary", "aborted"}:
        raise ValueError("unexpected E1.43a record keys")
    capacity = validate_expected_capacity(records.get("capacity"))
    if {row["label"] for row in capacity["partitions"]} != set(LEGACY_PARTITIONS):
        raise ValueError("unexpected extra partition in fixed legacy-layout probe")
    if (not isinstance(expected_low_alias_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_low_alias_sha256) is None
            or not isinstance(expected_low_protected_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_low_protected_sha256) is None
            or not isinstance(expected_boundary_hex, str) or re.fullmatch(r"[0-9a-f]{64}", expected_boundary_hex) is None
            or expected_boundary_hex[-32:] != raw_pattern[:16].hex()):
        raise ValueError("invalid expected alias digest or boundary pattern prefix")
    begin = records.get("begin")
    if isinstance(begin, dict) and any(type(begin.get(key)) is not int
                                       for key in ("start", "bytes", "sector_bytes", "chunk_bytes")):
        raise ValueError("probe geometry fields must be integers, not booleans")
    if begin != {"schema": "deskbot.upper_pattern.begin.v1", "start": PAGE_START,
                 "bytes": PAGE_BYTES, "sector_bytes": SECTOR_BYTES,
                 "chunk_bytes": 1024, "writes_performed": False}:
        raise ValueError("probe geometry or write-safety record changed")
    if begin["writes_performed"] is not False:
        raise ValueError("probe must not claim writes")
    if "aborted" in records:
        aborted = records["aborted"]
        if (set(aborted) != {"error", "writes_performed"} or not isinstance(aborted["error"], str)
                or not aborted["error"] or aborted["writes_performed"] is not False):
            raise ValueError("invalid E1.43a abort record")
        return {"capacity": capacity, "records": records,
                "checks": {"aborted": True}, "probe_passed": False}

    blocks = records.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != 16:
        raise ValueError("exactly sixteen block records are required")
    if [_integer(row.get("index"), "block.index") for row in blocks] != list(range(16)):
        raise ValueError("block records must cover indices 0..15 in order")
    ordered = blocks
    checks: dict[str, bool] = {}
    for index, row in enumerate(ordered):
        if set(row) != {"index", "address", "bytes", "read_sha256", "mmap_sha256",
                        "read_header_hex", "mmap_header_hex"}:
            raise ValueError("unexpected block fields")
        expected = manifest["blocks"][index]
        checks[f"block_{index:02d}"] = (
            _integer(row["index"], "block.index") == index
            and _integer(row["address"], "block.address") == expected["absolute_offset"]
            and _integer(row["bytes"], "block.bytes") == SECTOR_BYTES
            and row["read_sha256"] == expected["sha256"]
            and row["mmap_sha256"] == expected["sha256"]
            and row["read_header_hex"] == expected["header_hex"]
            and row["mmap_header_hex"] == expected["header_hex"])
    summary = records.get("summary")
    memory_keys = {f"{prefix}_{suffix}" for prefix in
                   ("before", "upper_mapped", "boundary_mapped", "after") for suffix in MEMORY_SUFFIXES}
    required = {"schema", "read_sha256", "mmap_sha256", "low_alias_read_sha256", "low_protected_read_sha256", "boundary_address",
                "boundary_read_hex", "boundary_mmap_hex", "boundary_read_status", "boundary_mmap_status",
                "pages_before", "pages_while_upper", "pages_after_upper", "pages_while_boundary",
                "pages_after_boundary", "upper_released", "boundary_released", "writes_performed", "probe_passed"}
    if not isinstance(summary, dict) or set(summary) != required | memory_keys:
        raise ValueError("summary fields are incomplete or unknown")
    if summary["schema"] != "deskbot.upper_pattern.summary.v1":
        raise ValueError("unknown summary schema")
    if not isinstance(summary["boundary_read_status"], str) or not isinstance(summary["boundary_mmap_status"], str):
        raise ValueError("invalid Flash operation status")
    for key in ("boundary_read_hex", "boundary_mmap_hex"):
        if not isinstance(summary[key], str) or re.fullmatch(r"[0-9a-f]{64}", summary[key]) is None:
            raise ValueError("invalid boundary sample")
    for key in ("read_sha256", "mmap_sha256", "low_alias_read_sha256", "low_protected_read_sha256"):
        if not isinstance(summary[key], str) or re.fullmatch(r"[0-9a-f]{64}", summary[key]) is None:
            raise ValueError("invalid SHA-256 field: " + key)
    for key in ("boundary_address", "pages_before", "pages_while_upper", "pages_after_upper",
                "pages_while_boundary", "pages_after_boundary", *memory_keys):
        _integer(summary[key], key)
    for key in ("upper_released", "boundary_released", "writes_performed", "probe_passed"):
        if type(summary[key]) is not bool:
            raise ValueError("invalid boolean summary field: " + key)
    for prefix in ("before", "upper_mapped", "boundary_mapped", "after"):
        for heap in ("internal", "spiram"):
            free = summary[f"{prefix}_{heap}_free"]
            if summary[f"{prefix}_{heap}_largest"] > free or summary[f"{prefix}_{heap}_minimum"] > free:
                raise ValueError("impossible heap snapshot")
    checks.update({
        "read_page_sha256": summary["read_sha256"] == manifest["sha256"],
        "mmap_page_sha256": summary["mmap_sha256"] == manifest["sha256"],
        "lower_address_did_not_alias_test_page": summary["low_alias_read_sha256"] != manifest["sha256"],
        "immutable_lower_prefix_matches_reference": summary["low_protected_read_sha256"] == expected_low_protected_sha256,
        "boundary_address_exact": summary["boundary_address"] == PAGE_START - 16,
        "boundary_read_matches_expected": summary["boundary_read_hex"] == expected_boundary_hex,
        "boundary_mmap_matches_expected": summary["boundary_mmap_hex"] == expected_boundary_hex,
        "boundary_paths_equal": summary["boundary_read_hex"] == summary["boundary_mmap_hex"],
        "boundary_statuses_ok": summary["boundary_read_status"] == "ESP_OK"
            and summary["boundary_mmap_status"] == "ESP_OK",
        "upper_mapping_consumed_one_page": summary["pages_while_upper"] == summary["pages_before"] - 1,
        "boundary_mapping_consumed_two_pages": summary["pages_while_boundary"] == summary["pages_before"] - 2,
        "mappings_released": summary["upper_released"] is True and summary["boundary_released"] is True,
        "mmap_pages_restored": summary["pages_after_upper"] == summary["pages_before"]
            and summary["pages_after_boundary"] == summary["pages_before"],
        "no_flash_writes": summary["writes_performed"] is False,
        "firmware_check_passed": summary["probe_passed"] is True,
    })
    passed = all(checks.values())
    return {"capacity": capacity, "records": records, "checks": checks, "probe_passed": passed,
            "observations": {"nvs_containing_low_page_matches_historical_reference":
                             summary["low_alias_read_sha256"] == expected_low_alias_sha256}}


__all__ = ["extract_records", "validate_capture"]
