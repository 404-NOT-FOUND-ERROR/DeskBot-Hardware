"""Local shell registration and memory-device rehearsal routes."""
import json
from urllib.parse import urlsplit

from protocol.character_profiles import CharacterConflict
from protocol.role_activation import RoleStorageError
from protocol.studio_usb_session import _unique


class ShellCharacterRoutes:
    def do_GET(self):
        if urlsplit(self.path).path != '/api/shell-characters/state':
            return super().do_GET()
        if not self.allowed():
            return
        try:
            self.json(200, self.server.shell_characters.state())
        except (ValueError, OSError):
            self.json(503, dict(error='请检查已保存的外壳和采样项目后重试'))

    def do_POST(self):
        routes = {
            '/api/shell-characters/save-shell': {'shell_id', 'name', 'expected_revision'},
            '/api/shell-characters/confirm': {'shell_id', 'role_id', 'profile_revision', 'source_key', 'source_sha256', 'expected_revision'},
            '/api/shell-characters/simulate': {'expected_revision', 'scenario', 'shell_a', 'shell_b'},
            '/api/shell-characters/session-lab': {'expected_revision', 'scenario', 'shell_a', 'shell_b'},
            '/api/shell-characters/replay': {'expected_revision', 'session_id'},
            '/api/shell-characters/replay-session': {'expected_revision', 'session_id'},
        }
        if self.path not in routes:
            return super().do_POST()
        if (self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                or self.headers.get('Origin', self.origin) != self.origin):
            return self.reject_role(403, '请从本机外壳页面打开')
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'
                or self.headers.get('X-DeskBot-Studio') != 'shell-characters-v1'
                or self.headers.get('Transfer-Encoding')):
            return self.reject_role(415, '请从外壳页面保存配置')
        try:
            lengths = self.headers.get_all('Content-Length', [])
            length = int(lengths[0]) if len(lengths) == 1 else -1
            if not 0 < length <= 8192:
                return self.reject_role(413, '外壳提交须小于 8 KiB')
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('外壳提交尚未完整接收')
            value = json.loads(raw, object_pairs_hook=_unique)
            if type(value) is not dict or set(value) != routes[self.path]:
                raise ValueError('外壳提交字段不完整')
            action = self.path.rsplit('/', 1)[1]
            store = self.server.shell_characters
            if action == 'save-shell': result = store.save_shell(**value)
            elif action == 'confirm': result = store.confirm(**value)
            elif action == 'session-lab': result = store.run_session(**value)
            elif action == 'replay-session': result = store.replay_session(**value)
            else: result = store.run(**value)
            self.json(200, result)
        except CharacterConflict as error:
            self.json(409, dict(error=str(error), state=self.server.shell_characters.state()))
        except RoleStorageError as error:
            self.json(503, dict(error=str(error)))
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            self.json(422, dict(error=str(error)))
        except OSError:
            self.close_connection = True
            self.json(503, dict(error='本次外壳操作暂未完成，请保留选择后重试'))
