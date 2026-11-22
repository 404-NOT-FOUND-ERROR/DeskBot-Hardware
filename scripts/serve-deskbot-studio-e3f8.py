#!/usr/bin/env python3
"""Two persistent expression roles over the existing workbench and USB owner."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.role_activation import (RoleActivation, RoleConflict, RoleStorageError,
                                      StateOwner, STATE_PATH, load_roles, digest)
from protocol.studio_usb_session import _unique

spec = importlib.util.spec_from_file_location("activation_parent", ROOT / "scripts/serve-deskbot-studio-e3f7.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class Handler(parent.Handler):
    server_version = "DeskBotStudio/3f8"

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/roles/state":
            if self.allowed():
                self.json(200, self.server.roles.snapshot())
            return
        if path.startswith("/role-media/"):
            if not self.allowed():
                return
            item = self.server.role_media.get(path[1:])
            if item is None:
                return self.json(404, dict(error="未找到角色预览"))
            try:
                raw = item[0].read_bytes()
                if digest(raw) != item[1]:
                    raise ValueError("角色预览已更新，请重新生成工作台。")
                self.reply(200, raw, "image/png")
            except (OSError, ValueError):
                self.json(503, dict(error="请重新生成角色预览"))
            return
        return super().do_GET()

    def json(self, code, value):
        if isinstance(value, dict) and value.get("edition") in ("E3f-2", "E3f-6", "E3f-7"):
            value = dict(value, edition="E3f-8", runtime_roles=True)
        return super().json(code, value)

    def do_POST(self):
        routes = {"/api/roles/activate": ("role_id", "activate"),
                  "/api/roles/recover": ("role_id", "recover"),
                  "/api/roles/expression": ("semantic", "resolve")}
        if self.path not in routes:
            return super().do_POST()
        if (self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Origin", self.origin) != self.origin):
            return self.reject_role(403, "请从本机工作台打开角色服务")
        if (self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"
                or self.headers.get("X-DeskBot-Studio") != "e3f8"
                or self.headers.get("Transfer-Encoding")):
            return self.reject_role(415, "请从工作台选择角色")
        try:
            sizes = self.headers.get_all("Content-Length", [])
            size = int(sizes[0]) if len(sizes) == 1 else -1
            if not 0 < size <= 1024:
                return self.reject_role(413, "角色请求大小应小于 1 KiB")
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("角色请求传输未完成")
            value = json.loads(raw, object_pairs_hook=_unique)
            field, method = routes[self.path]
            if type(value) is not dict or set(value) != {field, "expected_revision", "catalog_id"}:
                raise ValueError("角色请求字段不完整")
            result = getattr(self.server.roles, method)(value[field], value["expected_revision"], value["catalog_id"])
            self.json(200, result)
        except RoleConflict as error:
            self.json(409, dict(error=str(error), state=self.server.roles.snapshot()))
        except RoleStorageError as error:
            self.json(503, dict(error=str(error)))
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            self.json(422, dict(error=str(error)))
        except OSError:
            self.close_connection = True
            self.json(503, dict(error="本机角色请求暂未完成，请重试。"))


class StudioServer(parent.StudioServer):
    def __init__(self, port=8778, *, local=True, state_path=STATE_PATH, **kwargs):
        self.state_owner = StateOwner(state_path)
        try:
            super().__init__(port, local=local, **kwargs)
            catalog, media = load_roles()
            self.roles = RoleActivation(catalog, state_path)
            self.page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e3f8-20261111/index.html"
            if not self.page.is_file():
                raise FileNotFoundError("请先生成 E3f-8 工作台")
            self.role_media = {name: (self.page.parent / name, digest(source.read_bytes()))
                               for name, source in media.items()}
            self.RequestHandlerClass = Handler
        except Exception:
            self.state_owner.close()
            if hasattr(self, "socket"):
                self.server_close()
            raise

    def server_close(self):
        try:
            if hasattr(self, "roles"):
                self.roles.close()
            super().server_close()
        finally:
            self.state_owner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8778)
    parser.add_argument("--serial-python", type=Path)
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    args = parser.parse_args()
    server = StudioServer(args.port, serial_python=args.serial_python, state_path=args.state_path)
    print(f"DeskBot Studio E3f-8: http://127.0.0.1:{server.server_port}/#roles", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
