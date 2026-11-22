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

spec = importlib.util.spec_from_file_location("e3f2_builder_parent", ROOT / "scripts/build-deskbot-studio-e3f2.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
PUBLIC = ROOT / "artifacts/deskbot-studio-e3f4a-20261109"
LOCAL = ROOT / "tmp/deskbot-studio-e3f4a-20261109"
SOURCES = ("scripts/build-deskbot-studio-e3f4a.py", "scripts/serve-deskbot-studio-e3f4a.py", "scripts/deskbot-studio-e3c.js", "protocol/studio_role_overview.py", "protocol/studio_role_expression.py",
    "scripts/deskbot-studio-play-readiness.js",
    "scripts/build-deskbot-studio-e3f2.py", "scripts/serve-deskbot-studio-e3f2.py",
    "scripts/deskbot-studio-e3f2.js", "scripts/deskbot-studio-e3f2.css",
    "scripts/validate-studio-role-overview.py", "tests/test_studio_role_overview.py",
    "scripts/start-deskbot-studio-e3f2.ps1",
    "tests/test_deskbot_studio_e3f2.py", "tests/serve_studio_e3f2_fixture.py",
    "tests/studio-role-overview-browser.cjs")


def products(local=False):
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")

    old = '<span class="edition">E3f·2</span>'
    if html.count(old) != 1:
        raise ValueError("E3f-2 edition anchor changed")
    html = html.replace(old, '<span class="edition">E3f·4a</span>')
    names = sorted({row['path'] for row in json.loads(previous['report.json'])['sources']} | set(SOURCES))
    report = dict(edition='E3f-4a', result='eat_workbench_generated',
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
            if (folder / name).read_bytes() != raw:
                raise ValueError('E3f-2 generated product differs: ' + name)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print('E3f-4a workbench ' + ('checked' if args.check else 'built'))
