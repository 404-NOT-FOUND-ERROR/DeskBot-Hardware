#!/usr/bin/env python3
"""Add one editable, previewable, packageable two-role draft to Studio."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_role_package import Library
from protocol.studio_resource_package import json_bytes, sha

spec = importlib.util.spec_from_file_location('role_package_builder_parent', ROOT / 'scripts/build-deskbot-studio-e3f8.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
LOCAL = ROOT / 'tmp/deskbot-studio-e3f9b-20261112'
PUBLIC = ROOT / 'artifacts/deskbot-studio-e3f9b-20261112'
SOURCES = ('protocol/studio_role_package.py', 'scripts/build-deskbot-studio-e3f9b.py',
    'scripts/serve-deskbot-studio-e3f9b.py', 'scripts/deskbot-role-package-e3f9b.js',
    'scripts/deskbot-role-package-e3f9b.css', 'scripts/start-deskbot-studio-e3f9b.ps1',
    'docs/unified-role-package-e3f9b.md', 'tests/test_studio_role_package_e3f9b.py',
    'tests/test_deskbot_studio_e3f9b.py', 'tests/studio-role-package-e3f9b-browser.cjs')


def products(local=False):
    previous = parent.products(local)
    html = previous['index.html'].decode('utf-8')
    library = Library()
    bootstrap = dict(catalog=library.catalog, draft=library.initial)

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError('E3f-9b workbench anchor changed: ' + old[:100])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3f·8</span>', '<span class="edition">E3f·9b</span>')
    once('</head>', '<style>' + (ROOT / 'scripts/deskbot-role-package-e3f9b.css').read_text('utf-8') + '</style></head>')
    once('<script id="studio-data"', '<script id="unified-role-package-data" type="application/json">' +
        json.dumps(bootstrap, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c') + '</script><script>' +
        (ROOT / 'scripts/deskbot-role-package-e3f9b.js').read_text('utf-8') + '</script>\n<script id="studio-data"')
    once("  const rolePacks=DeskBotRolePacks.create(JSON.parse($('role-pack-data').textContent));",
         "  const unifiedData=JSON.parse($('unified-role-package-data').textContent);\n" +
         "  const unifiedPackage=DeskBotRolePackageE3f9b.create(D,unifiedData.catalog,unifiedData.draft);")
    once('  function rolesView(){return runtimeRoles.view()+rolePacks.view()+`<details class="runtime-draft-editor"><summary>角色草稿编辑 · 独立于当前启用角色</summary>${draftRolesView()}</details>`;}',
         '  function rolesView(){return unifiedPackage.view()+`<details class="runtime-draft-editor"><summary>电脑当前角色与已保存预览</summary>${runtimeRoles.view()}</details>`;}')
    once('  function publishView(){return rolePacks.view()+draftPublishView();}',
         '  function publishView(){return unifiedPackage.publishView();}')
    once('runtimeRoles.mount();', 'runtimeRoles.mount();unifiedPackage.mount();')
    once('  runtimeRoles.init();', '  runtimeRoles.init();\n  unifiedPackage.init();')
    once('runtimeRoles:()=>runtimeRoles.state(),', 'runtimeRoles:()=>runtimeRoles.state(),rolePackage:()=>unifiedPackage.state(),')
    once('      const action=target.dataset.action;', "      const action=target.dataset.action;\n      if((view==='roles'||view==='publish')&&(action==='import'||action==='export')){unifiedPackage.toolbar(action);return;}")
    once("if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();",
         "if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();if(view==='roles'||view==='publish'){unifiedPackage.move(event.shiftKey?1:-1);return;}")
    # Keep the previous advanced 29-semantic editor available, while the role page is the current entry.
    once('<span>角色</span></a>', '<span>角色编辑</span></a>')
    sources = sorted({row['path'] for row in json.loads(previous['report.json'])['sources']} | set(SOURCES))
    sources.extend(['protocol/studio_resource_package.py', 'protocol/studio_mirror_options.py',
        'protocol/studio_role_layout.py', 'protocol/studio_expression_layout.py',
        'protocol/expression-layout-requirements-v1.json', 'protocol/role_activation.py',
        'protocol/deskbot-expression-roles-v1.json', 'scripts/audit-expression-package-candidate.py',
        'scripts/audit-expression-resources.py', 'scripts/deskbot-mirror-render.js',
        'scripts/grok-role-scheduler.js', 'artifacts/deskbot-studio-e2a-20261104/index.html',
        'artifacts/studio-resources-e3f4d/expected-directory.json',
        'protocol/airboat_role_package.py',
        'tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin',
        json.loads((ROOT/'protocol/deskbot-expression-roles-v1.json').read_bytes())['composable']['manifest'],
        *[p.relative_to(ROOT).as_posix() for folder in ('clips', 'previews')
          for p in ((ROOT/json.loads((ROOT/'protocol/deskbot-expression-roles-v1.json').read_bytes())['composable']['manifest']).parent/folder).glob('*') if p.is_file()]])
    sources = sorted(set(sources))
    report = dict(edition='E3f-9b', result='unified_two_role_workbench_generated',
        role_catalog_id=library.catalog['catalog_id'], parent_report=sha(previous['report.json']),
        sources=[dict(path=name, sha256=sha((ROOT / name).read_bytes())) for name in sources],
        hardware_accessed=False, device_written=False, firmware_changed=False,
        scope='offline_role_authoring_and_packaging')
    return {**previous, **{name: path.read_bytes() for name, path in library.media.items()},
            'index.html': html.encode('utf-8'), 'report.json': json_bytes(report),
            'role-package-catalog.json': json_bytes(bootstrap)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    folder = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            if (folder / name).read_bytes() != raw:
                raise ValueError('generated E3f-9b product differs: ' + name)
        else:
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print('E3f-9b workbench ' + ('checked' if args.check else 'built'))
