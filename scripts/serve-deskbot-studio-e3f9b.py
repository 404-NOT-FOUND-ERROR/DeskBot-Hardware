#!/usr/bin/env python3
"""Unified offline role-package endpoints alongside the existing workbench."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_role_package import Library, PackageStore, MAX_REQUEST
from protocol.studio_resource_package import parse_draft
from protocol.role_activation import STATE_PATH, digest

spec = importlib.util.spec_from_file_location('role_package_parent', ROOT / 'scripts/serve-deskbot-studio-e3f8.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class Handler(parent.Handler):
    server_version = 'DeskBotStudio/3f9b'

    def json(self, code, value):
        if isinstance(value, dict) and value.get('edition') in ('E3f-2', 'E3f-6', 'E3f-7', 'E3f-8'):
            value = dict(value, edition='E3f-9b', unified_role_packages=True)
        return super().json(code, value)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/role-package/catalog':
            if self.allowed():
                self.json(200, dict(catalog=self.server.role_library.catalog, draft=self.server.role_library.initial))
            return
        if path.startswith('/role-packages/'):
            if not self.allowed():
                return
            parts = path.split('/')
            try:
                if len(parts) != 4:
                    raise ValueError('下载地址不同')
                raw = self.server.role_packages.download(parts[2], parts[3])
                self.reply(200, raw, 'application/octet-stream' if parts[3].endswith('.bin') else 'application/json; charset=utf-8')
            except (ValueError, OSError, KeyError):
                self.json(404, dict(error='未找到完整的角色包，请重新生成'))
            return
        return super().do_GET()

    def do_POST(self):
        if self.path != '/api/role-package/generate':
            return super().do_POST()
        if (self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                or self.headers.get('Origin', self.origin) != self.origin):
            return self.reject_role(403, '请从本机工作台生成角色包')
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'
                or self.headers.get('X-DeskBot-Studio') != 'e3f9b' or self.headers.get('Transfer-Encoding')):
            return self.reject_role(415, '请使用工作台的角色包生成入口')
        try:
            sizes = self.headers.get_all('Content-Length', [])
            size = int(sizes[0]) if len(sizes) == 1 else -1
            if not 0 < size <= MAX_REQUEST:
                return self.reject_role(413, '角色包草稿应小于 32 KiB')
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError('草稿传输未完成')
            value = parse_draft(raw)
            if type(value) is not dict or set(value) != {'draft'}:
                raise ValueError('生成请求应包含完整草稿')
            report = self.server.role_packages.generate(value['draft'])
            self.json(200, report)
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            self.json(422, dict(error=str(error)))
        except OSError:
            self.close_connection = True
            self.json(503, dict(error='角色包暂未完整保存，请重新生成'))


class StudioServer(parent.StudioServer):
    def __init__(self, port=8779, *, local=True, state_path=STATE_PATH, package_output=None, **kwargs):
        super().__init__(port, local=local, state_path=state_path, **kwargs)
        try:
            self.role_library = Library()
            self.role_packages = PackageStore(self.role_library, package_output or ROOT / 'tmp/deskbot-role-packages-e3f9b')
            self.page = ROOT / ('tmp' if local else 'artifacts') / 'deskbot-studio-e3f9b-20261112/index.html'
            if not self.page.is_file():
                raise FileNotFoundError('请先生成 E3f-9b 工作台')
            self.role_media = {name: (self.page.parent / name, digest(source.read_bytes()))
                               for name, source in self.role_library.media.items()}
            self.RequestHandlerClass = Handler
        except Exception:
            self.server_close()
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8779)
    parser.add_argument('--serial-python', type=Path)
    parser.add_argument('--state-path', type=Path, default=STATE_PATH)
    parser.add_argument('--package-output', type=Path)
    args = parser.parse_args()
    server = StudioServer(args.port, serial_python=args.serial_python, state_path=args.state_path, package_output=args.package_output)
    print(f'DeskBot Studio E3f-9b: http://127.0.0.1:{server.server_port}/#roles', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
