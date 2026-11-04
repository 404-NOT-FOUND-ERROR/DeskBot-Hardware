#!/usr/bin/env python3
"""Reproducible E3b Studio with a device-origin identity panel."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import json_bytes, sha

PUBLIC = ROOT / "artifacts/deskbot-studio-e3b-20261104"
LOCAL = ROOT / "tmp/deskbot-studio-e3b-20261104"


def products(local=False):
    spec = importlib.util.spec_from_file_location("studio_e3b_previous", ROOT / "scripts/build-deskbot-studio-e3a.py")
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    previous = parent.products(local)
    html = previous["index.html"].decode()
    html = html.replace('<span class="edition">E3a</span>', '<span class="edition">E3b</span>')
    html = html.replace('DeskBotStudioE3AUI.create({})', 'DeskBotStudioE3BUI.create({})')
    html = html.replace("edition:'E3a',catalog_id", "edition:'E3b',catalog_id")
    html = html.replace('CONNECTION · E3a', 'CONNECTION · E3b')
    html = html.replace('看见身体的状态。', '连接身体，认识它。')
    html = html.replace('本体实况与电脑模拟，分别查看。', '从 USB 到身份、表情与动作，逐步接通。')
    match = re.search(r'<script id="studio-data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(match[1]); data["edition"] = "E3b"
    html = html[:match.start(1)] + json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + html[match.end(1):]
    html = html.replace('window.DeskBotStudioE3AUI=Object.freeze({create});\n})();',
                        'window.DeskBotStudioE3AUI=Object.freeze({create});\n})();\n;\n' + (ROOT / 'scripts/deskbot-studio-e3b.js').read_text(encoding='utf-8'))
    html = html.replace('</style>', '\n' + (ROOT / 'scripts/deskbot-studio-e3b.css').read_text(encoding='utf-8') + '</style>', 1)
    report = json.loads(previous['report.json'])
    names = ['protocol/device_link.py', 'protocol/studio_identity_observation.py', 'protocol/studio_identity_bridge.py',
             'protocol/deskbot-device-link-v1.schema.json', 'protocol/deskbot-body-observation-v2.schema.json',
             'tools/observe-studio-body-e3b.py', 'scripts/build-deskbot-studio-e3b.py',
             'scripts/deskbot-studio-e3b.js', 'scripts/deskbot-studio-e3b.css', 'scripts/serve-deskbot-studio-e3b.py',
             'scripts/start-deskbot-studio-e3b.ps1']
    report.update(edition='E3b', result='identity_workbench_built',
                  device_link={'schema':'deskbot.device_link/v1','hello_interval_ms':10000,'fresh_ms':30000,
                               'catalog_confirmed':False,'expression_controls':False},
                  firmware_changed=True, device_written=False, hardware_accessed=False)
    report['sources'] += [{'path': name, 'sha256': sha((ROOT / name).read_bytes())} for name in names]
    return {**previous, 'index.html':html.encode(), 'report.json':json_bytes(report)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local',action='store_true');parser.add_argument('--check',action='store_true')
    args=parser.parse_args();output=LOCAL if args.local else PUBLIC
    for name,raw in products(args.local).items():
        if args.check:
            if (output/name).read_bytes()!=raw: raise ValueError('E3b product differs: '+name)
        else:
            output.mkdir(parents=True,exist_ok=True);(output/name).write_bytes(raw)
    print(json.dumps({'result':'checked' if args.check else 'built','edition':'E3b'}))
