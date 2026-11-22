"""Actual workbench and network tasks with a memory transport, for browser QA."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import types

import serve_studio_device_roles_e3f9d_fixture as previous
ROOT = previous.ROOT
package = types.ModuleType('tests'); package.__path__ = [str(ROOT / 'tests')]; sys.modules['tests'] = package
from tests.test_body_network_workbench import profiles, settings, result, DEVICE, ELF
from protocol.body_network_workbench import NetworkWorkbench
from protocol.studio_identity_observation import IdentityObservation

spec = importlib.util.spec_from_file_location('network_fixture_server', ROOT / 'scripts/serve-deskbot-shell-workbench.py')
service = importlib.util.module_from_spec(spec); spec.loader.exec_module(service)


class Bridge(previous.Bridge):
    def __init__(self):
        super().__init__()
        self.operation = threading.Lock()
        self.python = Path('memory-python.exe')
        self.listed = 0
        self.body = IdentityObservation().snapshot()
        self.body['connection'] = dict(status='connected', port='COM4', reason='fixture')
    def available(self): return True
    def ports(self):
        self.listed += 1
        return dict(available=True, ports=[dict(port='COM4', key='d' * 64, usb_id='303A:1001')])
    def _connect(self, *_): pass
    def _disconnect(self): pass


if __name__ == '__main__':
    folder = Path(sys.argv[1]); folder.mkdir(parents=True, exist_ok=True)
    bridge = Bridge()
    server = service.StudioServer(0, bridge=bridge, state_path=folder/'state.json',
        profiles_path=folder/'profiles.json', shell_root=folder/'shells', package_output=folder/'packages')
    commands, saved = [], settings()
    fail_next = False
    def run(command, **kwargs):
        global saved, fail_next
        commands.append(command)
        time.sleep(.25)
        if fail_next:
            fail_next = False
            return type(result())([], 1, '', 'body_network_conflict')
        if command[2] == 'set':
            saved = settings(saved['revision'] + 1, command[command.index('--host') + 1])
            saved['touch_port'] = int(command[command.index('--touch-port') + 1])
            saved['expression_port'] = int(command[command.index('--expression-port') + 1])
        elif command[2] == 'reset': saved = settings(saved['revision'] + 1)
        return result(saved)
    server.body_network.close()
    server.body_network = NetworkWorkbench(bridge, profiles(folder/'network'), runner=run,
        probe=lambda _: dict(touch='ready', expression='ready', xiaozhi='ready'))

    class Handler(service.Handler):
        def do_GET(self):
            if self.path == '/__fixture/network':
                return self.json(200, dict(commands=commands, listed=bridge.listed, hardware_accessed=False))
            return super().do_GET()
        def do_POST(self):
            global fail_next
            if self.path == '/__fixture/conflict':
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                fail_next = True
                return self.json(200, dict(ok=True))
            return super().do_POST()
    server.RequestHandlerClass = Handler
    print(f'Network fixture http://127.0.0.1:{server.server_port}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
