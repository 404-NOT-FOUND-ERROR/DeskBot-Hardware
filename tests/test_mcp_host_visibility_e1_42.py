from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from protocol.mcp_host_visibility_e1_42 import TARGET_TOOLS, validate_capture
from protocol.capacity_query import LEGACY_PARTITIONS, PHYSICAL_FLASH_BYTES
from protocol.storage_capacity_probe import BOUNDARY, LOWER_ADDRESS, SAMPLE_BYTES, SCHEMA as PROBE_SCHEMA, UPPER_ADDRESS

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate-mcp-host-e1-42.py"


def envelope(payload: dict, session: str = "session-e142") -> dict:
    return {"type": "mcp", "session_id": session, "payload": payload}


def rpc_request(request_id: int, method: str, params: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return envelope(payload)


def rpc_result(request_id: int, result: dict) -> dict:
    return envelope({"jsonrpc": "2.0", "id": request_id, "result": result})


def tool_list_exchange(request_id: int, names: list[str], *, cursor: str | None = None,
                       next_cursor: str | None = None) -> dict:
    params = {} if cursor is None else {"cursor": cursor}
    result = {"tools": [{"name": name, "description": "read-only"} for name in names]}
    if next_cursor is not None:
        result["nextCursor"] = next_cursor
    return {"request_message": rpc_request(request_id, "tools/list", params),
            "response_message": rpc_result(request_id, result)}


def tool_call_exchange(request_id: int, name: str, payload: dict | None = None) -> dict:
    request = rpc_request(request_id, "tools/call", {"name": name, "arguments": {}})
    if payload is None:
        response = envelope({"jsonrpc": "2.0", "id": request_id,
                             "error": {"message": "permission denied"}})
    else:
        text = json.dumps(payload, separators=(",", ":"))
        response = rpc_result(request_id, {"content": [{"type": "text", "text": text}],
                                           "isError": False})
    return {"request_message": request, "response_message": response}


def valid_probe() -> dict:
    payload = {
        "schema": PROBE_SCHEMA,
        "flash_size": PHYSICAL_FLASH_BYTES,
        "flash_size_matches_expected": True,
        "boundary": BOUNDARY,
        "sample_bytes": SAMPLE_BYTES,
        "lower_address": LOWER_ADDRESS,
        "upper_address": UPPER_ADDRESS,
        "lower_sample_hex": "ff" * SAMPLE_BYTES,
        "upper_sample_hex": "ff" * SAMPLE_BYTES,
        "lower_read_status": "ESP_OK",
        "upper_read_status": "ESP_OK",
        "out_of_range_read_status": "ESP_ERR_INVALID_ARG",
        "lower_mmap_status": "ESP_OK",
        "upper_mmap_status": "ESP_OK",
        "lower_read_mmap_equal": True,
        "upper_read_mmap_equal": True,
        "upper_read_all_ff": True,
        "out_of_range_read_rejected": True,
        "lower_mmap_released": True,
        "upper_mmap_released": True,
        "mmap_pages_before": 10,
        "mmap_pages_while_lower_mapped": 8,
        "mmap_pages_after_lower": 10,
        "mmap_pages_while_upper_mapped": 9,
        "mmap_pages_after_upper": 10,
        "lower_mmap_pages_restored": True,
        "upper_mmap_pages_restored": True,
        "mmap_pages_restored": True,
        "writes_performed": False,
        "probe_passed": True,
    }
    for prefix in ("before", "lower_mapped", "upper_mapped", "after"):
        for suffix in ("internal_free", "internal_largest", "internal_minimum",
                       "spiram_free", "spiram_largest", "spiram_minimum"):
            payload[f"{prefix}_{suffix}"] = 100
    return payload


def valid_capture() -> dict:
    capacity = {
        "schema": "deskbot.n32r16.capacity_query.e1_41/v1",
        "source_tool": "self.get_flash_capacity",
        "flash_size": PHYSICAL_FLASH_BYTES,
        "partition_table": [{"label": label, **row} for label, row in LEGACY_PARTITIONS.items()],
    }
    probe = valid_probe()
    return {
        "schema": "deskbot.mcp.host_visibility.e1_42/v1",
        "host_received_at_utc": "2026-10-31T12:00:00Z",
        "list_pages": [
            tool_list_exchange(1, TARGET_TOOLS[:2], next_cursor=TARGET_TOOLS[2]),
            tool_list_exchange(2, TARGET_TOOLS[2:], cursor=TARGET_TOOLS[2]),
        ],
        "tool_calls": [
            tool_call_exchange(3, TARGET_TOOLS[0], capacity),
            tool_call_exchange(4, TARGET_TOOLS[1], probe),
        ],
    }


class McpHostVisibilityE142Tests(unittest.TestCase):
    def test_complete_capture_passes_without_copying_session_id(self) -> None:
        report = validate_capture(json.dumps(valid_capture()).encode())
        self.assertEqual(report["result"], "host_tools_and_calls_captured")
        self.assertEqual(report["tools_list"]["visible_tools"], list(TARGET_TOOLS))
        self.assertEqual(report["source"]["session_ids_recorded"], 1)
        self.assertFalse(report["source"]["session_ids_copied"])

    def test_missing_params_is_allowed_for_tools_list(self) -> None:
        capture = valid_capture()
        capture["list_pages"][0]["request_message"]["payload"].pop("params")
        report = validate_capture(json.dumps(capture).encode())
        self.assertEqual(report["result"], "host_tools_and_calls_captured")

    def test_cursor_must_follow_previous_page(self) -> None:
        capture = valid_capture()
        capture["list_pages"][1]["request_message"]["payload"]["params"]["cursor"] = TARGET_TOOLS[3]
        with self.assertRaisesRegex(ValueError, "cursor does not match"):
            validate_capture(json.dumps(capture).encode())

    def test_page_overlap_is_rejected(self) -> None:
        capture = valid_capture()
        capture["list_pages"][1]["response_message"]["payload"]["result"]["tools"].insert(
            1, {"name": TARGET_TOOLS[0]})
        with self.assertRaisesRegex(ValueError, "pages overlap"):
            validate_capture(json.dumps(capture).encode())

    def test_missing_target_is_reported_without_claiming_pass(self) -> None:
        capture = valid_capture()
        capture["list_pages"][1]["response_message"]["payload"]["result"]["tools"] = [
            {"name": TARGET_TOOLS[2]}, {"name": TARGET_TOOLS[3]}]
        report = validate_capture(json.dumps(capture).encode())
        self.assertEqual(report["result"], "host_tools_missing")
        self.assertEqual(report["tools_list"]["missing_tools"], [TARGET_TOOLS[4]])

    def test_call_error_is_classified(self) -> None:
        capture = valid_capture()
        capture["tool_calls"][1]["response_message"]["payload"] = {
            "jsonrpc": "2.0", "id": 4, "error": {"message": "Unknown tool"}}
        report = validate_capture(json.dumps(capture).encode())
        self.assertEqual(report["result"], "host_call_error")
        self.assertEqual(report["tool_calls"]["errors"][TARGET_TOOLS[1]], "Unknown tool")
        self.assertEqual(report["tool_calls"]["error_kinds"][TARGET_TOOLS[1]], "unknown_tool")

    def test_mcp_is_error_is_classified_as_permission_denied(self) -> None:
        capture = valid_capture()
        capture["tool_calls"][1]["response_message"]["payload"] = {
            "jsonrpc": "2.0", "id": 4, "result": {
                "content": [{"type": "text", "text": "Permission denied for this tool"}],
                "isError": True,
            }}
        report = validate_capture(json.dumps(capture).encode())
        self.assertEqual(report["result"], "host_call_error")
        self.assertEqual(report["tool_calls"]["error_kinds"][TARGET_TOOLS[1]], "permission_denied")

    def test_jsonrpc_invalid_params_is_classified(self) -> None:
        capture = valid_capture()
        capture["tool_calls"][1]["response_message"]["payload"] = {
            "jsonrpc": "2.0", "id": 4,
            "error": {"code": -32602, "message": "invalid params"},
        }
        report = validate_capture(json.dumps(capture).encode())
        self.assertEqual(report["tool_calls"]["error_kinds"][TARGET_TOOLS[1]], "invalid_parameters")

    def test_nested_capacity_and_probe_json_are_retained(self) -> None:
        report = validate_capture(json.dumps(valid_capture()).encode())
        capacity = report["tool_calls"]["reports"][TARGET_TOOLS[0]]
        probe = report["tool_calls"]["reports"][TARGET_TOOLS[1]]
        self.assertEqual(capacity["capacity_validation"], "passed")
        self.assertEqual(capacity["payload"]["source_tool"], TARGET_TOOLS[0])
        self.assertEqual(json.loads(probe["payload_json"]), probe["payload"])
        self.assertEqual(probe["probe_validation"], "passed")
        self.assertEqual(probe["probe_runtime_result"], "probe_passed")

    def test_inconsistent_probe_payload_is_not_reported_as_captured(self) -> None:
        capture = valid_capture()
        payload = json.loads(capture["tool_calls"][1]["response_message"]["payload"]["result"]["content"][0]["text"])
        payload["upper_read_mmap_equal"] = False
        capture["tool_calls"][1]["response_message"]["payload"]["result"]["content"][0]["text"] = json.dumps(payload)
        report = validate_capture(json.dumps(capture).encode())
        self.assertEqual(report["result"], "tool_payload_invalid")
        self.assertEqual(report["tool_calls"]["payload_errors"][TARGET_TOOLS[1]]["kind"], "probe_payload_invalid")

    def test_valid_but_failed_runtime_probe_is_not_a_capture_pass(self) -> None:
        capture = valid_capture()
        payload = valid_probe()
        payload["upper_read_mmap_equal"] = False
        payload["probe_passed"] = False
        capture["tool_calls"][1]["response_message"]["payload"]["result"]["content"][0]["text"] = json.dumps(payload)
        report = validate_capture(json.dumps(capture).encode())
        self.assertEqual(report["result"], "probe_failed")
        self.assertEqual(
            report["tool_calls"]["reports"][TARGET_TOOLS[1]]["probe_runtime_result"],
            "probe_failed",
        )

    def test_cli_exports_raw_probe_json_under_repository_tmp(self) -> None:
        temp_root = ROOT / "tmp"
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mcp-e142-cli-", dir=temp_root) as directory:
            folder = Path(directory)
            capture_path = folder / "capture.json"
            report_path = folder / "report.json"
            probe_path = folder / "probe.json"
            capture_path.write_text(json.dumps(valid_capture()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), "--capture", str(capture_path),
                 "--output", str(report_path), "--probe-output", str(probe_path)],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            probe = json.loads(probe_path.read_text(encoding="utf-8"))
            self.assertEqual(report["result"], "host_tools_and_calls_captured")
            self.assertEqual(probe, valid_probe())

    def test_invalid_capacity_payload_stays_pending(self) -> None:
        capture = valid_capture()
        invalid = {"schema": "deskbot.n32r16.capacity_query.e1_41/v1",
                   "source_tool": TARGET_TOOLS[0], "flash_size": 16 * 1024 * 1024,
                   "partition_table": [{"label": "nvs", "address": 0x9000, "size": 0x4000}]}
        capture["tool_calls"][0]["response_message"]["payload"]["result"]["content"][0]["text"] = json.dumps(invalid)
        report = validate_capture(json.dumps(capture).encode())
        self.assertEqual(report["result"], "capacity_payload_invalid")
        self.assertEqual(report["tool_calls"]["reports"][TARGET_TOOLS[0]]["error_kind"], "capacity_payload_invalid")

    def test_user_only_list_is_rejected(self) -> None:
        capture = valid_capture()
        capture["list_pages"][0]["request_message"]["payload"]["params"] = {"withUserTools": True}
        with self.assertRaisesRegex(ValueError, "non-user tools"):
            validate_capture(json.dumps(capture).encode())

    def test_duplicate_json_key_is_rejected(self) -> None:
        raw = b'{"schema":"deskbot.mcp.host_visibility.e1_42/v1","schema":"x"}'
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            validate_capture(raw)

    def test_fixture_builder_does_not_mutate_source(self) -> None:
        original = valid_capture()
        clone = copy.deepcopy(original)
        validate_capture(json.dumps(clone).encode())
        self.assertEqual(original, valid_capture())


if __name__ == "__main__":
    unittest.main()
