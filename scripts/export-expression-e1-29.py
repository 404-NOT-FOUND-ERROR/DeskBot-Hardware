#!/usr/bin/env python3
"""Normalize a paired, host-captured MCP observation response for E1.27 import.

This is an offline file converter. It never contacts a server, serial port, or
device. The input capture contains the paired tools/call request and response;
the output contains the validated E1.26 snapshot and a privacy-minimized
provenance manifest, but not the transport session identifier or raw envelope.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA_PATH = ROOT / "protocol/expression-mcp-capture-e1-29.schema.json"
SNAPSHOT_SCHEMA_PATH = ROOT / "protocol/expression-runtime-observation-e1-26.schema.json"
INPUT_SCHEMA_ID = "deskbot.expression.mcp_capture_input.e1_29/v1"
SNAPSHOT_SCHEMA_ID = "deskbot.expression.runtime_observation.e1_26/v1"
TOOL_NAME = "self.expression.observation"
MAX_CAPTURE_BYTES = 131072
MAX_SNAPSHOT_TEXT_BYTES = 65536
MAX_SEQUENCE = 9_007_199_254_740_991
UINT32_MAX = 0xFFFFFFFF
SEMANTICS = {
    "angry", "beat_swing", "confident", "confused", "crying", "delicious",
    "embarrassed", "eat", "funny", "happy", "idle", "insert", "laughing",
    "listen", "look_around", "look_left", "look_right", "loving", "neutral",
    "question", "relaxed", "sad", "shocked", "silly", "sleepy", "surprised",
    "thinking", "tried", "winking", "other",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON numeric constant: {value}")


def strict_json(raw: bytes, *, max_bytes: int, label: str) -> Any:
    if len(raw) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes")
    try:
        text = raw.decode("utf-8-sig")
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error


def _object(value: Any, required: set[str], optional: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    keys = set(value)
    if not required <= keys or keys - required - optional:
        raise ValueError(f"{label} has missing or unexpected fields")
    return value


def _valid_utc(value: Any) -> str:
    if (not isinstance(value, str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value)):
        raise ValueError("host_received_at_utc must be an RFC 3339 UTC timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("host_received_at_utc must be an RFC 3339 UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("host_received_at_utc must use UTC")
    return value


def _mcp_payload(envelope: Any, label: str) -> dict[str, Any]:
    envelope = _object(envelope, {"type", "payload"}, {"session_id"}, label)
    if envelope["type"] != "mcp":
        raise ValueError(f"{label}.type must be mcp")
    if "session_id" in envelope and not isinstance(envelope["session_id"], str):
        raise ValueError(f"{label}.session_id must be a string when present")
    if "session_id" in envelope and len(envelope["session_id"]) > 256:
        raise ValueError(f"{label}.session_id exceeds 256 characters")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ValueError(f"{label}.payload must be an object")
    return payload


def _validate_snapshot(snapshot: Any) -> dict[str, Any]:
    required = {
        "ok", "read_only", "best_effort", "schema", "observation_point", "claim_scope",
        "captured_at_ms", "records_overwritten", "records_dropped_contention",
        "records_dropped_sequence_exhausted", "records",
    }
    snapshot = _object(snapshot, required, set(), "E1.26 snapshot")
    constants = {
        "ok": True,
        "read_only": True,
        "best_effort": True,
        "schema": SNAPSHOT_SCHEMA_ID,
        "observation_point": "EmoteDisplay::SetEmotion",
        "claim_scope": "set_emotion_call_only",
    }
    for key, expected in constants.items():
        if type(snapshot[key]) is not type(expected) or snapshot[key] != expected:
            raise ValueError(f"E1.26 snapshot.{key} does not match the fixed contract")

    def uint(value: Any, maximum: int, label: str) -> None:
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"{label} is outside its integer range")

    uint(snapshot["captured_at_ms"], UINT32_MAX, "captured_at_ms")
    for name in ("records_overwritten", "records_dropped_contention", "records_dropped_sequence_exhausted"):
        uint(snapshot[name], UINT32_MAX, name)
    records = snapshot["records"]
    if not isinstance(records, list) or len(records) > 24:
        raise ValueError("E1.26 records must contain at most 24 items")
    previous_sequence = 0
    record_required = {"sequence", "observation", "semantic", "first_seen_ms", "last_seen_ms", "repeat_count"}
    for index, row in enumerate(records):
        row = _object(row, record_required, set(), f"records[{index}]")
        uint(row["sequence"], MAX_SEQUENCE, f"records[{index}].sequence")
        if row["sequence"] == 0 or row["sequence"] <= previous_sequence:
            raise ValueError("record sequence values must be positive, unique and ascending")
        previous_sequence = row["sequence"]
        if row["observation"] != "set_emotion_call_entered":
            raise ValueError(f"records[{index}].observation is not supported")
        if not isinstance(row["semantic"], str) or row["semantic"] not in SEMANTICS:
            raise ValueError(f"records[{index}].semantic is not in the E1.26 allowlist")
        uint(row["first_seen_ms"], UINT32_MAX, f"records[{index}].first_seen_ms")
        uint(row["last_seen_ms"], UINT32_MAX, f"records[{index}].last_seen_ms")
        if type(row["repeat_count"]) is not int or not 1 <= row["repeat_count"] <= 65535:
            raise ValueError(f"records[{index}].repeat_count is outside 1..65535")
    return snapshot


def normalize_capture(raw: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a paired capture and return (E1.26 snapshot, safe provenance)."""
    capture = strict_json(raw, max_bytes=MAX_CAPTURE_BYTES, label="MCP capture")
    capture = _object(capture, {"schema", "host_received_at_utc", "request_message", "response_message"}, set(), "capture")
    if capture["schema"] != INPUT_SCHEMA_ID:
        raise ValueError("unsupported E1.29 capture schema")
    received = _valid_utc(capture["host_received_at_utc"])

    request = _mcp_payload(capture["request_message"], "request_message")
    request = _object(request, {"jsonrpc", "id", "method", "params"}, set(), "request_message.payload")
    if request["jsonrpc"] != "2.0" or request["method"] != "tools/call":
        raise ValueError("capture does not contain an MCP tools/call request")
    request_id = request["id"]
    if type(request_id) is not int or not 0 <= request_id <= 2_147_483_647:
        raise ValueError("request id must be a non-negative 32-bit JSON integer")
    params = _object(request["params"], {"name"}, {"arguments"}, "request_message.payload.params")
    if params["name"] != TOOL_NAME:
        raise ValueError("capture request tool must be self.expression.observation")
    if "arguments" in params and params["arguments"] != {}:
        raise ValueError("observation tool arguments must be empty")

    response = _mcp_payload(capture["response_message"], "response_message")
    if ("session_id" in capture["request_message"] and "session_id" in capture["response_message"]
            and capture["request_message"]["session_id"] != capture["response_message"]["session_id"]):
        raise ValueError("request and response session_id must match when both are present")
    response = _object(response, {"jsonrpc", "id", "result"}, set(), "response_message.payload")
    if response["jsonrpc"] != "2.0" or type(response["id"]) is not int or response["id"] != request_id:
        raise ValueError("response must be JSON-RPC 2.0 and match the request id")
    result = _object(response["result"], {"content", "isError"}, set(), "response_message.payload.result")
    if result["isError"] is not False:
        raise ValueError("MCP tool response is marked as an error")
    content = result["content"]
    if not isinstance(content, list) or len(content) != 1:
        raise ValueError("expected exactly one MCP text content block")
    block = _object(content[0], {"type", "text"}, set(), "MCP result content[0]")
    if block["type"] != "text" or not isinstance(block["text"], str):
        raise ValueError("MCP result must contain one text block")
    snapshot_text = block["text"].encode("utf-8")
    snapshot = _validate_snapshot(strict_json(snapshot_text, max_bytes=MAX_SNAPSHOT_TEXT_BYTES, label="E1.26 snapshot"))

    snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    provenance = {
        "schema": "deskbot.expression.runtime_observation_export.e1_29/v1",
        "edition": "E1.29",
        "result": "validated_mcp_snapshot_ready_for_e1_27_import",
        "source": {
            "transport": "xiaozhi_mcp_envelope",
            "method": "tools/call",
            "tool_name": TOOL_NAME,
            "request_id": request_id,
            "host_received_at_utc": received,
            "capture_file_sha256": sha(raw),
            "capture_file_bytes": len(raw),
        },
        "snapshot": {
            "schema": SNAPSHOT_SCHEMA_ID,
            "file": "observation-snapshot.json",
            "sha256": sha(snapshot_bytes),
            "records": len(snapshot["records"]),
            "first_sequence": snapshot["records"][0]["sequence"] if snapshot["records"] else None,
            "last_sequence": snapshot["records"][-1]["sequence"] if snapshot["records"] else None,
            "records_overwritten": snapshot["records_overwritten"],
            "records_dropped_contention": snapshot["records_dropped_contention"],
            "records_dropped_sequence_exhausted": snapshot["records_dropped_sequence_exhausted"],
        },
        "privacy": {
            "raw_capture_copied_to_output": False,
            "session_id_copied_to_output": False,
            "local_input_path_recorded": False,
        },
        "claim_scope": "SetEmotion call-entry only; not EAF load, LCD render, or action completion",
        "limits": [
            "The host UTC receipt time is provenance only and is not substituted for device uptime fields.",
            "The device snapshot has no boot/session identifier; do not merge sequence numbers across reboots as one timeline.",
            "The 24-record RAM window is best-effort and volatile; overwritten/drop counters must be retained.",
            "repeat_count is a coalesced per-record count, not lifetime call frequency.",
        ],
        "safety": {
            "hardware_accessed": False,
            "serial_accessed": False,
            "device_written": False,
            "nvs_changed": False,
            "deployment_eligible": False,
            "flash_authorized": False,
        },
    }
    return snapshot, provenance


def _safe_output_directory(path: Path) -> Path:
    tmp_root = (ROOT / "tmp").resolve()
    target = path if path.is_absolute() else ROOT / path
    resolved = target.resolve(strict=False)
    if resolved == tmp_root or not resolved.is_relative_to(tmp_root):
        raise ValueError("output directory must be a child of this repository's tmp directory")
    cursor = target
    while cursor != cursor.parent and cursor.resolve(strict=False) != tmp_root:
        if cursor.exists() and cursor.is_symlink():
            raise ValueError("output directory may not traverse symlinks")
        cursor = cursor.parent
    return resolved


def export_files(capture_path: Path, output_path: Path) -> dict[str, Any]:
    with capture_path.open("rb") as source:
        raw = source.read(MAX_CAPTURE_BYTES + 1)
    snapshot, provenance = normalize_capture(raw)
    snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    provenance_bytes = (json.dumps(provenance, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output = _safe_output_directory(output_path)
    if output.exists():
        raise ValueError("output directory already exists; refusing to overwrite evidence")
    output.mkdir(parents=True)
    try:
        (output / "observation-snapshot.json").write_bytes(snapshot_bytes)
        (output / "provenance.json").write_bytes(provenance_bytes)
    except Exception:
        # Keep a failed partial export from looking like a complete evidence set.
        for child in output.iterdir():
            child.unlink()
        output.rmdir()
        raise
    return {"result": provenance["result"], "output": str(output),
            "records": len(snapshot["records"]), "snapshot_sha256": sha(snapshot_bytes),
            "capture_sha256": provenance["source"]["capture_file_sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True, help="paired E1.29 MCP request/response JSON capture")
    parser.add_argument("--out-dir", type=Path, required=True, help="new output directory under repository tmp/")
    args = parser.parse_args(argv)
    try:
        result = export_files(args.capture, args.out_dir)
    except (OSError, ValueError) as error:
        print(f"E1.29 export rejected: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
