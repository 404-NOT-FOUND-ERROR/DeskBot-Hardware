"""Offline E3d-2 page generation and HTTP bridge checks."""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from scripts.archived_products import product_matches
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from protocol import studio_resource_package as packages
from scripts.publication_evidence import SourceReview

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "artifacts/deskbot-studio-e3d2-20261105"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class WorkbenchTests(unittest.TestCase):
    def test_public_page_contains_both_sources_and_existing_control(self):
        html = (PUBLIC / "index.html").read_text(encoding="utf-8")
        for anchor in ('id="live-directory-slot"', 'id="installed-directory-slot"', 'id="studio-control-slot"'):
            self.assertEqual(html.count(anchor), 1, anchor)
        data = json.loads(re.search(r'<script id="studio-data" type="application/json">(.*?)</script>', html, re.S)[1])
        self.assertFalse(data["installed_directory"]["live"])
        self.assertEqual(data["installed_directory"]["usb"]["supported_expression_ids"], ["neutral"])
        self.assertIn("DeskBotStudioE3D2UI.create", html)
        self.assertIn("liveDirectory:()=>liveDirectory.state()", html)

    @unittest.skipUnless(packages.BASE_IMAGE.is_file(), "private saved resource image absent")
    def test_public_and_local_outputs_reproduce(self):
        builder = module("e3d2_generation_test", "scripts/build-deskbot-studio-e3d2.py")
        for local, folder in ((False, builder.PUBLIC), (True, builder.LOCAL)):
            if local and not folder.exists():
                continue
            for name, raw in builder.products(local).items():
                saved = (folder / name).read_bytes()
                self.assertTrue(product_matches(saved, raw, PUBLIC / name, local=local), name)

    def test_service_starts_without_opening_serial_and_returns_directory_status(self):
        server_module = module("e3d2_service_test", "scripts/serve-deskbot-studio-e3d2.py")
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="e3d2-http-", dir=ROOT / "tmp") as temp:
            server = server_module.make_server(0, local=False, output=temp)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                opener = build_opener(ProxyHandler({}))
                with opener.open(origin + "/", timeout=5) as response:
                    self.assertIn(b"live-directory-slot", response.read())
                with opener.open(origin + "/api/studio/state", timeout=5) as response:
                    state = json.load(response)
                    self.assertEqual(state["catalog"]["status"], "disconnected")
                    self.assertIsNone(state["catalog"]["manifest"])
                    self.assertFalse(state["enabled"])
                with opener.open(origin + "/api/health", timeout=5) as response:
                    health = json.load(response)
                    self.assertEqual(health["edition"], "E3d-2")
                    self.assertFalse(health["hardware_accessed"])
                self.assertIsNone(server.bridge.process)
                with self.assertRaises(HTTPError) as caught:
                    opener.open(Request(origin + "/api/studio/state", headers={"Origin": "https://example.org"}), timeout=5)
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
