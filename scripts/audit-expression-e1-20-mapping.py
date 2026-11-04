#!/usr/bin/env python3
"""Audit an E1.19 expression mapping draft before any package or device work.

The audit consumes the self-contained E1.19 preview payload, an exported mapping
draft, and the published E1.16 package report.  It validates the mapping against
the official EAF whitelist, reports shared-resource impact, and proves that the
official action/layout/timing contract and rollback remain unchanged.  It never
creates a package, writes firmware, accesses a serial port, or modifies NVS.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_E119 = ROOT / "artifacts/expression-node-preview-e1-19-20261024/index.html"
DEFAULT_E116 = ROOT / "artifacts/expression-e1-16-repack-20261024/report.json"
DEFAULT_DRAFT = ROOT / "artifacts/expression-e1-20-mapping-audit-20261025/draft.json"
DEFAULT_OUTPUT = ROOT / "artifacts/expression-e1-20-mapping-audit-20261025/report.json"

DRAFT_SCHEMA = "deskbot.expression_mapping_draft/v1"
REPORT_SCHEMA = "deskbot.expression_mapping_audit/v1"
E119_EDITION = "E1.19"
E120_EDITION = "E1.20"
ASSET_PARTITION_BYTES = 6_144_000
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    return sha256_bytes(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8"))


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} cannot be read as JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def safe_project_path(value: Path, label: str, *, must_exist: bool = True) -> Path:
    """Resolve only project-relative paths and reject symlink traversal."""
    try:
        resolved = value.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError(f"{label} cannot be resolved: {exc}") from exc
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the project") from exc
    current = ROOT.resolve()
    try:
        relative = resolved.relative_to(current)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the project") from exc
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse symlinks")
    if must_exist and not resolved.is_file():
        raise ValueError(f"{label} is not a file")
    return resolved


def output_path(value: Path) -> Path:
    resolved = value.resolve()
    try:
        relative = resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("audit output must stay inside the project") from exc
    if not relative.parts or relative.parts[0] not in {"tmp", "artifacts"}:
        raise ValueError("audit output must be inside tmp/ or artifacts/")
    if resolved.suffix.lower() != ".json":
        raise ValueError("audit output must be a JSON file")
    return resolved


def extract_e119_payload(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    match = re.search(
        r'<script id="e119-data" type="application/json">(.*?)</script>', text, re.S,
    )
    if match is None:
        raise ValueError("E1.19 page is missing its embedded JSON payload")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"E1.19 payload is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("E1.19 payload must be an object")
    return payload, sha256_bytes(raw)


def validate_e119(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if payload.get("schema") != "deskbot.expression_node_preview/v1" or payload.get("edition") != E119_EDITION:
        raise ValueError("unsupported E1.19 preview payload")
    if payload.get("hardware_accessed") is not False or payload.get("firmware_changed") is not False:
        raise ValueError("E1.19 input is not explicitly offline-only")
    if payload.get("counts") != {"semantics": 29, "official_resources": 18, "routes": 56}:
        raise ValueError("E1.19 inventory counts drifted")
    baselines = payload.get("baselines")
    if not isinstance(baselines, dict) or not isinstance(baselines.get("deployed"), dict):
        raise ValueError("E1.19 has no deployed baseline")
    deployed_baseline = baselines["deployed"]
    for key in ("package_sha256", "index_sha256"):
        if not isinstance(deployed_baseline.get(key), str) or not SHA_RE.fullmatch(deployed_baseline[key]):
            raise ValueError(f"E1.19 deployed baseline has an invalid {key}")

    assets_raw = payload.get("assets")
    if not isinstance(assets_raw, dict) or len(assets_raw) != 18:
        raise ValueError("E1.19 official EAF whitelist is incomplete")
    assets: dict[str, dict[str, Any]] = {}
    for key, item in assets_raw.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            raise ValueError("E1.19 contains a malformed official asset")
        src = item.get("src")
        if src != key or item.get("origin") != "official":
            raise ValueError("E1.19 asset identity/origin mismatch")
        deployed_sha = item.get("deployed_sha256")
        if not isinstance(deployed_sha, str) or not SHA_RE.fullmatch(deployed_sha):
            raise ValueError(f"E1.19 asset has an invalid deployed hash: {key}")
        size = item.get("deployed_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError(f"E1.19 asset has an invalid size: {key}")
        assets[key] = {
            "src": key,
            "sha256": deployed_sha,
            "bytes": size,
            "fps": item.get("fps"),
            "loop": item.get("loop"),
            "atlas_sha256": item.get("atlas_sha256"),
        }

    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, dict) or not isinstance(snapshots.get("deployed"), dict):
        raise ValueError("E1.19 has no deployed semantic snapshot")
    semantic_map: dict[str, dict[str, Any]] = {}
    for semantic, item in snapshots["deployed"].items():
        if not isinstance(semantic, str) or not ID_RE.fullmatch(semantic) or not isinstance(item, dict):
            raise ValueError("E1.19 contains an invalid semantic entry")
        if semantic in semantic_map:
            raise ValueError("E1.19 contains a duplicate semantic")
        resource = item.get("resource_id")
        if resource not in assets:
            raise ValueError(f"semantic {semantic} points outside the official EAF whitelist")
        if (isinstance(item.get("fps"), bool) or not isinstance(item.get("fps"), int)
                or item["fps"] <= 0 or item["fps"] > 120):
            raise ValueError(f"semantic {semantic} has invalid timing")
        if not isinstance(item.get("loop"), bool) or not isinstance(item.get("layout_id"), str):
            raise ValueError(f"semantic {semantic} has invalid playback metadata")
        action = item.get("action_id")
        if action is not None and (not isinstance(action, str) or not ID_RE.fullmatch(action)):
            raise ValueError(f"semantic {semantic} has an invalid action")
        semantic_map[semantic] = {
            "resource_id": resource,
            "resource_sha256": assets[resource]["sha256"],
            "resource_bytes": assets[resource]["bytes"],
            "fps": item["fps"],
            "loop": item["loop"],
            "action_id": action,
            "layout_id": item["layout_id"],
        }
    if len(semantic_map) != 29:
        raise ValueError("E1.19 semantic inventory is incomplete")
    return semantic_map, assets


def validate_e116(report: dict[str, Any], e119: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema") != "deskbot.expression_actual_repack/v1" or report.get("edition") != "E1.16":
        raise ValueError("unsupported E1.16 package report")
    baseline, candidate, capacity = report.get("baseline"), report.get("candidate"), report.get("capacity")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict) or not isinstance(capacity, dict):
        raise ValueError("E1.16 package report is incomplete")
    if candidate.get("sha256") != e119["baselines"]["deployed"]["package_sha256"]:
        raise ValueError("E1.16 candidate package differs from E1.19 deployed baseline")
    if candidate.get("bytes") != 5_538_351 or candidate.get("remaining_bytes") != 605_649:
        raise ValueError("E1.16 package capacity baseline drifted")
    if capacity.get("fits_partition") is not True or capacity.get("new_eaf_bytes") != 0:
        raise ValueError("E1.16 package capacity is not a verified no-payload-change baseline")
    member_diff = report.get("member_diff")
    if not isinstance(member_diff, dict) or member_diff.get("eaf_payloads_changed") != []:
        raise ValueError("E1.16 reports an unexpected EAF payload change")
    safety = report.get("safety")
    if not isinstance(safety, dict) or any(safety.get(key) is not False for key in (
        "hardware_accessed", "firmware_built", "device_written", "c61_changed", "nvs_changed",
    )):
        raise ValueError("E1.16 package report is not offline-only")
    return {
        "package_bytes": candidate["bytes"],
        "remaining_bytes": candidate["remaining_bytes"],
        "package_sha256": candidate["sha256"],
        "index_sha256": report.get("index_patch", {}).get("after_sha256"),
        "member_count": candidate.get("member_count"),
        "report_result": report.get("result"),
    }


def validate_draft(
    draft: dict[str, Any],
    semantic_map: dict[str, dict[str, Any]],
    assets: dict[str, dict[str, Any]],
    e119: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    expected = {
        "schema", "edition", "base_package_sha256", "base_index_sha256", "firmware_deployable",
        "hardware_accessed", "changes", "constraints", "next_gate",
    }
    if set(draft) != expected or draft.get("schema") != DRAFT_SCHEMA or draft.get("edition") != E119_EDITION:
        raise ValueError("E1.19 mapping draft fields or edition do not match the export contract")
    deployed = e119["baselines"]["deployed"]
    if draft.get("base_package_sha256") != deployed.get("package_sha256"):
        raise ValueError("draft package baseline does not match E1.18")
    if draft.get("base_index_sha256") != deployed.get("index_sha256"):
        raise ValueError("draft index baseline does not match E1.18")
    if draft.get("firmware_deployable") is not False or draft.get("hardware_accessed") is not False:
        raise ValueError("mapping draft is not explicitly offline-only")
    constraints = draft.get("constraints")
    required_constraints = {"action_map_changed", "timing_changed", "layout_changed", "eaf_payloads_changed", "resources_removed"}
    if not isinstance(constraints, dict) or set(constraints) != required_constraints or any(
        constraints[key] is not False for key in required_constraints
    ):
        raise ValueError("draft constraints must keep every release boundary false")
    changes = draft.get("changes")
    if not isinstance(changes, list) or len(changes) > len(semantic_map):
        raise ValueError("draft changes must be a bounded list")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    effective = copy.deepcopy(semantic_map)
    for change in changes:
        required = {"semantic", "before", "after", "resource_sha256", "fps", "loop", "action_id", "layout_id"}
        if not isinstance(change, dict) or set(change) != required:
            raise ValueError("draft change fields do not match the E1.19 export contract")
        semantic = change["semantic"]
        if not isinstance(semantic, str) or not ID_RE.fullmatch(semantic) or semantic in seen:
            raise ValueError("draft contains an unknown or duplicate semantic")
        seen.add(semantic)
        if semantic not in semantic_map:
            raise ValueError(f"draft semantic is outside the official inventory: {semantic}")
        original = semantic_map[semantic]
        before, after = change["before"], change["after"]
        if before != original["resource_id"]:
            raise ValueError(f"draft before edge differs from E1.18: {semantic}")
        if not isinstance(after, str) or after not in assets:
            raise ValueError(f"draft after edge is outside the official EAF whitelist: {semantic}")
        if after == before:
            raise ValueError(f"draft contains a no-op change: {semantic}")
        if change["resource_sha256"] != assets[after]["sha256"]:
            raise ValueError(f"draft resource hash does not match the selected EAF: {semantic}")
        for field in ("fps", "loop", "action_id", "layout_id"):
            if change[field] != original[field]:
                raise ValueError(f"draft attempts to change protected {field}: {semantic}")
        effective[semantic] = {
            **original,
            "resource_id": after,
            "resource_sha256": assets[after]["sha256"],
            "resource_bytes": assets[after]["bytes"],
        }
        normalized.append({
            "semantic": semantic,
            "disconnected": before,
            "connected": after,
            "disconnected_sha256": original["resource_sha256"],
            "connected_sha256": assets[after]["sha256"],
            "fps": original["fps"],
            "loop": original["loop"],
            "action_id": original["action_id"],
            "layout_id": original["layout_id"],
        })
    return normalized, effective


def reference_map(semantic_map: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {}
    for semantic, item in semantic_map.items():
        refs.setdefault(item["resource_id"], []).append(semantic)
    for names in refs.values():
        names.sort()
    return refs


def audit(
    e119_path: Path, e116_path: Path, draft_path: Path,
) -> dict[str, Any]:
    e119, e119_hash = extract_e119_payload(e119_path)
    semantic_map, assets = validate_e119(e119)
    e116 = load_json(e116_path, "E1.16 package report")
    package = validate_e116(e116, e119)
    draft = load_json(draft_path, "E1.19 mapping draft")
    changes, effective = validate_draft(draft, semantic_map, assets, e119)
    before_refs, after_refs = reference_map(semantic_map), reference_map(effective)

    affected_resources = sorted({item["disconnected"] for item in changes} | {item["connected"] for item in changes})
    shared = []
    for resource in affected_resources:
        before = before_refs.get(resource, [])
        after = after_refs.get(resource, [])
        shared.append({
            "resource_id": resource,
            "before_references": before,
            "after_references": after,
            "before_count": len(before),
            "after_count": len(after),
            "was_shared": len(before) > 1,
            "remains_referenced": bool(after),
            "payload_changed": False,
        })

    changed_semantics = [item["semantic"] for item in changes]
    rollback = copy.deepcopy(effective)
    for item in changes:
        rollback[item["semantic"]] = copy.deepcopy(semantic_map[item["semantic"]])
    rollback_exact = rollback == semantic_map
    before_digest = canonical_sha(semantic_map)
    after_digest = canonical_sha(effective)
    rollback_digest = canonical_sha(rollback)
    action_map = {semantic: item["action_id"] for semantic, item in semantic_map.items()}
    action_map_after = {semantic: item["action_id"] for semantic, item in effective.items()}
    timing_after = all(
        effective[semantic][field] == semantic_map[semantic][field]
        for semantic in semantic_map for field in ("fps", "loop")
    )
    layout_after = all(effective[semantic]["layout_id"] == semantic_map[semantic]["layout_id"] for semantic in semantic_map)
    package_capacity = {
        "mode": "mapping_only",
        "assets_partition_bytes": ASSET_PARTITION_BYTES,
        "package_bytes": package["package_bytes"],
        "remaining_bytes": package["remaining_bytes"],
        "payload_delta_bytes": 0,
        "new_eaf_bytes": 0,
        "eaf_payloads_changed": [],
        "fits_partition": package["package_bytes"] <= ASSET_PARTITION_BYTES,
        "verified_from": "E1.16 actual package report; mapping-only draft has no payload bytes",
    }
    return {
        "schema": REPORT_SCHEMA,
        "edition": E120_EDITION,
        "result": "offline_mapping_audit_passed",
        "deployment_eligible": False,
        "hardware_accessed": False,
        "firmware_changed": False,
        "nvs_changed": False,
        "inputs": {
            "e119_preview": {"path": e119_path.relative_to(ROOT).as_posix(), "sha256": e119_hash},
            "e116_package_report": {"path": e116_path.relative_to(ROOT).as_posix(), "sha256": sha256_file(e116_path)},
            "draft": {"path": draft_path.relative_to(ROOT).as_posix(), "sha256": sha256_file(draft_path)},
        },
        "baseline": {
            "label": e119["baselines"]["deployed"].get("label", "E1.18 deployed snapshot"),
            "package_sha256": package["package_sha256"],
            "index_sha256": package["index_sha256"],
            "package_bytes": package["package_bytes"],
            "assets_partition_bytes": ASSET_PARTITION_BYTES,
            "remaining_bytes": package["remaining_bytes"],
            "official_eaf_count": len(assets),
            "semantic_count": len(semantic_map),
        },
        "draft": {
            "schema": draft["schema"],
            "edition": draft["edition"],
            "changed_semantics": changed_semantics,
            "change_count": len(changes),
            "next_gate": draft["next_gate"],
        },
        "mapping_comparison": {
            "changes": changes,
            "changed_semantics": changed_semantics,
            "unchanged_semantic_count": len(semantic_map) - len(changes),
            "before_map_sha256": before_digest,
            "after_map_sha256": after_digest,
            "rollback_map_sha256": rollback_digest,
            "rollback_exact": rollback_exact,
            "official_whitelist_unchanged": all(item["resource_id"] in assets for item in effective.values()),
            "action_map_before_sha256": canonical_sha(action_map),
            "action_map_after_sha256": canonical_sha(action_map_after),
            "action_map_unchanged": action_map == action_map_after,
            "timing_unchanged": timing_after,
            "layout_unchanged": layout_after,
            "resource_payloads_changed": [],
        },
        "shared_resource_impact": shared,
        "capacity": package_capacity,
        "rollback": {
            "available": rollback_exact,
            "scope": "offline_mapping_only",
            "exact_map_restored": rollback_exact,
            "from_map_sha256": after_digest,
            "to_map_sha256": before_digest,
            "device_rollback_performed": False,
            "package_rollback_required": False,
            "reason": "restore each disconnected semantic edge to its E1.18 resource without changing EAF bytes or actions",
        },
        "gates": [
            {"id": "draft_contract", "status": "passed", "label": "E1.19 导出字段、基线和官方资源白名单"},
            {"id": "shared_resource_review", "status": "passed", "label": "共享资源引用影响已列出，未删除资源"},
            {"id": "action_layout_timing", "status": "passed", "label": "动作、布局、FPS 和循环行为保持不变"},
            {"id": "capacity_baseline", "status": "passed", "label": "E1.16 实际包容量与分区余量复核"},
            {"id": "package_player_rollback", "status": "pending", "label": "候选包、官方播放器与回退审计"},
            {"id": "device_authorization", "status": "pending", "label": "单独授权本体 assets 部署"},
        ],
        "limits": [
            "本报告只审计映射草稿，不生成资源包或固件镜像。",
            "共享 EAF 只报告引用变化，不因引用减少而删除文件。",
            "动作字段是保留的协议关联，不代表电机已执行。",
            "离线通过不代表圆屏、运行时内存、设备播放或底座功能已验收。",
        ],
    }


def write_report_atomic(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".e120-report-", dir=path.parent) as directory:
        staged = Path(directory) / "report.json"
        staged.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(staged, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e119", type=Path, default=DEFAULT_E119)
    parser.add_argument("--e116", type=Path, default=DEFAULT_E116)
    parser.add_argument("--draft", type=Path, default=DEFAULT_DRAFT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = None
    try:
        e119_path = safe_project_path(args.e119, "E1.19 preview")
        e116_path = safe_project_path(args.e116, "E1.16 package report")
        draft_path = safe_project_path(args.draft, "E1.19 mapping draft")
        output = output_path(args.output)
        if output in {e119_path, e116_path, draft_path}:
            raise ValueError("audit report must not overwrite an input")
        report = audit(e119_path, e116_path, draft_path)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        report = {
            "schema": REPORT_SCHEMA,
            "edition": E120_EDITION,
            "result": "rejected",
            "error": str(exc),
            "deployment_eligible": False,
            "hardware_accessed": False,
            "firmware_changed": False,
            "nvs_changed": False,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if output is not None:
            try:
                write_report_atomic(output, report)
            except OSError:
                pass
        return 2
    write_report_atomic(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
