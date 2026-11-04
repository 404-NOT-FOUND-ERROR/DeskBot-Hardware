#!/usr/bin/env python3
"""Build the E1.27 offline expression-call snapshot workbench.

The page is derived from the already audited E1.19 node workbench.  It adds a
local JSON import panel and highlights semantic nodes for observations that
match the E1.26 ``SetEmotion`` call-entry schema.  It never opens a serial
port, writes firmware, or treats an observation as LCD/player evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
BASE_PAGE = ROOT / "artifacts/expression-node-preview-e1-19-20261024/index.html"
SCHEMA_PATH = ROOT / "protocol/expression-runtime-observation-e1-26.schema.json"
MODEL_PATH = ROOT / "protocol/expression_runtime_observation_e1_26.py"
CSS_PATH = ROOT / "scripts/expression-e1-27-observation.css"
ORAL_CSS_PATH = ROOT / "scripts/expression-e1-27-oral-summary.css"
JS_PATH = ROOT / "scripts/expression-e1-27-observation.js"
CORE_PATH = ROOT / "scripts/expression-e1-27-core.js"
OUT = ROOT / "artifacts/expression-e1-27-runtime-observation-20261027"

SCHEMA_ID = "deskbot.expression.runtime_observation.e1_26/v1"
OBSERVATION_POINT = "EmoteDisplay::SetEmotion"
CLAIM_SCOPE = "set_emotion_call_only"
SEMANTICS = {
    "angry", "beat_swing", "confident", "confused", "crying", "delicious",
    "embarrassed", "eat", "funny", "happy", "idle", "insert", "laughing",
    "listen", "look_around", "look_left", "look_right", "loving", "neutral",
    "question", "relaxed", "sad", "shocked", "silly", "sleepy", "surprised",
    "thinking", "tried", "winking", "other",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sample_snapshot() -> dict[str, Any]:
    """Return a deterministic, clearly-labelled offline demonstration snapshot."""
    records = [
        {"sequence": 1, "observation": "set_emotion_call_entered", "semantic": "idle",
         "first_seen_ms": 1000, "last_seen_ms": 1000, "repeat_count": 1},
        {"sequence": 2, "observation": "set_emotion_call_entered", "semantic": "listen",
         "first_seen_ms": 1600, "last_seen_ms": 1600, "repeat_count": 1},
        {"sequence": 3, "observation": "set_emotion_call_entered", "semantic": "happy",
         "first_seen_ms": 2200, "last_seen_ms": 2300, "repeat_count": 2},
        {"sequence": 4, "observation": "set_emotion_call_entered", "semantic": "look_around",
         "first_seen_ms": 3100, "last_seen_ms": 3100, "repeat_count": 1},
        {"sequence": 5, "observation": "set_emotion_call_entered", "semantic": "shocked",
         "first_seen_ms": 4200, "last_seen_ms": 4200, "repeat_count": 1},
        {"sequence": 6, "observation": "set_emotion_call_entered", "semantic": "happy",
         "first_seen_ms": 7000, "last_seen_ms": 7000, "repeat_count": 1},
        {"sequence": 7, "observation": "set_emotion_call_entered", "semantic": "other",
         "first_seen_ms": 8000, "last_seen_ms": 8000, "repeat_count": 1},
    ]
    return {
        "ok": True,
        "read_only": True,
        "best_effort": True,
        "schema": SCHEMA_ID,
        "observation_point": OBSERVATION_POINT,
        "claim_scope": CLAIM_SCOPE,
        "captured_at_ms": 9000,
        "records_overwritten": 0,
        "records_dropped_contention": 2,
        "records_dropped_sequence_exhausted": 0,
        "records": records,
    }


def oral_summary() -> dict[str, Any]:
    """Return the qualitative summary reported by the user from speech."""
    return {
        "schema": "deskbot.expression.runtime_observation_oral_summary.e1_27/v1",
        "edition": "E1.27-oral-summary",
        "source": "user_reported_voice_response",
        "captured_at": "2026-10-27",
        "raw_json_available": False,
        "device_query_reported_by_user": True,
        "reported_recent_record_count": 24,
        "reported_distribution_note": (
            "happy、embarrassed、sleepy 三类合计占多数；neutral 为其余类别；"
            "前三类各自的次数未知。"
        ),
        "user_wording": "他说的最近一共24条，大多数都是happy，embarassed，sleepy，其他的是neutral",
        "reported_semantics": [
            {"semantic": "happy", "label_zh": "开心", "qualitative_frequency": "多数组之一；单类次数未知"},
            {"semantic": "embarrassed", "label_zh": "害羞", "qualitative_frequency": "多数组之一；单类次数未知"},
            {"semantic": "sleepy", "label_zh": "困倦", "qualitative_frequency": "多数组之一；单类次数未知"},
            {"semantic": "neutral", "label_zh": "中性", "qualitative_frequency": "其余组；次数未知"},
        ],
        "evidence_note": (
            "这是用户转述的语音摘要，没有逐条序号、时间戳、准确次数或丢弃计数；"
            "不能据此计算精确频率、判断完整历史或证明 LCD 已播放。"
        ),
        "evidence_limits": [
            "语音回复没有提供原始 JSON，因此没有序号、时间戳、repeat_count 或丢弃计数。",
            "24 条是观察器当前保存窗口的上限或记录数口述，不代表设备历史总调用量。",
            "该摘要不能证明 EAF 已解析、LCD 已播放或动作已完成。",
            "该摘要不足以据此删除资源、合并语义或修改映射。",
        ],
        "mapping_reference": {
            "happy": "Happy.eaf",
            "embarrassed": "Happy.eaf",
            "sleepy": "sleep.eaf",
            "neutral": "winking.eaf",
        },
        "next_action": (
            "保持当前固件和映射，先审阅四类语义与动画；精确顺序与次数由程序导出设备原始 MCP JSON，"
            "不要求用户逐条转述。当前 App 没有电脑直读接口，导出路径需单独设计、离线审计。"
        ),
    }


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(snapshot)
    records = snapshot.get("records", [])
    sequences = [row["sequence"] for row in records]
    if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
        raise ValueError("observation sequences must be unique and ascending")
    # uint32 uptime may wrap during a coalesced record. Sequence, not the raw
    # timestamp sort order, is the source of ordering evidence.
    if any(row["semantic"] not in SEMANTICS for row in records):
        raise ValueError("snapshot contains an unsupported semantic")


def inject_page(base: str, payload: dict[str, Any]) -> str:
    if "connect-src 'none'" not in base:
        raise ValueError("E1.19 base page is missing its offline CSP")
    panel = (
        "<section class=\"surface e127-panel\" id=\"e127-panel\" aria-labelledby=\"e127-title\">"
        "<div class=\"e127-head\"><div><div class=\"eyebrow\">E1.26 CALL-ENTRY EVIDENCE / E1.27 IMPORT</div>"
        "<h2 id=\"e127-title\">运行时表情调用快照</h2>"
        "<p class=\"subtitle\">只显示进入 EmoteDisplay::SetEmotion 的调用证据；不代表资源已加载、LCD 已播放或动作已完成。</p></div>"
        "<div class=\"e127-actions\"><label for=\"e127-file\">导入 JSON 快照（≤64 KiB）</label>"
        "<input id=\"e127-file\" type=\"file\" accept=\"application/json,.json\">"
        "<button class=\"btn\" id=\"e127-sample\" type=\"button\">加载离线示例</button></div></div>"
        "<div class=\"e127-origin\" id=\"e127-origin\" role=\"status\" aria-live=\"polite\"></div>"
        "<div class=\"e127-summary\" id=\"e127-summary\"></div>"
        "<section class=\"e127-oral\" aria-labelledby=\"e127-oral-title\"><div class=\"e127-oral-head\"><div><h3 id=\"e127-oral-title\">设备语音回复摘要 · 用户转述</h3><p class=\"small muted\" id=\"e127-oral-count\"></p></div><span class=\"e127-oral-badge\">非原始 JSON</span></div><div class=\"e127-observed-list e127-oral-list\" id=\"e127-oral-list\"></div><p class=\"hint\" id=\"e127-oral-limit\"></p></section>"
        "<p class=\"hint\" id=\"e127-scope\">序号代表调用顺序；时间是本次启动的 uint32 毫秒计数，可能回绕。只包含最近一段记录，不能推算总频率或判断哪些资源可删除。</p>"
        "<div class=\"e127-body\"><div><h3>JSON 快照中的语义节点</h3><div class=\"e127-observed-list\" id=\"e127-observed-list\"></div></div>"
        "<div><h3>JSON 快照中的调用顺序</h3><div class=\"table-wrap\"><table class=\"table e127-table\"><thead><tr><th>序号</th><th>语义</th><th>首次 / 末次</th><th>合并次数</th></tr></thead>"
        "<tbody id=\"e127-records\"></tbody></table></div></div></div>"
        "<p class=\"hint\" id=\"e127-reference\">节点与圆屏动画来自归档的 E1.18 映射参考；没有实测资源解析、LCD 播放、触发来源或动作结果。</p>"
        "<p class=\"notice\" id=\"e127-message\" role=\"alert\" hidden></p></section>"
    )
    css = CSS_PATH.read_text(encoding="utf-8")
    oral_css = ORAL_CSS_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")
    core = CORE_PATH.read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = base.replace("</head>", f"<style id=\"e127-style\">{css}</style><style id=\"e127-oral-style\">{oral_css}</style></head>", 1)
    page = page.replace('<div class="workspace">', panel + '<div class="workspace">', 1)
    marker = '</body>'
    scripts = (
        f'<script id="e127-data" type="application/json">{data}</script>'
        f'<script id="e127-core">{core}</script>'
        f'<script id="e127-js">{js}</script>'
    )
    if page.count(marker) != 1:
        raise ValueError("E1.19 base page has an unexpected body marker count")
    return page.replace(marker, scripts + marker, 1)


def build(check: bool) -> dict[str, Any]:
    for path in (BASE_PAGE, SCHEMA_PATH, MODEL_PATH, CSS_PATH, ORAL_CSS_PATH, JS_PATH, CORE_PATH):
        if not path.is_file():
            raise FileNotFoundError(path)
    snapshot = sample_snapshot()
    validate_snapshot(snapshot)
    base = BASE_PAGE.read_text(encoding="utf-8")
    source = re.search(r'<script id="e119-data" type="application/json">(.*?)</script>', base, re.S)
    if source is None:
        raise ValueError("missing anchored node-preview payload")
    reference = json.loads(source[1])["snapshots"]["deployed"]
    spoken = oral_summary()
    for semantic, resource in spoken["mapping_reference"].items():
        if reference[semantic]["resource_id"] != resource:
            raise ValueError(f"oral summary mapping reference is stale: {semantic}")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    page = inject_page(base, {"edition": "E1.27", "schemaDefinition": schema,
                              "sample": {"origin": "synthetic_offline_sample", "snapshot": snapshot},
                              "oralSummary": spoken,
                              "reference": reference})
    report = {
        "schema": "deskbot.expression.e1_27_runtime_observation_workbench/v1",
        "edition": "E1.27",
        "date": "2026-10-27",
        "result": "offline_observation_snapshot_workbench_ready",
        "audit_pass": True,
        "validation_scope": "deterministic_input_and_build_invariants_only",
        "deployment_eligible": False,
        "flash_authorized": False,
        "hardware_accessed": False,
        "serial_accessed": False,
        "device_written": False,
        "nvs_changed": False,
        "source": {
            "observation_schema": "protocol/expression-runtime-observation-e1-26.schema.json",
            "observation_schema_sha256": sha(SCHEMA_PATH.read_bytes()),
            "host_model": "protocol/expression_runtime_observation_e1_26.py",
            "host_model_sha256": sha(MODEL_PATH.read_bytes()),
            "base_workbench": "artifacts/expression-node-preview-e1-19-20261024/index.html",
            "base_workbench_sha256": sha(BASE_PAGE.read_bytes()),
        },
        "implementation_sha256": {path.name: sha(path.read_bytes())
                                  for path in (CSS_PATH, ORAL_CSS_PATH, JS_PATH, CORE_PATH)},
        "sample": {
            "offline_only": True,
            "records": len(snapshot["records"]),
            "distinct_semantics": len({row["semantic"] for row in snapshot["records"]}),
            "snapshot": snapshot,
        },
        "user_reported_oral_summary": spoken,
        "ui_claims": [
            "导入合法 E1.26 JSON 快照并显示最近 24 条记录",
            "按导入快照语义高亮 E1.19 节点列表，并可点击记录跳转到节点",
            "将用户转述摘要独立标记并高亮到 E1.19 节点，不伪造记录顺序或次数",
            "显示覆盖、锁竞争丢弃和序号耗尽计数",
        ],
        "limits": [
            "观察点是 SetEmotion 调用入口，不是资源加载或 LCD 播放结果",
            "样例数据是离线演示，不是当前设备运行轨迹",
            "语音转述摘要不是原始设备 JSON，不能恢复时间、顺序、精确频次或丢弃计数",
            "工作台不连接串口、不写固件、不修改 NVS/C61/播放器/UI 热路径",
        ],
    }
    outputs = {
        OUT / "index.html": page.encode("utf-8"),
        OUT / "report.json": (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        OUT / "sample-snapshot.json": (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        OUT / "oral-summary.json": (json.dumps(spoken, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }
    for path, raw in outputs.items():
        if check:
            if not path.is_file() or path.read_bytes() != raw:
                raise ValueError(f"stale generated artifact: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    return {"result": "checked" if check else "built", "edition": "E1.27",
            "records": len(snapshot["records"]), "html_bytes": len(outputs[OUT / "index.html"]),
            "sample_sha256": sha(outputs[OUT / "sample-snapshot.json"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.check), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
