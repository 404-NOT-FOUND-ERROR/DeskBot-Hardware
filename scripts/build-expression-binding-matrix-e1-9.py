#!/usr/bin/env python3
"""Build the E1.9 offline expression/behavior cleanup matrix.

This tool only reads pinned reports and produces review artifacts.  It never
opens a serial port, builds firmware, edits the source tree, or changes a
device package.  The matrix deliberately treats missing runtime observation
as uncertainty, not as evidence that an asset is removable.
"""
from __future__ import annotations

import argparse
import csv
import io
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINDINGS = ROOT / "artifacts/expression-binding-e1-4a-20261021/bindings.json"
DEFAULT_RELEASE_AUDIT = ROOT / "artifacts/expression-node-workbench-e1-4d-20261021/release-audit.json"
DEFAULT_PACKAGE_AUDIT = ROOT / "tmp/expression-e1-3-20261020/package-candidate-audit.json"
DEFAULT_E1_3I_AUDIT = ROOT / "tmp/expression-e1-3i-20261021-run1/e1-3i-audit.json"
DEFAULT_OUTPUT = ROOT / "artifacts/expression-binding-matrix-e1-9-20261022"

SCHEMA = "deskbot.expression_binding_matrix/v1"
SOURCE_COMMIT = "46d427255756fe2a148777ea3f256b615b52e160"
INPUTS = {
    "bindings": (DEFAULT_BINDINGS, "98d57c67e52e240cb7ef0ed8c99cfc088f834fbf662424cd48c2c03c50bd4921"),
    "release_audit": (DEFAULT_RELEASE_AUDIT, "89b99c644e5bfec536ea331280489f79d9a10f15071bf4795b2f87d717ae08af"),
    "package_audit": (DEFAULT_PACKAGE_AUDIT, "e94475676648e9daa6b20e95d13b77592c2179987a737ac85ccae85558aade74"),
    "e1_3i_audit": (DEFAULT_E1_3I_AUDIT, "6e7a0ae75ce6722f75282c14be55eecbea45ba3aced0aa77747f656fbda7356e"),
}

CORE_RESOURCES = {"winking.eaf", "listen.eaf", "insert.eaf", "eat.eaf", "beat_swing.eaf"}
CORE_ROUTE_IDS = {
    "screen-tap", "state-idle", "state-connect", "state-listen", "state-speak",
    "base-insert", "fish-attach", "pair-detect", "slide-down", "slide-up",
    "slide-click", "calibration", "action-done", "base-offline", "beat-detect",
    "doa-follow", "pet-head", "alarm-end",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def evidence_refs(route: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for step in route.get("steps", []):
        refs.extend(step.get("evidence", []))
    for output in route.get("outputs", []):
        refs.extend(output.get("evidence", []))
    return unique(refs)


def evidence_details(refs: Iterable[str], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ref in refs:
        item = catalog.get(ref)
        if item:
            result.append({
                "ref": ref,
                "path": item.get("path"),
                "line": item.get("line"),
                "end_line": item.get("end_line"),
                "excerpt": item.get("excerpt"),
                "origin": item.get("origin"),
            })
        else:
            result.append({"ref": ref, "missing_from_catalog": True})
    return result


def action_map(bindings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in bindings.get("actions", [])}


def action_ids_for_route(route: dict[str, Any]) -> list[str]:
    ids = list(route.get("action_ids", []))
    ids.extend(output.get("action_id") for output in route.get("outputs", []))
    return unique(ids)


def confidence(resolution: str) -> str:
    return {
        "mapping_only": "static_mapping_only",
        "source_conditioned": "source_conditioned_not_runtime_observed",
        "state_dependent": "state_conditioned_not_runtime_observed",
        "dynamic_semantic": "dynamic_entry_not_runtime_observed",
        "server_dependent": "server_or_external_not_runtime_observed",
        "unmapped": "unmapped_static_call",
    }.get(resolution, "static_review_boundary")


def classification_for_semantic(semantic: dict[str, Any], shared: dict[str, Any]) -> tuple[str, str]:
    resource = semantic.get("src")
    if resource in CORE_RESOURCES and semantic.get("reachability") in {"literal_local_entry", "status_entry"}:
        return "core", "官方状态或本地固定入口直接使用；先保留以保护基础交互。"
    if len(shared.get(resource, [])) > 1:
        return "protected", "共享 EAF；替换或移除会同时影响多个语义。"
    return "protected", "官方或动态语义入口；静态清单不足以证明可移除。"


def classification_for_resource(name: str, shared: dict[str, Any]) -> tuple[str, str]:
    if name in CORE_RESOURCES:
        return "core", "官方固定状态/底座/节拍入口，属于基线行为保护范围。"
    if len(shared.get(name, [])) > 1:
        return "protected", "被多个语义共享，不能按单一语义清理。"
    return "protected", "官方资源或动态情绪入口；没有运行时可达性证据。"


def route_semantics(route: dict[str, Any]) -> list[str]:
    return unique(output.get("semantic") for output in route.get("outputs", []))


def route_resources(route: dict[str, Any]) -> list[str]:
    return unique(output.get("src") for output in route.get("outputs", []))


def build_matrix(bindings: dict[str, Any], release_audit: dict[str, Any], package_audit: dict[str, Any], e1_3i_audit: dict[str, Any]) -> dict[str, Any]:
    if bindings.get("source", {}).get("official_commit") != SOURCE_COMMIT:
        raise ValueError("official source identity drift")
    if any(bindings.get("safety", {}).values()):
        raise ValueError("source-only binding evidence required")
    if any(route.get("runtime_observation") != "not_collected" for route in bindings["routes"]):
        raise ValueError("source-only routes must not claim runtime observation")
    if release_audit.get("deployment_eligible") is not False:
        raise ValueError("offline-only candidate evidence required")
    if release_audit["mapping_comparison"]["official_emotion_action_map"] != bindings["emotion_action_map"]:
        raise ValueError("official action mapping drift")
    if (not release_audit["mapping_comparison"]["action_map_unchanged"]
            or not release_audit["mapping_comparison"]["rollback_exact"]):
        raise ValueError("candidate action map or exact rollback drift")
    if package_audit.get("official_baseline", {}).get("source_commit") != SOURCE_COMMIT:
        raise ValueError("official package audit identity drift")
    if e1_3i_audit.get("source_commit") != SOURCE_COMMIT or e1_3i_audit.get("eligibility", {}).get("device_package_eligible") is not False:
        raise ValueError("E1.3i must be the pinned offline, non-deployable candidate audit")
    eaf_assets = bindings.get("eaf_assets", {})
    shared = bindings.get("shared_assets", {})
    actions = action_map(bindings)
    evidence = bindings.get("evidence", {})
    for semantic in bindings["semantics"]:
        if semantic["src"] not in eaf_assets:
            raise ValueError("unresolved official EAF reference")
    for route in bindings["routes"]:
        if any(ref not in evidence for ref in evidence_refs(route)):
            raise ValueError("unresolved source evidence reference")

    route_rows: list[dict[str, Any]] = []
    for route in bindings.get("routes", []):
        refs = evidence_refs(route)
        semantic_ids = route_semantics(route)
        resource_ids = route_resources(route)
        action_ids = action_ids_for_route(route)
        emotion_action_ids = unique(output.get("action_id") for output in route.get("outputs", []))
        if route.get("id") == "tool-action":
            action_relation = {
                "semantic_action_mapping_requests": [],
                "source_branch_action_requests": [],
                "available_action_options": action_ids,
                "note": "五个动作是工具允许选择的选项，并非同时触发；没有本轮实机完成记录。",
            }
            requested_action_ids: list[str] = []
        else:
            requested_action_ids = unique(emotion_action_ids + [item for item in route.get("action_ids", []) if item not in emotion_action_ids])
            action_relation = {
                "semantic_action_mapping_requests": emotion_action_ids,
                "source_branch_action_requests": [item for item in requested_action_ids if item not in emotion_action_ids],
                "available_action_options": [],
                "note": "仅为源码请求或情绪动作映射；未观察到实机完成。",
            }
        affected: list[str] = []
        for resource_id in resource_ids:
            affected.extend(shared.get(resource_id, []))
        affected = unique(affected)
        resolution = route.get("resolution", "")
        route_class = "core" if route["id"] in CORE_ROUTE_IDS else "protected"
        if resolution == "unmapped":
            route_class = "protected"
        route_rows.append({
            "row_id": f"route:{route['id']}",
            "row_type": {"mapping_only": "semantic_reference", "unmapped": "unmapped_reference"}.get(resolution, "event_route"),
            "event_or_entry": route.get("title"),
            "event_id": route.get("id"),
            "group": route.get("group"),
            "condition": route.get("condition"),
            "semantic_ids": semantic_ids,
            "eaf_ids": resource_ids,
            "output_bindings": route.get("outputs", []),
            "resource_bytes_by_id": {name: eaf_assets[name]["bytes"] for name in resource_ids},
            "shared_object": resource_ids[0] if len(resource_ids) == 1 and len(shared.get(resource_ids[0], [])) > 1 else None,
            "affected_semantics": affected,
            "action_ids": action_ids,
            "action_relation": action_relation,
            "action_side_effects": [actions[action_id] for action_id in requested_action_ids if action_id in actions],
            "resolution": resolution,
            "confidence": confidence(resolution),
            "static_evidence": evidence_details(refs, evidence),
            "runtime_observation": route.get("runtime_observation", "not_collected"),
            "source_note": route.get("note"),
            "classification": route_class,
            "allowed_remove_from_device_profile": False,
            "disposition": "keep_and_review",
            "disposition_reason": "保留官方路由；事件事实、情绪、EAF 和动作副作用分列，未观察项不作删除依据。",
        })

    semantic_rows: list[dict[str, Any]] = []
    for semantic in bindings.get("semantics", []):
        resource = eaf_assets.get(semantic.get("src"), {})
        classification, reason = classification_for_semantic(semantic, shared)
        resource_id = semantic.get("src")
        refs = list(semantic.get("evidence", [])) + list(semantic.get("action_evidence", []))
        impacted = [item for item in shared.get(resource_id, []) if item != semantic.get("id")]
        semantic_rows.append({
            "row_id": f"semantic:{semantic['id']}",
            "row_type": "semantic",
            "event_or_entry": semantic["id"],
            "event_id": None,
            "group": "emotion_or_status_semantic",
            "condition": None,
            "semantic_ids": [semantic["id"]],
            "eaf_ids": [resource_id] if resource_id else [],
            "shared_object": resource_id if len(shared.get(resource_id, [])) > 1 else None,
            "affected_semantics": impacted,
            "action_ids": [semantic["set_emotion_action"]] if semantic.get("set_emotion_action") else [],
            "action_relation": {
                "semantic_action_mapping_requests": [semantic["set_emotion_action"]] if semantic.get("set_emotion_action") else [],
                "source_branch_action_requests": [],
                "available_action_options": [],
                "note": "情绪动作表的映射请求，不代表底座动作已经完成。",
            },
            "action_side_effects": [actions[semantic["set_emotion_action"]]] if semantic.get("set_emotion_action") in actions else [],
            "resolution": "mapping_only",
            "confidence": "static_mapping_only",
            "static_evidence": evidence_details(unique(refs), evidence),
            "runtime_observation": semantic.get("runtime_observation", "not_collected"),
            "classification": classification,
            "allowed_remove_from_device_profile": False,
            "disposition": "keep_in_device_profile",
            "disposition_reason": reason,
            "resource_size_bytes": resource.get("bytes"),
            "resource_sha256": resource.get("sha256"),
            "reachability": semantic.get("reachability"),
            "fps": semantic["fps"],
            "loop": semantic["loop"],
            "literal_call_ids": semantic.get("literal_call_ids", []),
        })

    resource_route_ids: dict[str, list[str]] = {name: [] for name in eaf_assets}
    resource_actions: dict[str, list[str]] = {name: [] for name in eaf_assets}
    for route in bindings.get("routes", []):
        for resource_id in route_resources(route):
            resource_route_ids.setdefault(resource_id, []).append(route["id"])
        for resource_id in route_resources(route):
            resource_actions.setdefault(resource_id, []).extend(action_ids_for_route(route))
    for semantic in bindings.get("semantics", []):
        if semantic.get("src"):
            resource_actions.setdefault(semantic["src"], []).append(semantic.get("set_emotion_action"))

    resource_rows: list[dict[str, Any]] = []
    for name, resource in eaf_assets.items():
        classification, reason = classification_for_resource(name, shared)
        resource_refs = unique(ref for semantic in bindings["semantics"] if semantic["src"] == name
                               for ref in semantic["evidence"])
        semantic_actions = unique(semantic.get("set_emotion_action") for semantic in bindings["semantics"] if semantic["src"] == name)
        source_actions = unique(action for route in route_rows if name in route["eaf_ids"]
                                for action in route["action_relation"]["source_branch_action_requests"])
        resource_rows.append({
            "row_id": f"resource:{name}",
            "row_type": "official_eaf_resource",
            "event_or_entry": name,
            "event_id": None,
            "group": "official_device_resource",
            "condition": None,
            "semantic_ids": list(resource.get("semantic_ids", shared.get(name, []))),
            "eaf_ids": [name],
            "shared_object": name if len(shared.get(name, resource.get("semantic_ids", []))) > 1 else None,
            "affected_semantics": list(resource.get("semantic_ids", shared.get(name, []))),
            "action_ids": unique(resource_actions.get(name, [])),
            "action_relation": {
                "semantic_action_mapping_requests": semantic_actions,
                "source_branch_action_requests": source_actions,
                "available_action_options": [],
                "note": "汇总引用者的动作请求/映射；EAF 文件自身不会因此执行电机动作。",
            },
            "action_side_effects": [actions[action_id] for action_id in unique(resource_actions.get(name, [])) if action_id in actions],
            "resolution": "resource_inventory",
            "confidence": "static_resource_inventory",
            "static_evidence": evidence_details(resource_refs, evidence),
            "runtime_observation": "not_collected",
            "classification": classification,
            "allowed_remove_from_device_profile": False,
            "disposition": "keep_in_device_profile",
            "disposition_reason": reason,
            "resource_size_bytes": resource.get("bytes"),
            "resource_sha256": resource.get("sha256"),
            "resource_path": resource.get("path"),
            "route_ids": unique(resource_route_ids.get(name, [])),
        })

    replacement = release_audit["replacement"]
    candidate = {
        "row_id": "candidate:shy_legacy.eaf",
        "row_type": "candidate_eaf_resource",
        "event_or_entry": "shy_legacy.eaf",
        "event_id": None,
        "group": "offline_candidate_only",
        "condition": "候选 profile 将 bashful 指向 embarrassed 时的离线替代资源",
        "semantic_ids": ["embarrassed"],
        "eaf_ids": ["shy_legacy.eaf"],
        "shared_object": None,
        "affected_semantics": ["embarrassed"],
        "action_ids": ["cat_nuzzle"],
        "action_relation": {
            "semantic_action_mapping_requests": ["cat_nuzzle"],
            "source_branch_action_requests": [],
            "available_action_options": [],
            "note": "候选只改离线 EAF 映射，保留官方动作映射；不代表部署。",
        },
        "action_side_effects": [actions["cat_nuzzle"]] if "cat_nuzzle" in actions else [],
        "resolution": "offline_candidate",
        "confidence": "offline_candidate_not_runtime_observed",
        "static_evidence": [],
        "runtime_observation": "not_collected",
        "classification": "candidate",
        "allowed_remove_from_device_profile": False,
        "disposition": "do_not_add_yet",
        "disposition_reason": "E1.4d 候选；未通过完整包容量、播放器边界、圆屏和实机播放闸门。",
        "resource_size_bytes": replacement["candidate_bytes"],
        "resource_sha256": replacement["candidate_sha256"],
        "resource_path": "tmp/expression-e1-2-20261020/shy_legacy.eaf",
        "route_ids": ["semantic-embarrassed"],
    }
    resource_rows.append(candidate)

    missing_rows: list[dict[str, Any]] = []
    for call in bindings.get("call_inventory", []):
        if call.get("mapping") != "missing":
            continue
        semantic_id = call.get("semantic")
        missing_rows.append({
            "row_id": f"unmapped:{call['id']}",
            "row_type": "unmapped_static_call",
            "event_or_entry": semantic_id,
            "event_id": call.get("id"),
            "group": "status_or_error_icon_pending_review",
            "condition": None,
            "semantic_ids": [semantic_id] if semantic_id else [],
            "eaf_ids": [],
            "shared_object": None,
            "affected_semantics": [],
            "action_ids": [],
            "action_side_effects": [],
            "resolution": "unmapped",
            "confidence": "unmapped_static_call",
            "static_evidence": evidence_details(call.get("evidence", []), bindings.get("evidence", {})),
            "runtime_observation": "not_collected",
            "classification": "protected",
            "allowed_remove_from_device_profile": False,
            "disposition": "keep_and_recheck",
            "disposition_reason": "源码调用存在但当前情绪资源索引未登记；可能是 Alert/status icon，不得据此删除。",
            "literal_call_ids": [call.get("id")],
        })

    official_package = package_audit.get("official_baseline", {})
    package_capacity = package_audit.get("capacity", {})
    candidate_package = e1_3i_audit.get("package", {})

    rows = route_rows + semantic_rows + resource_rows + missing_rows
    matrix = {
        "schema": SCHEMA,
        "edition": "E1.9",
        "result": "offline_binding_cleanup_matrix",
        "review_date": "2026-10-22",
        "source": {
            "official_commit": bindings.get("source", {}).get("official_commit", SOURCE_COMMIT),
            "repository": bindings.get("source", {}).get("repository"),
            "scope": "Pinned archived reports, not a live device or current working-tree audit",
            "inputs": {
                key: {
                    "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "sha256": expected_hash,
                }
                for key, (path, expected_hash) in INPUTS.items()
            },
        },
        "safety": {
            "hardware_accessed": False,
            "serial_ports_accessed": [],
            "firmware_changed": False,
            "nvs_changed": False,
            "resources_deleted": False,
            "device_package_changed": False,
            "runtime_observed": False,
            "automatic_cleanup": False,
        },
        "policy": {
            "official_baseline_immutable": True,
            "missing_runtime_evidence_means_keep": True,
            "shared_resource_replacement_is_not_single_semantic_replacement": True,
            "candidate_separate_from_device_profile": True,
            "removal_requires_separate_review": True,
        },
        "summary": {
            "route_count": len(route_rows),
            "behavior_entry_count": sum(row["row_type"] == "event_route" for row in route_rows),
            "semantic_reference_count": sum(row["row_type"] == "semantic_reference" for row in route_rows),
            "unmapped_reference_count": sum(row["row_type"] == "unmapped_reference" for row in route_rows),
            "semantic_count": len(semantic_rows),
            "official_eaf_count": len(eaf_assets),
            "candidate_eaf_count": 1,
            "unmapped_call_count": len(missing_rows),
            "unmapped_name_count": len({row["event_or_entry"] for row in missing_rows}),
            "shared_official_resource_count": sum(1 for name in eaf_assets if len(shared.get(name, [])) > 1),
            "classification_counts": {
                "core": sum(1 for row in rows if row["classification"] == "core"),
                "protected": sum(1 for row in rows if row["classification"] == "protected"),
                "candidate": sum(1 for row in rows if row["classification"] == "candidate"),
            },
        },
        "capacity": {
            "official_package_bytes": official_package.get("package_size_bytes"),
            "official_package_sha256": official_package.get("package_sha256"),
            "assets_partition_bytes": package_capacity.get("partition_bytes"),
            "official_remaining_bytes": package_capacity.get("official_remaining_bytes"),
            "official_eaf_total_bytes": sum(asset["bytes"] for asset in eaf_assets.values()),
            "other_members_and_container_bytes": official_package["package_size_bytes"] - sum(asset["bytes"] for asset in eaf_assets.values()),
            "counting_rule": "Count each EAF once; semantic aliases do not duplicate its bytes. Other members/container are not assumed reclaimable.",
            "official_measurement": "built expression_assets.bin; not source-directory sum",
            "e1_3g_four_bit_candidate_package_bytes": candidate_package.get("e1_3g_expected_package_bytes"),
            "e1_3g_four_bit_candidate_remaining_bytes": package_capacity.get("partition_bytes", 0) - candidate_package.get("e1_3g_expected_package_bytes", 0),
            "e1_3i_alias_candidate_eaf_bytes": candidate_package.get("shy_candidate", {}).get("size_bytes"),
            "e1_3i_alias_candidate_package_bytes": candidate_package.get("candidate_bytes"),
            "e1_3i_alias_candidate_remaining_bytes": candidate_package.get("remaining_bytes"),
            "e1_4d_shy_legacy_eaf_bytes": release_audit.get("replacement", {}).get("candidate_bytes"),
            "e1_4d_full_package_size_measured": False,
            "e1_3i_device_package_eligible": e1_3i_audit.get("eligibility", {}).get("device_package_eligible", False),
            "capacity_note": "官方包近满；候选容量数字来自不同阶段的隔离审计，不能混用，也不能单凭不超分区判定可刷写。",
        },
        "routes": route_rows,
        "semantics": semantic_rows,
        "resources": resource_rows,
        "unmapped_calls": missing_rows,
        "review_queue": [
            {
                "id": "differentiate-happy-semantics",
                "scope": ["embarrassed", "surprised"],
                "proposal": "先做两种情绪的独立视觉样稿和离线映射比较，再选择一个小体积候选。",
                "constraint": "Happy.eaf 仍被其他语义使用；单语义替换不会释放该共享文件的 81,645 B，也不改变动作表。",
                "status": "proposal_only",
            },
            {
                "id": "optimize-large-protected-resources",
                "scope": ["insert.eaf", "eat.eaf"],
                "proposal": "大资源优先评估等价编码/逐帧去重；若改变时长或画面则另列设计审批。",
                "constraint": "保留底座连接和配件反馈，不删除事件，不把它们替换成无关表情。",
                "status": "proposal_only",
            },
            {
                "id": "resolve-unobserved-and-unmapped",
                "scope": ["dynamic_semantics", "unmapped_calls"],
                "proposal": "先用电脑端回放建立代表性输入用例；设备端采集另行评估，不改 UI 热路径。",
                "constraint": "未观察到不等于不使用；不据此删除任何官方语义或资源。",
                "status": "proposal_only",
            },
        ],
        "limitations": [
            "这是固定源码和既有审计报告的离线矩阵，不是运行时播放统计。",
            "动态服务端情绪、MCP 决策、编译器可达性和设备时序没有在本轮观测。",
            "无 literal call 不等于无用或可删除；动态入口必须保留并单独验证。",
            "官方资源与候选资源分层；候选 shy_legacy.eaf 不进入设备 Profile。",
            "本轮不修改小智、播放器、C61、NVS、分区表或官方动画。",
        ],
    }
    return matrix


def flatten_csv_rows(matrix: dict[str, Any]) -> list[dict[str, str]]:
    fields = [
        "row_id", "row_type", "event_or_entry", "event_id", "group", "classification",
        "semantic_ids", "eaf_ids", "shared_object", "affected_semantics", "action_ids",
        "resolution", "confidence", "runtime_observation", "resource_size_bytes",
        "resource_sha256", "allowed_remove_from_device_profile", "disposition", "disposition_reason",
        "condition", "source_note", "fps", "loop", "reachability", "literal_call_ids",
        "static_evidence", "action_relation", "output_bindings", "resource_bytes_by_id",
    ]
    rows: list[dict[str, str]] = []
    for row in matrix["routes"] + matrix["semantics"] + matrix["resources"] + matrix["unmapped_calls"]:
        output: dict[str, str] = {}
        for field in fields:
            value = row.get(field)
            if field in {"static_evidence", "action_relation", "output_bindings", "resource_bytes_by_id"}:
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else ""
            elif isinstance(value, list):
                value = ";".join(str(item) for item in value)
            elif value is None:
                value = ""
            else:
                value = str(value)
            output[field] = value
        rows.append(output)
    return rows


def build_html(matrix: dict[str, Any]) -> str:
    data = json.dumps(matrix, ensure_ascii=False).replace("</", "<\\/")
    summary = matrix["summary"]
    rows = matrix["routes"] + matrix["semantics"] + matrix["resources"] + matrix["unmapped_calls"]
    template = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>E1.9 表情绑定清查矩阵</title>
<style>
:root{color-scheme:light dark;--bg:#0f1419;--panel:#182027;--text:#edf2f7;--muted:#a9b6c3;--line:#33414d;--accent:#78c2ff;--core:#315c46;--protected:#5b4a28;--candidate:#4c365c}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1600px;margin:auto}h1{margin:0 0 6px;font-size:24px}p{color:var(--muted)}.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:18px 0}input,select{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px 10px}.stats{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 20px}.stat{background:var(--panel);border:1px solid var(--line);padding:10px 14px;border-radius:8px;min-width:135px}.stat b{display:block;font-size:20px;color:var(--accent)}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px}table{border-collapse:collapse;width:100%;min-width:1200px}th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}th{position:sticky;top:0;background:var(--panel);z-index:1}td{color:var(--text)}.small{color:var(--muted);font-size:12px}.core{background:color-mix(in srgb,var(--core) 45%,transparent)}.protected{background:color-mix(in srgb,var(--protected) 35%,transparent)}.candidate{background:color-mix(in srgb,var(--candidate) 45%,transparent)}.pill{display:inline-block;padding:2px 6px;border-radius:4px;background:var(--panel);border:1px solid var(--line);white-space:nowrap}details{margin:18px 0}summary{cursor:pointer;color:var(--accent)}code{font-family:ui-monospace,Consolas,monospace}
:root{--bg:#151515;--panel:#222222;--text:#ededed;--muted:#b5b5b5;--line:#3a3a3a;--accent:#9fd7c0}
table{table-layout:fixed;min-width:1080px}th,td{overflow-wrap:anywhere}.core,.protected,.candidate{background:transparent}.core .pill{color:#a9d9c0}.protected .pill{color:#dfca9a}.candidate .pill{color:#dac0ed}a{color:var(--accent)}pre{white-space:pre-wrap;overflow-wrap:anywhere}label{max-width:100%}input,select{max-width:100%}details{margin:10px 0 0}.stat b{color:var(--text)}@media(max-width:600px){body{padding:12px}h1{font-size:20px}.bar{align-items:stretch}.bar label{display:grid;gap:4px;flex:1 1 180px}}
</style></head><body><main>
<h1>E1.9 事件—语义—EAF—动作清查矩阵</h1>
<p>固定官方源码与既有离线审计的只读结果。分级是保留策略，不是使用频率或删除许可。<a href="../expression-node-workbench-e1-4d-20261021/index.html">节点与动画预览</a></p>
<div class="stats"><div class="stat"><span>审阅路由记录</span><b>__ROUTE_COUNT__</b></div><div class="stat"><span>源码行为入口</span><b>__BEHAVIOR_ENTRY_COUNT__</b></div><div class="stat"><span>语义项</span><b>__SEMANTIC_COUNT__</b></div><div class="stat"><span>官方 EAF</span><b>__OFFICIAL_EAF_COUNT__</b></div><div class="stat"><span>候选 EAF</span><b>__CANDIDATE_EAF_COUNT__</b></div><div class="stat"><span>未映射名称</span><b>__UNMAPPED_NAME_COUNT__</b></div></div>
<div class="bar"><label>筛选 <input id="q" type="search" placeholder="事件、语义、EAF、动作"></label><label>类型 <select id="type"><option value="">全部</option><option value="event_route">源码行为入口</option><option value="semantic_reference">语义反查</option><option value="unmapped_reference">未映射反查</option><option value="semantic">语义项</option><option value="official_eaf_resource">官方 EAF</option><option value="candidate_eaf_resource">离线候选 EAF</option><option value="unmapped_static_call">未映射调用</option></select></label><label>分级 <select id="class"><option value="">全部</option><option value="core">核心保护</option><option value="protected">保留复核</option><option value="candidate">候选不部署</option></select></label><span id="count" class="small" role="status" aria-live="polite"></span></div>
<div class="table-wrap"><table><colgroup><col style="width:24%"><col style="width:17%"><col style="width:22%"><col style="width:19%"><col style="width:18%"></colgroup><thead><tr><th>入口 / 对象</th><th>语义</th><th>EAF / 大小</th><th>动作关联</th><th>复核结论</th></tr></thead><tbody id="body"></tbody></table></div>
<details><summary>容量、候选与来源限制</summary><pre id="capacity"></pre><ul id="limits"></ul></details>
<script>
const matrix=__MATRIX_JSON__;
const rows=matrix.routes.concat(matrix.semantics,matrix.resources,matrix.unmapped_calls);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c==="'"?'&#39;':'&quot;');
const text=v=>Array.isArray(v)?v.join(', '):(v??'');
const typeNames={event_route:'源码行为入口',semantic_reference:'语义反查',unmapped_reference:'未映射调用反查',semantic:'语义项',official_eaf_resource:'官方 EAF',candidate_eaf_resource:'离线候选 EAF',unmapped_static_call:'未映射源码调用'};
const classNames={core:'核心保护',protected:'保留复核',candidate:'候选不部署'};
const dispositions={keep_and_review:'保留，待复核',keep_in_device_profile:'保留在设备配置',do_not_add_yet:'暂不加入设备',keep_and_recheck:'保留，核对映射'};
const boundaries={static_mapping_only:'静态映射，未采集运行记录',source_conditioned_not_runtime_observed:'源码条件分支，未采集运行记录',state_conditioned_not_runtime_observed:'依赖会话状态，未采集运行记录',dynamic_entry_not_runtime_observed:'动态入口，未采集运行记录',server_or_external_not_runtime_observed:'依赖服务端，未采集运行记录',unmapped_static_call:'源码调用名尚未映射',static_resource_inventory:'资源清单，非播放统计',offline_candidate_not_runtime_observed:'离线候选，未经设备验证'};
const details=r=>{const ev=(r.static_evidence||[]).map(e=>`${esc(e.path||'')} ${esc(e.line||'')}: ${esc(e.excerpt||e.ref||'')}`).join('<br>');const outputs=(r.output_bindings||[]).map(o=>`${o.api}: ${o.semantic} / ${o.src} / ${o.layout} / ${o.fps} FPS / loop=${o.loop} / duration=${o.duration_ms??'未标注'}`).join(' | ');const affected=text(r.affected_semantics);return `<details><summary>条件、影响与证据</summary><div>${esc(r.condition||'')}</div><div>共享影响：${esc(affected||'无额外语义')}</div><div>${esc(r.source_note||r.disposition_reason||'')}</div><div>${esc(outputs)}</div><div class="small">${ev}</div></details>`};
const resourceText=r=>{const names=text(r.eaf_ids);const sizes=r.resource_size_bytes?[`${r.resource_size_bytes} B`]:Object.entries(r.resource_bytes_by_id||{}).map(([k,v])=>`${k}: ${v} B`);return `<code>${esc(names)}</code><div class="small">${esc(sizes.join(', '))}</div>`};
const actionText=r=>{const relation=r.action_relation||{};const ids=[...new Set([...(relation.semantic_action_mapping_requests||[]),...(relation.source_branch_action_requests||[]),...(relation.available_action_options||[])])];return `${esc(ids.join(', '))}<div class="small">${esc(relation.note||'')}</div>`};
function render(){const q=document.querySelector('#q').value.toLowerCase(), t=document.querySelector('#type').value, c=document.querySelector('#class').value;const filtered=rows.filter(r=>{const hay=JSON.stringify(r).toLowerCase();return(!q||hay.includes(q))&&(!t||r.row_type===t)&&(!c||r.classification===c)});document.querySelector('#count').textContent=`显示 ${filtered.length} / ${rows.length} 条`;document.querySelector('#body').innerHTML=filtered.map(r=>`<tr class="${esc(r.classification)}"><td><span class="pill">${esc(typeNames[r.row_type]||r.row_type)}</span><div><b>${esc(r.event_or_entry)}</b></div><div class="small">${esc(r.event_id||'')}</div>${details(r)}</td><td>${esc(text(r.semantic_ids))}</td><td>${resourceText(r)}</td><td>${actionText(r)}</td><td><b>${esc(classNames[r.classification]||r.classification)}</b><div class="small">${esc(dispositions[r.disposition]||r.disposition)}</div><div class="small">${esc(r.disposition_reason)}</div><div class="small">${esc(boundaries[r.confidence]||r.confidence)}</div></td></tr>`).join('')}
document.querySelector('#q').addEventListener('input',render);document.querySelector('#type').addEventListener('change',render);document.querySelector('#class').addEventListener('change',render);document.querySelector('#capacity').textContent=JSON.stringify(matrix.capacity,null,2);document.querySelector('#limits').innerHTML=matrix.limitations.map(v=>`<li>${esc(v)}</li>`).join('');render();
</script></main></body></html>'''
    return (template.replace("__ROUTE_COUNT__", str(summary["route_count"])).replace("__BEHAVIOR_ENTRY_COUNT__", str(summary["behavior_entry_count"])).replace("__SEMANTIC_COUNT__", str(summary["semantic_count"])).replace("__OFFICIAL_EAF_COUNT__", str(summary["official_eaf_count"])).replace("__CANDIDATE_EAF_COUNT__", str(summary["candidate_eaf_count"])).replace("__UNMAPPED_NAME_COUNT__", str(summary["unmapped_name_count"])).replace("__MATRIX_JSON__", data))


def write_outputs(matrix: dict[str, Any], output: Path, *, refresh: bool = False) -> dict[str, Any]:
    output = output.resolve()
    artifact_root = (ROOT / "artifacts").resolve()
    if output.parent != artifact_root or not output.name.startswith("expression-binding-matrix-e1-9-"):
        raise ValueError("output must be an E1.9 direct child of project artifacts")
    rows = flatten_csv_rows(matrix)
    csv_text = io.StringIO(newline="")
    writer = csv.DictWriter(csv_text, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    contents = {
        "matrix.json": (json.dumps(matrix, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "matrix.csv": csv_text.getvalue().encode("utf-8-sig"),
        "index.html": build_html(matrix).encode("utf-8"),
        "README.txt": (
            "E1.9 离线清查矩阵\n\n"
            "index.html：可筛选审阅页；matrix.json：机器可读矩阵；matrix.csv：表格导出；report.json：生成与安全边界。\n"
            "本轮只读固定报告，没有访问 COM4/COM8，没有改固件、NVS、分区或设备资源，也没有删除资源。\n"
            "未观察到运行时播放不等于无用；共享 EAF 的替换会影响所有列出的语义。候选资源与设备 Profile 分开。\n"
        ).encode("utf-8"),
    }
    report = {
        "schema": "deskbot.expression_binding_matrix_report/v1",
        "result": "offline_matrix_created",
        "edition": matrix["edition"],
        "source_commit": matrix["source"]["official_commit"],
        "hardware_accessed": False,
        "firmware_changed": False,
        "nvs_changed": False,
        "resources_deleted": False,
        "device_package_changed": False,
        "deployment_eligible": False,
        "counts": matrix["summary"],
        "files": {},
        "validation": {
            "all_official_rows_disallow_removal": all(not row["allowed_remove_from_device_profile"] for row in matrix["resources"] if row["row_type"] == "official_eaf_resource"),
            "candidate_separated_from_official": all(row["row_type"] != "candidate_eaf_resource" or row["classification"] == "candidate" for row in matrix["resources"]),
            "runtime_observation_not_overstated": all(row["runtime_observation"] == "not_collected" for row in matrix["routes"] + matrix["semantics"] + matrix["resources"] + matrix["unmapped_calls"]),
            "shared_resources_explicit": all(row["shared_object"] is not None for row in matrix["resources"] if len(row["semantic_ids"]) > 1),
        },
    }
    for name, raw in contents.items():
        report["files"][name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    contents["report.json"] = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if refresh and output.exists():
        previous = output / "matrix.json"
        if not previous.exists() or read_json(previous).get("schema") != SCHEMA:
            raise ValueError("refresh requires an existing E1.9 generated matrix")
    for name, raw in contents.items():
        target = output / name
        if target.exists() and target.read_bytes() != raw and not refresh:
            raise ValueError(f"refusing to replace different generated evidence without --refresh: {target}")
    output.mkdir(parents=True, exist_ok=True)
    for name, raw in contents.items():
        target = output / name
        if not target.exists() or target.read_bytes() != raw:
            temporary = output / (name + ".pending")
            temporary.write_bytes(raw)
            temporary.replace(target)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--refresh", action="store_true", help="replace only E1.9 generated artifacts under review")
    args = parser.parse_args()
    for path, expected_hash in INPUTS.values():
        if not path.exists():
            raise SystemExit(f"missing input: {path}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise SystemExit(f"pinned input hash mismatch: {path}")
    matrix = build_matrix(read_json(DEFAULT_BINDINGS), read_json(DEFAULT_RELEASE_AUDIT), read_json(DEFAULT_PACKAGE_AUDIT), read_json(DEFAULT_E1_3I_AUDIT))
    report = write_outputs(matrix, args.output, refresh=args.refresh)
    print(json.dumps({"output": str(args.output), "summary": matrix["summary"], "validation": report["validation"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
