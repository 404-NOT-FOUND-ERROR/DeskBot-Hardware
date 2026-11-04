#!/usr/bin/env python3
"""Studio E3a: same-origin packages plus a single passive USB observer."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_body_bridge import ConsoleBridge

spec = importlib.util.spec_from_file_location("studio_e3a_package_parent", ROOT / "scripts/serve-deskbot-studio-e2b.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class StudioHandler(parent.StudioHandler):
    server_version = "DeskBotStudio/3a"

    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in ("/api/body/ports", "/api/body/state", "/api/health"):
            return super().do_GET()
        if not self.allowed():
            return
        if path == "/api/body/ports":
            self.json(200, self.server.bridge.ports())
        elif path == "/api/body/state":
            self.json(200, self.server.bridge.snapshot())
        else:
            missing = [label for label, file in (("E1.18 基线资源", parent.packages.BASE_IMAGE),
                       ("害羞候选", parent.packages.CANDIDATE)) if not file.is_file()]
            self.json(200, {"service": "deskbot.studio.packager/v1", "edition": "E3a", "catalog_id": self.server.catalog["catalog_id"],
                           "target_id": self.server.target["id"], "ready": not missing, "missing": missing,
                           "hardware_accessed": self.server.bridge.snapshot()["hardware_accessed"],
                           "device_written": False, "observation": "usb_console", "device_controls": False})

    def do_POST(self):
        if self.path not in ("/api/body/connect", "/api/body/disconnect"):
            return super().do_POST()
        if not self.allowed():
            return
        if (self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"
                or self.headers.get("X-DeskBot-Studio") != "e3a" or self.headers.get("Transfer-Encoding")):
            self.json(415, {"error": "请从工作台选择观察连接"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length <= 1024:
                raise ValueError("连接参数过长或缺失")
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("连接参数传输未完成")
            value = json.loads(raw)
            if self.path.endswith("/connect"):
                if not isinstance(value, dict) or set(value) != {"port", "key"}:
                    raise ValueError("请选择本次枚举到的 USB 串口")
                state = self.server.bridge.connect(value["port"], value["key"])
            else:
                if value != {}:
                    raise ValueError("断开操作参数应为空")
                state = self.server.bridge.disconnect()
            self.json(200, state)
        except (ValueError, TypeError) as error:
            self.json(422, {"error": str(error)})
        except OSError:
            self.json(503, {"error": "串口观察进程未启动，请检查本机 Python 环境"})


class StudioServer(parent.StudioServer):
    def __init__(self, port=8766, *, local=True, output=None, serial_python=None, bridge=None):
        self.bridge = bridge or ConsoleBridge(serial_python)
        super().__init__(port, **({"output": output} if output else {}), local=local)
        self.RequestHandlerClass = StudioHandler
        self.page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e3a-20261104/index.html"
        if not self.page.is_file():
            self.server_close()
            raise FileNotFoundError("请先生成 E3a 工作台")

    def server_close(self):
        self.bridge.close()
        super().server_close()


make_server = StudioServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--serial-python", type=Path, help="verified Python containing pyserial")
    args = parser.parse_args()
    server = make_server(args.port, local=not args.public, serial_python=args.serial_python)
    print(f"DeskBot Studio E3a: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
