#!/usr/bin/env python3
"""Eight expression controls over the existing E3f-6 role-node/USB service."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("common_service_parent", ROOT / "scripts/serve-deskbot-studio-e3f6.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class Handler(parent.Handler):
    server_version = "DeskBotStudio/3f7"

    def json(self, code, value):
        if isinstance(value, dict) and value.get("edition") in ("E3f-2", "E3f-6"):
            value = dict(value, edition="E3f-7", asset_layouts=True)
        return super().json(code, value)


class StudioServer(parent.StudioServer):
    def __init__(self, port=8777, *, local=True, **kwargs):
        super().__init__(port, local=local, **kwargs)
        try:
            self.RequestHandlerClass = Handler
            self.page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e3f7-20261110/index.html"
            if not self.page.is_file():
                raise FileNotFoundError("请先生成 E3f-7 工作台")
        except Exception:
            self.server_close()
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--serial-python", type=Path)
    args = parser.parse_args()
    server = StudioServer(args.port, serial_python=args.serial_python)
    print(f"DeskBot Studio E3f-7: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
