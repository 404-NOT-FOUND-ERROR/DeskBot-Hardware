#!/usr/bin/env python3
"""E3d-1 workbench: saved directory plus the existing USB owner and control."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e3d_server_parent", ROOT / "scripts/serve-deskbot-studio-e3c.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class StudioServer(parent.StudioServer):
    def __init__(self, port=8766, *, local=True, **kwargs):
        super().__init__(port, local=local, **kwargs)
        self.page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e3d1-20261105/index.html"
        if not self.page.is_file():
            self.server_close()
            raise FileNotFoundError("请先生成 E3d-1 工作台")


make_server = StudioServer

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--serial-python", type=Path)
    args = parser.parse_args()
    server = make_server(args.port, local=not args.public, serial_python=args.serial_python)
    print(f"DeskBot Studio E3d-1: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
