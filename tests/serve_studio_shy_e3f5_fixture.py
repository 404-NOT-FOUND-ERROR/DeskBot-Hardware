"""Run the current HTTP workbench with E1.45 in the memory-only USB fixture."""
import json

import serve_studio_play_fixture as fixture

# The fixture owns this process-local directory. Production services and the
# connected device are untouched; automatic catalog reads return all 37 rows.
fixture.MANIFEST = json.loads(
    (fixture.ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json").read_bytes())


if __name__ == "__main__":
    server = fixture.service.StudioServer(0, local=True, bridge=fixture.Bridge())
    server.RequestHandlerClass = fixture.Handler
    print(f"Fixture E3f-5 http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
