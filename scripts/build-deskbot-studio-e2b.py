#!/usr/bin/env python3
"""Add reproducible package generation to the accepted Studio editor."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import load_target, sha, json_bytes

PUBLIC = ROOT / "artifacts/deskbot-studio-e2b-20261104"
LOCAL = ROOT / "tmp/deskbot-studio-e2b-20261104"


def parent_builder():
    spec = importlib.util.spec_from_file_location("studio_e2a_parent", ROOT / "scripts/build-deskbot-studio-e2a.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("E2a extension point differs: " + old[:70])
    return text.replace(old, new)


def controller():
    """Keep the E2a artifact reproducible and extend only its release view."""
    raw = (ROOT / "scripts/deskbot-studio-e2a-controller.js").read_text(encoding="utf-8")
    raw = once(raw, "  const editor=Core.create(D,initial);", "  const editor=Core.create(D,initial);\n"
               "  const release=DeskBotStudioReleaseUI.create(D,{draft:()=>editor.exportDocument(),budget:()=>editor.budget(),"
               "render:()=>{if(view==='publish')render();},toast});")
    raw, count = re.subn(r"  function publishView\(\)\{\n.*?\n  \}\n  function render\(\)\{",
                        "  function publishView(){return release.view();}\n  function render(){", raw, flags=re.S)
    if count != 1:
        raise ValueError("E2a release view extension point differs")
    raw = once(raw, "edition:'E2a',catalog_id", "edition:'E2b',catalog_id")
    raw = once(raw, "snapshot:()=>editor.snapshot(),budget:()=>editor.budget(),",
               "snapshot:()=>editor.snapshot(),budget:()=>editor.budget(),release:()=>release.status(),")
    raw = once(raw, "  // Expose read-only snapshots", "  release.init();\n  // Expose read-only snapshots")
    return raw


def products(local=False):
    parent = parent_builder()
    data, old_report = parent.build(local)
    target = load_target()
    data.update(edition="E2b", target=target)
    template = (ROOT / "scripts/deskbot-studio-e2a.template.html").read_text(encoding="utf-8")
    template = once(template, '<span class="edition">E2a</span>', '<span class="edition">E2b</span>')
    template = once(template, '<input type="file" id="import-file" accept=".json,application/json" hidden>',
                    '<input id="package-report-file" type="file" accept="application/json,.json" hidden>\n'
                    '<input type="file" id="import-file" accept=".json,application/json" hidden>')
    template = once(template, '<title>', '<meta http-equiv="Content-Security-Policy" content="default-src \'self\' data: blob:; script-src \'self\' \'unsafe-inline\'; style-src \'self\' \'unsafe-inline\'; connect-src \'self\'; object-src \'none\'">\n  <title>')
    js = (ROOT / "scripts/deskbot-studio-e2b-release-core.js").read_text(encoding="utf-8")
    js += "\n;\n" + (ROOT / "scripts/deskbot-studio-e2b-release.js").read_text(encoding="utf-8")
    js += "\n;\n" + controller()
    replacements = {
        "__STUDIO_DATA__": json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"),
        "__STUDIO_CSS__": (ROOT / "scripts/deskbot-studio-e2a.css").read_text(encoding="utf-8") + "\n" + (ROOT / "scripts/deskbot-studio-e2b.css").read_text(encoding="utf-8"),
        "__STUDIO_CORE__": (ROOT / "scripts/deskbot-studio-e2a-core.js").read_text(encoding="utf-8"),
        "__STUDIO_CONTROLLER__": js,
    }
    for key, value in replacements.items():
        template = once(template, key, value)
    source_files = ["protocol/studio_resource_package.py", "scripts/build-deskbot-studio-e2b.py",
                    "scripts/build-deskbot-studio-e2b-firmware.py", "scripts/preflight-deskbot-toolchain.ps1",
                    "scripts/deskbot-studio-e2b-release-core.js", "scripts/deskbot-studio-e2b-release.js",
                    "scripts/deskbot-studio-e2b.css", "scripts/serve-deskbot-studio-e2b.py",
                    "firmware/releases/esp_vocat_n32r16/target.json", "firmware/releases/esp_vocat_n32r16/partitions.csv",
                    "firmware/releases/esp_vocat_n32r16/sdkconfig.defaults"]
    report = {"schema": "deskbot.studio_build/v1", "edition": "E2b", "date": "2026-11-04",
              "result": "studio_package_pipeline_built", "catalog_id": data["catalog_id"],
              "media_scope": data["media_scope"], "counts": old_report["counts"],
              "target": target, "hardware_accessed": False, "firmware_changed": False,
              "sources": old_report["sources"] + [{"path": name, "sha256": sha((ROOT / name).read_bytes())} for name in source_files]}
    return {"index.html": template.encode("utf-8"), "report.json": json_bytes(report)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            if (output / name).read_bytes() != raw:
                raise ValueError("E2b product differs: " + name)
        else:
            output.mkdir(parents=True, exist_ok=True)
            (output / name).write_bytes(raw)
    print(json.dumps({"result": "checked" if args.check else "built", "path": str(output / "index.html")}))


if __name__ == "__main__":
    main()
