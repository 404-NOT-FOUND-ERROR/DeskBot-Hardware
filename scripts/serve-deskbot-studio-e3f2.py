#!/usr/bin/env python3
"""Role overview over the existing node workbench and single-owner bridge."""
import argparse
import importlib.util
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_role_overview as overview
from protocol import studio_resource_package as packages

spec = importlib.util.spec_from_file_location("e3f2_server_parent", ROOT / "scripts/serve-deskbot-studio-e3e.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class StudioHandler(parent.StudioHandler):
    server_version = "DeskBotStudio/3f2"

    def do_GET(self):
        if urlsplit(self.path).path != "/api/health":
            return super().do_GET()
        if not self.allowed():
            return
        control = self.server.bridge.control()
        self.json(200, dict(service="deskbot.studio.packager/v1", edition="E3f-2",
            catalog_id=self.server.catalog["catalog_id"], target_id=self.server.target["id"],
            ready=packages.BASE_IMAGE.is_file() and packages.CANDIDATE.is_file(),
            hardware_accessed=self.server.bridge.snapshot()["hardware_accessed"], device_written=False,
            observation="usb_console", device_controls=control["can_submit"], control_status=control["status"],
            catalog_status=control["catalog"]["status"], role_overview=True))

    def do_POST(self):
        if self.path != "/api/role-expression/overview":
            return super().do_POST()
        if (self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Origin", self.origin) != self.origin):
            return self.reject_role(403, "请从本机工作台打开此服务")
        if (self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"
                or self.headers.get("X-DeskBot-Studio") != "e3f2" or self.headers.get("Transfer-Encoding")):
            return self.reject_role(415, "请从工作台查看角色总览")
        try:
            size = int(self.headers.get("Content-Length", "-1"))
            if not 0 < size <= packages.MAX_DRAFT_BYTES:
                return self.reject_role(413, "草稿应小于 512 KiB")
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("角色草稿传输未完成")
            value = packages.parse_draft(raw)
            if type(value) is not dict or set(value) != {"draft", "source", "context", "request_id"}:
                raise ValueError("角色总览字段不完整")
            control = self.server.bridge.control() if value["source"] == "device" else None
            now = time.monotonic_ns() // 1000000
            report = overview.overview(value["draft"], self.server.catalog, self.server.reference,
                control=control, source=value["source"], context=value["context"], request_id=value["request_id"],
                issued_ms=now, now_ms=now, layout_requirements=getattr(self.server, "layout_requirements", None))
            self.json(200, report)
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            self.json(422, dict(error=str(error)))
        except OSError:
            self.json(503, dict(error="本机分析暂未完成，请重试"))


class StudioServer(parent.StudioServer):
    def __init__(self, port=8774, *, local=True, **kwargs):
        super().__init__(port, local=local, **kwargs)
        self.RequestHandlerClass = StudioHandler
        self.page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e3f2-20261109/index.html"
        if not self.page.is_file():
            self.server_close()
            raise FileNotFoundError("请先生成 E3f-2 工作台")


make_server = StudioServer
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8774)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--serial-python", type=Path)
    args = parser.parse_args()
    server = make_server(args.port, local=not args.public, serial_python=args.serial_python)
    print(f"DeskBot Studio E3f-2: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
