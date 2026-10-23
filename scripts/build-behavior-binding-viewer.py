#!/usr/bin/env python3
"""Build a static, read-only behavior graph with the accepted EAF preview atlases."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_DEFAULT = ROOT / "tmp/expression-bindings-e1-4a-20261021/bindings.json"
OUTPUT_DEFAULT = ROOT / "artifacts/expression-binding-e1-4a-20261021"
PREVIEW_DEFAULT = ROOT / "artifacts/expression-workbench-e1-2-20261020/index.html"
COMMIT = "46d427255756fe2a148777ea3f256b615b52e160"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def script_json(value: object) -> str:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def load_preview(report: dict[str, Any], path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    match = re.search(r'<script\b(?=[^>]*\bid="bundle-data")(?=[^>]*\btype="application/json")[^>]*>(.*?)</script>',
                      raw.decode("utf-8"), re.S)
    if match is None:
        raise ValueError("accepted E1 preview has no embedded bundle")
    bundle = json.loads(match[1])
    if bundle.get("schema") != "deskbot.expression_workbench_bundle.v1":
        raise ValueError("unsupported E1 preview bundle")
    if bundle.get("source", {}).get("commit") != COMMIT:
        raise ValueError("preview bundle source commit differs from the pinned audit")

    assets: dict[str, dict[str, Any]] = {}
    expressions: dict[str, dict[str, Any]] = {}
    for semantic in report["semantics"]:
        expression = bundle["expressions"].get(semantic["id"])
        if not expression or expression.get("origin") != "official":
            raise ValueError("official preview missing: " + semantic["id"])
        if any(expression.get(key) != semantic[key] for key in ("src", "fps", "loop")):
            raise ValueError("preview mapping drift: " + semantic["id"])
        asset = bundle["assets"][expression["asset_key"]]
        expected = report["eaf_assets"][semantic["src"]]
        if asset.get("origin") != "official" or asset.get("sha256") != expected["sha256"]:
            raise ValueError("preview EAF does not match fixed source: " + semantic["src"])
        header, encoded = asset["atlas"].split(",", 1)
        if header != "data:image/png;base64":
            raise ValueError("unexpected atlas format")
        atlas = base64.b64decode(encoded, validate=True)
        if (not atlas.startswith(b"\x89PNG\r\n\x1a\n")
                or sha256(atlas) != asset.get("atlas_sha256")):
            raise ValueError("preview atlas checksum mismatch: " + semantic["src"])
        assets[asset["asset_key"]] = asset
        expressions[semantic["id"]] = expression
    return {"expressions": expressions, "assets": assets, "html_sha256": sha256(raw),
            "device_playback_verified": False, "method": "reused verified E1.2 host atlases"}


def validate_report(report: dict[str, Any]) -> None:
    if report.get("schema") != "deskbot.behavior_bindings.v1":
        raise ValueError("unsupported behavior binding report")
    if report.get("source", {}).get("official_commit") != COMMIT:
        raise ValueError("source commit differs from the pinned official audit")
    safety = report.get("safety", {})
    for key in ("hardware_accessed", "firmware_changed", "runtime_observed",
                "device_playback_verified", "automatic_cleanup"):
        if safety.get(key) is not False:
            raise ValueError("offline evidence boundary is not explicit: " + key)
    evidence = report["evidence"]
    inputs = {item["path"]: item for item in report["source"]["inputs"]}
    for item in evidence.values():
        source = inputs.get(item["path"])
        if source is None or source["sha256"] != item["source_sha256"]:
            raise ValueError("evidence does not point at a hashed source input")
        if item["line"] < 1 or item["end_line"] < item["line"]:
            raise ValueError("invalid evidence line span")
    semantics = {item["id"]: item for item in report["semantics"]}
    if len(semantics) != len(report["semantics"]):
        raise ValueError("duplicate semantic ID")
    layouts = report["layouts"]
    for route in report["routes"]:
        if route["runtime_observation"] != "not_collected":
            raise ValueError("static route must not claim runtime observation")
        for output in route["outputs"]:
            semantic = semantics.get(output["semantic"])
            if semantic is None or any(output[key] != semantic[key] for key in ("src", "fps", "loop")):
                raise ValueError("route resource differs from official semantic map")
            if output["layout"] not in layouts:
                raise ValueError("route refers to an unknown layout")
            if output["api"] != "SetEmotion" and output["action_id"] is not None:
                raise ValueError("non-emotion animation inherited a motor action")
    route_ids = {item["id"] for item in report["routes"]}
    nodes = report["graph"]["nodes"]
    node_ids = {item["id"] for item in nodes}
    if len(node_ids) != len(nodes) or len(route_ids) != len(report["routes"]):
        raise ValueError("duplicate graph or route ID")
    for node in nodes:
        if node["route_id"] not in route_ids or not node["evidence"]:
            raise ValueError("graph node is missing route or source evidence")
    for edge in report["graph"]["edges"]:
        if edge["from"] not in node_ids or edge["to"] not in node_ids or not edge["evidence"]:
            raise ValueError("dangling graph edge or evidence")
        if edge.get("runtime_observation") != "not_collected":
            raise ValueError("static graph cannot assert runtime observations")
    refs = [ref for node in nodes for ref in node["evidence"]]
    refs += [ref for edge in report["graph"]["edges"] for ref in edge["evidence"]]
    if any(ref not in evidence for ref in refs):
        raise ValueError("unresolved source evidence ID")


def build(report: dict[str, Any], report_path: Path, preview_path: Path, output: Path) -> dict[str, Any]:
    validate_report(report)
    preview = load_preview(report, preview_path)
    data = script_json({"report": report, "preview": preview})
    template = (ROOT / "scripts/templates/behavior-binding-viewer.html").read_text(encoding="utf-8")
    core = (ROOT / "scripts/templates/expression-workbench-core.js").read_text(encoding="utf-8")
    viewer = (ROOT / "scripts/templates/behavior-binding-viewer.js").read_text(encoding="utf-8")
    page = template.replace("__DATA_JSON__", data).replace("__CORE_JS__", core).replace("__VIEWER_JS__", viewer)
    if any(token in page for token in ("__DATA_JSON__", "__CORE_JS__", "__VIEWER_JS__")):
        raise ValueError("viewer template contains an unexpanded token")
    target = output.resolve()
    target.relative_to(ROOT.resolve())
    target.mkdir(parents=True, exist_ok=True)
    page_raw = page.encode("utf-8")
    binding_raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (target / "index.html").write_bytes(page_raw)
    (target / "bindings.json").write_bytes(binding_raw)
    result = {
        "schema": "deskbot.behavior_binding_viewer.v1",
        "result": "offline_binding_viewer_created",
        "hardware_accessed": False,
        "firmware_changed": False,
        "device_playback_verified": False,
        "source_commit": COMMIT,
        "route_count": len(report["routes"]),
        "semantic_count": len(report["semantics"]),
        "eaf_preview_count": len(preview["assets"]),
        "preview_provenance": {key: value for key, value in preview.items()
                               if key not in ("assets", "expressions")},
        "files": {
            "index.html": {"sha256": sha256(page_raw), "bytes": len(page_raw)},
            "bindings.json": {"sha256": sha256(binding_raw), "bytes": len(binding_raw)},
        },
    }
    (target / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8", newline="\n")
    (target / "README.txt").write_text(
        "DeskBot E1.4a official behavior binding viewer\n\n"
        "Open index.html locally. EAF preview atlases are embedded; no server is needed.\n"
        "Source routes are not runtime observations. No device, COM, firmware or NVS access.\n",
        encoding="utf-8", newline="\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT_DEFAULT)
    parser.add_argument("--preview", type=Path, default=PREVIEW_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        result = build(report, args.report, args.preview, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"result": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
