#!/usr/bin/env python3
"""Validate one host-captured E1.42 tools/list and tools/call file."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.mcp_host_visibility_e1_42 import MAX_CAPTURE_BYTES, validate_capture


def _safe_output(path: Path) -> Path:
    tmp_root = (ROOT / "tmp").resolve()
    target = (ROOT / path if not path.is_absolute() else path).resolve(strict=False)
    if target == tmp_root or not target.is_relative_to(tmp_root):
        raise ValueError("output must be a file under this repository's tmp directory")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-output", type=Path,
                        help="optionally export the captured probe JSON under this repository's tmp directory")
    args = parser.parse_args(argv)
    try:
        raw = args.capture.read_bytes()
        if len(raw) > MAX_CAPTURE_BYTES:
            raise ValueError(f"capture exceeds {MAX_CAPTURE_BYTES} bytes")
        report = validate_capture(raw)
        output = _safe_output(args.output)
        probe_output = _safe_output(args.probe_output) if args.probe_output is not None else None
        if probe_output == output:
            raise ValueError("report and probe output paths must differ")
        if output.exists() or (probe_output is not None and probe_output.exists()):
            raise ValueError("refusing to overwrite an existing report")
        probe_json = None
        if probe_output is not None:
            probe = report["tool_calls"]["reports"].get("self.probe_flash_addressing")
            if probe is None or probe.get("status") != "ok" or not isinstance(probe.get("payload_json"), str):
                raise ValueError("no valid self.probe_flash_addressing JSON is available to export")
            probe_json = probe["payload_json"]
        output.parent.mkdir(parents=True, exist_ok=True)
        if probe_output is not None:
            probe_output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if probe_output is not None and probe_json is not None:
            with probe_output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(probe_json + "\n")
        details = {"result": report["result"], "output": str(output)}
        if probe_output is not None:
            details["probe_output"] = str(probe_output)
        print(json.dumps(details, ensure_ascii=False))
        return 0 if report["result"] == "host_tools_and_calls_captured" else 1
    except (OSError, ValueError) as error:
        print(f"E1.42.2 host capture rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
