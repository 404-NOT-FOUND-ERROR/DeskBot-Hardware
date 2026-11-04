#!/usr/bin/env python3
"""Serve Studio and its resource packager on loopback, using standard Python."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_resource_package as packages

WORKBENCH = ROOT / "tmp/deskbot-studio-e2b-20261104/index.html"
PUBLIC = ROOT / "artifacts/deskbot-studio-e2b-20261104/index.html"
DOWNLOADS = {"report.json": "application/json", "selected-role.json": "application/json",
             "members.json": "application/json", "expression-package.bin": "application/octet-stream",
             "expression-assets-8m.bin": "application/octet-stream"}


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8766, output=packages.RELEASES, local=True):
        self.catalog = packages.load_catalog()
        self.target = packages.load_target()
        self.output = Path(output)
        self.page = WORKBENCH if local and WORKBENCH.is_file() else PUBLIC
        self.package_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), StudioHandler)


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "DeskBotStudio/2b"

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def log_message(self, fmt, *args):
        # Keep user-provided request paths and draft text out of routine logs.
        pass

    def reply(self, status, raw, content_type="application/json", filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(raw)

    def json(self, status, value):
        self.reply(status, packages.json_bytes(value))

    def allowed(self):
        if (self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Origin", self.origin) != self.origin):
            self.json(403, {"error": "请从本机工作台打开此服务"})
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self.reply(200, self.server.page.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/health":
            missing = [label for label, file in (("E1.18 基线资源", packages.BASE_IMAGE),
                       ("害羞候选", packages.CANDIDATE)) if not file.is_file()]
            self.json(200, {"service": "deskbot.studio.packager/v1", "catalog_id": self.server.catalog["catalog_id"],
                            "target_id": self.server.target["id"], "ready": not missing, "missing": missing,
                            "hardware_accessed": False})
        elif path == "/reference/e145":
            self.reply(200, (ROOT / "artifacts/expression-e1-45-staging-20261104/index.html").read_bytes(),
                       "text/html; charset=utf-8")
        else:
            match = re.fullmatch(r"/packages/([a-f0-9]{64})/([a-z0-9.-]+)", path)
            if match and match[2] in DOWNLOADS:
                directory = self.server.output / match[1]
                target = directory / match[2]
                if target.is_file() and directory.resolve().is_relative_to(self.server.output.resolve()):
                    self.reply(200, target.read_bytes(), DOWNLOADS[match[2]], match[2])
                    return
            self.json(404, {"error": "未找到工作台资源"})

    def do_POST(self):
        if not self.allowed():
            return
        if self.path != "/api/package":
            self.json(404, {"error": "未找到此操作"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json" or self.headers.get("X-DeskBot-Studio") != "e2b":
            self.json(415, {"error": "请使用工作台提交 JSON 草稿"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.json(400, {"error": "请提交带长度的完整草稿"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 < length <= packages.MAX_DRAFT_BYTES:
            self.json(413, {"error": "草稿文件应小于 512 KiB"})
            return
        if not self.server.package_lock.acquire(blocking=False):
            self.json(409, {"error": "正在生成资源包，请稍候"})
            return
        try:
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("草稿传输未完成")
            report, _ = packages.generate(packages.parse_draft(raw), self.server.catalog, self.server.output)
            downloads = {name: f'/packages/{report["package_id"]}/{name}' for name in DOWNLOADS}
            self.json(200, {"report": report, "downloads": downloads})
        except (ValueError, KeyError, TypeError) as error:
            self.json(422, {"error": str(error)})
        except FileNotFoundError:
            self.json(503, {"error": "本机资源缺失，请运行环境预检并恢复 E1.18 或候选资源"})
        except OSError:
            self.json(500, {"error": "本机文件操作未完成，请查看磁盘空间与资源目录"})
        finally:
            self.server.package_lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--public", action="store_true")
    args = parser.parse_args()
    server = StudioServer(args.port, local=not args.public)
    print(f"DeskBot Studio E2b: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
