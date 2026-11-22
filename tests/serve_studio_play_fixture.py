"""Readiness browser fixture with production HTTP and an explicit memory boundary."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

from serve_studio_e3e_fixture import MemoryBridge, Handler, ROOT
from protocol.studio_control_link import validate_command

spec = importlib.util.spec_from_file_location('readiness_http', ROOT / 'scripts/serve-deskbot-studio-e3f4b.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
MANIFEST = json.loads((ROOT / 'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json').read_bytes())


class Bridge(MemoryBridge):
    def command(self, raw):
        value = validate_command(raw)
        if value['operation'] != 'read_catalog':
            if not self.live['can_submit']:
                raise ValueError('fixture_request_not_ready')
            row = super().command(value)
            receipt = dict(request_id='expression-'+value['command_id'],sequence=len(self.submitted),status='dispatched',
                reason='submitted_to_display', target_id=value['target_id'],boot_id=value['boot_id'],session_id=value['session_id'])
            self.live['receipts'] = [receipt]
            return self.control()
        if any(value[k] != self.live.get(k) for k in ('connection_id','target_id','boot_id','session_id')):
            raise ValueError('fixture_connection_changed')
        self.requests.append(value)
        self.live['catalog'] = dict(status='complete',reason='verified',
            target_id=value['target_id'],boot_id=value['boot_id'],session_id=value['session_id'],
            manifest=deepcopy(MANIFEST),received_entries=len(MANIFEST['entries']),total=len(MANIFEST['entries']))
        self.live['last_command'] = dict(command_id=value['command_id'],operation='read_catalog',status='processed',reason='command_processed')
        return self.control()


if __name__ == '__main__':
    server = service.StudioServer(0, local=True, bridge=Bridge())
    server.RequestHandlerClass = Handler
    print(f'Fixture readiness http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
