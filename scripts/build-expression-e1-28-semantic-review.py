#!/usr/bin/env python3
"""Build the E1.28 offline review of four common expression semantics.

The builder extracts only the three official EAF atlases needed by happy,
embarrassed, sleepy and neutral from the audited E1.19 payload.  It joins
those resources with current source-level trigger and action facts.  The
result is a self-contained, read-only page; it never opens a serial port,
changes firmware, or treats a SetEmotion call as LCD or motor completion.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/expression-node-preview-e1-19-20261024/index.html"
ORAL = ROOT / "artifacts/expression-e1-27-runtime-observation-20261027/oral-summary.json"
TEMPLATE = ROOT / "scripts/expression-e1-28-semantic-review.template.html"
CSS = ROOT / "scripts/expression-e1-28-semantic-review.css"
JS = ROOT / "scripts/expression-e1-28-semantic-review.js"
OUT = ROOT / "artifacts/expression-e1-28-semantic-review-20261027"

SEMANTICS = ("happy", "embarrassed", "sleepy", "neutral")
ASSETS = ("Happy.eaf", "sleep.eaf", "winking.eaf")
ACTION_LABELS = {
    "shark_head": "摇头动作",
    "cat_nuzzle": "亲昵/蹭头",
    "shark_head_decay": "缓慢摇头",
}
SOURCE_FILES = {
    "touch": ROOT / "firmware/body/main/boards/esp_vocat/touch_sensor.cc",
    "application": ROOT / "firmware/body/main/application.cc",
    "base": ROOT / "firmware/body/main/boards/esp_vocat/base_control.cc",
    "emote": ROOT / "firmware/body/main/display/emote_display.cc",
    "emote_json": ROOT / "firmware/body/main/boards/esp_vocat/assets/360_360/emote.json",
}

# Freeze the E1.26 observer-enabled source behind this offline review.  Later
# edits must fail closed rather than silently changing historical evidence.
SOURCE_HASHES = {
    "touch": "7b9b3cbe3c8bdeef85080ddb8b6c546dce43263d33a045ac09190ce2a19a081e",
    "application": "a55aab231b8996404b8342d029fd0dc7a358046efa5bdc7296f55845254a020d",
    "base": "c00cb5f6f8b8a7249e7327c9ab9c3a03a11f33ef11e5ef3ae3475291b82ad870",
    "emote": "a008dc843e28b7e4c634d3ef65ee9ffcfba7f8442a7b967c01d3af036e34bd60",
    "emote_json": "bc8534b2cc9807f18747ce491dfeab6ff785bca79cb5f2f6fd920b34892fec47",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_e119() -> dict[str, Any]:
    text = BASE.read_text(encoding="utf-8")
    match = re.search(r'<script id="e119-data" type="application/json">(.*?)</script>', text, re.S)
    if match is None:
        raise ValueError("E1.19 payload not found")
    return json.loads(match.group(1))


def span(path: Path, start: str, end: str | None = None) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start_index = next((i for i, line in enumerate(lines) if start in line), None)
    if start_index is None:
        raise ValueError(f"source anchor not found: {path} / {start}")
    end_index = start_index if end is None else next(
        (i for i in range(start_index, len(lines)) if end in lines[i]), None
    )
    if end_index is None:
        raise ValueError(f"source end anchor not found: {path} / {end}")
    lo = max(0, start_index - 1)
    hi = min(len(lines), end_index + 2)
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "line_start": lo + 1,
        "line_end": hi,
        "ref": f"{str(path.relative_to(ROOT)).replace(chr(92), '/') }#L{lo + 1}-L{hi}",
        "snippet": "\n".join(lines[lo:hi]),
    }


def json_line(path: Path, token: str) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if token in line:
            relative = str(path.relative_to(ROOT)).replace("\\", "/")
            return {
                "path": relative,
                "line_start": index + 1,
                "line_end": index + 1,
                "ref": f"{relative}#L{index + 1}",
                "snippet": line,
            }
    raise ValueError(f"JSON source anchor not found: {path} / {token}")


def source_evidence() -> dict[str, dict[str, Any]]:
    return {
        "touch_triple": span(SOURCE_FILES["touch"], "constexpr int PRESS_DOWN_TRIGGER_COUNT", "app.WakeWordInvoke(wake_word);") ,
        "llm_emotion": span(SOURCE_FILES["application"], 'strcmp(type->valuestring, "llm")', "display->SetEmotion(emotion_str.c_str());"),
        "state_emotion": span(SOURCE_FILES["application"], "void Application::SetDeviceState", "case kDeviceStateSpeaking:"),
        "pair_detect": span(SOURCE_FILES["base"], "case VOCAT_BASE_CMD_RECV_SWITCH_PAIR_DETECT", "vocat_base_control_set_action(VOCAT_BASE_CMD_SET_ACTION_LOOK_AROUND);"),
        "set_emotion": span(SOURCE_FILES["emote"], "void EmoteDisplay::SetEmotion", "vocat_base_control_set_action(it->second);"),
        "power_save": span(SOURCE_FILES["emote"], "void EmoteDisplay::SetPowerSaveMode", "void EmoteDisplay::SetTheme"),
        "happy_resource": json_line(SOURCE_FILES["emote_json"], '"emote": "happy"'),
        "embarrassed_resource": json_line(SOURCE_FILES["emote_json"], '"emote": "embarrassed"'),
        "sleepy_resource": json_line(SOURCE_FILES["emote_json"], '"emote": "sleepy"'),
        "neutral_resource": json_line(SOURCE_FILES["emote_json"], '"emote": "neutral"'),
    }


def build_data() -> tuple[dict[str, Any], dict[str, Any]]:
    for required in (BASE, ORAL, TEMPLATE, CSS, JS, *SOURCE_FILES.values()):
        if not required.is_file():
            raise FileNotFoundError(required)
    e119 = load_e119()
    deployed = e119["snapshots"]["deployed"]
    assets = {name: dict(e119["assets"][name]) for name in ASSETS}
    for name, asset in assets.items():
        atlas = asset.get("atlas", "")
        if not atlas.startswith("data:image/png;base64,"):
            raise ValueError(f"missing embedded atlas: {name}")
        if asset.get("object_kind") != "eye":
            raise ValueError(f"unexpected object kind: {name}")
        if asset.get("atlas_sha256") is None or asset.get("frame_count", 0) <= 0:
            raise ValueError(f"incomplete asset metadata: {name}")
        try:
            atlas_bytes = base64.b64decode(atlas.split(",", 1)[1], validate=True)
        except (ValueError, IndexError) as error:
            raise ValueError(f"invalid embedded atlas: {name}") from error
        if not atlas_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"embedded atlas is not PNG: {name}")
        if sha(atlas_bytes) != asset["atlas_sha256"]:
            raise ValueError(f"atlas checksum mismatch: {name}")
        asset["atlas_verified"] = True
        asset["atlas_bytes"] = len(atlas_bytes)

    for key, path in SOURCE_FILES.items():
        if sha(path.read_bytes()) != SOURCE_HASHES[key]:
            raise ValueError(f"E1.28 source anchor drift: {key}")

    bindings: dict[str, dict[str, Any]] = {}
    for semantic in SEMANTICS:
        row = dict(deployed[semantic])
        action_id = row.get("action_id")
        bindings[semantic] = {
            "resource_id": row["resource_id"],
            "fps": row["fps"],
            "loop": row["loop"],
            "layout_id": row["layout_id"],
            "action_id": action_id,
            "action_label": ACTION_LABELS.get(action_id, action_id or ""),
        }
    expected = {
        "happy": ("Happy.eaf", "shark_head", True),
        "embarrassed": ("Happy.eaf", "cat_nuzzle", True),
        "sleepy": ("sleep.eaf", "shark_head_decay", True),
        "neutral": ("winking.eaf", None, False),
    }
    for semantic, values in expected.items():
        if (bindings[semantic]["resource_id"], bindings[semantic]["action_id"], bindings[semantic]["loop"]) != values:
            raise ValueError(f"E1.19 mapping drift: {semantic}")

    evidence = source_evidence()
    semantics = [
        {
            "id": "happy", "label_zh": "开心", "shared_resource": True,
            "trigger_kind": "mixed", "trigger_kind_label": "静态 + 动态",
            "trigger_title": "PAIR_DETECT / llm.emotion",
            "trigger_summary": "配对检测本地直接进入 happy；云端 llm emotion 也可动态选择 happy。",
            "call_detail": "emote_set_anim_emoji 后按静态表请求 shark_head。",
            "action_detail": "SetEmotion 的动作映射；配对流程还会另行请求 LOOK_AROUND。",
            "graph_note": "PAIR_DETECT 的顺序来自源码；动作是否完成仍需底座运行证据。",
            "evidence_scope": "源码有本地 PAIR_DETECT 入口；云端 emotion 入口是动态的。",
            "sources": [
                {"label": "配对事件", "detail": "收到 PAIR_DETECT 后调用 SetEmotion(\"happy\")。", "ref": evidence["pair_detect"]["ref"]},
                {"label": "云端入口", "detail": "type=llm 且 emotion 为字符串时动态调用 SetEmotion。", "ref": evidence["llm_emotion"]["ref"]},
                {"label": "动作表", "detail": "happy → shark_head。", "ref": evidence["set_emotion"]["ref"]},
            ],
        },
        {
            "id": "embarrassed", "label_zh": "害羞", "shared_resource": True,
            "trigger_kind": "dynamic", "trigger_kind_label": "动态入口",
            "trigger_title": "摸头三次 → WakeWordInvoke",
            "trigger_summary": "仅在 Idle 时，1500 ms 内三次 PRESS_DOWN 才调用 WakeWordInvoke(“我在摸你猫头”)；害羞表情由服务端 emotion 决定。",
            "call_detail": "当前源码没有在触摸回调中直接 SetEmotion(\"embarrassed\")。",
            "action_detail": "若 SetEmotion 收到 embarrassed，会请求 cat_nuzzle。",
            "graph_note": "不能把摸头三次直接画成固定 Happy/害羞画面；中间有服务端回应。",
            "evidence_scope": "触摸入口是静态可见的；embarrassed 的实际调用与次数没有本地固定记录。",
            "sources": [
                {"label": "摸头入口", "detail": "1500 ms 内三次 PRESS_DOWN 后调用 WakeWordInvoke。", "ref": evidence["touch_triple"]["ref"]},
                {"label": "云端入口", "detail": "服务端返回 llm.emotion 后才进入动态 SetEmotion。", "ref": evidence["llm_emotion"]["ref"]},
                {"label": "动作表", "detail": "embarrassed → cat_nuzzle。", "ref": evidence["set_emotion"]["ref"]},
            ],
        },
        {
            "id": "sleepy", "label_zh": "困倦", "shared_resource": False,
            "trigger_kind": "dynamic", "trigger_kind_label": "动态入口",
            "trigger_title": "llm.emotion= sleepy",
            "trigger_summary": "当前活动显示实现没有把 SetPowerSaveMode 直接绑定为 sleepy；表情入口来自动态 emotion。",
            "call_detail": "收到 sleepy 后播放 sleep.eaf，并按静态表请求 shark_head_decay。",
            "action_detail": "动作请求是 shark_head_decay；不等于进入休眠或关闭背光。",
            "graph_note": "休眠电源逻辑与 sleepy 表情是两条需要分别验证的关系。",
            "evidence_scope": "可确认当前映射和动态入口；不能从源码推断每次休眠都调用 sleepy。",
            "sources": [
                {"label": "云端入口", "detail": "type=llm 的 emotion 字符串动态调用 SetEmotion。", "ref": evidence["llm_emotion"]["ref"]},
                {"label": "休眠边界", "detail": "活动显示的 SetPowerSaveMode 只记录日志并返回，不切换 sleepy。", "ref": evidence["power_save"]["ref"]},
                {"label": "动作表", "detail": "sleepy → shark_head_decay。", "ref": evidence["set_emotion"]["ref"]},
            ],
        },
        {
            "id": "neutral", "label_zh": "中性", "shared_resource": False,
            "trigger_kind": "literal", "trigger_kind_label": "状态入口",
            "trigger_title": "Unknown / Idle / Connecting / Listening",
            "trigger_summary": "设备状态切换中直接调用 SetEmotion(\"neutral\")；Listening 另有 listen.eaf 状态条。",
            "call_detail": "进入 winking.eaf 单次过渡，不在动作映射表中请求底座动作。",
            "action_detail": "无固定动作请求。",
            "graph_note": "neutral 的眼睛资源与 listen.eaf 顶部状态动画是两层，不应合并为一个资源。",
            "evidence_scope": "设备状态分支是源码静态事实；具体切换次数仍需运行时导出。",
            "sources": [
                {"label": "状态分支", "detail": "Unknown/Idle、Connecting、Listening 分支调用 SetEmotion(\"neutral\")。", "ref": evidence["state_emotion"]["ref"]},
                {"label": "资源表", "detail": "neutral → winking.eaf，单次播放。", "ref": evidence["neutral_resource"]["ref"]},
                {"label": "动作表", "detail": "neutral 不在动作映射表中。", "ref": evidence["set_emotion"]["ref"]},
            ],
        },
    ]
    oral = json.loads(ORAL.read_text(encoding="utf-8"))
    if oral.get("reported_recent_record_count") != 24 or oral.get("raw_json_available") is not False:
        raise ValueError("E1.27 oral summary boundary drift")
    if {row["semantic"] for row in oral.get("reported_semantics", [])} != set(SEMANTICS):
        raise ValueError("E1.27 oral summary semantics drift")
    data = {
        "schema": "deskbot.expression.e1_28_semantic_review/v1",
        "edition": "E1.28",
        "date": "2026-10-27",
        "bindings": bindings,
        "semantics": semantics,
        "assets": assets,
        "oral_summary": oral,
        "source_refs": evidence,
        "render_rule": "E1.19 eye_anim: native frame, x=10, y=center+10, mirrored across x=350 inside a 360px circular clip",
    }
    report = {
        "schema": "deskbot.expression.e1_28_semantic_review/v1",
        "edition": "E1.28",
        "date": "2026-10-27",
        "result": "offline_semantic_review_ready",
        "audit_pass": True,
        "validation_scope": "deterministic_source_join_and_local_render_only",
        "deployment_eligible": False,
        "flash_authorized": False,
        "hardware_accessed": False,
        "serial_accessed": False,
        "device_written": False,
        "nvs_changed": False,
        "source": {
            "e119_workbench": "artifacts/expression-node-preview-e1-19-20261024/index.html",
            "e119_sha256": sha(BASE.read_bytes()),
            "oral_summary": "artifacts/expression-e1-27-runtime-observation-20261027/oral-summary.json",
            "oral_summary_sha256": sha(ORAL.read_bytes()),
            "files": {key: {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(path.read_bytes())}
                      for key, path in SOURCE_FILES.items()},
        },
        "asset_selection": {
            "included": list(ASSETS),
            "excluded_from_new_page": "all other EAFs; E1.19 remains the complete historical inventory",
            "hashes": {name: {"eaf_sha256": assets[name]["sha256"], "atlas_sha256": assets[name]["atlas_sha256"],
                              "frame_count": assets[name]["frame_count"], "bytes": assets[name]["deployed_bytes"],
                              "atlas_bytes": assets[name]["atlas_bytes"], "atlas_verified": assets[name]["atlas_verified"]}
                       for name in ASSETS},
        },
        "bindings": bindings,
        "source_refs": evidence,
        "oral_summary_limits": oral["evidence_limits"],
        "checks": [
            "四类映射与 E1.19 已部署快照一致",
            "Happy.eaf 共享关系被标注，未推断为相同触发或动作",
            "摸头三次仅连接到 WakeWordInvoke，未伪造本地 embarrassed 直连",
            "neutral 与 listen.eaf 状态条分开",
            "sleepy 与 SetPowerSaveMode 分开",
            "SetEmotion 后的 EAF 播放输入与动作请求被审查为两条独立支路，没有串行完成假设",
            "三个嵌入 PNG 图集已解码、签名校验并通过 SHA-256",
            "页面不连接串口、不写固件、不修改 NVS/C61/播放器",
        ],
        "limits": [
            "圆屏画面是官方解码帧参考合成，不是设备实拍",
            "SetEmotion 调用关系不证明资源加载、LCD 刷新或电机完成",
            "动态 llm emotion 的实际顺序和频率仍需原始 E1.26 JSON",
            "用户口述 24 条摘要没有逐条序号、时间戳或精确次数",
            "本审阅以锁定的 E1.26 源文件 SHA-256 作为证据锚点；源文件漂移会使生成器失败关闭",
        ],
    }
    return data, report


def build(check: bool) -> dict[str, Any]:
    data, report = build_data()
    template = TEMPLATE.read_text(encoding="utf-8")
    page = template.replace("__E128_CSS__", CSS.read_text(encoding="utf-8"))
    page = page.replace("__E128_DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    page = page.replace("__E128_JS__", JS.read_text(encoding="utf-8"))
    if "__E128_" in page or "connect-src 'none'" not in page:
        raise ValueError("E1.28 page placeholders or offline CSP remain")
    report["generated_html_bytes"] = len(page.encode("utf-8"))
    report["generated_html_sha256"] = sha(page.encode("utf-8"))
    readme = (
        "E1.28 离线语义审查页\n\n"
        "本页只审查 happy、embarrassed、sleepy、neutral 的触发入口、SetEmotion、EAF 和动作请求关系。\n"
        "圆屏动画来自官方解码帧参考合成，不是 LCD 实拍；动态服务器入口、资源加载和动作完成仍需独立运行时证据。\n"
        "本阶段不访问串口、不刷写、不修改固件、NVS、C61 或播放器。\n"
    )
    outputs = {
        OUT / "index.html": page.encode("utf-8"),
        OUT / "report.json": (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        OUT / "README.txt": readme.encode("utf-8"),
    }
    for path, raw in outputs.items():
        if check:
            if not path.is_file() or path.read_bytes() != raw:
                raise ValueError(f"stale generated artifact: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    return {"result": "checked" if check else "built", "edition": "E1.28", "html_bytes": len(outputs[OUT / "index.html"]), "assets": list(ASSETS)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.check), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
