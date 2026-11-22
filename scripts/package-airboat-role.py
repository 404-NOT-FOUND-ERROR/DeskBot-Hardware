"""Generate the complete resident role, the original-role return package and a decoded preview."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_role_package import Library
from protocol.studio_resource_package import json_bytes


def build():
    library = Library()
    folder = ROOT / 'artifacts/airboat-role-20261121'
    reports = {}
    for name, role_id in (('package', 'airboat-face-v1'), ('original-role', 'xiaozhi-e145')):
        draft = deepcopy(library.initial)
        draft['selected_role_id'] = role_id
        report, files = library.assemble(draft)
        target = folder / name
        target.mkdir(exist_ok=True)
        for file, raw in {**files, 'report.json': json_bytes(report)}.items():
            (target / file).write_bytes(raw)
        reports[name] = report
    manifest = json.loads((folder / 'manifest.json').read_bytes())
    template = (ROOT / 'scripts/airboat-role-gallery.html').read_text('utf-8')
    data = dict(manifest=manifest, package=reports['package'])
    (folder / 'index.html').write_text(template.replace('__ROLE_DATA__',
        json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')), encoding='utf-8')
    return {name: dict(bytes=r['package_bytes'], free=r['partition_free_bytes'],
        image_sha256=r['image_sha256'], graph_sha256=r['download_sha256']['deskbot-roles.json'])
        for name,r in reports.items()}


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=False))
