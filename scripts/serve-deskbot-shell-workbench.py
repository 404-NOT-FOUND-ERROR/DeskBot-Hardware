"""Shell capture projects and role rehearsal in the existing Studio."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.shell_workbench import SessionStore
from protocol.shell_workbench_http import ShellRoutes
from protocol.shell_profile_archive import digest
from protocol.character_profiles import CharacterProfiles, STATE_PATH as PROFILES_PATH
from protocol.character_profiles_http import CharacterRoutes
from protocol.role_activation import load_roles
from protocol.shell_characters import ShellCharacters
from protocol.shell_characters_http import ShellCharacterRoutes
from protocol.shell_switch_service import ShellRuntimeConfig, ShellSwitchService
from protocol.shell_switch_http import ShellRuntimeRoutes
from protocol.client_presentation_ledger import PresentationLedger
from protocol.client_presentation import ClientHTTP, ClientPresentationService
from protocol.client_presentation_http import ClientPresentationRoutes
from protocol.shell_installation import ShellInstallation
from protocol.shell_installation_http import ShellInstallationRoutes
from protocol.client_installation_runtime import ClientInstallationRuntime
from protocol.body_network_workbench import NetworkRoutes, NetworkWorkbench
from protocol.expression_pipeline import ExpressionProjects
from protocol.expression_pipeline_http import ExpressionRoutes

spec = importlib.util.spec_from_file_location('shell_studio_parent', ROOT / 'scripts/serve-deskbot-studio-e3f9d.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


def reference_graph(package=None):
    path = Path(package).resolve() / 'deskbot-roles.json' if package else ROOT / 'artifacts/studio-role-package-e3f9b/deskbot-roles.json'
    raw = path.read_bytes()
    report = json.loads((path.parent / ('report.json' if package else 'default-package-report.json')).read_bytes())
    graph = json.loads(raw)
    if digest(raw) != report['download_sha256']['deskbot-roles.json']:
        raise ValueError('角色图与留存包不同，请核对资源版本')
    roles = graph['roles']
    return dict(sha256=digest(raw), roles=[r['id'] for r in roles],
                names={r['id']: r['name'] for r in roles}, source='saved_accepted_package')


class Handler(ExpressionRoutes, NetworkRoutes, ShellInstallationRoutes, ClientPresentationRoutes, ShellRuntimeRoutes, ShellCharacterRoutes, CharacterRoutes, ShellRoutes, parent.Handler):
    server_version = 'DeskBotStudio/Shells1'

    def send_header(self,keyword,value):
        # The resource preview is embedded by this same-origin workbench.
        if (keyword.lower()=='content-security-policy' and
                (urlsplit(self.path).path in ('/layered-face/','/layered-face/index.html') or
                 urlsplit(self.path).path == '/api/expressions/file')):
            value=value.replace("frame-ancestors 'none'","frame-ancestors 'self'")
        return super().send_header(keyword,value)

    def do_GET(self):
        path=urlsplit(self.path).path
        folder=getattr(self.server,'layered_face_path',None)
        if folder and path in ('/','/index.html'):
            if self.allowed():
                html=self.server.page.read_bytes().replace(b'<head>',b'<head><script>globalThis.DESKBOT_LAYERED_FACE=true;</script>',1)
                html=html.replace(b'<a href="#roles"', '<a href="/expressions/">表情制作</a><a href="#roles"'.encode('utf-8'),1)
                self.reply(200,html,'text/html; charset=utf-8')
            return
        if folder and path in ('/layered-face/','/layered-face/index.html','/layered-face/preview.js','/layered-face/device.js','/layered-face/assets-8m.bin'):
            if self.allowed():
                name='index.html' if path.endswith('/') else path.rsplit('/',1)[1]
                mime={'index.html':'text/html; charset=utf-8','preview.js':'text/javascript; charset=utf-8','device.js':'text/javascript; charset=utf-8','assets-8m.bin':'application/octet-stream'}[name]
                self.reply(200,(folder/name).read_bytes(),mime)
            return
        return super().do_GET()


class StudioServer(parent.StudioServer):
    def __init__(self, port=8779, *, shell_root=None, profiles_path=PROFILES_PATH,
                 runtime_options=None, client_options=None, installation_options=None,
                 client_installation_options=None, body_network_profile=None, installed_package=None, expression_baseline_run=None,
                 expression_project_root=None, **kwargs):
        self.layered_face_path = None
        self.expression_projects = None
        if installed_package and (Path(installed_package)/'face-motion.dfm').is_file():
            self.layered_face_path = Path(installed_package).resolve()
            baseline = expression_baseline_run or ROOT / 'tmp/layered-face-live-20261124-insert'
            if (Path(baseline)/'state.json').is_file():
                project_root=Path(expression_project_root) if expression_project_root else ROOT/'tmp/expression-projects'
                self.expression_projects=ExpressionProjects(project_root,self.layered_face_path,baseline)
                self.expression_projects.on_install=self._adopt_expression_graph
                self.layered_face_path=self.expression_projects.baseline
        self.body_network = None
        self.shells = None
        self.characters = None
        self.shell_characters = None
        self.shell_runtime = None
        self.shell_installation = None
        self.client_installation = None
        self.client_presentation = self.client_ledger = self.client_thread = None
        self.client_stop = threading.Event()
        super().__init__(port, **kwargs)
        try:
            self.body_network = NetworkWorkbench(self.bridge, body_network_profile)
            graph = reference_graph(self.layered_face_path)
            self.shells = SessionStore(shell_root or ROOT / 'tmp/deskbot-shell-workbench',
                                       project_root=ROOT, graph=graph)
            self.characters = CharacterProfiles(profiles_path, catalog=load_roles()[0], graph=graph)
            self.shell_characters = ShellCharacters(Path(profiles_path).with_name('shell-characters.json'),
                characters=self.characters, sessions=self.shells)
            self.page = ROOT / 'tmp/deskbot-shell-workbench-20261113/index.html'
            if not self.page.is_file():
                raise FileNotFoundError('请先生成外壳工作台')
            self.role_media = {name: (self.page.parent / name, sha) for name, (_, sha) in self.role_media.items()}
            self.RequestHandlerClass = Handler
            self.shell_runtime = ShellSwitchService(ShellRuntimeConfig(self.shell_characters), self.bridge,
                                                   **(runtime_options or {}))
            if client_options:
                options = dict(client_options)
                http = ClientHTTP(options.pop('url'))
                self.client_ledger = PresentationLedger(options.pop('ledger'))
                self.client_presentation = ClientPresentationService(self.bridge, http, self.client_ledger, **options)
                self.client_thread = threading.Thread(target=self._client_loop, daemon=True)
                self.client_thread.start()
            options = dict(installation_options or {})
            options.setdefault('character_id', self.client_presentation.character_id if self.client_presentation else None)
            self.shell_installation = ShellInstallation(Path(profiles_path).with_name('shell-installations.json'),
                self.shell_characters, self.bridge, self.shell_runtime,
                **options)
            if client_installation_options:
                self.client_installation = ClientInstallationRuntime(self.shell_installation,
                    Path(profiles_path).with_name('client-installation-sync.json'), **client_installation_options)
        except Exception:
            self.server_close()
            raise

    def server_close(self):
        self.client_stop.set()
        if self.expression_projects is not None:
            self.expression_projects.close()
            self.expression_projects = None
        if self.body_network is not None:
            self.body_network.close()
            self.body_network = None
        if self.client_installation is not None:
            self.client_installation.close()
            self.client_installation = None
        if self.shell_installation is not None:
            self.shell_installation.close()
            self.shell_installation = None
        if self.client_thread is not None:
            self.client_thread.join()
            self.client_thread = None
        if self.client_presentation is not None:
            self.client_presentation.close()
            self.client_presentation = None
        if self.client_ledger is not None:
            self.client_ledger.close()
            self.client_ledger = None
        if self.shell_runtime is not None:
            self.shell_runtime.close()
        if self.shell_characters is not None:
            self.shell_characters.close()
        if self.characters is not None:
            self.characters.close()
        if self.shells is not None:
            self.shells.close()
        super().server_close()

    def _adopt_expression_graph(self,path):
        """Refresh graph references after package adoption without changing personas."""
        self.layered_face_path=Path(path).resolve()
        graph=reference_graph(self.layered_face_path)
        if self.shells is not None:self.shells.graph=graph
        if self.characters is not None:self.characters.graph=graph

    def _client_loop(self):
        while not self.client_stop.wait(0.5):
            self.client_presentation.tick()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8779)
    parser.add_argument('--serial-python', type=Path)
    parser.add_argument('--state-path', type=Path, default=parent.parent.STATE_PATH)
    parser.add_argument('--shell-root', type=Path)
    parser.add_argument('--profiles-path', type=Path, default=PROFILES_PATH)
    parser.add_argument('--body-network-profile', type=Path)
    parser.add_argument('--installed-package', type=Path)
    parser.add_argument('--expression-baseline-run', type=Path)
    parser.add_argument('--image-python-packages', type=Path,
        default=Path(os.environ.get('LOCALAPPDATA',''))/'DeskBot/expression-runtime/site-packages')
    parser.add_argument('--expression-project-root',type=Path)
    parser.add_argument('--client-url')
    parser.add_argument('--client-device-id')
    parser.add_argument('--client-ledger', type=Path, default=ROOT / 'tmp/client-presentation/ledger.sqlite')
    args = parser.parse_args()
    if bool(args.client_url) != bool(args.client_device_id):
        parser.error('--client-url 与 --client-device-id 需同时提供')
    if args.image_python_packages:
        sys.path.insert(0,str(args.image_python_packages.resolve()))
    server = StudioServer(args.port, serial_python=args.serial_python, state_path=args.state_path,
                          shell_root=args.shell_root, profiles_path=args.profiles_path, body_network_profile=args.body_network_profile,
                          installed_package=args.installed_package, expression_baseline_run=args.expression_baseline_run,
                          expression_project_root=args.expression_project_root,
                          client_options=dict(url=args.client_url, device_id=args.client_device_id,
                                              ledger=args.client_ledger) if args.client_url else None)
    print(f'DeskBot Workbench: http://127.0.0.1:{server.server_port}/#characters', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
