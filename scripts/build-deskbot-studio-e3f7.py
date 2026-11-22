#!/usr/bin/env python3
"""Generate the common-expression edition over the accepted node workbench."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_display_query import EXPRESSION_SEMANTICS
from protocol.studio_resource_package import json_bytes, sha

spec = importlib.util.spec_from_file_location("common_workbench_parent", ROOT / "scripts/build-deskbot-studio-e3f6.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
LOCAL = ROOT / "tmp/deskbot-studio-e3f7-20261110"
PUBLIC = ROOT / "artifacts/deskbot-studio-e3f7-20261110"
SOURCES = ("scripts/build-deskbot-studio-e3f7.py", "scripts/serve-deskbot-studio-e3f7.py",
    "scripts/start-deskbot-studio-e3f7.ps1", "scripts/build-studio-common-e3f7.py",
    "scripts/deskbot-studio-common-e3f7.js", "docs/studio-common-expressions-e3f7.md",
    "protocol/studio_display_query.py", "protocol/studio_control_link.py", "protocol/studio_control_bridge.py",
    "protocol/deskbot-studio-display-v1.schema.json", "protocol/deskbot-studio-control-link-v1.schema.json",
    "scripts/validate-studio-common-e3f7.py", "tests/test_studio_common_e3f7.py",
    "tests/serve_studio_common_e3f7_fixture.py", "tests/studio-common-e3f7-browser.cjs")


def products(local=False):
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError("E3f-7 workbench anchor changed: " + old[:100])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3f·6</span>', '<span class="edition">E3f·7</span>')
    original = (ROOT / "scripts/deskbot-studio-e3c.js").read_text(encoding="utf-8")
    owner = (ROOT / "scripts/deskbot-studio-role-owner-e3f6.js").read_text(encoding="utf-8")
    owner_anchor = "    function init(){document.addEventListener('click',e=>{const b=e.target.closest('[data-e3c-action]');"
    if original.count(owner_anchor) != 1:
        raise ValueError("shared node owner anchor changed")
    original = original.replace(owner_anchor, owner + "\n" + owner_anchor, 1).replace(
        "return Object.freeze({view,init,state:()=>({device:value?JSON.parse(JSON.stringify(value)):null,error,busy})});",
        "return Object.freeze({view,init,playRole,state:()=>({device:value?JSON.parse(JSON.stringify(value)):null,error,busy})});")
    # E3e adjusted these labels before E3f-6 embedded the shared owner.
    original = original.replace("session_reply_timeout:'当前固件尚未响应状态接口；设备联调时接入 E3c 固件'",
        "session_reply_timeout:'会话回复超过 8 秒，请重新启用查询'").replace(
        "command_processed:'连接进程已处理请求'",
        "command_processed:'连接进程已处理请求',request_expired:'这次点击已过期，请重新点击',command_clock_invalid:'本机计时已变化，请重新连接'").replace(
        '设备查询 · ${(v.age_ms/1000).toFixed(1)} 秒前', '最近回复 · ${(v.age_ms/1000).toFixed(1)} 秒前')
    common = (ROOT / "scripts/deskbot-studio-common-e3f7.js").read_text(encoding="utf-8")
    modified = original.replace("  'use strict';", "  'use strict';\n" + common, 1)
    # Navigation rebuilds the card. Paint its retained state after the DOM is mounted.
    anchor = "    function view(){return "
    if modified.count(anchor) != 1:
        raise ValueError("common view anchor changed")
    modified = modified.replace(anchor, "    function view(){queueMicrotask(paint);return ", 1)
    modified = modified.replace("['neutral','embarrassed','eat'].includes(", "expressionOperations.includes(")
    anchor = '<p id="e3c-reason"'
    if modified.count(anchor) != 1:
        raise ValueError("common card anchor changed")
    modified = modified.replace(anchor, '${commonExpressionView()}<p id="e3c-reason"', 1)
    anchor = "      let cancel=slot.querySelector('[data-e3c-action=\"cancel\"]');"
    if modified.count(anchor) != 1:
        raise ValueError("common paint anchor changed")
    modified = modified.replace(anchor, "      paintCommonExpressions({slot,d,semantics,sameDirectory,directory,occupied,usable,preparing,text});\n" + anchor, 1)
    once(original, modified)
    once("{neutral:'平静',embarrassed:'害羞',eat:'进食'}[id]",
         "{neutral:'平静',embarrassed:'害羞',eat:'进食',happy:'开心',sad:'难过',sleepy:'困倦',confused:'困惑',surprised:'惊讶'}[id]")
    names = sorted({row["path"] for row in json.loads(previous["report.json"])["sources"]} | set(SOURCES))
    report = dict(edition="E3f-7", result="common_expression_workbench_generated",
        parent_report=sha(previous["report.json"]),
        sources=[dict(path=name, sha256=sha((ROOT / name).read_bytes())) for name in names],
        expression_interfaces=list(EXPRESSION_SEMANTICS), installed_reference_entries=37,
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
            if (folder / name).read_bytes() != raw:
                raise ValueError("generated E3f-7 product differs: " + name)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print("E3f-7 workbench " + ("checked" if args.check else "built"))
