"""Production workbench and owner-install routes with a ControlLink memory device."""
from pathlib import Path
import sys
import threading

import serve_studio_device_roles_e3f9d_fixture as previous
from tests.test_character_profiles import service_module
from tests.test_shell_installation import InstallationTests


if __name__=='__main__':
    folder=Path(sys.argv[1])
    case=InstallationTests();case.setUp()
    service=service_module()
    server=service.StudioServer(0,bridge=previous.Bridge(),state_path=folder/'state.json',
        profiles_path=folder/'profiles.json',shell_root=folder/'shells',package_output=folder/'packages')
    server.shell_installation.close()
    server.shell_runtime.close()
    server.shell_characters.close();server.characters.close();server.shells.close()
    server.shell_runtime=case.automatic
    server.shell_characters=case.registry
    server.characters=case.characters
    server.shells=case.sessions
    server.shell_installation=case.service
    server.page=Path(sys.argv[2])
    stop=threading.Event()

    class Handler(service.Handler):
        def do_POST(self):
            if self.path=='/__fixture/rebind':
                with case.service.transaction():
                    case.registry.confirm('b','profile-a',1,None,None,case.registry.doc['revision'])
                    return self.json(200,case.service.state())
            return super().do_POST()

        def do_GET(self):
            if self.path=='/__fixture/installation':
                return self.json(200,dict(activations=len(case.owner.activations),hardware_accessed=False))
            return super().do_GET()

    server.RequestHandlerClass=Handler

    def pump():
        while not stop.wait(.05):
            case.run_to(case.clock.ms+100)

    worker=threading.Thread(target=pump,daemon=True);worker.start()
    print(f'Installation fixture http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        stop.set();worker.join(3);server.server_close();case.doCleanups()
