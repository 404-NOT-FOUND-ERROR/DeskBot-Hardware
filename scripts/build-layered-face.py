"""Build the pinned native-resolution layered role without device access."""
import argparse
import base64
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.layered_face import build,package

if __name__ == '__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output',type=Path,default=ROOT/'artifacts/layered-face-20261122')
    cli.add_argument('--mirror',type=Path,help='Optional semantic -> boolean JSON overrides')
    args=cli.parse_args()
    binary,report=build(mirror=json.loads(args.mirror.read_bytes()) if args.mirror else None)
    files,report=package(binary,report)
    args.output.mkdir(parents=True,exist_ok=True)
    for name,raw in files.items(): (args.output/name).write_bytes(raw)
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    from protocol.airboat_role_assets import LABELS
    template=(ROOT/'scripts/layered-face-preview.html').read_text(encoding='utf-8')
    (args.output/'index.html').write_text(template.replace('__DFM_BASE64__',base64.b64encode(binary).decode())
        .replace('__BUILD_REPORT__',json.dumps(report,ensure_ascii=False)).replace('__LABELS__',json.dumps(LABELS,ensure_ascii=False)),encoding='utf-8')
    (args.output/'preview.js').write_bytes((ROOT/'scripts/layered-face-preview.js').read_bytes())
    (args.output/'device.js').write_bytes((ROOT/'scripts/layered-face-device.js').read_bytes())
    print(json.dumps({k:report[k] for k in ('binary_bytes','package_bytes','partition_free_bytes','sprites','semantics')}))
