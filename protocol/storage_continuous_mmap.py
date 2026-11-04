"""Offline E1.43b content references and bounded CPU capture validation."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from protocol.capacity_query import UPPER_ASSETS_8M_PARTITIONS, validate_expected_capacity

START = 0x01000000
SIZE = 0x00800000
BLOCK = 0x00100000
PAGE = 65536
HOLD_MS = 180000
INTERVAL_MS = 10000
SAMPLES = 19
MEMORY = ("internal_free", "internal_largest", "internal_minimum",
          "spiram_free", "spiram_largest", "spiram_minimum")
FLOORS = {"internal_free": 16384, "internal_largest": 8192,
          "spiram_free": 1048576, "spiram_largest": 262144, "stack_free_bytes": 1024}
RECORD = re.compile(r"\[DESKBOT_E143B (capacity|begin|snapshot|block|sample|summary|aborted) bytes=([0-9]{1,4})\] (.*)$")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(name + " must be a lowercase SHA-256 digest")
    return value


def _integer(value: Any, name: str, maximum: int = 0xFFFFFFFF) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(name + " must be a bounded non-negative integer")
    return value


def reference_for(raw: bytes, *, identity_sha256: str, run_label: str,
                  source_sha256: str) -> dict:
    """Build a reference from the same run's captured eight MiB, not a build asset."""
    if len(raw) != SIZE:
        raise ValueError("reference must contain exactly eight MiB")
    _digest(identity_sha256, "identity_sha256")
    _digest(source_sha256, "source_sha256")
    if not isinstance(run_label, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", run_label) is None:
        raise ValueError("invalid run label")
    return {"schema": "deskbot.continuous_mmap.reference.v1", "identity_sha256": identity_sha256,
            "run_label": run_label, "source_sha256": source_sha256, "start": START, "bytes": SIZE,
            "sha256": sha(raw), "block_sha256": [sha(raw[i:i + BLOCK]) for i in range(0, SIZE, BLOCK)],
            "head_hex": raw[:32].hex(), "tail_hex": raw[-32:].hex()}


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError("non-finite JSON value: " + value)


def extract_records(log: str) -> list[dict]:
    records = []
    for line in log.splitlines():
        if "[DESKBOT_E143B " not in line:
            continue
        match = RECORD.search(line)
        if match is None:
            raise ValueError("malformed E1.43b record")
        kind, count, payload = match.groups()
        if not 0 < int(count) <= 2048 or len(payload.encode("utf-8")) != int(count):
            raise ValueError("E1.43b record length mismatch")
        try:
            value = json.loads(payload, object_pairs_hook=_unique, parse_constant=_nonfinite)
        except json.JSONDecodeError as error:
            raise ValueError("invalid E1.43b JSON") from error
        if not isinstance(value, dict):
            raise ValueError("record payload must be an object")
        records.append({"kind": kind, "payload": value})
        if len(records) > 40:
            raise ValueError("too many E1.43b records")
    if not records:
        raise ValueError("no E1.43b records")
    return records


def _reference(value: dict) -> None:
    keys = {"schema", "identity_sha256", "run_label", "source_sha256", "start", "bytes", "sha256",
            "block_sha256", "head_hex", "tail_hex"}
    if not isinstance(value, dict) or set(value) != keys or value["schema"] != "deskbot.continuous_mmap.reference.v1":
        raise ValueError("invalid reference fields or schema")
    if (_integer(value["start"], "start") != START or _integer(value["bytes"], "bytes") != SIZE
            or not isinstance(value["run_label"], str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", value["run_label"]) is None):
        raise ValueError("invalid reference geometry or run label")
    for key in ("identity_sha256", "source_sha256", "sha256"):
        _digest(value[key], key)
    if not isinstance(value["block_sha256"], list) or len(value["block_sha256"]) != 8:
        raise ValueError("eight reference block digests required")
    for digest in value["block_sha256"]:
        _digest(digest, "block digest")
    for key in ("head_hex", "tail_hex"):
        _digest(value[key], key)


def _snapshot(row: dict, phase: str, index: int) -> None:
    keys = {"phase", "index", "elapsed_ms", "hold_elapsed_ms", "state", "audio_idle", "pages",
            "stack_free_bytes", "head_hex", "tail_hex", *MEMORY}
    if not isinstance(row, dict) or set(row) != keys or row["phase"] != phase:
        raise ValueError("invalid snapshot fields or phase")
    for key in ("index", "elapsed_ms", "hold_elapsed_ms", "state", "pages", "stack_free_bytes", *MEMORY):
        _integer(row[key], key)
    if row["index"] != index or row["state"] > 10 or row["pages"] > 1024 or row["stack_free_bytes"] > 8192:
        raise ValueError("invalid snapshot index, state, pages or stack")
    if type(row["audio_idle"]) is not bool:
        raise ValueError("audio_idle must be a boolean")
    for heap in ("internal", "spiram"):
        if row[heap + "_largest"] > row[heap + "_free"] or row[heap + "_minimum"] > row[heap + "_free"]:
            raise ValueError("impossible heap snapshot")
    for key in ("head_hex", "tail_hex"):
        if phase == "hold":
            _digest(row[key], key)
        elif row[key] != "":
            raise ValueError("released/unmapped snapshot must not dereference a mapping")


def validate_capture(records: list[dict], reference: dict) -> dict:
    _reference(reference)
    if not isinstance(records, list) or not records:
        raise ValueError("records must be a non-empty list")
    if any(not isinstance(row, dict) or set(row) != {"kind", "payload"} for row in records):
        raise ValueError("invalid record envelope")
    kinds = [row["kind"] for row in records]
    if kinds == ["aborted"]:
        aborted = records[0]["payload"]
        if (set(aborted) != {"error", "writes_performed"} or aborted["writes_performed"] is not False
                or aborted["error"] not in {"capacity_or_quiet_state_rejected", "quiet_state_timeout"}):
            raise ValueError("invalid preflight abort")
        return {"probe_passed": False, "result": "probe_aborted", "error": aborted["error"]}
    expected_order = ["capacity", "begin", "snapshot", "snapshot"] + ["block"] * 8 + ["sample"] * SAMPLES + ["snapshot", "summary"]
    if kinds != expected_order:
        raise ValueError("incomplete, duplicate or out-of-order E1.43b capture")
    capacity = validate_expected_capacity(records[0]["payload"], layout="upper-assets-8m")
    if {row["label"] for row in capacity["partitions"]} != set(UPPER_ASSETS_8M_PARTITIONS):
        raise ValueError("unexpected extra partition")
    begin = records[1]["payload"]
    expected = {"schema": "deskbot.continuous_mmap.begin.v1", "start": START, "bytes": SIZE,
                "page_bytes": PAGE, "chunk_bytes": 1024, "hold_ms": HOLD_MS,
                "interval_ms": INTERVAL_MS, "writes_performed": False}
    if (begin != expected or begin["writes_performed"] is not False
            or any(type(begin[key]) is not int for key in expected if key not in {"schema", "writes_performed"})):
        raise ValueError("mapping geometry or read-only claim changed")
    before, mapped = records[2]["payload"], records[3]["payload"]
    after, summary = records[-2]["payload"], records[-1]["payload"]
    for row, phase in ((before, "before"), (mapped, "mapped"), (after, "after")):
        _snapshot(row, phase, 0)
    blocks = [row["payload"] for row in records[4:12]]
    samples = [row["payload"] for row in records[12:-2]]
    checks = {}
    for index, row in enumerate(blocks):
        if set(row) != {"index", "address", "bytes", "read_sha256", "mmap_sha256"}:
            raise ValueError("invalid block fields")
        for key in ("index", "address", "bytes"):
            _integer(row[key], key)
        _digest(row["read_sha256"], "read_sha256")
        _digest(row["mmap_sha256"], "mmap_sha256")
        checks[f"block_{index}"] = (row["index"] == index and row["address"] == START + index * BLOCK
                                    and row["bytes"] == BLOCK
                                    and row["read_sha256"] == reference["block_sha256"][index]
                                    and row["mmap_sha256"] == reference["block_sha256"][index])
    for index, row in enumerate(samples):
        _snapshot(row, "hold", index)
    required = {"schema", "error", "mapping_status", "mapping_acquired", "mapping_released", "blocks", "samples",
                "held_ms", "pages_before", "pages_after", "read_sha256", "mmap_sha256", "memory_ok",
                "writes_performed", "probe_passed"}
    if not isinstance(summary, dict) or set(summary) != required or summary["schema"] != "deskbot.continuous_mmap.summary.v1":
        raise ValueError("invalid summary fields or schema")
    for key in ("mapping_acquired", "mapping_released", "memory_ok", "writes_performed", "probe_passed"):
        if type(summary[key]) is not bool:
            raise ValueError("invalid summary boolean: " + key)
    for key in ("blocks", "samples", "held_ms", "pages_before", "pages_after"):
        _integer(summary[key], key)
    for key in ("read_sha256", "mmap_sha256"):
        _digest(summary[key], key)
    snapshots = [before, mapped, *samples, after]
    times = [row["elapsed_ms"] for row in snapshots]
    starts = [row["elapsed_ms"] - row["hold_elapsed_ms"] for row in samples]
    checks.update({
        "single_8m_mapping_page_accounting": before["pages"] >= 16
            and before["pages"] - mapped["pages"] in {0, 128} and mapped["pages"] >= 16,
        "hold_page_reserve": all(row["pages"] >= 16 for row in samples),
        "pages_restored": after["pages"] == before["pages"] == summary["pages_before"] == summary["pages_after"],
        "memory_floors": all(all(row[key] >= floor for key, floor in FLOORS.items()) for row in snapshots),
        "snapshots_monotonic": all(a <= b for a, b in zip(times, times[1:])),
        "hold_window": all(index * INTERVAL_MS <= row["hold_elapsed_ms"] <= index * INTERVAL_MS + 30000
                           for index, row in enumerate(samples))
            and all(a["hold_elapsed_ms"] < b["hold_elapsed_ms"] for a, b in zip(samples, samples[1:]))
            and max(starts) - min(starts) <= 2,
        "endpoints_unchanged": all(row["head_hex"] == reference["head_hex"]
                                   and row["tail_hex"] == reference["tail_hex"] for row in samples),
        "read_digest_matches_snapshot": summary["read_sha256"] == reference["sha256"],
        "mapped_digest_matches_snapshot": summary["mmap_sha256"] == reference["sha256"],
        "mapping_released": summary["mapping_acquired"] is True and summary["mapping_released"] is True,
        "summary_matches_window": summary["blocks"] == 8 and summary["samples"] == SAMPLES
            and summary["held_ms"] == samples[-1]["hold_elapsed_ms"] == after["hold_elapsed_ms"]
            and before["hold_elapsed_ms"] == mapped["hold_elapsed_ms"] == 0,
        "firmware_passed": summary["error"] == "none" and summary["mapping_status"] == "ESP_OK"
            and summary["memory_ok"] is True and summary["probe_passed"] is True,
        "read_only": summary["writes_performed"] is False,
    })
    passed = all(checks.values())
    return {"schema": "deskbot.continuous_mmap.validation.v1", "probe_passed": passed,
            "result": "runtime_checks_passed" if passed else "runtime_checks_failed", "checks": checks,
            "capacity": capacity, "reference": reference,
            "mapping_mode": "shared_existing" if mapped["pages"] == before["pages"] else "new_128_pages",
            "memory_minima": {key: min(row[key] for row in snapshots) for key in (*MEMORY, "stack_free_bytes")},
            "observed_states": sorted({row["state"] for row in samples}),
            "audio_active_observed": any(not row["audio_idle"] for row in samples),
            "physical_interaction_accepted": False,
            "claim_scope": "continuous_mapping_content_pages_and_sampled_memory_only"}
