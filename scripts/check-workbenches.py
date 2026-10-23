#!/usr/bin/env python3
"""Static JS/JSON/link checks and loopback HTTP smoke; not browser QA."""
from __future__ import annotations

from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.parse import unquote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    "artifacts/expression-node-workbench-e1-4d-20261021/index.html",
    "artifacts/expression-binding-matrix-e1-9-20261022/index.html",
    "artifacts/expression-e1-10-review-20261022/index.html",
)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "href" in attributes:
            self.links.append(attributes["href"])
        if tag == "script":
            if "src" in attributes:
                raise ValueError("external script is not allowed")
            self.current = (attributes.get("type", "text/javascript"), [])

    def handle_data(self, data):
        if self.current:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current:
            kind, pieces = self.current
            self.scripts.append((kind, "".join(pieces)))
            self.current = None


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main() -> int:
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for inline JS syntax checks")
    total_js = total_json = 0
    for relative in PAGES:
        page = ROOT / relative
        parser = PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        for link in parser.links:
            if ":" in link or link.startswith("#"):
                continue
            if not (page.parent / unquote(link.split("#", 1)[0])).exists():
                raise ValueError(f"broken visualization link: {relative} -> {link}")
        for kind, script in parser.scripts:
            if kind == "application/json":
                json.loads(script)
                total_json += 1
            else:
                result = subprocess.run(
                    [node, "-e", "new (require('node:vm').Script)(require('node:fs').readFileSync(0,'utf8'))"],
                    input=script.encode("utf-8"), capture_output=True,
                )
                if result.returncode:
                    raise ValueError(result.stderr.decode("utf-8", errors="replace"))
                total_js += 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for relative in PAGES:
            with urlopen(f"http://127.0.0.1:{server.server_port}/{relative}", timeout=10) as response:
                if response.status != 200 or response.read() != (ROOT / relative).read_bytes():
                    raise ValueError("HTTP response differs: " + relative)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print(f"workbench checks passed: {len(PAGES)} pages, {total_js} JS scripts, {total_json} JSON payloads, loopback HTTP")
    print("No browser rendering, physical playback or hardware acceptance is implied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
