#!/usr/bin/env python3
"""Serve the eating edition through the existing single-owner connection."""
import argparse
import importlib.util
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("eat_server_parent", ROOT / "scripts/serve-deskbot-studio-e3f2.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)

class Handler(parent.StudioHandler):
    server_version = "DeskBotStudio/3f4a"
    def json(self, code, value):
        if isinstance(value, dict) and value.get("edition") == "E3f-2":
            value = dict(value, edition="E3f-4a")
        return super().json(code, value)

class StudioServer(parent.StudioServer):
    def __init__(self, port=8775, **kwargs):
        super().__init__(port, **kwargs)
        self.RequestHandlerClass = Handler
        self.page = ROOT / "tmp/deskbot-studio-e3f4a-20261109/index.html"
        if not self.page.is_file():
            self.server_close()
            raise FileNotFoundError("请先生成进食工作台")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8775)
    parser.add_argument("--serial-python", type=Path)
    args = parser.parse_args()
    server = StudioServer(args.port, local=True, serial_python=args.serial_python)
    print(f"DeskBot Studio E3f-4a: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
