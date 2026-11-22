"""Shell production HTTP/UI backed by the established memory device owner."""
import importlib.util
from pathlib import Path
import sys

import serve_studio_device_roles_e3f9d_fixture as previous

ROOT = previous.ROOT
spec = importlib.util.spec_from_file_location('shell_workbench_fixture_server', ROOT / 'scripts/serve-deskbot-shell-workbench.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class Handler(service.Handler):
    def do_GET(self):
        if self.path == '/__fixture/requests':
            return self.json(200, dict(requests=self.server.bridge.requests, hardware_accessed=False))
        return super().do_GET()


if __name__ == '__main__':
    folder = Path(sys.argv[1])
    server = service.StudioServer(0, bridge=previous.Bridge(), state_path=folder / 'active.json',
                                  package_output=folder / 'packages', shell_root=folder / 'shells')
    server.RequestHandlerClass = Handler
    print(f'Shell fixture http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
