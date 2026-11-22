#!/usr/bin/env python3
"""Build the role-node edition without replacing the active E3f-4b page."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_role_layout as layouts
from protocol.studio_resource_package import json_bytes, load_catalog, sha

spec = importlib.util.spec_from_file_location("role_node_builder_parent", ROOT / "scripts/build-deskbot-studio-e3f4b.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
LOCAL = ROOT / "tmp/deskbot-studio-e3f6-20261110"
PUBLIC = ROOT / "artifacts/deskbot-studio-e3f6-20261110"
SOURCES = ("scripts/build-deskbot-studio-e3f6.py", "scripts/serve-deskbot-studio-e3f6.py",
           "scripts/deskbot-studio-e3f6.js", "scripts/deskbot-studio-role-owner-e3f6.js",
           "protocol/studio_role_layout.py", "protocol/expression-layout-requirements-v1.json",
           "docs/role-node-layout-e3f6.md", "artifacts/studio-resources-e3f4d/expected-directory.json",
           "scripts/validate-studio-role-layout-e3f6.py", "tests/test_studio_role_layout_e3f6.py",
           "tests/test_studio_role_layout_native_e3f6.py", "tests/serve_studio_role_layout_e3f6_fixture.py",
           "tests/studio-role-layout-e3f6-browser.cjs", "scripts/start-deskbot-studio-e3f6.ps1")


def products(local=False):
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError("E3f-6 source anchor changed: " + old[:80])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3f·4b</span>', '<span class="edition">E3f·6</span>')
    once((ROOT / "scripts/deskbot-studio-e3e.js").read_text(encoding="utf-8"),
         (ROOT / "scripts/deskbot-studio-e3f6.js").read_text(encoding="utf-8"))
    anchor = "    function init(){document.addEventListener('click',e=>{const b=e.target.closest('[data-e3c-action]');"
    once(anchor, (ROOT / "scripts/deskbot-studio-role-owner-e3f6.js").read_text(encoding="utf-8") + "\n" + anchor)
    once("return Object.freeze({view,init,state:()=>({device:value?JSON.parse(JSON.stringify(value)):null,error,busy})});",
         "return Object.freeze({view,init,playRole,state:()=>({device:value?JSON.parse(JSON.stringify(value)):null,error,busy})});")
    once("const roleExpression=DeskBotStudioE3EUI.create(D,{draft:()=>editor.exportDocument(),control:()=>liveControl.state(),canvas,paint:()=>{observeCanvases();startRaf();}});",
         "const roleExpression=DeskBotStudioE3EUI.create(D,{draft:()=>editor.exportDocument(),control:()=>liveControl.state(),playRole:options=>liveControl.playRole(options),selectedSemantic:()=>semantic,canvas,paint:()=>{observeCanvases();startRaf();}});")
    once("const mode=layout||D.semantics[semanticId]?.layout_id||(D.assets[id]?.object_kind==='listen'?'listen_anim':'eye_anim');",
         "const mode=layout||D.asset_layouts[id]||D.semantics[semanticId]?.layout_id||(D.assets[id]?.object_kind==='listen'?'listen_anim':'eye_anim');")
    # Old connections show historical geometry; the connected resource uses its reviewed layout.
    once("${canvas(baseline(),{animate:true})}", "${canvas(baseline(),{animate:true,layout:s.layout_id})}")
    once("${host.canvas(row.resource_id,{animate:true,cls:'screen',semanticId:row.id})}",
         "${host.canvas(row.resource_id,{animate:true,cls:'screen',semanticId:row.id,layout:row.layout_id})}")
    once("draftResource=draft.bindings[row.id],changed=draftResource!==row.resource_id;",
         "draftResource=draft.bindings[row.id],changed=draftResource!==row.resource_id||(data.asset_layouts[draftResource]||row.layout_id)!==row.layout_id;")
    once("${changed?'草稿已改接，等待发布':'与资源快照一致'}",
         "${changed?'素材或布局与历史快照不同':'与资源快照一致'}")
    once("${canvas(a?.id,{animate:true,cls:'screen'})}", "${canvas(a?.id,{animate:true,cls:'screen',layout:previewMode==='baseline'?D.semantics[semantic].layout_id:undefined})}")
    once("${chip(s.layout_id==='eye_anim'?'双眼镜像':'聆听布局')}",
         "${chip((D.asset_layouts[current]||s.layout_id)==='full_frame'?'完整画面 · 无镜像':s.layout_id==='listen_anim'?'聆听布局':'双眼镜像')}")
    once("${button('恢复当前','restore','button-small')}",
         "${button('恢复当前','restore','button-small')}<button class=\"button button-primary button-small\" data-e3f6-node>在设备上播放</button>")
    once('<div class="graph-note">${icon(\'info\')}<span>',
         '<p id="e3f6-node-result" role="status" style="padding:0 18px"></p><div class="graph-note">${icon(\'info\')}<span>')
    # The separate historical directory panel stays E1.18; role analysis uses the current saved reference.
    old = (ROOT / "scripts/deskbot-studio-e3f2.js").read_text(encoding="utf-8")
    new = old.replace("E1.18 留存快照", "E1.45 留存目录 · 37 项").replace(
        "r.playback.layout_id==='listen_anim'?'聆听布局':'眼部布局'",
        "r.playback.layout_id==='full_frame'?'完整画面 · 无镜像':r.playback.layout_id==='listen_anim'?'聆听布局':'双眼镜像'")
    once(old, new)
    once("'三分钟映射、对话、菜单与摸头'", "'圆屏、对话、菜单与摸头'")
    match = re.search(r'<script id="studio-data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(match[1])
    requirements = layouts.load_requirements(load_catalog())
    catalog = load_catalog()
    data["asset_layouts"] = {name: layouts.playback(catalog, "neutral", name, requirements)["layout_id"]
                             for name in requirements}
    data["role_reference"] = json.loads((ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json").read_bytes())
    data["layout_profiles"] = {row["id"]: layouts.expected_layout(row["id"], data["role_reference"], requirements)
                               for row in data["role_reference"]["entries"] if row["kind"] == "layout"}
    once(match[0], '<script id="studio-data" type="application/json">' + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c") + '</script>')
    names = sorted({row["path"] for row in json.loads(previous["report.json"])["sources"]} | set(SOURCES))
    report = dict(edition="E3f-6", result="role_node_asset_layout_workbench_generated",
                  parent_report=sha(previous["report.json"]),
                  sources=[dict(path=name, sha256=sha((ROOT / name).read_bytes())) for name in names],
                  installed_reference_entries=37, legacy_snapshot_entries=36,
                  hardware_accessed=False, device_written=False, firmware_changed=False,
                  role_playback="existing_live_control_owner", automatic_expression_commands=False)
    return {**previous, "index.html": html.encode("utf-8"), "report.json": json_bytes(report)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    folder = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            assert (folder / name).read_bytes() == raw, name
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print("E3f-6 workbench " + ("checked" if args.check else "built"))
