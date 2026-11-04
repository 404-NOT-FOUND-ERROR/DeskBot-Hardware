"""Fail-closed parser for the E1.42 CPU Flash read/mmap probe."""
from __future__ import annotations

import json
from typing import Any

MIB = 1024 * 1024
PHYSICAL_FLASH_BYTES = 32 * MIB
BOUNDARY = 16 * MIB
SAMPLE_BYTES = 32
LOWER_ADDRESS = BOUNDARY - SAMPLE_BYTES // 2
UPPER_ADDRESS = BOUNDARY
SCHEMA = "deskbot.n32r16.addressing_probe.e1_42/v1"


def _payload(value: str | bytes | bytearray | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("addressing probe response is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("addressing probe response must be a JSON object")
    return value


def _bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _non_negative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def parse_probe(
    value: str | bytes | bytearray | dict[str, Any],
    *,
    expected_lower_sample: bytes | None = None,
    expected_upper_sample: bytes | None = None,
) -> dict[str, Any]:
    payload = _payload(value)
    if payload.get("schema") != SCHEMA:
        raise ValueError("unexpected E1.42 probe schema")
    if _non_negative_int(payload, "flash_size") != PHYSICAL_FLASH_BYTES:
        raise ValueError("probe did not report 32 MiB Flash")
    if _non_negative_int(payload, "boundary") != BOUNDARY:
        raise ValueError("probe boundary is not 16 MiB")
    if _non_negative_int(payload, "sample_bytes") != SAMPLE_BYTES:
        raise ValueError("probe sample length changed")
    if _non_negative_int(payload, "lower_address") != LOWER_ADDRESS:
        raise ValueError("probe lower address changed")
    if _non_negative_int(payload, "upper_address") != UPPER_ADDRESS:
        raise ValueError("probe upper address changed")
    samples: dict[str, bytes] = {}
    for key in ("lower_sample_hex", "upper_sample_hex"):
        sample = payload.get(key)
        if not isinstance(sample, str) or len(sample) != SAMPLE_BYTES * 2:
            raise ValueError(f"{key} must be a 32-byte hexadecimal sample")
        try:
            samples[key] = bytes.fromhex(sample)
        except ValueError as error:
            raise ValueError(f"{key} is not hexadecimal") from error
        if len(samples[key]) != SAMPLE_BYTES:
            raise ValueError(f"{key} is not a 32-byte hexadecimal sample")
    for key in ("lower_read_status", "upper_read_status", "out_of_range_read_status",
                "lower_mmap_status", "upper_mmap_status"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError(f"{key} must be a non-empty status name")
    bool_keys = (
        "flash_size_matches_expected", "lower_read_mmap_equal", "upper_read_mmap_equal",
        "upper_read_all_ff", "out_of_range_read_rejected", "lower_mmap_released",
        "upper_mmap_released", "lower_mmap_pages_restored", "upper_mmap_pages_restored",
        "mmap_pages_restored", "writes_performed", "probe_passed",
    )
    for key in bool_keys:
        _bool(payload, key)
    if payload["flash_size_matches_expected"] is not True:
        raise ValueError("probe Flash-size claim is inconsistent")
    if payload["out_of_range_read_rejected"] != (
        payload["out_of_range_read_status"] == "ESP_ERR_INVALID_ARG"
    ):
        raise ValueError("out-of-range status and rejection claim disagree")
    if payload["upper_read_all_ff"] != all(
        byte == 0xFF for byte in samples["upper_sample_hex"]
    ):
        raise ValueError("upper sample and erased-region claim disagree")
    if payload["writes_performed"]:
        raise ValueError("E1.42 probe claims a write")
    for key in (
        "mmap_pages_before", "mmap_pages_while_lower_mapped", "mmap_pages_after_lower",
        "mmap_pages_while_upper_mapped", "mmap_pages_after_upper",
    ):
        _non_negative_int(payload, key)
    if payload["lower_mmap_pages_restored"] != (
        payload["mmap_pages_after_lower"] == payload["mmap_pages_before"]
    ):
        raise ValueError("lower mmap page-restoration claim is inconsistent")
    if payload["upper_mmap_pages_restored"] != (
        payload["mmap_pages_after_upper"] == payload["mmap_pages_before"]
    ):
        raise ValueError("upper mmap page-restoration claim is inconsistent")
    if payload["mmap_pages_restored"] != (
        payload["lower_mmap_pages_restored"]
        and payload["upper_mmap_pages_restored"]
    ):
        raise ValueError("aggregate mmap page-restoration claim is inconsistent")
    for prefix in ("before", "lower_mapped", "upper_mapped", "after"):
        for suffix in ("internal_free", "internal_largest", "internal_minimum",
                       "spiram_free", "spiram_largest", "spiram_minimum"):
            _non_negative_int(payload, f"{prefix}_{suffix}")
    for key, expected in (("lower_sample_hex", expected_lower_sample),
                          ("upper_sample_hex", expected_upper_sample)):
        if expected is not None and (
            not isinstance(expected, bytes)
            or len(expected) != SAMPLE_BYTES
            or samples[key] != expected
        ):
            raise ValueError(f"{key} does not match the E1.40 full-readback reference")
    passed = all(payload[key] for key in (
        "flash_size_matches_expected", "lower_read_mmap_equal", "upper_read_mmap_equal",
        "upper_read_all_ff", "out_of_range_read_rejected", "lower_mmap_released",
        "upper_mmap_released", "mmap_pages_restored",
    )) and all(payload[key] == "ESP_OK" for key in (
        "lower_read_status", "upper_read_status", "lower_mmap_status", "upper_mmap_status"
    )) and payload["out_of_range_read_status"] == "ESP_ERR_INVALID_ARG"
    if payload["probe_passed"] != passed:
        raise ValueError("probe_passed is inconsistent with its component checks")
    return {
        "schema": SCHEMA,
        "flash_size_bytes": payload["flash_size"],
        "boundary": payload["boundary"],
        "sample_bytes": payload["sample_bytes"],
        "probe_passed": passed,
        "writes_performed": False,
        "lower_sample_hex": payload["lower_sample_hex"],
        "upper_sample_hex": payload["upper_sample_hex"],
        "mmap_pages_before": payload["mmap_pages_before"],
        "mmap_pages_after_lower": payload["mmap_pages_after_lower"],
        "mmap_pages_after_upper": payload["mmap_pages_after_upper"],
    }


__all__ = ["BOUNDARY", "LOWER_ADDRESS", "PHYSICAL_FLASH_BYTES", "SAMPLE_BYTES",
           "SCHEMA", "UPPER_ADDRESS", "parse_probe"]
