"""Same-origin HTTP routes for the host shell workbench."""
import json
from urllib.parse import parse_qs, urlsplit

from protocol.shell_repeatability import MAX_LOG_BYTES
from protocol.shell_workbench import SessionConflict
from protocol.studio_usb_session import _unique


class ShellRoutes:
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != '/api/shells/state':
            return super().do_GET()
        if not self.allowed():
            return
        try:
            query = parse_qs(url.query, keep_blank_values=True)
            if set(query) - {'session_id'} or any(len(v) != 1 for v in query.values()):
                raise ValueError('项目查询参数无效')
            self.json(200, self.server.shells.state(query.get('session_id', [None])[0]))
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.json(422, dict(error=str(error)))

    def do_POST(self):
        routes = {
            '/api/shells/create': ('name', 'device_id', 'context', 'shell_names'),
            '/api/shells/prepare': ('session_id', 'revision', 'layout_fixed', 'frame_confirmed', 'unknown_reference'),
            '/api/shells/import': ('session_id', 'revision', 'task_id', 'capture_id', 'mount_id', 'log_text'),
            '/api/shells/evaluate': ('session_id', 'revision'),
            '/api/shells/bind': ('session_id', 'revision', 'shell_id', 'role_id'),
            '/api/shells/rehearse': ('session_id', 'revision'),
            '/api/shells/example': (),
        }
        if self.path not in routes:
            return super().do_POST()
        if not self.allowed():
            return
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'
                or self.headers.get('X-DeskBot-Studio') != 'shells-v1'
                or self.headers.get('Transfer-Encoding')):
            self.close_connection = True
            return self.json(415, dict(error='请使用外壳工作台提交'))
        try:
            sizes = self.headers.get_all('Content-Length', [])
            length = int(sizes[0]) if len(sizes) == 1 else -1
            maximum = 6 * MAX_LOG_BYTES + 4096 if self.path.endswith('/import') else 8192
            if not 0 < length <= maximum:
                self.close_connection = True
                return self.json(413, dict(error='本次提交超过大小限制'))
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('提交尚未完整接收')
            value = json.loads(raw, object_pairs_hook=_unique)
            if type(value) is not dict or set(value) != set(routes[self.path]):
                raise ValueError('项目提交字段不完整')
            action = self.path.rsplit('/', 1)[1]
            store = self.server.shells
            if action == 'create':
                result = store.create(**value)
            elif action == 'example':
                result = store.example()
            else:
                ident, revision = value.pop('session_id'), value.pop('revision')
                if action == 'import':
                    result = store.import_window(ident, revision, **value)
                elif action == 'prepare':
                    result = store.prepare(ident, revision, **value)
                elif action == 'evaluate':
                    result = store.evaluate(ident, revision)
                elif action == 'bind':
                    result = store.bind(ident, revision, **value)
                else:
                    result = store.rehearse(ident, revision, control=self.server.bridge.control())
            self.json(200, result)
        except SessionConflict as error:
            self.json(409, dict(error=str(error)))
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            self.json(422, dict(error=str(error)))
        except OSError:
            self.close_connection = True
            self.json(503, dict(error='项目暂未完整保存，请刷新后重试'))
