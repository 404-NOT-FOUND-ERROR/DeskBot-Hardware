#!/usr/bin/env python3
"""Add passive body observation to the accepted Studio without changing firmware."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import json_bytes, sha

PUBLIC = ROOT / "artifacts/deskbot-studio-e3a-20261104"
LOCAL = ROOT / "tmp/deskbot-studio-e3a-20261104"


def products(local=False):
    spec = importlib.util.spec_from_file_location("studio_e3a_previous", ROOT / "scripts/build-deskbot-studio-e2c.py")
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")
    once = parent.parent_builder().once
    match = re.search(r'<script id="studio-data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(match[1])
    data["edition"] = "E3a"
    html = html[:match.start(1)] + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + html[match.end(1):]
    html = once(html, '<span class="edition">E2c</span>', '<span class="edition">E3a</span>')
    html = once(html, '<span>连接测试</span>', '<span>设备连接</span>')
    html = once(html, "connection:'连接测试'", "connection:'设备连接'")
    html = once(html, "  const simulator=DeskBotStudioE2CUI.create", "  const body=DeskBotStudioE3AUI.create({});\n  const simulator=DeskBotStudioE2CUI.create")
    html = once(html, "connection:()=>simulator.view()", 'connection:()=>`<div class="page-heading"><div><div class="eyebrow">CONNECTION · E3a</div><h1>看见身体的状态。</h1><p>本体实况与电脑模拟，分别查看。</p></div></div><div id="body-observer-slot">${body.view()}</div><details class="e3a-simulator"><summary>表达请求模拟 <span>电脑模拟器</span></summary>${simulator.view()}</details>`')
    html = once(html, "  simulator.init();", "  body.init();\n  simulator.init();")
    html = once(html, "simulation:()=>simulator.state(),", "simulation:()=>simulator.state(),observation:()=>body.state(),")
    html = once(html, "edition:'E2c',catalog_id", "edition:'E3a',catalog_id")
    html = once(html, "['06','身体','电脑模拟测试','connection']", "['06','身体','只读观察与模拟','connection']")
    html = once(html, "实时设备连接将在后续阶段接入。", "只读状态在“设备连接”页观察。")
    html = once(html, '  function render(){\n    canvasObserver', '  function render(){\n    const simulatorOpen=document.querySelector(".e3a-simulator")?.open;\n    canvasObserver')
    html = once(html, '    syncPlayback();startRaf();', '    syncPlayback();startRaf();\n    if(simulatorOpen&&document.querySelector(".e3a-simulator"))document.querySelector(".e3a-simulator").open=true;')
    html = once(html, "电脑模拟 · 当前设备快照 E1.18", "电脑模拟 · 已保存目录 E1.18")
    js = (ROOT / "scripts/deskbot-studio-e3a.js").read_text(encoding="utf-8")
    html = once(html, 'window.DeskBotStudioE2CUI=Object.freeze({create});\n})();', 'window.DeskBotStudioE2CUI=Object.freeze({create});\n})();\n;\n' + js)
    html = once(html, '</style>', '\n' + (ROOT / "scripts/deskbot-studio-e3a.css").read_text(encoding="utf-8") + '</style>')
    report = json.loads(previous["report.json"])
    names = ["protocol/body_console_observation.py", "protocol/studio_body_bridge.py", "protocol/deskbot-body-observation-v1.schema.json",
             "tools/observe-studio-body-e3a.py", "scripts/serve-deskbot-studio-e3a.py", "scripts/build-deskbot-studio-e3a.py",
             "scripts/deskbot-studio-e3a.js", "scripts/deskbot-studio-e3a.css", "scripts/start-deskbot-studio-e3a.ps1"]
    report.update(edition="E3a", result="passive_observation_workbench_built",
                  observation={"source": "usb_console", "fresh_ms": 30000,
                               "history_limit": 24, "max_line_bytes": 2048, "read_only": True,
                               "device_controls": False, "saved_catalog_live": False},
                  hardware_accessed=False, device_written=False, firmware_changed=False)
    report["sources"] += [{"path": name, "sha256": sha((ROOT / name).read_bytes())} for name in names]
    return {**previous, "index.html": html.encode("utf-8"), "report.json": json_bytes(report),
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            if (output / name).read_bytes() != raw:
                raise ValueError("E3a product differs: " + name)
        else:
            output.mkdir(parents=True, exist_ok=True)
            (output / name).write_bytes(raw)
    print(json.dumps({"result": "checked" if args.check else "built", "path": str(output / "index.html")}))


if __name__ == "__main__":
    main()
