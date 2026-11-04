#!/usr/bin/env python3
"""Build the static E1.29 MCP capture-path workbench from synthetic data."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/expression-e1-29-capture.template.html"
CSS = ROOT / "scripts/expression-e1-29-capture.css"
JS = ROOT / "scripts/expression-e1-29-capture.js"
EXPORTER_PATH = ROOT / "scripts/export-expression-e1-29.py"
OUT = ROOT / "artifacts/expression-e1-29-mcp-capture-20261028"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_exporter():
    spec = importlib.util.spec_from_file_location("e1_29_capture_exporter", EXPORTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load E1.29 exporter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample_capture() -> dict[str, Any]:
    snapshot = {
        "ok": True,
        "read_only": True,
        "best_effort": True,
        "schema": "deskbot.expression.runtime_observation.e1_26/v1",
        "observation_point": "EmoteDisplay::SetEmotion",
        "claim_scope": "set_emotion_call_only",
        "captured_at_ms": 17400,
        "records_overwritten": 0,
        "records_dropped_contention": 0,
        "records_dropped_sequence_exhausted": 0,
        "records": [
            {"sequence": 4, "observation": "set_emotion_call_entered", "semantic": "happy",
             "first_seen_ms": 15800, "last_seen_ms": 15800, "repeat_count": 1},
            {"sequence": 5, "observation": "set_emotion_call_entered", "semantic": "neutral",
             "first_seen_ms": 17120, "last_seen_ms": 17120, "repeat_count": 1},
        ],
    }
    request_payload = {
        "jsonrpc": "2.0", "id": 29, "method": "tools/call",
        "params": {"name": "self.expression.observation", "arguments": {}},
    }
    response_payload = {
        "jsonrpc": "2.0", "id": 29,
        "result": {
            "content": [{"type": "text", "text": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))}],
            "isError": False,
        },
    }
    return {
        "schema": "deskbot.expression.mcp_capture_input.e1_29/v1",
        "host_received_at_utc": "2026-10-28T00:00:29.000Z",
        "request_message": {"type": "mcp", "payload": request_payload},
        "response_message": {"type": "mcp", "payload": response_payload},
    }


def build(check: bool = False) -> dict[str, Any]:
    for path in (TEMPLATE, CSS, JS, EXPORTER_PATH):
        if not path.is_file():
            raise FileNotFoundError(path)
    capture = sample_capture()
    capture_bytes = (json.dumps(capture, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    exporter = _load_exporter()
    snapshot, provenance = exporter.normalize_capture(capture_bytes)
    data = {"edition": "E1.29", "sample_origin": "synthetic_offline_sample", "capture": capture,
            "snapshot": snapshot, "sample_provenance": provenance}
    template = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{E1_29_CSS}}": CSS.read_text(encoding="utf-8"),
        "{{E1_29_JS}}": JS.read_text(encoding="utf-8"),
        "{{E1_29_DATA}}": json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"),
    }
    page = template
    for token, value in replacements.items():
        if page.count(token) != 1:
            raise ValueError(f"template must contain {token} exactly once")
        page = page.replace(token, value)
    page_bytes = page.encode("utf-8")
    snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    report = {
        "schema": "deskbot.expression.e1_29_mcp_capture_workbench/v1",
        "edition": "E1.29",
        "date": "2026-10-28",
        "result": "offline_mcp_capture_contract_and_visualization_ready",
        "audit_pass": True,
        "validation_scope": "synthetic_capture_normalized_by_the_same_offline_exporter",
        "real_device_capture_available": False,
        "exporter": "scripts/export-expression-e1-29.py",
        "capture_schema": "protocol/expression-mcp-capture-e1-29.schema.json",
        "snapshot_schema": "protocol/expression-runtime-observation-e1-26.schema.json",
        "sample": {"origin": "synthetic_offline_sample", "request_id": 29,
                   "records": len(snapshot["records"]), "snapshot_sha256": sha(snapshot_bytes)},
        "outputs": ["index.html", "sample-capture.json", "sample-snapshot.json", "report.json", "README.txt"],
        "boundaries": {
            "hardware_accessed": False, "serial_accessed": False, "device_written": False,
            "firmware_changed": False, "nvs_changed": False, "client_transport_changed": False,
            "deployment_eligible": False, "flash_authorized": False,
            "claim_scope": "SetEmotion call-entry only; not EAF load, LCD render, or action completion",
        },
        "implementation_sha256": {path.name: sha(path.read_bytes()) for path in (TEMPLATE, CSS, JS, EXPORTER_PATH)},
    }
    report_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    outputs = {
        OUT / "index.html": page_bytes,
        OUT / "sample-capture.json": capture_bytes,
        OUT / "sample-snapshot.json": snapshot_bytes,
        OUT / "report.json": report_bytes,
        OUT / "README.txt": ("E1.29 MCP 捕获链路离线演示。sample-capture.json 与 sample-snapshot.json 均为合成数据，非真实设备记录。\n"
                             "页面不连接网络、串口或设备。真实请求/响应捕获入口尚待电脑端 MCP 宿主提供。\n").encode("utf-8"),
    }
    if check:
        for path, raw in outputs.items():
            if not path.is_file() or path.read_bytes() != raw:
                raise ValueError(f"stale generated artifact: {path.relative_to(ROOT)}")
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        for path, raw in outputs.items():
            path.write_bytes(raw)
    return {"result": "checked" if check else "built", "edition": "E1.29",
            "sample_records": len(snapshot["records"]), "html_bytes": len(page_bytes),
            "real_capture": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated artifacts are stale")
    args = parser.parse_args()
    print(json.dumps(build(args.check), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
