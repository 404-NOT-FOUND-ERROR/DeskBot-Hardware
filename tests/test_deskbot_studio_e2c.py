from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from protocol import expression_control as control

ROOT = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


B = module("e2c_builder_test", "scripts/build-deskbot-studio-e2c.py")
S = module("e2c_server_test", "scripts/serve-deskbot-studio-e2c.py")


class BuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.products = B.products()

    def test_saved_public_products_are_reproducible(self):
        self.assertEqual(self.products, B.products())
        for name, raw in self.products.items():
            self.assertEqual((B.PUBLIC / name).read_bytes(), raw, name)

    def test_saved_directory_is_separate_from_role_drafts(self):
        catalog = json.loads(self.products["expression-catalog.json"])
        self.assertEqual(control.validate_catalog(catalog)["semantics"], 29)
        self.assertEqual(len(catalog["actions"]), 5)
        self.assertFalse(catalog["live"])
        self.assertEqual(catalog["revision"], "E1.18")
        self.assertEqual(next(row for row in catalog["expressions"] if row["semantic_id"] == "embarrassed")["resource_id"], "Happy.eaf")

    def test_simulator_joins_the_existing_editor_and_publisher(self):
        html = self.products["index.html"]
        self.assertIn(b'data-view="connection"', html)
        self.assertIn(b'data-view="editor"', html)
        self.assertIn(b'data-view="publish"', html)
        self.assertLess(html.index(b'window.DeskBotStudioE2CUI='), html.index(b'const simulator=DeskBotStudioE2CUI.create'))
        self.assertNotIn(b'__STUDIO_', html)
        self.assertTrue(b'package-report-file' in html, "publisher report import is present")
        self.assertTrue(b'data-package-action=\"generate\"' in html, "publisher generation is present")

    def test_report_records_contract_inputs_and_computer_scope(self):
        report = json.loads(self.products["report.json"])
        self.assertEqual(report["edition"], "E2c")
        self.assertFalse(report["hardware_accessed"])
        self.assertFalse(report["device_written"])
        self.assertEqual(report["simulator"]["requestable_expressions"], 28)
        self.assertIn("protocol/deskbot-expression-catalog-v1.schema.json", [row["path"] for row in report["sources"]])


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tmp").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e2c-http-")
        cls.server = S.make_server(0, local=False, output=cls.temp.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def get(self, path, headers=None):
        try:
            response = urlopen(Request(self.origin + path, headers=headers or {}), timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read()

    def test_e2c_page_is_served_on_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, raw = self.get("/")
        self.assertEqual(status, 200)
        self.assertEqual(raw, (B.PUBLIC / "index.html").read_bytes())

    def test_original_packager_health_and_scope_are_preserved(self):
        status, raw = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["hardware_accessed"])
        self.assertEqual(self.get("/api/flash")[0], 404)
        self.assertEqual(self.get("/README.md")[0], 404)

    def test_service_rejects_foreign_origins(self):
        self.assertEqual(self.get("/", {"Origin": "https://other.example"})[0], 403)


if __name__ == "__main__":
    unittest.main()
