"""Shell capture projects and role rehearsal in the existing Studio."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.shell_workbench import SessionStore
from protocol.shell_workbench_http import ShellRoutes
from protocol.shell_profile_archive import digest

spec = importlib.util.spec_from_file_location('shell_studio_parent', ROOT / 'scripts/serve-deskbot-studio-e3f9d.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


def reference_graph():
    path = ROOT / 'artifacts/studio-role-package-e3f9b/deskbot-roles.json'
    raw = path.read_bytes()
    report = json.loads((path.parent / 'default-package-report.json').read_bytes())
    graph = json.loads(raw)
    if digest(raw) != report['download_sha256']['deskbot-roles.json']:
        raise ValueError('角色图与留存包不同，请核对资源版本')
    roles = graph['roles']
    return dict(sha256=digest(raw), roles=[r['id'] for r in roles],
                names={r['id']: r['name'] for r in roles}, source='saved_accepted_package')


class Handler(ShellRoutes, parent.Handler):
    server_version = 'DeskBotStudio/Shells1'


class StudioServer(parent.StudioServer):
    def __init__(self, port=8779, *, shell_root=None, **kwargs):
        self.shells = None
        super().__init__(port, **kwargs)
        try:
            self.shells = SessionStore(shell_root or ROOT / 'tmp/deskbot-shell-workbench',
                                       project_root=ROOT, graph=reference_graph())
            self.page = ROOT / 'tmp/deskbot-shell-workbench-20261113/index.html'
            if not self.page.is_file():
                raise FileNotFoundError('请先生成外壳工作台')
            self.role_media = {name: (self.page.parent / name, sha) for name, (_, sha) in self.role_media.items()}
            self.RequestHandlerClass = Handler
        except Exception:
            self.server_close()
            raise

    def server_close(self):
        if self.shells is not None:
            self.shells.close()
        super().server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8779)
    parser.add_argument('--serial-python', type=Path)
    parser.add_argument('--state-path', type=Path, default=parent.parent.STATE_PATH)
    parser.add_argument('--shell-root', type=Path)
    args = parser.parse_args()
    server = StudioServer(args.port, serial_python=args.serial_python, state_path=args.state_path,
                          shell_root=args.shell_root)
    print(f'DeskBot Shell Workbench: http://127.0.0.1:{server.server_port}/#shells', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
