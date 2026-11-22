"""Production E3f-6 HTTP/page with a memory-only bridge."""
import importlib.util
import json

import serve_studio_play_fixture as play_fixture

ROOT = play_fixture.ROOT
spec = importlib.util.spec_from_file_location("node_layout_fixture_server", ROOT / "scripts/serve-deskbot-studio-e3f6.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
play_fixture.MANIFEST = json.loads((ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json").read_bytes())


class Bridge(play_fixture.Bridge):
    def ports(self):
        return dict(available=True, ports=[dict(port="COM4", usb_id="303A:1001", key="memory-only")])


class Handler(service.Handler):
    """Only fixture endpoints are overridden; production routes use E3f-6."""

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
    print(f"Fixture E3f-6 http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
