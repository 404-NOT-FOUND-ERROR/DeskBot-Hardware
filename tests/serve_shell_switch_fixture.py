"""Controlled ready service for browser tests; all devices and bindings stay in memory."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import serve_studio_device_roles_e3f9d_fixture as previous
from tests.test_shell_switch_service import ConfigFixture, MemoryOwner, Clock
from protocol.shell_switch_service import ShellSwitchService

ROOT = previous.ROOT
spec = importlib.util.spec_from_file_location('shell_switch_fixture_server', ROOT/'scripts/serve-deskbot-shell-workbench.py')
service = importlib.util.module_from_spec(spec); spec.loader.exec_module(service)


class BrowserOwner(previous.Bridge):
    def __init__(self, config):
        super().__init__()
        self.zero = time.monotonic()
        self.memory = MemoryOwner(Clock(), config)
        self.lock = self.memory.lock
        self.profile = 1

    def shell_inputs(self):
        self.memory.advance(int((time.monotonic()-self.zero)*1000), profile=self.profile)
        return self.memory.shell_inputs()

    def command(self, value):
        if value['operation'] == 'read_roles' or value['operation'].startswith('role.activate.'):
            self.requests.append(value)
            return self.memory.command(value)
        return super().command(value)


class Handler(service.Handler):
    def do_GET(self):
        if self.path == '/__fixture/state':
            return self.json(200, dict(requests=self.server.bridge.requests, hardware_accessed=False,
                                      memory_device=True))
        return super().do_GET()

    def do_POST(self):
        if self.path != '/__fixture/runtime': return super().do_POST()
        if not self.allowed(): return
        size = int(self.headers.get('Content-Length', '0'))
        if self.headers.get('X-DeskBot-Fixture') != 'memory' or not 0 < size < 1024:
            return self.json(400, dict(error='fixture_input'))
        value = json.loads(self.rfile.read(size))
        with self.server.shell_runtime.config.transaction(), self.server.shell_runtime.lock:
            if 'motion' in value: self.server.bridge.memory.motion = value['motion']
            if 'ready' in value: self.server.shell_runtime.config.ready = value['ready']
            if 'fail_save' in value: self.server.bridge.memory.lab.device.fail_save = value['fail_save']
            if 'profile' in value: self.server.bridge.profile = value['profile']
            if value.get('config_edit'): self.server.shell_runtime.config.version += 1
        self.json(200, dict(ok=True))


if __name__ == '__main__':
    folder = Path(sys.argv[1]); config = ConfigFixture(); bridge = BrowserOwner(config)
    server = service.StudioServer(0, bridge=bridge, state_path=folder/'active.json',
        package_output=folder/'packages', shell_root=folder/'shells', profiles_path=folder/'characters.json')
    server.shell_runtime.close()
    server.shell_runtime = ShellSwitchService(config, bridge, clock=lambda: time.monotonic()-bridge.zero, interval=.05)
    server.shell_installation.automatic = server.shell_runtime
    server.shell_runtime.installation_busy = server.shell_installation.busy
    server.RequestHandlerClass = Handler
    if os.environ.get('DESKBOT_TEST_SHELL_PAGE'):
        server.page = Path(os.environ['DESKBOT_TEST_SHELL_PAGE'])
    print(f'Shell switch fixture http://127.0.0.1:{server.server_port}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
