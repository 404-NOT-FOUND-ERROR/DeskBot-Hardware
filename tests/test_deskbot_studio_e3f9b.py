"""Production HTTP generate/download/unpack integration with zero device commands."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from tests.test_deskbot_studio_e3f8 import module

ROOT = Path(__file__).resolve().parents[1]


class UnifiedPackageHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = module('unified_package_http', 'scripts/serve-deskbot-studio-e3f9b.py')
        cls.helpers = module('unified_package_fake', 'tests/test_deskbot_studio_e3e.py')
        cls.manifest = json.loads((ROOT/'artifacts/studio-resources-e3f4d/expected-directory.json').read_bytes())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'tmp', prefix='role-package-http-')
        self.addCleanup(self.temp.cleanup)
        self.bridge = self.helpers.FakeBridge(deepcopy(self.manifest))
        self.server = self.service.StudioServer(0, bridge=self.bridge, output=self.temp.name,
            state_path=Path(self.temp.name)/'active.json', package_output=Path(self.temp.name)/'packages')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = build_opener(ProxyHandler({}))

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def request(self, path, value=None, raw=None, headers=None):
        if value is not None:
            raw = json.dumps(value).encode()
        if raw is not None and headers is None:
            headers = {'Content-Type':'application/json','X-DeskBot-Studio':'e3f9b'}
        try:
            with self.opener.open(Request(self.origin+path, data=raw, headers=headers or {}), timeout=10) as response:
                body = response.read()
                return response.status, json.loads(body) if response.headers['Content-Type'].startswith('application/json') else body
        except HTTPError as error:
            return error.code, json.load(error)

    def test_actual_http_build_download_and_unpack_no_device_access(self):
        status, body = self.request('/api/role-package/catalog')
        self.assertEqual(status, 200)
        draft = body['draft']
        draft['roles'][0]['bindings']['happy'].update(expression_id='eat.eaf', fps=7, loop=False)
        status, report = self.request('/api/role-package/generate', dict(draft=draft))
        self.assertEqual(status, 200, report)
        for name, url in report['downloads'].items():
            code, downloaded = self.request(url)
            self.assertEqual(code, 200, name)
            if name == 'assets-8m.bin':
                self.assertEqual(self.service.digest(downloaded), report['image_sha256'])
            if name == 'role-draft.json':
                self.assertEqual(downloaded, draft)
        self.assertEqual((self.bridge.control_calls, self.bridge.commands), (0, []))
        self.assertFalse((Path(self.temp.name)/'active.json').exists())
        self.assertEqual(self.request('/api/health')[1]['edition'], 'E3f-9b')

    def test_input_limits_duplicate_keys_and_invalid_draft(self):
        self.assertEqual(self.request('/api/role-package/generate', raw=b'x'*32769)[0], 413)
        self.assertEqual(self.request('/api/role-package/generate', raw=b'{"draft":{},"draft":{}}')[0], 422)
        self.assertEqual(self.request('/api/role-package/generate', dict(draft={}, output='../'))[0], 422)
        self.assertEqual(self.request('/api/role-package/generate', dict(draft={}))[0], 422)

    def test_same_origin_and_download_path_boundaries(self):
        self.assertEqual(self.request('/api/role-package/catalog', headers={'Host':'localhost:999'})[0], 403)
        headers={'Content-Type':'application/json','X-DeskBot-Studio':'e3f9b','Origin':'https://elsewhere.example'}
        self.assertEqual(self.request('/api/role-package/generate', dict(draft={}), headers=headers)[0], 403)
        self.assertEqual(self.request('/api/role-package/generate', dict(draft={}), headers={'Content-Type':'text/plain'})[0], 415)
        for path in ('/role-packages/../index.html', '/role-packages/'+('0'*64)+'/checksums.json', '/role-packages/bad/role-draft.json'):
            self.assertEqual(self.request(path)[0], 404)

    def test_workbench_is_the_new_editor_and_publisher(self):
        code, html = self.request('/')
        self.assertEqual(code, 200)
        for needle in ('E3f·9b','DeskBotRolePackageE3f9b','role-package-slot','unifiedPackage.publishView()'):
            self.assertIn(needle.encode(), html)
        self.assertEqual((self.bridge.control_calls, self.bridge.commands), (0, []))


if __name__ == '__main__':
    unittest.main()
