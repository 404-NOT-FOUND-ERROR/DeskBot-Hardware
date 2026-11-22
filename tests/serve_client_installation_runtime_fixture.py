"""Real original-client SQLite, production workbench UI and memory device."""
from pathlib import Path
import sys
import threading

import serve_studio_device_roles_e3f9d_fixture as previous
from tests.test_character_profiles import service_module
from tests.test_client_installation_sync import ClientInstallationSyncTests, CHARACTER, SOURCE
from tests.test_shell_installation import DEVICE
from protocol.client_installation_runtime import ClientInstallationRuntime


if __name__=='__main__':
    folder, page = Path(sys.argv[1]), Path(sys.argv[2])
    case=ClientInstallationSyncTests();case.setUp()
    service=service_module()
    server=service.StudioServer(0,bridge=previous.Bridge(),state_path=folder/'state.json',
        profiles_path=folder/'profiles.json',shell_root=folder/'shells',package_output=folder/'packages')
    server.shell_installation.close();server.shell_runtime.close()
    server.shell_characters.close();server.characters.close();server.shells.close()
    server.shell_runtime=case.f.automatic;server.shell_characters=case.f.registry
    server.characters=case.f.characters;server.shells=case.f.sessions;server.shell_installation=case.f.service
    server.client_installation=ClientInstallationRuntime(case.f.service,folder/'sync.json',url=case.client.http.url,
        source_id=SOURCE,character_id=CHARACTER,device_id=DEVICE,interval=.1)
    server.page=page
    stop=threading.Event()

    class Offline:
        def request(self,*args): raise OSError('fixture_client_offline')

    class Handler(service.Handler):
        def do_POST(self):
            if self.path=='/__fixture/offline':
                server.client_installation.client=Offline()
                return self.json(200,dict(ok=True))
            if self.path=='/__fixture/online':
                server.client_installation.client=case.client.http
                return self.json(200,dict(ok=True))
            return super().do_POST()

        def do_GET(self):
            if self.path=='/__fixture/current':
                return self.json(200,case.current())
            return super().do_GET()

    server.RequestHandlerClass=Handler
    def pump():
        while not stop.wait(.05): case.f.run_to(case.f.clock.ms+100)
    worker=threading.Thread(target=pump,daemon=True);worker.start()
    print(f'Runtime fixture http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        stop.set();worker.join(5);server.server_close();case.doCleanups()
