"""Optional localhost text ingress in the existing workbench process."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from protocol.client_presentation import ClientHTTPError
from protocol.studio_usb_session import _unique


class ClientPresentationRoutes:
    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in ('/api/client-presentation/state', '/client-expression'):
            return super().do_GET()
        if not self.allowed():
            return
        if path == '/client-expression':
            page = Path(__file__).resolve().parents[1] / 'scripts/client-expression-4b1b.html'
            return self.reply(200, page.read_bytes(), 'text/html; charset=utf-8')
        service = self.server.client_presentation
        self.json(200, service.state() if service else dict(status='not_configured'))

    def do_POST(self):
        if self.path != '/api/client-presentation/chat':
            return super().do_POST()
        if (self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                or self.headers.get('Origin', self.origin) != self.origin):
            return self.reject_role(403, '请从本机工作台发起')
        if (self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'
                or self.headers.get('X-DeskBot-Studio') != 'client-presentation-v1'
                or self.headers.get('Transfer-Encoding')):
            return self.reject_role(415, '请使用工作台文字接口')
        service = self.server.client_presentation
        if not service:
            return self.json(503, dict(error='client_not_configured'))
        try:
            lengths = self.headers.get_all('Content-Length', [])
            length = int(lengths[0]) if len(lengths) == 1 else -1
            if not 0 < length <= 32768:
                return self.json(413, dict(error='chat_too_large'))
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('incomplete_chat')
            body = json.loads(raw, object_pairs_hook=_unique)
            if type(body) is not dict or set(body) != {'event_id', 'correlation_id', 'message'}:
                raise ValueError('chat_fields_required')
            self.json(200, service.chat(**body))
        except ClientHTTPError as error:
            self.json(error.status, error.body)
        except (ValueError, TypeError, KeyError) as error:
            self.json(409, dict(error=str(error)))
        except OSError:
            self.json(503, dict(error='client_unavailable'))
