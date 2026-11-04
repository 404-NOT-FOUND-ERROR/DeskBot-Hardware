#!/usr/bin/env python3
"""Build the read-only E1.21 before/after expression preview from anchored evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
E119_PAGE = ROOT / "artifacts/expression-node-preview-e1-19-20261024/index.html"
E119_REPORT = ROOT / "artifacts/expression-node-preview-e1-19-20261024/report.json"
E121_REPORT = ROOT / "artifacts/expression-e1-21-package-player-20261026/report.json"
TEMPLATE = ROOT / "scripts/expression-e1-22-candidate-preview.template.html"
OUT = ROOT / "artifacts/expression-e1-22-candidate-preview-20261026"
DATA_RE = re.compile(
    r'<script id="e119-data" type="application/json">(.*?)</script>', re.DOTALL
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    page_text = E119_PAGE.read_text(encoding="utf-8")
    match = DATA_RE.search(page_text)
    if not match:
        raise SystemExit("E1.19 embedded evidence payload was not found")
    e119 = json.loads(match.group(1))
    e119_report = load_json(E119_REPORT)
    e121 = load_json(E121_REPORT)

    deployed = e119["snapshots"]["deployed"]["surprised"]
    change = e121["mapping"]
    if (deployed["resource_id"], change["before"], change["after"]) != (
        "shocked.eaf", "shocked.eaf", "Happy.eaf"
    ):
        raise SystemExit("E1.18 deployed route and E1.21 candidate do not match the preview contract")
    if any((deployed[key], change[key]) != (expected, expected) for key, expected in (
        ("fps", 20), ("loop", True), ("action_id", "look_around"), ("layout_id", "eye_anim")
    )):
        raise SystemExit("E1.18 and E1.21 non-resource behavior diverged")
    if e121.get("deployment_eligible") is not False or e121.get("safety", {}).get("device_written") is not False:
        raise SystemExit("E1.21 is not an offline-only candidate")

    previews = {}
    for key in ("shocked.eaf", "Happy.eaf"):
        asset = e119["assets"][key]
        if asset.get("origin") != "official" or not asset.get("atlas", "").startswith("data:image/png;base64,"):
            raise SystemExit(f"official decoded preview atlas is missing: {key}")
        player_key = "shocked" if key == "shocked.eaf" else "happy"
        player = e121["player"]["cases"][player_key]
        if asset["sha256"] != player["sha256"] or asset["frame_count"] != player["image_frame_count"]:
            raise SystemExit(f"E1.19 preview asset does not match E1.21 player evidence: {key}")
        previews[key] = {
            "resource": key,
            "origin": asset["origin"],
            "sha256": asset["sha256"],
            "atlas_sha256": asset["atlas_sha256"],
            "atlas_width": asset["atlas_width"],
            "atlas_height": asset["atlas_height"],
            "columns": asset["columns"],
            "frame_width": asset["frame_width"],
            "frame_height": asset["frame_height"],
            "frame_count": asset["frame_count"],
            "fps": asset["fps"],
            "loop": asset["loop"],
            "preview_basis": asset["preview_basis"],
            "atlas": asset["atlas"],
        }

    report = {
        "schema": "deskbot.expression_e1_22_candidate_preview/v1",
        "edition": "E1.22",
        "date": "2026-10-26",
        "result": "offline_before_after_preview_ready",
        "audit_pass": True,
        "deployment_eligible": False,
        "safety": {
            "hardware_accessed": False,
            "serial_accessed": False,
            "firmware_built": False,
            "firmware_changed": False,
            "nvs_changed": False,
            "device_written": False,
            "flash_authorized": False,
        },
        "source_anchors": {
            "e119_preview_page_sha256": sha256(E119_PAGE.read_bytes()),
            "e119_report_sha256": sha256(E119_REPORT.read_bytes()),
            "e121_report_sha256": sha256(E121_REPORT.read_bytes()),
            "e119_deployed_state_source": e119["deployed_state_source"],
        },
        "comparison": {
            "semantic": "surprised",
            "before_layer": "E1.18 accepted mapping, as recorded by E1.19",
            "before_resource": change["before"],
            "candidate_layer": "E1.21 offline candidate",
            "candidate_resource": change["after"],
            "old_edge": {"from": "surprised", "to": change["before"], "operation": "disconnect"},
            "candidate_edge": {"from": "surprised", "to": change["after"], "operation": "connect"},
            "fps": change["fps"],
            "loop": change["loop"],
            "action_id": change["action_id"],
            "layout_id": change["layout_id"],
            "action_unchanged": True,
            "layout_unchanged": True,
            "timing_unchanged": True,
        },
        "previews": {
            "shocked.eaf": {key: value for key, value in previews["shocked.eaf"].items() if key != "atlas"},
            "Happy.eaf": {key: value for key, value in previews["Happy.eaf"].items() if key != "atlas"},
        },
        "limitations": [
            "动画图像是 E1.19 官方解码帧图集，不是设备实拍或实时屏幕捕获。",
            "圆屏合成按 E1.19 的 eye_anim 参考布局绘制；不能证明 LCD、运行时调度或并发表现。",
            "本页只展示 E1.18 记录映射与 E1.21 候选差异，不修改映射、不导出包、不授权部署。",
            "是否写入本体 assets 分区仍需用户单独确认，并在写入后进行回读和实机回归。",
        ],
    }
    payload = {"report": report, "previews": previews}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded.replace("<", "\\u003c").replace("&", "\\u0026")
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("__E122_DATA__") != 1:
        raise SystemExit("E1.22 template must contain one data placeholder")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(template.replace("__E122_DATA__", encoded), encoding="utf-8", newline="\n")
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (OUT / "README.txt").write_text(
        "E1.22 offline comparison of the recorded E1.18 route and E1.21 candidate.\n"
        "Open index.html. Animation previews use official decoded source-frame atlases.\n"
        "No device access, firmware build, mapping edit, package write, or deployment authorization.\n",
        encoding="utf-8", newline="\n",
    )
    print(f"built {OUT.relative_to(ROOT).as_posix()}/index.html")
    print(f"official decoded preview atlases: {len(previews)}; deployment_eligible=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
