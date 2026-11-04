from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from protocol.body_console_observation import Observation

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


B = module('e3a_builder_test', 'scripts/build-deskbot-studio-e3a.py')
S = module('e3a_server_test', 'scripts/serve-deskbot-studio-e3a.py')


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.products = B.products()

    def test_public_build_reproduces_and_keeps_saved_catalog(self):
        for name, raw in self.products.items():
            self.assertEqual((B.PUBLIC / name).read_bytes(), raw, name)
        catalog = json.loads(self.products['expression-catalog.json'])
        self.assertFalse(catalog['live'])
        self.assertEqual(catalog['source'], 'saved_release_record')
        self.assertEqual(catalog['revision'], 'E1.18')

    def test_live_panel_and_simulator_remain_separate(self):
        html = self.products['index.html'].decode()
        self.assertIn('id="body-observer-slot"', html)
        self.assertIn('class="e3a-simulator"', html)
        self.assertIn('observation:()=>body.state()', html)
        self.assertIn('simulation:()=>simulator.state()', html)
        self.assertIn('data-view="editor"', html)
        self.assertIn('data-view="publish"', html)

    def test_report_records_firmware_unchanged(self):
        report = json.loads(self.products['report.json'])
        self.assertEqual(report['edition'], 'E3a')
        self.assertFalse(report['firmware_changed'])
        self.assertFalse(report['hardware_accessed'])
        self.assertFalse(report['observation']['device_controls'])
        # Hardware evidence is recorded separately from the reproducible build.
        self.assertNotIn('live-validation.json', self.products)
        live = json.loads((B.PUBLIC / 'live-validation.json').read_text(encoding='utf-8'))
        self.assertTrue(live['hardware_accessed'])
        self.assertFalse(live['serial_commands'])
        self.assertFalse(live['device_written'])
        self.assertFalse(live['nvs_changed'])
        self.assertFalse(live['observation']['application_state_change_observed'])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='e3a-http-')
        self.server = S.make_server(0, local=False, output=self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def call(self, path, value=None, headers=None):
        args = {'headers': headers or {}}
        if value is not None:
            args['data'] = json.dumps(value).encode()
            args['headers'] = {'Content-Type':'application/json','X-DeskBot-Studio':'e3a', **(headers or {})}
        try:
            response = urlopen(Request(self.origin + path, **args), timeout=10)
        except HTTPError as e:
            response = e
        with response:
            return response.status, response.read()

    def test_new_page_and_old_packager_served(self):
        self.assertEqual(self.call('/')[1], (B.PUBLIC / 'index.html').read_bytes())
        status, raw = self.call('/api/health')
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(data['service'], 'deskbot.studio.packager/v1')
        self.assertEqual(data['edition'], 'E3a')
        self.assertFalse(data['hardware_accessed'])
        self.assertFalse(data['device_controls'])

    def test_state_get_never_opens_a_device(self):
        for _ in range(3):
            status, raw = self.call('/api/body/state')
            self.assertEqual(status, 200)
            data = json.loads(raw)
            self.assertEqual(data['freshness'], 'disabled')
            self.assertFalse(data['hardware_accessed'])
        self.assertIsNone(self.server.bridge.process)

    def test_ports_without_runtime_and_connect_failure_are_explicit(self):
        status, raw = self.call('/api/body/ports')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)['available'])
        self.assertEqual(self.call('/api/body/connect', {'port':'COM4','key':'a'*64})[0], 422)

    def test_origins_headers_and_parameter_scope(self):
        self.assertEqual(self.call('/api/body/state', headers={'Origin':'https://other.example'})[0], 403)
        self.assertEqual(self.call('/api/body/disconnect', {}, {'X-DeskBot-Studio':'other'})[0], 415)
        self.assertEqual(self.call('/api/body/disconnect', {'command':'reset'})[0], 422)
        self.assertEqual(self.call('/api/body/connect', {'port':'COM4','key':'a'*64,'reset':True})[0], 422)
        self.assertEqual(self.call('/api/body/connect', {'port':'x'*1200,'key':'a'*64})[0], 422)

    def test_no_control_route(self):
        for path in ('/api/body/command','/api/body/flash','/api/body/reset','/api/expression','/api/flash'):
            self.assertEqual(self.call(path,{})[0],404)

    def test_disconnect_is_idempotent_and_does_not_mark_hardware_used(self):
        for _ in range(2):
            status, raw = self.call('/api/body/disconnect', {})
            self.assertEqual(status,200)
            self.assertFalse(json.loads(raw)['hardware_accessed'])


if __name__ == '__main__':
    unittest.main()
