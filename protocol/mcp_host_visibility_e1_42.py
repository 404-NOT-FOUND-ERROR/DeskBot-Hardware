"""Validate a paired MCP host capture for the E1.42 read-only tools.

The capture is an offline evidence file. It contains the host's complete
tools/list pagination and paired tools/call exchanges, but this module never
opens a serial port, contacts a host, or writes a device.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from protocol.capacity_query import validate_expected_capacity
from protocol.storage_capacity_probe import parse_probe

SCHEMA = "deskbot.mcp.host_visibility.e1_42/v1"
REPORT_SCHEMA = "deskbot.mcp.host_visibility_report.e1_42/v1"
TARGET_TOOLS = (
    "self.get_flash_capacity",
    "self.probe_flash_addressing",
    "self.echo_base.events",
    "self.shell.identify",
    "self.shell.list",
)
REQUIRED_CALL_TOOLS = ("self.get_flash_capacity", "self.probe_flash_addressing")
MAX_CAPTURE_BYTES = 524_288
MAX_PAGES = 64
MAX_CALLS = 32
MAX_TEXT_BYTES = 131_072


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON numeric constant: {value}")


def strict_json(raw: bytes, *, limit: int, label: str) -> Any:
    if len(raw) > limit:
        raise ValueError(f"{label} exceeds {limit} bytes")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error


def _object(value: Any, required: set[str], optional: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    keys = set(value)
    if not required <= keys or keys - required - optional:
        raise ValueError(f"{label} has missing or unexpected fields")
    return value


def _utc(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("host_received_at_utc must be an RFC 3339 UTC timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("host_received_at_utc must be an RFC 3339 UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("host_received_at_utc must use UTC")
    return value


def _envelope(value: Any, label: str) -> tuple[dict[str, Any], str | None]:
    envelope = _object(value, {"type", "payload"}, {"session_id"}, label)
    if envelope["type"] != "mcp":
        raise ValueError(f"{label}.type must be mcp")
    session_id = envelope.get("session_id")
    if session_id is not None and (not isinstance(session_id, str) or len(session_id) > 256):
        raise ValueError(f"{label}.session_id must be a string of at most 256 characters")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ValueError(f"{label}.payload must be an object")
    return payload, session_id


def _rpc_pair(exchange: Any, method: str, index: int) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    exchange = _object(exchange, {"request_message", "response_message"}, set(), f"exchange[{index}]")
    request, request_session = _envelope(exchange["request_message"], f"exchange[{index}].request_message")
    response, response_session = _envelope(exchange["response_message"], f"exchange[{index}].response_message")
    if request_session is not None and response_session is not None and request_session != response_session:
        raise ValueError(f"exchange[{index}] request and response session_id differ")
    session = request_session if request_session is not None else response_session
    request = _object(request, {"jsonrpc", "id", "method"}, {"params"}, f"exchange[{index}].request")
    if request["jsonrpc"] != "2.0" or type(request["id"]) is not int or not 0 <= request["id"] <= 2_147_483_647:
        raise ValueError(f"exchange[{index}].request is not a valid JSON-RPC request")
    if request["method"] != method or not isinstance(request.get("params", {}), dict):
        raise ValueError(f"exchange[{index}].request method or params mismatch")
    request["params"] = request.get("params", {})
    response = _object(response, {"jsonrpc", "id"}, {"result", "error"}, f"exchange[{index}].response")
    if response["jsonrpc"] != "2.0" or type(response["id"]) is not int or response["id"] != request["id"]:
        raise ValueError(f"exchange[{index}].response does not match request id")
    if ("result" in response) == ("error" in response):
        raise ValueError(f"exchange[{index}].response must contain exactly one result or error")
    if "error" in response:
        error = _object(response["error"], {"message"}, {"code", "data"}, f"exchange[{index}].error")
        if not isinstance(error["message"], str) or not error["message"]:
            raise ValueError(f"exchange[{index}].error.message must be non-empty")
        if "code" in error and (isinstance(error["code"], bool) or not isinstance(error["code"], int)):
            raise ValueError(f"exchange[{index}].error.code must be an integer")
    return request, response, session


def _list_result(response: dict[str, Any], index: int) -> tuple[list[str], str | None]:
    if "error" in response:
        raise ValueError(f"tools/list exchange[{index}] returned an error")
    result = _object(response["result"], {"tools"}, {"nextCursor"}, f"tools/list[{index}].result")
    tools = result["tools"]
    if not isinstance(tools, list):
        raise ValueError(f"tools/list[{index}].result.tools must be an array")
    names: list[str] = []
    for tool_index, tool in enumerate(tools):
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
            raise ValueError(f"tools/list[{index}].tools[{tool_index}] has no valid name")
        if tool["name"] in names:
            raise ValueError(f"tools/list[{index}] repeats tool {tool['name']}")
        names.append(tool["name"])
    cursor = result.get("nextCursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise ValueError(f"tools/list[{index}].result.nextCursor must be a non-empty string")
    return names, cursor


def _classify_error(message: str, code: int | None = None) -> str:
    """Classify host/MCP failures without treating a voice summary as evidence."""
    lowered = message.casefold()
    if code == -32601 or re.search(r"unknown\s+(?:tool|method)|(?:tool|method)\s+not\s+found|no\s+such\s+tool", lowered):
        return "unknown_tool"
    if code == -32602 or re.search(r"invalid\s+(?:param(?:eter)?s?|argument)|parameter\s+validation|argument\s+validation", lowered):
        return "invalid_parameters"
    if re.search(r"permission|forbidden|unauthori[sz]ed|access\s+denied|not\s+allowed|user[- ]only", lowered):
        return "permission_denied"
    return "rpc_error"


def _error_report(message: str, *, code: int | None = None, source: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "error",
        "error_kind": _classify_error(message, code),
        "message": message,
        "source": source,
    }
    if code is not None:
        report["code"] = code
    return report


def _call_result(response: dict[str, Any], index: int, name: str) -> dict[str, Any]:
    if "error" in response:
        error = response["error"]
        return _error_report(error["message"], code=error.get("code"), source="jsonrpc")
    result = _object(response["result"], {"content", "isError"}, set(), f"tools/call[{index}].result")
    if type(result["isError"]) is not bool:
        raise ValueError(f"tools/call[{index}].result.isError must be boolean")
    content = result["content"]
    if not isinstance(content, list) or len(content) != 1:
        raise ValueError(f"tools/call[{index}].result.content must contain one block")
    block = _object(content[0], {"type", "text"}, set(), f"tools/call[{index}].content[0]")
    if block["type"] != "text" or not isinstance(block["text"], str):
        raise ValueError(f"tools/call[{index}] must return one text block")
    raw_text = block["text"].encode("utf-8")
    if result["isError"]:
        return _error_report(block["text"], source="mcp_result")
    try:
        payload = strict_json(raw_text, limit=MAX_TEXT_BYTES, label=f"tools/call[{index}].text")
    except ValueError as error:
        return {"status": "invalid_payload", "error_kind": "invalid_json_payload",
                "message": str(error), "source": "mcp_result"}
    if not isinstance(payload, dict):
        return {"status": "invalid_payload", "error_kind": "invalid_json_payload",
                "message": f"tools/call[{index}].text must contain a JSON object",
                "source": "mcp_result"}
    report: dict[str, Any] = {
        "status": "ok",
        "payload_sha256": hashlib.sha256(raw_text).hexdigest(),
        "payload_bytes": len(raw_text),
        "payload_keys": sorted(payload),
        "payload": payload,
        "payload_json": block["text"],
    }
    if name == "self.get_flash_capacity":
        try:
            validate_expected_capacity(payload)
        except (TypeError, ValueError) as error:
            report["status"] = "invalid_payload"
            report["error_kind"] = "capacity_payload_invalid"
            report["message"] = str(error)
            report["capacity_validation"] = "failed"
        else:
            report["capacity_validation"] = "passed"
    elif name == "self.probe_flash_addressing":
        try:
            normalized_probe = parse_probe(payload)
        except (TypeError, ValueError) as error:
            report["status"] = "invalid_payload"
            report["error_kind"] = "probe_payload_invalid"
            report["message"] = str(error)
            report["probe_validation"] = "failed"
        else:
            report["probe_validation"] = "passed"
            report["probe_runtime_result"] = (
                "probe_passed" if normalized_probe["probe_passed"] else "probe_failed"
            )
            report["probe_summary"] = normalized_probe
    return report


def validate_capture(raw: bytes) -> dict[str, Any]:
    capture = strict_json(raw, limit=MAX_CAPTURE_BYTES, label="host capture")
    capture = _object(capture, {"schema", "host_received_at_utc", "list_pages", "tool_calls"}, set(), "capture")
    if capture["schema"] != SCHEMA:
        raise ValueError("unsupported E1.42 host capture schema")
    received = _utc(capture["host_received_at_utc"])
    pages = capture["list_pages"]
    calls = capture["tool_calls"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError(f"list_pages must contain 1..{MAX_PAGES} exchanges")
    if not isinstance(calls, list) or not 1 <= len(calls) <= MAX_CALLS:
        raise ValueError(f"tool_calls must contain 1..{MAX_CALLS} exchanges")

    sessions: set[str] = set()
    all_names: list[str] = []
    page_reports: list[dict[str, Any]] = []
    expected_cursor: str | None = None
    seen_cursors: set[str] = set()
    for index, exchange in enumerate(pages):
        request, response, session = _rpc_pair(exchange, "tools/list", index)
        if session is not None:
            sessions.add(session)
        params = request["params"]
        cursor = params.get("cursor")
        with_user_tools = params.get("withUserTools", False)
        if cursor is not None and not isinstance(cursor, str):
            raise ValueError(f"tools/list[{index}].params.cursor must be a string")
        if with_user_tools is not False:
            raise ValueError("host visibility capture must use the default non-user tools list")
        if index == 0 and cursor is not None:
            raise ValueError("first tools/list request must not contain a cursor")
        if index > 0 and cursor != expected_cursor:
            raise ValueError(f"tools/list[{index}] cursor does not match the previous nextCursor")
        if cursor is not None:
            if cursor in seen_cursors:
                raise ValueError("tools/list cursor chain contains a cycle")
            seen_cursors.add(cursor)
        names, next_cursor = _list_result(response, index)
        if cursor is not None and (not names or names[0] != cursor):
            raise ValueError(f"tools/list[{index}] does not resume at its cursor tool")
        overlap = sorted(set(all_names).intersection(names))
        if overlap:
            raise ValueError(f"tools/list pages overlap: {overlap}")
        all_names.extend(names)
        if next_cursor is None and index != len(pages) - 1:
            raise ValueError("tools/list has an exchange after its terminal page")
        if next_cursor is not None and index == len(pages) - 1:
            raise ValueError("tools/list capture ends before its nextCursor page")
        expected_cursor = next_cursor
        page_reports.append({"index": index, "request_cursor": cursor, "tool_names": names,
                             "next_cursor": next_cursor})

    call_reports: dict[str, dict[str, Any]] = {}
    for index, exchange in enumerate(calls):
        request, response, session = _rpc_pair(exchange, "tools/call", index)
        if session is not None:
            sessions.add(session)
        params = request["params"]
        if set(params) - {"name", "arguments"} or not isinstance(params.get("name"), str):
            raise ValueError(f"tools/call[{index}].params must contain a tool name")
        name = params["name"]
        if name not in TARGET_TOOLS:
            raise ValueError(f"tools/call[{index}] targets an unreviewed tool: {name}")
        arguments = params.get("arguments", {})
        if arguments != {}:
            raise ValueError(f"tools/call[{index}] target tools require an empty arguments object")
        if name in call_reports:
            raise ValueError(f"tools/call repeats target tool: {name}")
        call_reports[name] = _call_result(response, index, name)

    missing_tools = sorted(set(TARGET_TOOLS) - set(all_names))
    missing_calls = sorted(set(REQUIRED_CALL_TOOLS) - set(call_reports))
    call_errors = {name: report["message"] for name, report in call_reports.items()
                   if report["status"] == "error"}
    call_error_kinds = {name: report["error_kind"] for name, report in call_reports.items()
                        if report["status"] == "error"}
    payload_errors = {name: {"kind": report["error_kind"], "message": report["message"]}
                      for name, report in call_reports.items()
                      if report["status"] == "invalid_payload"}
    capacity_error = None
    capacity = call_reports.get("self.get_flash_capacity")
    if capacity and capacity["status"] == "ok":
        if capacity.get("capacity_validation") != "passed":
            capacity_error = capacity.get("message", "capacity payload validation failed")
    elif capacity and capacity.get("error_kind") == "capacity_payload_invalid":
        capacity_error = capacity.get("message", "capacity payload validation failed")
    if missing_tools:
        status = "host_tools_missing"
    elif missing_calls:
        status = "required_calls_missing"
    elif call_errors:
        status = "host_call_error"
    elif capacity_error:
        status = "capacity_payload_invalid"
    elif payload_errors:
        status = "tool_payload_invalid"
    elif call_reports.get("self.probe_flash_addressing", {}).get("probe_runtime_result") == "probe_failed":
        status = "probe_failed"
    else:
        status = "host_tools_and_calls_captured"
    return {
        "schema": REPORT_SCHEMA,
        "edition": "E1.42.2",
        "result": status,
        "source": {
            "capture_file_sha256": hashlib.sha256(raw).hexdigest(),
            "capture_file_bytes": len(raw),
            "host_received_at_utc": received,
            "session_ids_recorded": len(sessions),
            "session_ids_copied": False,
        },
        "tools_list": {
            "pages": page_reports,
            "visible_tools": all_names,
            "target_tools": list(TARGET_TOOLS),
            "missing_tools": missing_tools,
            "page_overlap": False,
        },
        "tool_calls": {
            "required_tools": list(REQUIRED_CALL_TOOLS),
            "reports": call_reports,
            "missing_required_calls": missing_calls,
            "errors": call_errors,
            "error_kinds": call_error_kinds,
            "payload_errors": payload_errors,
        },
        "safety": {
            "serial_accessed": False,
            "device_written": False,
            "nvs_changed": False,
            "deployment_eligible": False,
        },
        "next_action": (
            "Review the retained nested payloads. Export payload_json for self.probe_flash_addressing "
            "with scripts/validate-mcp-host-e1-42.py --probe-output, then run "
            "scripts/validate-expression-e1-42-probe.py against the E1.40 full-readback reference."
        ),
    }


__all__ = ["MAX_CAPTURE_BYTES", "REPORT_SCHEMA", "REQUIRED_CALL_TOOLS", "SCHEMA",
           "TARGET_TOOLS", "validate_capture"]
