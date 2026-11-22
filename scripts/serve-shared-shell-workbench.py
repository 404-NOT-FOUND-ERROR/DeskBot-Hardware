"""Run the new shell workbench against the existing local USB owner."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.studio_shared_owner import SharedWorkbenchBridge


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-url',required=True)
    parser.add_argument('--device-id',required=True)
    parser.add_argument('--port',type=int,default=8782)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--page',type=Path,required=True)
    parser.add_argument('--client-url')
    parser.add_argument('--sync-source-id')
    args=parser.parse_args()
    if bool(args.client_url)!=bool(args.sync_source_id):
        parser.error('--client-url 与 --sync-source-id 需同时提供')
    bridge=SharedWorkbenchBridge(args.owner_url,args.device_id)
    control=bridge.control()
    if bridge.request('/api/shell-runtime/state').get('enabled') is not False:
        raise ValueError('请先在原工作台暂停自动换角')
    client=bridge.request('/api/client-presentation/state')
    if client.get('device_id')!=args.device_id or not client.get('character_id'):
        raise ValueError('请先核对原客户端的持续个体与设备关联')
    spec=importlib.util.spec_from_file_location('shared_shell_server',ROOT/'scripts/serve-deskbot-shell-workbench.py')
    service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
    server=service.StudioServer(args.port,bridge=bridge,state_path=args.runtime/'role-selection.json',
        profiles_path=args.runtime/'profiles.json',shell_root=args.runtime/'capture',package_output=args.runtime/'packages',
        installation_options=dict(character_id=client['character_id']),
        client_installation_options=dict(url=args.client_url,source_id=args.sync_source_id,
            character_id=client['character_id'],device_id=args.device_id) if args.client_url else None)
    server.page=args.page.resolve()
    print(f'Shared-owner shell workbench http://127.0.0.1:{server.server_port}/#shells',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
