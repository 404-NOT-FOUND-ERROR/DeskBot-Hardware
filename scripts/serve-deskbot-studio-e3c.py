#!/usr/bin/env python3
"""Studio E3c-6: current state and one neutral expression over the console owner."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_control_bridge import ControlBridge
from protocol.studio_usb_session import _unique
from protocol import studio_resource_package as packages

spec = importlib.util.spec_from_file_location("studio_e3c_server_parent", ROOT / "scripts/serve-deskbot-studio-e3b.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class StudioHandler(parent.StudioHandler):
    server_version = "DeskBotStudio/3c6"

    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in ("/api/studio/state", "/api/health"):
            return super().do_GET()
        if not self.allowed():
            return
        state = self.server.bridge.control()
        if path == "/api/studio/state":
            self.json(200, state)
        else:
            self.json(200, dict(service="deskbot.studio.packager/v1", edition="E3c-6",
                catalog_id=self.server.catalog["catalog_id"], target_id=self.server.target["id"],
                ready=packages.BASE_IMAGE.is_file() and packages.CANDIDATE.is_file(),
                hardware_accessed=self.server.bridge.snapshot()["hardware_accessed"], device_written=False,
                observation="usb_console", device_controls=state["can_submit"], control_status=state["status"]))

    def do_POST(self):
        if self.path != "/api/studio/command":
            return super().do_POST()
        if not self.allowed():
            return
        if (self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"
                or self.headers.get("X-DeskBot-Studio") != "e3c6" or self.headers.get("Transfer-Encoding")):
            self.json(415, {"error": "请从本机工作台提交请求"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length <= 512:
                raise ValueError("请求长度应为 1–512 字节")
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("请求传输未完成")
            value = json.loads(raw, object_pairs_hook=_unique)
            self.json(200, self.server.bridge.command(value))
        except (ValueError, TypeError, RecursionError):
            self.json(422, {"error": "连接状态已变化，请刷新状态后再试"})
        except OSError:
            self.json(503, {"error": "连接进程已退出，请重新连接 USB"})


class StudioServer(parent.StudioServer):
    def __init__(self, port=8766, *, local=True, output=None, serial_python=None, bridge=None):
        super().__init__(port, local=local, output=output, bridge=bridge or ControlBridge(serial_python))
        self.RequestHandlerClass = StudioHandler
        self.page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e3c6-20261105/index.html"
        if not self.page.is_file():
            self.server_close()
            raise FileNotFoundError("请先生成 E3c-6 工作台")


make_server = StudioServer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--serial-python", type=Path)
    args = parser.parse_args()
    server = make_server(args.port, local=not args.public, serial_python=args.serial_python)
    print(f"DeskBot Studio E3c-6: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
