#!/usr/bin/env python3
"""Add the role overview to the existing node and expression workbench."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import json_bytes, sha

spec = importlib.util.spec_from_file_location("e3f2_builder_parent", ROOT / "scripts/build-deskbot-studio-e3e.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
PUBLIC = ROOT / "artifacts/deskbot-studio-e3f2-20261109"
LOCAL = ROOT / "tmp/deskbot-studio-e3f2-20261109"
SOURCES = ("protocol/studio_role_overview.py", "protocol/studio_role_expression.py",
    "scripts/build-deskbot-studio-e3f2.py", "scripts/serve-deskbot-studio-e3f2.py",
    "scripts/deskbot-studio-e3f2.js", "scripts/deskbot-studio-e3f2.css",
    "scripts/validate-studio-role-overview.py", "tests/test_studio_role_overview.py",
    "scripts/start-deskbot-studio-e3f2.ps1",
    "tests/test_deskbot_studio_e3f2.py", "tests/serve_studio_e3f2_fixture.py",
    "tests/studio-role-overview-browser.cjs")


def products(local=False):
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError("E3f-2 source anchor changed: " + old[:80])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3e</span>', '<span class="edition">E3f·2</span>')
    anchor = 'const roleExpression=DeskBotStudioE3EUI.create(D,{draft:()=>editor.exportDocument(),control:()=>liveControl.state(),canvas,paint:()=>{observeCanvases();startRaf();}});'
    once(anchor, anchor + '\n  const roleOverview=DeskBotStudioE3F2UI.create(D,{draft:()=>editor.exportDocument(),control:()=>liveControl.state()});')
    anchor = '<div id="role-expression-slot">${roleExpression.view()}</div>'
    once(anchor, '<div id="role-overview-slot">${roleOverview.view()}</div>' + anchor)
    once('  roleExpression.init();', '  roleExpression.init();\n  roleOverview.init();')
    once('roleExpression:()=>roleExpression.state(),', 'roleExpression:()=>roleExpression.state(),roleOverview:()=>roleOverview.state(),')
    once('window.DeskBotStudioE3EUI=Object.freeze({create});\n})();',
         'window.DeskBotStudioE3EUI=Object.freeze({create});\n})();\n;\n' + (ROOT / 'scripts/deskbot-studio-e3f2.js').read_text(encoding='utf-8'))
    once('</style>', (ROOT / 'scripts/deskbot-studio-e3f2.css').read_text(encoding='utf-8') + '</style>')
    names = sorted({row['path'] for row in json.loads(previous['report.json'])['sources']} | set(SOURCES))
    report = dict(edition='E3f-2', result='offline_role_overview_generated',
        parent_report=sha(previous['report.json']),
        sources=[dict(path=name, sha256=sha((ROOT / name).read_bytes())) for name in names],
        hardware_accessed=False, device_written=False, automatic_device_commands=False,
        overview=dict(expressions=28, voice_statuses=1, generates_commands=False))
    return {**previous, 'index.html': html.encode('utf-8'), 'report.json': json_bytes(report)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    folder = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            from scripts.archived_products import product_matches
            if not product_matches((folder / name).read_bytes(), raw, PUBLIC / name, local=args.local):
                raise ValueError('E3f-2 generated product differs: ' + name)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print('E3f-2 workbench ' + ('checked' if args.check else 'built'))
