"""Local character configuration endpoints; preview uses static media only."""
import json
from urllib.parse import urlsplit

from protocol.character_profiles import CharacterConflict
from protocol.role_activation import RoleStorageError
from protocol.studio_usb_session import _unique


class CharacterRoutes:
    def do_GET(self):
        if urlsplit(self.path).path != '/api/characters/state':
            return super().do_GET()
        if self.allowed():
            self.json(200, self.server.characters.snapshot())

    def do_POST(self):
        routes = {'/api/characters/save': {'profile', 'expected_revision'},
                  '/api/characters/migrate': {'legacy_profile', 'expression_role_id'}}
        if self.path not in routes:
            return super().do_POST()
        if (self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                or self.headers.get('Origin', self.origin) != self.origin):
            return self.reject_role(403, '请从本机角色管理页面打开')
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'
                or self.headers.get('X-DeskBot-Studio') != 'characters-v1'
                or self.headers.get('Transfer-Encoding')):
            return self.reject_role(415, '请从角色管理页面保存')
        try:
            lengths = self.headers.get_all('Content-Length', [])
            length = int(lengths[0]) if len(lengths) == 1 else -1
            if not 0 < length <= 65536:
                # The inherited bounded drain preserves rejection replies on Windows sockets.
                return self.reject_role(413, '角色提交须小于 64 KiB')
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('角色提交尚未完整接收')
            value = json.loads(raw, object_pairs_hook=_unique)
            if type(value) is not dict or set(value) != routes[self.path]:
                raise ValueError('角色提交字段不完整')
            action = self.path.rsplit('/', 1)[1]
            self.json(200, getattr(self.server.characters, action)(**value))
        except CharacterConflict as error:
            self.json(409, dict(error=str(error), state=self.server.characters.snapshot()))
        except RoleStorageError as error:
            self.json(503, dict(error=str(error)))
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            self.json(422, dict(error=str(error)))
        except OSError:
            self.close_connection = True
            self.json(503, dict(error='角色保存暂未完成，请保留草稿后重试'))
