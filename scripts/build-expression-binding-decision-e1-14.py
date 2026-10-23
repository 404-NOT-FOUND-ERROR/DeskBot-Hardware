#!/usr/bin/env python3
"""Build/check E1.14 from public, pinned reports; never access firmware or ports."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/expression-binding-decision-e1-14-20261024"
TEMPLATE = ROOT / "scripts/expression-binding-decision-e1-14.template.html"
MATRIX = "artifacts/expression-binding-matrix-e1-9-20261022/matrix.json"
BINDINGS = "artifacts/expression-binding-e1-4a-20261021/bindings.json"
INPUT_HASHES = {
    MATRIX: "f2867059513910d91d21a534b5953869dd5312eadcfb961494fc5ae6d29d90c1",
    BINDINGS: "98d57c67e52e240cb7ef0ed8c99cfc088f834fbf662424cd48c2c03c50bd4921",
}
CAPACITY = {
    "edition": "E1.13", "accepted": True,
    "partition_bytes": 6_144_000, "package_bytes": 5_538_349,
    "remaining_bytes": 605_651, "member_count": 29,
    "changed_members": ["insert.eaf", "eat.eaf"],
    "package_sha256": "5e342db35da13531836339a98c25d6e74ac6fdff446ad4e515fcf39f28b7e81f",
    "partition_readback_sha256": "6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205",
    "source": "docs/baseline-status.md",
    "measurement": "accepted package record; no package rebuilt or device read in E1.14",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_pinned(relative: str) -> dict[str, Any]:
    raw = (ROOT / relative).read_bytes()
    if digest(raw) != INPUT_HASHES[relative]:
        raise ValueError("pinned input changed; evidence review required: " + relative)
    return json.loads(raw)


def resource_decision(item: dict[str, Any]) -> dict[str, Any]:
    candidate = item["row_type"] == "candidate_eaf_resource"
    shared = len(item["semantic_ids"]) > 1
    if candidate:
        kind, label = "offline_candidate", "仅离线候选"
        reason = "许可、布局与当前播放器适配尚未闭合；只保留元数据。"
        recommendation = "继续离线研究"
    elif item["classification"] == "core":
        kind, label = "core", "核心保护"
        reason = "官方固定状态或底座/节拍入口使用，保留为当前基线。"
        recommendation = "保留；变化需单独回归"
    elif shared:
        kind, label = "protected_shared", "共享保护"
        reason = "多个语义共用文件；直接换文件会影响所有引用者。"
        recommendation = "保留；单语义变化使用独立映射提案"
    else:
        kind, label = "protected_single", "独立保护"
        reason = "未观察到不等于无用；可研究单资源替换，暂不删除。"
        recommendation = "保留；替换需单独测试"
    return {
        "resource_id": item["event_or_entry"], "kind": kind, "label": label,
        "reason": reason, "recommendation": recommendation,
        "current_profile_member": not candidate,
        "removal_authorized": False, "replacement_authorized": False,
        "semantic_ids": item["semantic_ids"], "action_ids": item["action_ids"],
        "route_ids": item["route_ids"],
        "reference_bytes": item["resource_size_bytes"],
        "reference_sha256": item["resource_sha256"],
        "size_basis": "offline_candidate" if candidate else "upstream_source_not_deployed_member_size",
        "e1_13_member_reencoded": item["event_or_entry"] in CAPACITY["changed_members"],
        "runtime_observation": "not_collected",
    }


def route_projection(item: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    event_id = item["event_id"]
    kind = "semantic" if event_id.startswith("semantic-") else (
        "unmapped" if event_id.startswith("missing-") else "behavior")
    relation = item["action_relation"]
    return {
        "route_id": item["row_id"], "event_id": event_id,
        "route_kind": kind, "trigger": item["event_or_entry"],
        "condition": item["condition"], "resolution": item["resolution"],
        "source_note": item.get("source_note"),
        "semantics": item["semantic_ids"], "resources": item["eaf_ids"],
        "outputs": [{key: output.get(key) for key in
                     ("semantic", "src", "layout", "fps", "loop", "action_id", "api")}
                    for output in item.get("output_bindings", [])],
        "actions": relation["semantic_action_mapping_requests"],
        "source_branch_actions": relation["source_branch_action_requests"],
        "available_action_options": relation["available_action_options"],
        "action_note": relation["note"],
        "source_steps": source.get("steps", []),
        "evidence": [{key: evidence.get(key) for key in
                      ("ref", "path", "line", "end_line", "origin")}
                     for evidence in item.get("static_evidence", [])],
        "runtime_observation": "not_collected",
    }


def build_decision() -> dict[str, Any]:
    matrix, bindings = read_pinned(MATRIX), read_pinned(BINDINGS)
    record = (ROOT / "docs/baseline-status.md").read_bytes()
    acceptance = (ROOT / "docs/e1-13-acceptance-20261024.md").read_bytes()
    for value in ("5,538,349 B", "605,651 B", CAPACITY["package_sha256"],
                  CAPACITY["partition_readback_sha256"]):
        if value not in record.decode("utf-8"):
            raise ValueError("E1.13 capacity record drift")
    if "`accepted`" not in acceptance.decode("utf-8"):
        raise ValueError("E1.13 acceptance record missing")
    if matrix["safety"]["runtime_observed"] or matrix["safety"]["hardware_accessed"]:
        raise ValueError("static source evidence required")
    sources = {item["id"]: item for item in bindings["routes"]}
    routes = [route_projection(item, sources[item["event_id"]]) for item in matrix["routes"]]
    resources = [resource_decision(item) for item in matrix["resources"]]
    counts = Counter(item["kind"] for item in resources)
    return {
        "schema": "deskbot.expression_binding_decision/v1", "edition": "E1.14",
        "review_date": "2026-10-24", "result": "offline_binding_decision",
        "deployment_eligible": False,
        "source": {
            "inputs": [{"path": name, "sha256": value} for name, value in INPUT_HASHES.items()],
            "capacity_record_sha256": digest(record), "acceptance_record_sha256": digest(acceptance),
            "official_commit": matrix["source"]["official_commit"],
            "scope": "静态源码证据；没有逐条运行轨迹。本体验收仅继承 E1.13 记录。",
        },
        "safety": {
            "hardware_accessed": False, "serial_ports_accessed": [],
            "firmware_changed": False, "nvs_changed": False,
            "device_package_changed": False, "candidate_media_included": False,
        },
        "policy": {
            "official_baseline_immutable": True, "missing_runtime_evidence_means_keep": True,
            "candidate_separate_from_device_profile": True,
            "shared_resource_replacement_is_not_single_semantic_replacement": True,
            "action_mapping_is_separate_from_eaf": True,
        },
        "capacity": CAPACITY,
        "summary": {
            "route_count": len(routes), "behavior_entry_count": 20, "semantic_count": 29,
            "unmapped_call_count": 9, "unmapped_name_count": 7,
            "resource_count": len(resources), "official_eaf_count": 18,
            "core_count": counts["core"], "protected_shared_count": counts["protected_shared"],
            "protected_single_count": counts["protected_single"],
            "offline_candidate_count": counts["offline_candidate"],
        },
        "layout_catalog": bindings["layouts"], "action_catalog": bindings["actions"],
        "resource_decisions": resources, "route_projections": routes,
        "unmapped_calls": [{"name": item["event_or_entry"], "evidence": item["static_evidence"]}
                           for item in matrix["unmapped_calls"]],
        "review_queue": [
            {"id": "differentiate-happy-semantics", "status": "next_offline_review",
             "scope": ["embarrassed", "surprised"],
             "decision": "先对比 surprised→shocked.eaf 的单语义复用提案；embarrassed 独立样稿继续研究。共享 Happy.eaf 保留，动作表保持独立。"},
            {"id": "optimize-large-protected-resources", "status": "accepted_body_scope",
             "scope": ["insert.eaf", "eat.eaf"],
             "decision": "E1.13 已无损重编码并通过本体验收；不重复压缩。底座触发待维修后补测。"},
            {"id": "resolve-unobserved-and-unmapped", "status": "offline_replay_first",
             "scope": ["dynamic_semantics", "unmapped_calls"],
             "decision": "先整理电脑回放用例；未映射调用不据此删除，也不自动新增固件挂钩。"},
        ],
        "limitations": [
            "5/3/10/1 是资源分类数，不是 E1.9 所有行的 core/protected 数。",
            "参考文件大小来自上游源码；insert/eat 已重编码，不能当作设备成员当前尺寸。",
            "摸头依赖服务端动态情绪；不能从静态清单推断每次都播放 embarrassed。",
            "未映射名称不等于现有 EAF 未使用，也不足以证明应删除错误/更新状态入口。",
            "候选只有元数据，未获部署许可；本页不执行资源替换或电机动作。",
        ],
    }


def render_outputs() -> dict[str, bytes]:
    decision = build_decision()
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("__DECISION_DATA__") != 1:
        raise ValueError("template must contain one data placeholder")
    payload = json.dumps(decision, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return {
        "decision.json": (json.dumps(decision, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "index.html": template.replace("__DECISION_DATA__", payload).encode("utf-8"),
        "README.txt": b"E1.14 offline binding decisions. Open index.html locally.\nNo serial, network, firmware or device writes. Candidate metadata only.\nRebuild: python scripts/build-expression-binding-decision-e1-14.py\n",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare generated evidence without writing")
    args = parser.parse_args()
    outputs = render_outputs()
    if args.check:
        for name, raw in outputs.items():
            if not (OUTPUT / name).is_file() or (OUTPUT / name).read_bytes() != raw:
                raise SystemExit("stale/missing artifact: " + name)
        print("E1.14 pinned sources, accepted capacity and generated artifacts match")
    else:
        if OUTPUT.is_symlink() or OUTPUT.resolve().parent != (ROOT / "artifacts").resolve():
            raise SystemExit("unsafe output directory")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name, raw in outputs.items():
            target = OUTPUT / name
            if target.is_symlink():
                raise SystemExit("refusing symlink output: " + name)
            target.write_bytes(raw)
        print("generated E1.14: 56 routes, 18 official resources, 1 metadata-only candidate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
