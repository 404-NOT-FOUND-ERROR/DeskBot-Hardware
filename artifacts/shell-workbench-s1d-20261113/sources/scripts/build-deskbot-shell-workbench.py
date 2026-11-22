"""Add shell registration and binding views to the current node workbench."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shell_workbench_builder_parent', ROOT / 'scripts/build-deskbot-studio-e3f9d.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
OUTPUT = ROOT / 'tmp/deskbot-shell-workbench-20261113'


def products():
    previous = parent.products(True)
    html = previous['index.html'].decode('utf-8')

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError('外壳工作台入口已变化：' + old[:80])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3f·9d</span>', '<span class="edition">S1d · S2a</span>')
    once('<span>App E3f-7</span><span>资源 E1.45</span>', '<span>App E3f-9d.3</span><span>双角色包</span>')
    once('</head>', '<style>' + (ROOT / 'scripts/deskbot-shell-workbench.css').read_text('utf-8') + '</style></head>')
    once('<script id="studio-data"', '<script>' + (ROOT / 'scripts/deskbot-shell-workbench.js').read_text('utf-8') + '</script><script id="studio-data"')
    once("roles:'角色',connection:", "roles:'角色',shells:'外壳',connection:")
    once('<a href="#connection" data-view="connection">', '<a href="#shells" data-view="shells"><span data-icon="device"></span><span>外壳</span></a>\n        <a href="#connection" data-view="connection">')
    once('  function rolesView(){return deviceRoles.view()', '  const shells=DeskBotShellWorkbench.create();\n  function rolesView(){return deviceRoles.view()')
    once('library,roles:rolesView,connection:', 'library,roles:rolesView,shells:()=>shells.view(),connection:')
    once('unifiedPackage.mount();deviceRoles.mount();', 'unifiedPackage.mount();deviceRoles.mount();shells.mount();')
    once('  deviceRoles.init();', '  deviceRoles.init();\n  shells.init();')
    once('deviceRoles:()=>deviceRoles.state(),', 'deviceRoles:()=>deviceRoles.state(),shells:()=>shells.state(),')
    sources = ('protocol/shell_workbench.py', 'protocol/shell_workbench_http.py',
        'protocol/shell_capture_readiness.py',
        'protocol/shell_role_assignment.py', 'protocol/shell_profile_archive.py',
        'protocol/shell_repeatability.py', 'scripts/build-deskbot-shell-workbench.py',
        'scripts/serve-deskbot-shell-workbench.py', 'scripts/start-deskbot-shell-workbench.ps1',
        'scripts/deskbot-shell-workbench.js',
        'scripts/deskbot-shell-workbench.css', 'docs/shell-workbench-s1c-s2a.md',
        'docs/shell-field-guide-s1d.md', 'tests/test_shell_capture_readiness.py',
        'tests/test_shell_workbench.py', 'tests/shell-workbench-browser.cjs',
        'tests/serve_shell_workbench_fixture.py')
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    report = dict(edition='S1d / S2a', result='shell_workbench_generated',
        scope='field_guide_capture_project_and_role_rehearsal', parent_report_sha256=sha(previous['report.json']),
        sources=[dict(path=name, sha256=sha((ROOT / name).read_bytes())) for name in sources],
        hardware_accessed=False, device_written=False, firmware_changed=False)
    return {**previous, 'index.html': html.encode('utf-8'),
            'report.json': (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    for name, raw in products().items():
        path = OUTPUT / name
        if args.check:
            if path.read_bytes() != raw:
                raise ValueError('工作台生成内容已变化：' + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print('Shell workbench ' + ('checked' if args.check else 'built'))


if __name__ == '__main__':
    main()
