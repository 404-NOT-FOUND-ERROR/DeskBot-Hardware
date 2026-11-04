#!/usr/bin/env python3
"""Build the E1.31 offline multi-role expression workbench.

The page is a derived, read-only-with-local-draft view of the accepted E1.18
resource snapshot. It never reads a device, writes firmware, or emits a
deployable package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/expression-node-preview-e1-19-20261024/index.html"
OUT = ROOT / "artifacts/role-expression-workbench-e1-31-20261029"
TEMPLATE = ROOT / "scripts/role-expression-e1-31.template.html"
CORE = ROOT / "scripts/role-expression-e1-31-core.js"
CONTROLLER = ROOT / "scripts/role-expression-e1-31-controller.js"

LABELS = {
    "happy": "开心", "laughing": "大笑", "funny": "有趣", "loving": "喜欢",
    "embarrassed": "害羞", "confident": "自信", "delicious": "美味", "sad": "难过",
    "crying": "哭泣", "sleepy": "困倦", "silly": "俏皮", "angry": "生气",
    "surprised": "惊讶", "shocked": "震惊", "thinking": "思考", "winking": "眨眼",
    "relaxed": "放松", "confused": "困惑", "neutral": "平静", "idle": "待机",
    "insert": "插入配件", "look_left": "向左看", "look_right": "向右看",
    "look_around": "环顾", "question": "疑问", "beat_swing": "摇摆",
    "eat": "进食", "tried": "尝试", "listen": "聆听",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def hash_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha(raw)


def load_e119() -> dict[str, Any]:
    text = SOURCE.read_text(encoding="utf-8")
    match = re.search(r'<script id="e119-data" type="application/json">(.*?)</script>', text, re.S)
    if not match:
        raise ValueError("E1.19 data payload is missing")
    data = json.loads(match.group(1))
    if data.get("edition") != "E1.19" or data.get("hardware_accessed") is not False:
        raise ValueError("E1.19 is not the expected offline baseline")
    if len(data.get("snapshots", {}).get("deployed", {})) != 29 or len(data.get("assets", {})) != 18:
        raise ValueError("E1.19 semantic/resource inventory drift")
    return data


def role_profile(shell_id: str, magnet_id: str, role_id: str, display: str, voice: str,
                 phrases: list[str], expression_map: dict[str, str], action_map: dict[str, str]) -> dict[str, Any]:
    return {
        "schema": "deskbot.role_profile.v1",
        "shell_id": shell_id,
        "magnet_profile_id": magnet_id,
        "role_id": role_id,
        "display_name": display,
        "voice_id": voice,
        "catchphrases": phrases,
        "expression_map": expression_map,
        "action_map": action_map,
        "asset_version": "deskbot.expression_assets/e1-18",
        "binding_version": "deskbot.expression_bindings/v1",
        "profile_revision": 1,
        "binding_state": "draft",
        "notes": "E1.31 合成示例；只用于离线角色接线审阅，不代表已登记外壳或设备激活。",
    }


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    e119 = load_e119()
    deployed = e119["snapshots"]["deployed"]
    source_assets = e119["assets"]
    names = sorted(source_assets)
    baseline = {
        "release_id": "E1.18",
        "package_sha256": e119["baselines"]["deployed"]["package_sha256"],
        "index_sha256": e119["baselines"]["deployed"]["index_sha256"],
        "mapping_sha256": hash_json(deployed),
        "catalog_sha256": hash_json({name: {"sha256": source_assets[name]["deployed_sha256"],
                                             "bytes": source_assets[name]["deployed_bytes"]} for name in names}),
    }
    semantics: dict[str, Any] = {}
    layout_object_kinds = {"eye_anim": "eye", "listen_anim": "listen"}
    for semantic, binding in deployed.items():
        expected_kind = layout_object_kinds.get(binding["layout_id"])
        if expected_kind is None:
            raise ValueError(f"unsupported E1.18 layout: {binding['layout_id']}")
        allowed_resources = sorted(
            name for name, item in source_assets.items()
            if item.get("object_kind") == expected_kind
        )
        if binding["resource_id"] not in allowed_resources:
            raise ValueError(f"baseline resource/layout mismatch: {semantic}")
        semantics[semantic] = {
            "label": LABELS.get(semantic, semantic),
            "resource_id": binding["resource_id"],
            "allowed_resources": allowed_resources,
            "fps": binding["fps"],
            "loop": binding["loop"],
            "action_id": binding.get("action_id"),
            "layout_id": binding["layout_id"],
            "baseline_evidence": binding.get("evidence", []),
        }
    assets: dict[str, Any] = {}
    for name, item in source_assets.items():
        assets[name] = {key: value for key, value in item.items() if key != "asset_key"}
        assets[name]["bytes"] = item["deployed_bytes"]
        assets[name]["source"] = "official E1.18 snapshot"

    def mappings(overrides: dict[str, str] | None = None) -> dict[str, str]:
        result = {semantic: info["resource_id"] for semantic, info in semantics.items()}
        if overrides:
            result.update(overrides)
        return result

    roles = [
        {
            "role_profile": role_profile(
                "shell-a", "profile-a", "frog-chef", "青蛙厨师", "voice-demo-chef",
                ["今天要不要试试新菜？", "呱，闻起来很香。"],
                {"greeting": "happy", "thinking": "confused", "bashful": "embarrassed",
                 "sleep": "sleepy", "unknown_shell": "question"},
                {"greeting": "cat_nuzzle", "curious": "look_around", "celebrate": "beat_swing"},
            ),
            "semantic_resources": mappings({"happy": "Happy.eaf", "delicious": "eat.eaf"}),
        },
        {
            "role_profile": role_profile(
                "shell-b", "profile-b", "astronaut-cat", "宇航猫", "voice-demo-space",
                ["导航完成，喵。", "前方发现新星球。"],
                {"greeting": "surprised", "thinking": "question", "bashful": "embarrassed",
                 "sleep": "sleepy", "unknown_shell": "question"},
                {"greeting": "look_around", "curious": "shark_head_decay", "celebrate": "cat_nuzzle"},
            ),
            "semantic_resources": mappings({"happy": "shocked.eaf", "surprised": "shocked.eaf", "look_around": "look_around.eaf"}),
        },
    ]
    initial = {
        "schema": "deskbot.role_expression_draft/v1",
        "edition": "E1.31",
        "scope": "offline_draft_only",
        "firmware_deployable": False,
        "draft_revision": 1,
        "baseline": baseline,
        "selected_role_id": "frog-chef",
        "roles": roles,
    }
    data = {
        "schema": "deskbot.role_expression_workbench/v1",
        "edition": "E1.31",
        "date": "2026-10-29",
        "hardware_accessed": False,
        "firmware_changed": False,
        "deployment_eligible": False,
        "baseline": baseline,
        "asset_version": "deskbot.expression_assets/e1-18",
        "binding_version": "deskbot.expression_bindings/v1",
        "semantics": semantics,
        "assets": assets,
        "initial": initial,
        "limits": [
            "角色和表情接线只保存在浏览器内存，刷新页面即丢失，必须显式导出。",
            "E1.18 基线资源是只读参照；E1.31 不生成资源包、固件或 NVS 写入。",
            "未接线是合法的离线草稿状态；只有与语义布局兼容的官方资源可接入。",
            "预览使用官方解码图集，不等于圆屏实拍；动作仍是协议意图，不模拟电机执行。",
        ],
    }
    report = {
        "schema": "deskbot.role_expression_workbench/v1",
        "edition": "E1.31",
        "result": "offline_multi_role_workbench_created",
        "hardware_accessed": False,
        "firmware_changed": False,
        "deployment_eligible": False,
        "baseline_release": "E1.18",
        "semantic_count": len(semantics),
        "official_resource_count": len(assets),
        "role_count": len(roles),
        "draft_schema": "deskbot.role_expression_draft/v1",
        "baseline": baseline,
    }
    return data, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated files without rewriting them")
    args = parser.parse_args()
    data, report = build()
    template = TEMPLATE.read_text(encoding="utf-8")
    html = template.replace("__E131_DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    html = html.replace("__E131_CORE__", CORE.read_text(encoding="utf-8"))
    html = html.replace("__E131_CONTROLLER__", CONTROLLER.read_text(encoding="utf-8"))
    if any(token in html for token in ("__E131_DATA__", "__E131_CORE__", "__E131_CONTROLLER__")):
        raise ValueError("unexpanded E1.31 template token")
    products = {
        OUT / "index.html": html.encode("utf-8"),
        OUT / "report.json": (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        OUT / "README.txt": (
            "DeskBot E1.31 多角色表情工作台 / Offline multi-role expression workbench\n\n"
            "Open index.html directly or through a loopback HTTP server.\n"
            "This page is an offline draft editor: it has no COM, network, firmware, NVS, or device writes.\n"
            "E1.18 is a read-only baseline. Export the draft JSON if it needs to be reviewed or archived.\n"
        ).encode("utf-8"),
    }
    for path, content in products.items():
        if args.check:
            if not path.exists() or path.read_bytes() != content:
                raise SystemExit(f"stale generated artifact: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print(json.dumps({"result": "checked" if args.check else "built", "edition": "E1.31",
                      "semantic_count": report["semantic_count"], "official_resource_count": report["official_resource_count"],
                      "role_count": report["role_count"], "html_bytes": len(products[OUT / "index.html"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
