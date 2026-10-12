"""Minimal DeskBot text gateway.

The gateway intentionally handles text first. Audio and TTS adapters will be
added after the device-side baseline is compiled and measured.
"""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HOST = os.getenv("DESKBOT_GATEWAY_HOST", "127.0.0.1")
PORT = int(os.getenv("DESKBOT_GATEWAY_PORT", "8787"))
UPSTREAM = os.getenv("DESKBOT_DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DESKBOT_DEEPSEEK_MODEL", "deepseek-v4-flash")


class Handler(BaseHTTPRequestHandler):
    server_version = "DeskBotGateway/0.1"

    def _write_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"ok": True, "service": "deskbot-gateway", "model": MODEL})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/v1/chat/completions":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        api_key = os.getenv("DESKBOT_DEEPSEEK_API_KEY")
        if not api_key:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "DESKBOT_DEEPSEEK_API_KEY is not set"})
            return

        try:
            size = int(self.headers.get("Content-Length", "0"))
            request_body = json.loads(self.rfile.read(size))
        except (ValueError, json.JSONDecodeError):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "request body must be valid JSON"})
            return

        if not isinstance(request_body, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "request body must be an object"})
            return

        request_body.setdefault("model", MODEL)
        upstream_url = UPSTREAM.rstrip("/") + "/chat/completions"
        upstream_request = Request(
            upstream_url,
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(upstream_request, timeout=90) as response:
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                body = response.read()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            self._write_json(error.code, {"error": "upstream_error", "detail": detail})
        except URLError as error:
            self._write_json(HTTPStatus.BAD_GATEWAY, {"error": "upstream_unreachable", "detail": str(error.reason)})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[gateway] {self.address_string()} - {format % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"DeskBot Gateway listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping gateway")
    finally:
        server.server_close()
