#!/usr/bin/env python3
"""Audit the firmware-side MCP visibility contract without opening a device."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER = ROOT / "firmware/body/main/mcp_server.cc"
DEV_TOOLS = ROOT / "firmware/body/main/boards/esp_vocat/dev_tools.cc"
TARGET_TOOLS = [
    "self.get_flash_capacity",
    "self.probe_flash_addressing",
    "self.echo_base.events",
    "self.shell.identify",
    "self.shell.list",
]


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise ValueError(f"missing {label}: {needle}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--boot-log", type=Path,
                        help="optional startup log from this exact firmware build")
    args = parser.parse_args(argv)

    server = MCP_SERVER.read_text(encoding="utf-8")
    dev = DEV_TOOLS.read_text(encoding="utf-8")
    boot = None
    if args.boot_log is not None:
        if not args.boot_log.is_file():
            raise ValueError(f"startup log does not exist: {args.boot_log}")
        boot = args.boot_log.read_text(encoding="utf-8", errors="replace")

    add_tool = server[server.index("void McpServer::AddTool(McpTool* tool)"):]
    add_user = server[server.index("void McpServer::AddUserOnlyTool"):]
    require(add_tool, "tools_.push_back(tool);", "ordinary AddTool insertion")
    require(add_user, "tool->set_user_only(true);", "user-only marker")
    require(server, "bool list_user_only_tools = false;", "default user-only filter")
    require(server, "const int max_payload_size = 8000;", "tools/list payload bound")
    require(server, 'next_cursor = (*it)->name();', "tools/list cursor")
    list_body = server[server.index("void McpServer::GetToolsList("):server.index("void McpServer::DoToolCall(")]
    cursor_assignment = list_body.index('next_cursor = (*it)->name();')
    page_append = list_body.index("json += tool_json;")
    cursor_resume = list_body.index("if ((*it)->name() == cursor)")
    if not cursor_assignment < page_append:
        raise ValueError("nextCursor must be assigned before the boundary tool is appended")
    if "found_cursor = true;" not in list_body[cursor_resume:page_append]:
        raise ValueError("follow-up page must resume at the nextCursor tool")

    positions = {}
    for name in TARGET_TOOLS:
        ordinary = f'mcp_server.AddTool("{name}"'
        user_only = f'mcp_server.AddUserOnlyTool("{name}"'
        if ordinary not in dev or user_only in dev:
            raise ValueError(f"{name} is missing ordinary AddTool registration or is user-only")
        positions[name] = dev.index(ordinary)
    ordered = [name for name, _ in sorted(positions.items(), key=lambda item: item[1])]
    if ordered != TARGET_TOOLS:
        raise ValueError(f"target registration order drifted: {ordered!r}")

    boot_hits = None if boot is None else {
        name: f"MCP: Add tool: {name}" in boot for name in TARGET_TOOLS
    }
    report = {
        "schema": "deskbot.mcp_visibility.e1_42_static/v1",
        "edition": "E1.42.1",
        "result": "firmware_visibility_contract_passed_host_evidence_pending",
        "target_tools": TARGET_TOOLS,
        "registration": {
            "dev_tools_registration_order": ordered,
            "ordinary_add_tool": True,
            "user_only_marker_is_explicit": True,
            "ordinary_tools_pass_default_user_only_filter": True,
            "payload_limit_bytes": 8000,
            "next_cursor_supported": True,
            "cursor_is_first_unreturned_tool": True,
            "pages_overlap_at_cursor": False,
            "cursor_note": "The size-limited page stops before appending the cursor tool; the follow-up page resumes at that first unreturned tool.",
        },
        "startup_log": {
            "provided": boot is not None,
            "source": str(args.boot_log) if args.boot_log is not None else None,
            "target_registration_hits": boot_hits,
        },
        "host_evidence": {
            "tools_list_pages": False,
            "target_tools_visible": False,
            "raw_tools_call": False,
            "status": "pending",
        },
        "safety": {
            "serial_opened": False,
            "device_written": False,
            "nvs_changed": False,
        },
        "next_action": "Capture all tools/list pages and one raw tools/call response from the same MCP host; do not change firmware permissions before that evidence.",
    }
    output = args.output or ROOT / "artifacts/expression-capacity-read-mmap-e1-42-20261031/mcp-visibility-static.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"E1.42.1 MCP visibility audit passed: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"E1.42.1 MCP visibility audit blocked: {error}", file=sys.stderr)
        raise SystemExit(2)
