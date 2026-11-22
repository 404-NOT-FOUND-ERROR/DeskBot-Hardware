"""Eating page on the production HTTP routes with an in-memory USB boundary."""
from serve_studio_e3e_fixture import service, MemoryBridge, Handler, ROOT
if __name__ == "__main__":
    server = service.make_server(0, local=True, bridge=MemoryBridge())
    server.RequestHandlerClass = Handler
    server.page = ROOT / "tmp/deskbot-studio-e3f4a-20261109/index.html"
    print(f"Fixture eating http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
