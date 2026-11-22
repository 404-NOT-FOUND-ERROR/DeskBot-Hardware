#!/usr/bin/env python3
"""E3d-2 workbench over the existing single-owner USB bridge."""
import argparse
import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e3d2_server_parent", ROOT / "scripts/serve-deskbot-studio-e3d.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class StudioHandler(parent.parent.StudioHandler):
    server_version = "DeskBotStudio/3d2"

    def do_GET(self):
        if urlsplit(self.path).path != "/api/health":
            return super().do_GET()
        if not self.allowed():
            return
        state = self.server.bridge.control()
        self.json(200, dict(service="deskbot.studio.packager/v1", edition="E3d-2",
            catalog_id=self.server.catalog["catalog_id"], target_id=self.server.target["id"],
            ready=parent.parent.packages.BASE_IMAGE.is_file() and parent.parent.packages.CANDIDATE.is_file(),
            hardware_accessed=self.server.bridge.snapshot()["hardware_accessed"], device_written=False,
            observation="usb_console", device_controls=state["can_submit"], control_status=state["status"],
            catalog_status=state["catalog"]["status"]))


class StudioServer(parent.StudioServer):
    def __init__(self, port=8772, *, local=True, **kwargs):
        super().__init__(port, local=local, **kwargs)
        self.RequestHandlerClass = StudioHandler
        self.page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e3d2-20261105/index.html"
        if not self.page.is_file():
            self.server_close()
            raise FileNotFoundError("请先生成 E3d-2 工作台")


make_server = StudioServer

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--serial-python", type=Path)
    args = parser.parse_args()
    server = make_server(args.port, local=not args.public, serial_python=args.serial_python)
    print(f"DeskBot Studio E3d-2: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
