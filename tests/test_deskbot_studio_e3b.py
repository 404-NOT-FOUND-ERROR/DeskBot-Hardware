from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


B = module('e3b_builder_test', 'scripts/build-deskbot-studio-e3b.py')
S = module('e3b_server_test', 'scripts/serve-deskbot-studio-e3b.py')
E3A = module('e3b_previous_service_tests', 'tests/test_deskbot_studio_e3a.py')


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.products = B.products()

    def test_public_products_reproduce(self):
        for name, raw in self.products.items():
            self.assertEqual((B.PUBLIC / name).read_bytes(), raw, name)

    @unittest.skipUnless((B.LOCAL / 'index.html').is_file(), 'local preview media is private')
    def test_local_products_reproduce(self):
        for name, raw in B.products(True).items():
            self.assertEqual((B.LOCAL / name).read_bytes(), raw, name)

    def test_saved_catalog_is_not_device_confirmed(self):
        catalog = json.loads(self.products['expression-catalog.json'])
        self.assertFalse(catalog['live'])
        self.assertEqual(catalog['revision'], 'E1.18')
        self.assertEqual(catalog['source'], 'saved_release_record')
        report = json.loads(self.products['report.json'])
        self.assertEqual(report['edition'], 'E3b')
        self.assertFalse(report['device_link']['catalog_confirmed'])
        self.assertFalse(report['device_link']['expression_controls'])
        self.assertTrue(report['firmware_changed'])
        self.assertFalse(report['device_written'])
        self.assertFalse(report['hardware_accessed'])

    def test_identity_panel_preserves_editor_and_simulation(self):
        html = self.products['index.html'].decode()
        self.assertIn('const body=DeskBotStudioE3BUI.create({})', html)
        self.assertIn('认识连接的桌宠', html)
        self.assertIn('身份与版本', html)
        self.assertIn('表情目录', html)
        self.assertIn('observation:()=>body.state()', html)
        self.assertIn('simulation:()=>simulator.state()', html)
        self.assertIn('data-view="editor"', html)
        self.assertIn('data-view="publish"', html)
        self.assertNotIn('observer.observe(document.getElementById', html)


class ServiceTests(E3A.ServiceTests):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='e3b-http-')
        self.server = S.make_server(0, local=False, output=self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def test_new_page_and_old_packager_served(self):
        self.assertEqual(self.call('/')[1], (B.PUBLIC / 'index.html').read_bytes())
        status, raw = self.call('/api/health')
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data['service'], 'deskbot.studio.packager/v1')
        self.assertEqual(data['edition'], 'E3b')
        self.assertEqual(data['identity_status'], 'disconnected')
        self.assertFalse(data['hardware_accessed'])
        self.assertFalse(data['device_controls'])

    def test_identity_snapshot_uses_v2_and_inactive_runtime(self):
        status, raw = self.call('/api/body/state')
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data['schema'], 'deskbot.body_observation/v2')
        self.assertEqual(data['identity']['status'], 'disconnected')
        self.assertIsNone(data['identity']['hello'])
        self.assertEqual(self.server.bridge.worker.name, 'observe-studio-body-e3b.py')
        self.assertIsNone(self.server.bridge.process)


if __name__ == '__main__':
    unittest.main()
