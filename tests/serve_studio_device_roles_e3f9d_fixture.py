"""Production E3f-9d HTTP with a memory-only USB owner for browser tests."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import threading

import serve_studio_common_e3f7_fixture as common
from protocol.studio_control_link import validate_command
from protocol.studio_device_roles import ACTIVATE

ROOT=common.ROOT
spec=importlib.util.spec_from_file_location('roles9d_fixture_server',ROOT/'scripts/serve-deskbot-studio-e3f9d.py')
service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)


class Bridge(common.Bridge):
    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()

    def command(self, raw):
        value=validate_command(raw)
        op=value['operation']
        if op!='read_roles' and not ACTIVATE.fullmatch(op): return super().command(value)
        if any(value[k]!=self.live.get(k) for k in ('connection_id','target_id','boot_id','session_id')):
            raise ValueError('fixture_connection_changed')
        self.requests.append(value)
        self.live['last_command']=dict(command_id=value['command_id'],operation=op,status='processed',reason='command_processed')
        if op=='read_roles':
            row=dict(schema='deskbot.studio_roles/v1',message_type='roles',request_id='fixture-1',
                **{k:value[k] for k in ('target_id','boot_id','session_id')},sequence=3,at_ms=1000,lease_ms=30000,
                result='state',reason='none',loaded=True,graph_sha256='a'*64,
                roles=['grok-round-personal-v1','xiaozhi-e145'],default_index=1,target_index=1,current_index=1,
                active_index=1,phase='held',blocked=0,sleep='unknown',saved_index=1,save_state='saved',
                activation=dict(request_id='',status='idle'))
        else:
            row=deepcopy(self.live['roles']['reply']);index=int(op.split('.')[2])
            row.update(target_index=index,current_index=index,active_index=index,saved_index=index,
                save_state='saved',reason='first_frame_executed',result='accepted',
                activation=dict(request_id='fixture-2',status='applied'))
        self.live['roles']=dict(status='current',reply=row,age_ms=20,pending=None,error=None)
        return self.control()


class Handler(service.Handler):
    def do_GET(self):
        if self.path!='/__fixture/state': return super().do_GET()
        if self.allowed(): self.json(200,dict(requests=self.server.bridge.requests,hardware_accessed=False))
    def do_POST(self):
        if self.path!='/__fixture/state': return super().do_POST()
        if not self.allowed(): return
        size=int(self.headers.get('Content-Length','0'))
        if self.headers.get('X-DeskBot-Fixture')!='memory' or not 0<size<1024*1024:
            return self.json(400,dict(error='fixture_input'))
        value=json.loads(self.rfile.read(size))
        for key in ('live','body'):
            if key in value: setattr(self.server.bridge,key,value[key])
        self.json(200,dict(ok=True))


if __name__=='__main__':
    work=Path(sys.argv[1])
    server=service.StudioServer(0,bridge=Bridge(),state_path=work/'state.json',package_output=work/'packages')
    server.RequestHandlerClass=Handler
    print(f'Fixture E3f-9d http://127.0.0.1:{server.server_port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
