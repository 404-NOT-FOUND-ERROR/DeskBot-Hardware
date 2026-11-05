#!/usr/bin/env python3
"""Build E3c-6 on the existing editor, package builder and simulator."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import json_bytes, sha
PUBLIC = ROOT / "artifacts/deskbot-studio-e3c6-20261105"
LOCAL = ROOT / "tmp/deskbot-studio-e3c6-20261105"


def products(local=False):
    spec = importlib.util.spec_from_file_location("studio_e3c_previous", ROOT / "scripts/build-deskbot-studio-e3b.py")
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError("E3c input anchor differs: " + old[:70])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3b</span>', '<span class="edition">E3c·6</span>')
    once("const body=DeskBotStudioE3BUI.create({});", "const body=DeskBotStudioE3BUI.create({});\n  const liveControl=DeskBotStudioE3CUI.create({observation:()=>body.state()});")
    once('<div id="body-observer-slot">${body.view()}</div>', '<div class="e3c-page"><div id="studio-control-slot">${liveControl.view()}</div><div id="body-observer-slot">${body.view()}</div></div>')
    once("CONNECTION · E3b", "CONNECTION · E3c-6")
    once("连接身体，认识它。", "从连接，到表达。")
    once("从 USB 到身份、表情与动作，逐步接通。", "实时状态、平静表情和设备回执，一起查看。")
    once("  body.init();", "  body.init();\n  liveControl.init();")
    once("observation:()=>body.state(),", "observation:()=>body.state(),control:()=>liveControl.state(),")
    once("window.DeskBotStudioE3BUI=Object.freeze({create});\n})();", "window.DeskBotStudioE3BUI=Object.freeze({create});\n})();\n;\n" + (ROOT / "scripts/deskbot-studio-e3c.js").read_text(encoding="utf-8"))
    once("</style>", (ROOT / "scripts/deskbot-studio-e3c.css").read_text(encoding="utf-8")+"</style>")
    report = json.loads(previous["report.json"])
    sources = ["protocol/studio_control_link.py", "protocol/studio_control_bridge.py", "tools/observe-studio-body-e3c.py",
               "scripts/serve-deskbot-studio-e3c.py", "scripts/build-deskbot-studio-e3c.py",
               "scripts/deskbot-studio-e3c.js", "scripts/deskbot-studio-e3c.css"]
    report.update(edition="E3c-6", date="2026-11-05", result="state_and_neutral_workbench_built", firmware_changed=False,
                  hardware_accessed=False, device_written=False,
                  control_link=dict(schema="deskbot.studio_control_link/v1", serial_owners=1,
                      explicit_enable=True, command="neutral", automatic_expression_retry=False,
                      receipt_source="device", hardware_acceptance="pending"))
    report["sources"] += [dict(path=n, sha256=sha((ROOT / n).read_bytes())) for n in sources]
    return {**previous, "index.html": html.encode("utf-8"), "report.json": json_bytes(report)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            if (output / name).read_bytes() != raw:
                raise ValueError("E3c product differs: " + name)
        else:
            output.mkdir(parents=True, exist_ok=True)
            (output / name).write_bytes(raw)
    print(json.dumps(dict(result="checked" if args.check else "built", edition="E3c-6")))
