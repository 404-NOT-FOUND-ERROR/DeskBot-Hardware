"""Local API for the server-owned automatic shell-switch service."""
import json
from urllib.parse import urlsplit

from protocol.role_activation import RoleStorageError
from protocol.shell_switch_service import RuntimeConflict
from protocol.studio_usb_session import _unique


class ShellRuntimeRoutes:
    def do_GET(self):
        if urlsplit(self.path).path != '/api/shell-runtime/state':
            return super().do_GET()
        if not self.allowed():
            return
        try:
            self.json(200, self.server.shell_runtime.state())
        except (ValueError, OSError):
            self.json(503, dict(error='请检查已保存的外壳与采样项目后重试'))

    def do_POST(self):
        routes = {'/api/shell-runtime/start': {'session_id', 'expected_generation'},
                  '/api/shell-runtime/stop': {'expected_generation'},
                  '/api/shell-runtime/retry': {'expected_generation'}}
        if self.path not in routes:
            return super().do_POST()
        if (self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                or self.headers.get('Origin', self.origin) != self.origin):
            return self.reject_role(403, '请从本机外壳页面打开')
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'
                or self.headers.get('X-DeskBot-Studio') != 'shell-runtime-v1'
                or self.headers.get('Transfer-Encoding')):
            return self.reject_role(415, '请从自动换角卡片发起操作')
        try:
            lengths = self.headers.get_all('Content-Length', [])
            length = int(lengths[0]) if len(lengths) == 1 else -1
            if not 0 < length <= 8192:
                return self.reject_role(413, '自动换角请求须小于 8 KiB')
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('请求尚未完整接收')
            value = json.loads(raw, object_pairs_hook=_unique)
            if type(value) is not dict or set(value) != routes[self.path]:
                raise ValueError('自动换角请求字段不完整')
            action = self.path.rsplit('/', 1)[1]
            result = getattr(self.server.shell_runtime, action)(**value)
            self.json(200, result)
        except RuntimeConflict as error:
            self.json(409, dict(error=str(error), state=self.server.shell_runtime.state()))
        except RoleStorageError as error:
            self.json(503, dict(error=str(error)))
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            self.json(422, dict(error=str(error)))
        except OSError:
            self.close_connection = True
            self.json(503, dict(error='本次操作暂未完成，请读取运行状态后重试'))
