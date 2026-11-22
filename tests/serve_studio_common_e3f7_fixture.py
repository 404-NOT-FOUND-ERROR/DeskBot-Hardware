"""Production E3f-7 HTTP and page backed by a memory-only device fixture."""
import importlib.util
import json
import serve_studio_role_layout_e3f6_fixture as nodes
from protocol.studio_control_link import validate_command
from protocol.studio_display_query import EXPRESSION_SEMANTICS

ROOT = nodes.ROOT
spec = importlib.util.spec_from_file_location("common_fixture_service", ROOT / "scripts/serve-deskbot-studio-e3f7.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class Bridge(nodes.Bridge):
    def command(self, raw):
        value = validate_command(raw)
        if value["operation"] not in EXPRESSION_SEMANTICS[3:]:
            return super().command(value)
        if not self.live["can_submit"] or any(value[k] != self.live.get(k)
                for k in ("connection_id", "target_id", "boot_id", "session_id")):
            raise ValueError("fixture_connection_changed")
        self.requests.append(value)
        self.submitted.setdefault(value["command_id"], value)
        self.live["last_command"] = dict(command_id=value["command_id"], operation=value["operation"],
                                        status="processed", reason="command_processed")
        self.live["receipts"] = [dict(request_id="expression-"+value["command_id"], sequence=len(self.submitted),
            status="dispatched", reason="submitted_to_display",
            **{k: value[k] for k in ("target_id", "boot_id", "session_id")})]
        return self.control()


class Handler(service.Handler):
    def do_GET(self):
        if self.path != "/__fixture/state":
            return super().do_GET()
        if self.allowed():
            self.json(200, dict(requests=self.server.bridge.requests,
                submitted=list(self.server.bridge.submitted), hardware_accessed=False))

    def do_POST(self):
        if self.path != "/__fixture/state":
            return super().do_POST()
        if not self.allowed():
            return
        size = int(self.headers.get("Content-Length", "0"))
        if self.headers.get("X-DeskBot-Fixture") != "memory" or not 0 < size < 1024*1024:
            return self.json(400, dict(error="fixture_input"))
        value = json.loads(self.rfile.read(size))
        for key in ("live", "body"):
            if key in value:
                setattr(self.server.bridge, key, value[key])
        self.json(200, dict(ok=True))


if __name__ == "__main__":
    server = service.StudioServer(0, bridge=Bridge())
    server.RequestHandlerClass = Handler
    print(f"Fixture E3f-7 http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
