"""Device role controls above the existing two-role node editor."""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('role_device_builder_parent', ROOT/'scripts/build-deskbot-studio-e3f9b.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
LOCAL = ROOT/'tmp/deskbot-studio-e3f9d-20261112'
PUBLIC = ROOT/'artifacts/deskbot-studio-e3f9d-20261112'


def products(local=False):
    previous = parent.products(local)
    html = previous['index.html'].decode('utf-8')
    def once(old, new):
        nonlocal html
        if html.count(old) != 1: raise ValueError('E3f-9d anchor changed: '+old[:100])
        html = html.replace(old, new, 1)
    once('<span class="edition">E3f·9b</span>', '<span class="edition">E3f·9d</span>')
    once('</head>', '<style>'+(ROOT/'scripts/deskbot-device-roles-e3f9d.css').read_text('utf-8')+'</style></head>')
    once('<script id="studio-data"', '<script>'+(ROOT/'scripts/deskbot-device-roles-e3f9d.js').read_text('utf-8')+'</script><script id="studio-data"')
    once('return Object.freeze({view,init,playRole,state:', 'return Object.freeze({view,init,playRole,command,state:')
    once('  function rolesView(){return unifiedPackage.view()',
         '  const deviceRoles=DeskBotDeviceRolesE3f9d.create({catalog:unifiedData.catalog,control:()=>liveControl.state(),command:op=>liveControl.command(op)});\n  function rolesView(){return deviceRoles.view()+unifiedPackage.view()')
    once('runtimeRoles.mount();unifiedPackage.mount();', 'runtimeRoles.mount();unifiedPackage.mount();deviceRoles.mount();')
    once('  unifiedPackage.init();', '  unifiedPackage.init();\n  deviceRoles.init();')
    once('rolePackage:()=>unifiedPackage.state(),', 'rolePackage:()=>unifiedPackage.state(),deviceRoles:()=>deviceRoles.state(),')
    report = dict(edition='E3f-9d', result='device_role_controls_generated', parent_report=parent.sha(previous['report.json']),
        sources=[dict(path=p,sha256=parent.sha((ROOT/p).read_bytes())) for p in (
            'scripts/build-deskbot-studio-e3f9d.py','scripts/deskbot-device-roles-e3f9d.js',
            'scripts/deskbot-device-roles-e3f9d.css','protocol/studio_device_roles.py')])
    return {**previous,'index.html':html.encode('utf-8'),'report.json':parent.json_bytes(report)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local',action='store_true');parser.add_argument('--check',action='store_true')
    args=parser.parse_args();folder=LOCAL if args.local else PUBLIC
    for name,raw in products(args.local).items():
        if args.check:
            if (folder/name).read_bytes()!=raw: raise ValueError('workbench differs: '+name)
        else:
            (folder/name).parent.mkdir(parents=True,exist_ok=True);(folder/name).write_bytes(raw)
    print('E3f-9d workbench '+('checked' if args.check else 'built'))
