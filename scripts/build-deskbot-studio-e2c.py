#!/usr/bin/env python3
"""Extend E2b with a transport-independent expression simulator."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.expression_control import build_catalog
from protocol.studio_resource_package import json_bytes, sha

PUBLIC = ROOT / "artifacts/deskbot-studio-e2c-20261104"
LOCAL = ROOT / "tmp/deskbot-studio-e2c-20261104"


def parent_builder():
    spec = importlib.util.spec_from_file_location("studio_e2b_parent", ROOT / "scripts/build-deskbot-studio-e2b.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def products(local=False):
    parent = parent_builder()
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")
    match = re.search(r'<script id="studio-data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(match[1])
    catalog = build_catalog(data)
    data.update(edition="E2c", expression_catalog=catalog)
    html = html[:match.start(1)] + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + html[match.end(1):]
    html = parent.once(html, '<span class="edition">E2b</span>', '<span class="edition">E2c</span>')
    html = parent.once(html, '<a href="#publish" data-view="publish">',
                       '<a href="#connection" data-view="connection"><span data-icon="device"></span><span>连接测试</span></a>\n        <a href="#publish" data-view="publish">')
    html = parent.once(html, "roles:'角色',publish:'发布'", "roles:'角色',connection:'连接测试',publish:'发布'")
    html = parent.once(html, "  const release=DeskBotStudioReleaseUI.create", "  const simulator=DeskBotStudioE2CUI.create(D,{render:()=>{if(view==='connection')render();},canvas,download,toast});\n  const release=DeskBotStudioReleaseUI.create")
    html = parent.once(html, "roles:rolesView,publish:publishView", "roles:rolesView,connection:()=>simulator.view(),publish:publishView")
    html = parent.once(html, "  release.init();", "  simulator.init();\n  release.init();")
    html = parent.once(html, "release:()=>release.status(),", "release:()=>release.status(),simulation:()=>simulator.state(),")
    html = parent.once(html, "edition:'E2b',catalog_id", "edition:'E2c',catalog_id")
    html = parent.once(html, "['06','身体','待实时接入','device-info']", "['06','身体','电脑模拟测试','connection']")
    core = (ROOT / "scripts/deskbot-studio-e2c-core.js").read_text(encoding="utf-8")
    ui = (ROOT / "scripts/deskbot-studio-e2c-ui.js").read_text(encoding="utf-8")
    html = parent.once(html, '<script>/* Package report validation', '<script>' + core + '\n;\n' + ui + '\n;\n/* Package report validation')
    css = (ROOT / "scripts/deskbot-studio-e2c.css").read_text(encoding="utf-8")
    html = parent.once(html, '</style>', '\n' + css + '</style>')
    report = json.loads(previous["report.json"])
    report.update(edition="E2c", result="expression_simulator_built", expression_catalog_id=catalog["catalog_id"],
                  simulator={"expressions": len(catalog["expressions"]), "actions": len(catalog["actions"]),
                             "requestable_expressions": sum(row["requestable"] for row in catalog["expressions"]),
                             "limits": catalog["limits"]}, hardware_accessed=False, device_written=False)
    source_files = ["protocol/expression_control.py", "protocol/deskbot-expression-control-v1.schema.json",
                    "protocol/deskbot-expression-catalog-v1.schema.json",
                    "scripts/build-deskbot-studio-e2c.py", "scripts/deskbot-studio-e2c-core.js",
                    "scripts/deskbot-studio-e2c-ui.js", "scripts/deskbot-studio-e2c.css",
                    "scripts/serve-deskbot-studio-e2c.py"]
    report["sources"] += [{"path": name, "sha256": sha((ROOT / name).read_bytes())} for name in source_files]
    return {**previous, "index.html": html.encode("utf-8"), "report.json": json_bytes(report), "expression-catalog.json": json_bytes(catalog)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            if (output / name).read_bytes() != raw:
                raise ValueError("E2c product differs: " + name)
        else:
            output.mkdir(parents=True, exist_ok=True)
            (output / name).write_bytes(raw)
    print(json.dumps({"result": "checked" if args.check else "built", "path": str(output / "index.html")}))


if __name__ == "__main__":
    main()
