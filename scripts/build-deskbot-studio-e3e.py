#!/usr/bin/env python3
"""Add a concise role-expression route to the existing node/device workbench."""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e3e_builder_parent", ROOT / "scripts/build-deskbot-studio-e3d2.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
PUBLIC = ROOT / "artifacts/deskbot-studio-e3e-20261108"
LOCAL = ROOT / "tmp/deskbot-studio-e3e-20261108"


def products(local=False):
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")

    def once(before, after):
        nonlocal html
        if html.count(before) != 1:
            raise ValueError("E3e source anchor changed: " + before[:70])
        html = html.replace(before, after, 1)

    once('<span class="edition">E3d·2</span>', '<span class="edition">E3e</span>')
    once("session_reply_timeout:'当前固件尚未响应状态接口；设备联调时接入 E3c 固件'",
         "session_reply_timeout:'会话回复超过 8 秒，请重新启用查询'")
    once("command_processed:'连接进程已处理请求'",
         "command_processed:'连接进程已处理请求',request_expired:'这次点击已过期，请重新点击',command_clock_invalid:'本机计时已变化，请重新连接'")
    once('设备查询 · ${(v.age_ms/1000).toFixed(1)} 秒前', '最近回复 · ${(v.age_ms/1000).toFixed(1)} 秒前')
    anchor = 'const liveDirectory=DeskBotStudioE3D2UI.create(D,{control:()=>liveControl.state(),observation:()=>body.state()});'
    once(anchor, anchor + '\n  const roleExpression=DeskBotStudioE3EUI.create(D,{draft:()=>editor.exportDocument(),control:()=>liveControl.state(),canvas,paint:()=>{observeCanvases();startRaf();}});')
    anchor = '<div id="live-directory-slot">${liveDirectory.view()}</div>'
    once(anchor, anchor + '<div id="role-expression-slot">${roleExpression.view()}</div>')
    once('  liveDirectory.init();', '  liveDirectory.init();\n  roleExpression.init();')
    once('liveDirectory:()=>liveDirectory.state(),', 'liveDirectory:()=>liveDirectory.state(),roleExpression:()=>roleExpression.state(),')
    once('window.DeskBotStudioE3D2UI=Object.freeze({create});\n})();',
         'window.DeskBotStudioE3D2UI=Object.freeze({create});\n})();\n;\n' + (ROOT / "scripts/deskbot-studio-e3e.js").read_text(encoding="utf-8"))
    once('</style>', (ROOT / "scripts/deskbot-studio-e3e.css").read_text(encoding="utf-8") + '</style>')
    names = sorted({row["path"] for row in json.loads(previous["report.json"])["sources"]} | {
        "scripts/build-deskbot-studio-e3e.py", "scripts/serve-deskbot-studio-e3e.py",
        "scripts/start-deskbot-studio-e3e.ps1", "scripts/deskbot-studio-e3e.js",
        "scripts/deskbot-studio-e3e.css", "protocol/studio_role_expression.py",
        "scripts/build-e3e-source-review.py", "tests/test_deskbot_studio_e3e.py",
        "tests/test_studio_role_expression.py", "tests/test_studio_control_native.py",
        "tests/deskbot-studio-e3e-browser.cjs", "tests/serve_studio_e3e_fixture.py",
        "tests/test_storage_upper_migration_preflight.py", ".github/workflows/host-checks.yml",
        "protocol/storage_continuous_mmap_experiment.py", "tests/test_storage_continuous_mmap_experiment.py",
        "protocol/studio_timing.py", "tests/test_studio_timing.py",
        "scripts/build-studio-shy.py", "scripts/validate-studio-shy.py", "tests/test_studio_shy.py",
        "tests/studio-shy-browser.cjs",
        "protocol/deskbot-studio-display-v1.schema.json", "protocol/studio_display_query.py",
        "scripts/deskbot-studio-e3c.js", "tests/test_studio_expression_control.py",
        "tests/cpp/studio_expression_control_test.cc",
        *("firmware/body/main/boards/esp_vocat/" + name for name in (
            "studio_expression_control.h", "studio_expression_adapter.cc", "studio_display_readiness.h")),
        "tools/observe-studio-body-e3c.py",
        "tests/test_studio_control_link.py", "tests/test_studio_catalog_query.py",
        "tests/test_studio_runtime_query.py", "tests/test_studio_display_query.py"})
    report = dict(edition="E3e", result="offline_role_route_generated", parent_report=parent.parent.packages.sha(previous["report.json"]),
        sources=[dict(path=n, sha256=parent.parent.packages.sha((ROOT / n).read_bytes())) for n in names],
        hardware_accessed=False, automatic_device_commands=False, supported_device_expressions=["neutral"], optional_device_expressions=["embarrassed"])
    return {**previous, "index.html": html.encode("utf-8"), "report.json": parent.parent.packages.json_bytes(report)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, raw in products(args.local).items():
        folder = LOCAL if args.local else PUBLIC
        if args.check:
            from scripts.archived_products import product_matches
            if not product_matches((folder / name).read_bytes(), raw, PUBLIC / name, local=args.local):
                raise ValueError("generated E3e output differs: " + name)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print("E3e workbench " + ("checked" if args.check else "built"))
