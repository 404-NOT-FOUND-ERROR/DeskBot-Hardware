#!/usr/bin/env python3
"""Generate the click-then-check workbench from the existing eating edition."""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ready_parent', ROOT / 'scripts/build-deskbot-studio-e3f4a.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
LOCAL = ROOT / 'tmp/deskbot-studio-e3f4b-20261109'
PUBLIC = ROOT / 'artifacts/deskbot-studio-e3f4b-20261109'


def products(local=False):
    previous = parent.products(local)
    html = previous['index.html'].decode('utf-8')
    assert html.count('<span class="edition">E3f·4a</span>') == 1
    html = html.replace('<span class="edition">E3f·4a</span>', '<span class="edition">E3f·4b</span>')
    anchor = 'DeskBotStudioE3CUI.create({observation:()=>body.state()})'
    assert html.count(anchor) == 1
    html = html.replace(anchor, 'DeskBotStudioE3CUI.create({observation:()=>body.state(),automaticDirectory:true})')
    names = sorted({row['path'] for row in json.loads(previous['report.json'])['sources']} | {
        'scripts/build-deskbot-studio-e3f4b.py', 'scripts/serve-deskbot-studio-e3f4b.py',
        'scripts/deskbot-studio-play-readiness.js', 'scripts/validate-studio-play-readiness.py',
        'tests/studio-play-readiness.test.cjs', 'tests/studio-play-browser.cjs',
        'tests/serve_studio_play_fixture.py', 'tests/test_studio_play_workbench.py',
        'docs/studio-play-readiness-e3f4b.md'})
    report = dict(edition='E3f-4b', result='click_then_check_workbench_generated',
        parent_report=parent.sha(previous['report.json']),
        sources=[dict(path=n, sha256=parent.sha((ROOT/n).read_bytes())) for n in names],
        hardware_accessed=False, device_written=False, automatic_expression_commands=False,
        automatic_read_only_catalog='once_per_enabled_session', firmware_changed=False)
    return {**previous, 'index.html': html.encode('utf-8'), 'report.json': parent.json_bytes(report)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    folder = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            assert (folder/name).read_bytes() == raw, name
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder/name).write_bytes(raw)
    print('E3f-4b workbench ' + ('checked' if args.check else 'built'))
