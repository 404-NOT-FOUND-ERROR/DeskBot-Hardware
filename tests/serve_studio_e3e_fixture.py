"""Browser-only server: production HTTP/planner with an injected memory bridge."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_control_link import validate_command

spec = importlib.util.spec_from_file_location("e3e_browser_server", ROOT / "scripts/serve-deskbot-studio-e3e.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class MemoryBridge:
    def __init__(self):
        self.live = dict(can_submit=False, status="off", catalog=dict(status="disconnected"))
        self.body = dict(hardware_accessed=False)
        self.requests, self.submitted = [], {}
        self.fail_resolve = False

    def control(self):
        return deepcopy(self.live)

    def snapshot(self):
        return deepcopy(self.body)

    def ports(self):
        return dict(available=False, ports=[])

    def command(self, raw):
        value = validate_command(raw)
        self.requests.append(value)
        if value["operation"] not in ("neutral", "embarrassed", "eat") or any(value[k] != self.live.get(k)
                for k in ("connection_id", "target_id", "boot_id", "session_id")):
            raise ValueError("fixture_connection_changed")
        self.submitted.setdefault(value["command_id"], value)
        self.live["last_command"] = dict(command_id=value["command_id"], operation=value["operation"],
            status="processed", reason="command_processed")
        return self.control()

    def close(self):
        pass


class Handler(service.StudioHandler):
    def do_GET(self):
        if self.path != "/__fixture/state":
            return super().do_GET()
        if self.allowed():
            self.json(200, dict(requests=self.server.bridge.requests,
                submitted=list(self.server.bridge.submitted), hardware_accessed=False))

    def do_POST(self):
        if self.path == "/__fixture/state":
            if not self.allowed():
                return
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get("X-DeskBot-Fixture") != "memory" or not 0 < size < 1024*1024:
                return self.json(400, dict(error="fixture_input"))
            value = json.loads(self.rfile.read(size))
            bridge = self.server.bridge
            for key in ("live", "body", "fail_resolve"):
                if key in value:
                    setattr(bridge, key, value[key])
            return self.json(200, dict(ok=True))
        if self.path == "/api/role-expression/resolve" and self.server.bridge.fail_resolve:
            # Failure injection belongs only to this test server.
            return self.json(503, dict(error="本机分析暂未完成，请重试"))
        return super().do_POST()


if __name__ == "__main__":
    server = service.make_server(0, local="--public" not in sys.argv, bridge=MemoryBridge())
    server.RequestHandlerClass = Handler
    print(f"Fixture E3e http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
