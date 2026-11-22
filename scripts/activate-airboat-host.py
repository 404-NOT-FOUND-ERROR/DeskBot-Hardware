"""Adopt the verified installed role in local receivers and Studio; retain the prior configuration."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.role_activation import RoleActivation, STATE_PATH, load_roles, atomic_save
from protocol import studio_app_deployment as app


def run(folder,restore=False,package=None,profile_root=None):
    folder=folder.resolve()
    if folder.parent!=(ROOT/'tmp').resolve():raise ValueError('workspace_run_required')
    runtime=Path(os.environ['LOCALAPPDATA'])/'DeskBot/companion/runtime.json'
    current=json.loads(runtime.read_bytes())
    paths={'runtime':runtime,'touch':Path(current['touch_wifi_config']),
           'expression':Path(current['expression_wifi_config']),'role':STATE_PATH}
    archive=folder/'host-before';archive.mkdir(exist_ok=True)
    if restore:
        manifest=json.loads((archive/'manifest.json').read_bytes())
        for name,row in manifest.items():
            raw=(archive/(name+'.json')).read_bytes()
            if app.sha(raw)!=row['sha256'] or paths[name].resolve()!=Path(row['path']).resolve():
                raise ValueError('host_recovery_changed')
        for name,row in manifest.items():atomic_save(paths[name],json.loads((archive/(name+'.json')).read_bytes()))
        return dict(result='previous_host_configuration_restored')
    state=json.loads((folder/'state.json').read_bytes())
    if state['phase']!='installed' or state.get('readback_equal') is not True or state.get('boot_verified') is not True:
        raise ValueError('verified_paired_installation_required')
    if (archive/'manifest.json').exists():raise ValueError('host_configuration_already_adopted')
    package=Path(package) if package else ROOT/'artifacts/airboat-role-20261121/package'
    profile_root=Path(profile_root) if profile_root else ROOT/'tmp/airboat-role-20261121'
    report=json.loads((package/'report.json').read_bytes())
    build=json.loads((profile_root/'expression/build/report.json').read_bytes())
    plan=json.loads((folder/'plan.json').read_bytes())
    if plan['app_elf_sha256']!=build['app_elf_sha256'] or plan['graph_sha256']!=report['download_sha256']['deskbot-roles.json']:
        raise ValueError('installed_role_binding_changed')
    updates={}
    for channel in ('touch','expression'):
        path=paths[channel];value=json.loads(path.read_bytes())
        prepared=profile_root/channel
        settings=json.loads((prepared/'receiver.json').read_bytes())
        if value['device_id']!=settings['device_id']:raise ValueError('receiver_device_changed')
        for field in ('token_file','cert_file','key_file'):
            if (path.parent/value[field]).read_bytes()!=(prepared/settings[field]).read_bytes():
                raise ValueError('receiver_credentials_changed')
        value['firmware_sha256']=build['app_elf_sha256']
        if channel=='expression':value.update(graph_sha256=plan['graph_sha256'],role_id='airboat-face-v1',role_revision=None)
        updates[path]=value
    # runtime.touch_firmware_sha256 belongs to the retained USB receiver.
    # Wi-Fi receivers carry their own firmware pins; keep the transports distinct.
    manifest={}
    for name,path in paths.items():
        if path.exists():
            raw=path.read_bytes();app.write_new(archive/(name+'.json'),raw)
            manifest[name]=dict(path=str(path),sha256=app.sha(raw))
    app.write_new(archive/'manifest.json',app.canonical(manifest))
    for path,value in updates.items():atomic_save(path,value)
    roles=RoleActivation(load_roles()[0],STATE_PATH)
    try:
        selection=roles.snapshot()
        if selection['storage_status']=='unavailable':roles.recover('airboat-face-v1',0,roles.catalog['catalog_id'])
        else:roles.activate('airboat-face-v1',selection['activation']['revision'],roles.catalog['catalog_id'])
    finally:roles.close()
    receipt=dict(result='installed_role_adopted',role_id='airboat-face-v1',graph_sha256=plan['graph_sha256'],
        firmware_sha256=build['app_elf_sha256'],credentials_preserved=True,life_database_modified=False,
        usb_receiver_binding_preserved=True,
        rollback='stop local services, recover paired device, then restore host-before configuration')
    app.write_new(folder/'host-adoption.json',app.canonical(receipt))
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True)
    parser.add_argument('--restore',action='store_true');parser.add_argument('--package',type=Path)
    parser.add_argument('--profile-root',type=Path);args=parser.parse_args()
    print(json.dumps(run(ROOT/'tmp'/args.run,args.restore,args.package,args.profile_root)))
