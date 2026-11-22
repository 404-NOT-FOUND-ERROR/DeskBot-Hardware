"""Same-origin owner installation endpoints in the existing workbench."""
import json
from urllib.parse import urlsplit

from protocol.character_profiles import CharacterConflict
from protocol.role_activation import RoleStorageError
from protocol.studio_usb_session import _unique


class ShellInstallationRoutes:
    def installation_state(self):
        state = self.server.shell_installation.state()
        sync = getattr(self.server, 'client_installation', None)
        if sync is not None:
            state['client_sync'] = sync.snapshot(state)
        return state

    def do_GET(self):
        if urlsplit(self.path).path != '/api/shell-installation/state':
            return super().do_GET()
        if not self.allowed():
            return
        try:
            self.json(200, self.installation_state())
        except (ValueError, OSError) as error:
            self.json(503, dict(error=str(error)))

    def do_POST(self):
        routes = {
            '/api/shell-installation/confirm': {'installation_id', 'character_id', 'device_id', 'shell_id',
                'binding_sha256', 'plan_id', 'expected_revision'},
            '/api/shell-installation/retry': {'installation_id', 'expected_revision'},
        }
        if self.path not in routes:
            return super().do_POST()
        if (self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                or self.headers.get('Origin', self.origin) != self.origin):
            return self.reject_role(403, '请从本机外壳页面确认安装')
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'
                or self.headers.get('X-DeskBot-Studio') != 'shell-installation-v1'
                or self.headers.get('Transfer-Encoding')):
            return self.reject_role(415, '请从安装卡片提交')
        try:
            lengths = self.headers.get_all('Content-Length', [])
            length = int(lengths[0]) if len(lengths) == 1 else -1
            if not 0 < length <= 8192:
                return self.reject_role(413, '安装请求须小于 8 KiB')
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('安装请求尚未完整接收')
            value = json.loads(raw, object_pairs_hook=_unique)
            if type(value) is not dict or set(value) != routes[self.path]:
                raise ValueError('安装请求字段不完整')
            action = self.path.rsplit('/', 1)[1]
            getattr(self.server.shell_installation, action)(**value)
            self.json(200, self.installation_state())
        except CharacterConflict as error:
            self.json(409, dict(error=str(error), state=self.installation_state()))
        except RoleStorageError as error:
            self.json(503, dict(error=str(error)))
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            self.json(422, dict(error=str(error)))
        except OSError:
            self.close_connection = True
            self.json(503, dict(error='请重新读取本次安装进度'))
