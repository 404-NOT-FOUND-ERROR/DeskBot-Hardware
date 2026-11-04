#!/usr/bin/env python3
"""Audit static expression-route coverage against the accepted E1.18 choice.

This is a read-only host audit. It consumes published E1.14/E1.19/E1.22/E1.23
records, checks their cross-references, and never contacts hardware or builds a
firmware/package image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EDITION = "E1.24"
REPORT_SCHEMA = "deskbot.expression_e1_24_coverage_audit/v1"
DEFAULTS = {
    "bindings": "artifacts/expression-binding-e1-4a-20261021/bindings.json",
    "e114": "artifacts/expression-binding-decision-e1-14-20261024/decision.json",
    "e119": "artifacts/expression-node-preview-e1-19-20261024/report.json",
    "e122": "artifacts/expression-e1-22-candidate-preview-20261026/report.json",
    "e123": "artifacts/expression-e1-23-deployment-preflight-20261026/report.json",
    "decision": "artifacts/expression-e1-22-candidate-preview-20261026/selection-record.json",
    "output": "artifacts/expression-e1-24-coverage-audit-20261026/report.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_file(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside this project") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} is missing: {resolved.relative_to(ROOT).as_posix()}")
    return resolved


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def grouped(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        value = item.get(key)
        if not isinstance(value, str):
            raise ValueError(f"missing string {key} in an input row")
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def build_report(paths: dict[str, Path]) -> dict[str, Any]:
    bindings = load_json(paths["bindings"], "E1.4a bindings")
    e114 = load_json(paths["e114"], "E1.14 decision")
    e119 = load_json(paths["e119"], "E1.19 report")
    e122 = load_json(paths["e122"], "E1.22 preview report")
    e123 = load_json(paths["e123"], "E1.23 preflight report")
    decision = load_json(paths["decision"], "E1.22 user choice")

    if bindings.get("edition") != "E1.4a" or bindings.get("summary", {}).get("emotion_semantics") != 28:
        raise ValueError("E1.4a semantic inventory anchor drifted")
    declared_binding_hash = next((
        item.get("sha256") for item in e114.get("source", {}).get("inputs", [])
        if item.get("path") == DEFAULTS["bindings"]
    ), None)
    if declared_binding_hash != sha256(paths["bindings"]):
        raise ValueError("E1.14 does not anchor the supplied E1.4a bindings")
    if e114.get("edition") != "E1.14" or e114.get("result") != "offline_binding_decision":
        raise ValueError("unsupported E1.14 decision report")

    semantics = bindings.get("semantics")
    routes = e114.get("route_projections")
    resources = e114.get("resource_decisions")
    if not isinstance(semantics, list) or not isinstance(routes, list) or not isinstance(resources, list):
        raise ValueError("E1.14/E1.4a inventory arrays are missing")
    reachability = grouped(semantics, "reachability")
    if reachability != {
        "dynamic_entry_only_in_scope": 23,
        "literal_local_entry": 5,
        "status_entry": 1,
    }:
        raise ValueError("E1.4a semantic reachability counts changed")
    route_kinds = grouped(routes, "route_kind")
    if route_kinds != {"behavior": 20, "semantic": 29, "unmapped": 7}:
        raise ValueError("E1.14 route inventory counts changed")
    resource_kinds = grouped(resources, "kind")
    if resource_kinds != {
        "core": 5,
        "offline_candidate": 1,
        "protected_shared": 3,
        "protected_single": 10,
    }:
        raise ValueError("E1.14 resource protection categories changed")
    if e114.get("summary", {}).get("route_count") != 56:
        raise ValueError("E1.14 route summary no longer totals 56")
    if any(row.get("runtime_observation") != "not_collected" for row in semantics + routes):
        raise ValueError("E1.14 unexpectedly claims runtime observations")
    if any(row.get("runtime_observation") != "not_collected" for row in semantics):
        raise ValueError("E1.4a unexpectedly claims semantic runtime observations")

    if (e119.get("schema") != "deskbot.expression_node_preview/v1"
            or e119.get("edition") != "E1.19"
            or e119.get("hardware_accessed") is not False
            or e119.get("firmware_changed") is not False
            or e119.get("counts") != {"semantics": 29, "official_resources": 18, "routes": 56}):
        raise ValueError("E1.19 snapshot or offline boundary changed")
    deployed = e119.get("snapshots", {}).get("deployed")
    media = e119.get("media")
    if not isinstance(deployed, dict) or not isinstance(media, list):
        raise ValueError("E1.19 deployed map or media inventory is missing")
    official_resources = {row.get("name") for row in media if isinstance(row, dict)}
    if len(deployed) != 29 or len(official_resources) != 18:
        raise ValueError("E1.19 deployed semantic/resource counts changed")
    references: dict[str, list[str]] = {}
    for semantic, row in deployed.items():
        if not isinstance(row, dict) or row.get("resource_id") not in official_resources:
            raise ValueError(f"E1.19 semantic {semantic} does not resolve to an official EAF")
        references.setdefault(row["resource_id"], []).append(semantic)
    if set(references) != official_resources:
        raise ValueError("at least one official EAF is not referenced by the deployed mapping")
    references = {name: sorted(names) for name, names in sorted(references.items())}

    if (e122.get("edition") != "E1.22"
            or e122.get("result") != "offline_before_after_preview_ready"
            or e122.get("deployment_eligible") is not False
            or e122.get("comparison", {}).get("before_resource") != "shocked.eaf"
            or e122.get("comparison", {}).get("candidate_resource") != "Happy.eaf"):
        raise ValueError("E1.22 before/after anchor or offline deployment boundary changed")
    if (decision.get("schema") != "deskbot.expression_visual_decision/v1"
            or decision.get("edition") != "E1.22"
            or decision.get("decision") != "retain_current"
            or decision.get("semantic") != "surprised"
            or decision.get("selected_resource") != "shocked.eaf"
            or decision.get("current_e118_resource") != "shocked.eaf"
            or decision.get("mapping_changed") is not False
            or decision.get("flash_authorized") is not False):
        raise ValueError("E1.22 choice must retain the current shocked.eaf mapping without flash authorization")
    if deployed.get("surprised", {}).get("resource_id") != decision["selected_resource"]:
        raise ValueError("selected resource does not match the E1.19 E1.18 deployed snapshot")
    if (e123.get("edition") != "E1.23"
            or e123.get("deployment_eligible") is not False
            or e123.get("flash_authorized") is not False
            or e123.get("device_written") is not False):
        raise ValueError("E1.23 deployment preflight boundary changed")

    source_hashes = {
        name: {"path": paths[name].relative_to(ROOT).as_posix(), "sha256": sha256(paths[name])}
        for name in ("bindings", "e114", "e119", "e122", "e123", "decision")
    }
    names = sorted({row.get("name") for row in e114.get("unmapped_calls", []) if row.get("name")})
    if len(e114.get("unmapped_calls", [])) != 9 or len(names) != 7:
        raise ValueError("E1.14 unmapped call/name counts changed")

    return {
        "schema": REPORT_SCHEMA,
        "edition": EDITION,
        "date": "2026-10-26",
        "result": "offline_static_mapping_coverage_audit_passed",
        "audit_pass": True,
        "deployment_eligible": False,
        "flash_authorized": False,
        "safety": {
            "hardware_accessed": False,
            "serial_accessed": False,
            "firmware_built": False,
            "firmware_changed": False,
            "nvs_changed": False,
            "device_written": False,
        },
        "sources": source_hashes,
        "visual_decision": {
            "semantic": "surprised",
            "selected_resource": "shocked.eaf",
            "current_e118_resource": "shocked.eaf",
            "candidate_not_selected": "Happy.eaf",
            "mapping_changed": False,
            "flash_authorized": False,
        },
        "static_inventory": {
            "e114_route_count": len(routes),
            "route_kinds": route_kinds,
            "semantic_reachability": {
                "counts": reachability,
                "semantic_ids": {
                    category: sorted(row["id"] for row in semantics if row["reachability"] == category)
                    for category in sorted(reachability)
                },
            },
            "resource_protection_kinds": resource_kinds,
            "current_e118_mapping": {
                "semantic_count": len(deployed),
                "official_resource_count": len(official_resources),
                "resource_references": references,
                "unreferenced_official_resources": [],
            },
            "unmapped_calls": {"call_site_count": 9, "distinct_name_count": 7, "names": names},
        },
        "runtime_observation": {
            "status": "not_collected",
            "e14_route_count": len(routes),
            "e14_routes_not_collected": len(routes),
            "e14_semantics_not_collected": len(semantics),
            "device_trace_available": False,
        },
        "next_gate": "design_bounded_passive_runtime_observation_before_any_mapping_cleanup_or_firmware_change",
        "limits": [
            "Reachability categories describe the audited source scope; they are not frequency or execution evidence.",
            "Dynamic entry means a value can enter through the scoped dynamic API; it does not prove that value was received or played.",
            "The 56 routes are static source relationships. No device traces, LCD capture, motor completion, or per-route hardware acceptance were collected.",
            "Resource reference counts do not authorize deletion or replacement; shared resources remain protected.",
            "The E1.22 choice records a visual preference only. It does not authorize flashing; the E1.18 mapping remains current.",
        ],
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        stream.write(payload)
        temp = Path(stream.name)
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("bindings", "e114", "e119", "e122", "e123", "decision", "output"):
        parser.add_argument("--" + key, default=DEFAULTS[key])
    args = parser.parse_args()
    try:
        paths = {key: project_file(getattr(args, key), key) for key in DEFAULTS if key != "output"}
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        output = output.resolve()
        relative = output.relative_to(ROOT.resolve())
        if not relative.parts or relative.parts[0] not in {"artifacts", "tmp"} or output.suffix.lower() != ".json":
            raise ValueError("output must be a JSON file inside artifacts/ or tmp/")
        report = build_report(paths)
        write_json(output, report)
        print(f"{report['result']}: {relative.as_posix()}")
        print("Static inventory only; deployment_eligible=false; flash_authorized=false; runtime observations not collected.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"E1.24 audit failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
