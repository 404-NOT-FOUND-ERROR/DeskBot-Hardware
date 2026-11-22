"""Production overview HTTP with an injected bridge; no USB process exists."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from scripts.archived_products import product_matches
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from protocol import studio_resource_package as packages

ROOT=Path(__file__).resolve().parents[1]


def load(name, path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class OverviewHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service=load('e3f2_http','scripts/serve-deskbot-studio-e3f2.py')
        cls.helpers=load('e3f2_http_helpers','tests/test_deskbot_studio_e3e.py')
        cls.catalog=packages.load_catalog()
        cls.manifest=json.loads((ROOT/'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json').read_bytes())

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='e3f2-http-',dir=ROOT/'tmp')
        self.addCleanup(self.temp.cleanup)
        self.bridge=self.helpers.FakeBridge(deepcopy(self.manifest))
        self.server=self.service.make_server(0,local=False,output=self.temp.name,bridge=self.bridge)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        self.opener=build_opener(ProxyHandler({}))
        self.value=dict(draft=deepcopy(self.catalog['initial']),source='device',context=self.bridge.context,
                        request_id='overview-http')

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def post(self, value=None, *, raw=None, headers=None, path='/api/role-expression/overview'):
        request=Request(self.origin+path,data=raw if raw is not None else packages.canonical(value or self.value),
                        headers=headers or {'Content-Type':'application/json','X-DeskBot-Studio':'e3f2'})
        try:
            with self.opener.open(request,timeout=5) as response:
                return response.status,json.load(response)
        except HTTPError as error:
            return error.code,json.load(error)

    def test_all_semantics_share_one_state_read_and_emit_no_commands(self):
        status,report=self.post()
        self.assertEqual((status,report['total']),(200,29))
        self.assertEqual(self.bridge.control_calls,1)
        self.assertEqual(report['counts']['ready'],1)
        self.assertEqual(self.bridge.commands,[])
        self.assertTrue(all('command' not in row for row in report['entries']))

    def test_saved_snapshot_does_not_read_device(self):
        self.value['source']='saved_snapshot'
        status,report=self.post()
        self.assertEqual((status,report['counts']['matched']),(200,28))
        self.assertEqual(self.bridge.control_calls,0)
        self.assertEqual(self.bridge.commands,[])

    def test_single_expression_route_and_explicit_command_are_preserved(self):
        request={**self.value,'semantic':'neutral'}
        status,result=self.post(request,headers={'Content-Type':'application/json','X-DeskBot-Studio':'e3e'},
                                path='/api/role-expression/resolve')
        self.assertEqual(status,200)
        self.assertEqual(self.bridge.commands,[])
        status,_=self.post(result['command'],headers={'Content-Type':'application/json','X-DeskBot-Studio':'e3c6'},
                           path='/api/studio/command')
        self.assertEqual(status,200)
        self.assertEqual(len(self.bridge.commands),1)

    def test_wrong_origin_header_or_oversize_never_reads_bridge(self):
        good={'Content-Type':'application/json','X-DeskBot-Studio':'e3f2'}
        self.assertEqual(self.post(headers={**good,'Origin':'https://example.org'})[0],403)
        self.assertEqual(self.post(headers={**good,'X-DeskBot-Studio':'wrong'})[0],415)
        self.assertEqual(self.post(raw=b'x'*(packages.MAX_DRAFT_BYTES+1))[0],413)
        self.assertEqual(self.bridge.control_calls,0)

    def test_malformed_duplicate_and_extra_fields_are_rejected(self):
        self.assertEqual(self.post(raw=b'{')[0],422)
        self.assertEqual(self.post(raw=b'{"source":"device","source":"saved_snapshot"}')[0],422)
        self.assertEqual(self.post({**self.value,'command':{}})[0],422)
        self.assertEqual(self.post({**self.value,'source':'candidate_device'})[0],422)
        self.assertEqual(self.bridge.commands,[])

    def test_incomplete_directory_and_bad_digest_are_distinct(self):
        self.bridge.state['catalog']['status']='reading'
        status,report=self.post()
        self.assertEqual((status,report['counts']['waiting']),(200,29))
        self.bridge.state['catalog']['status']='complete'
        self.bridge.state['catalog']['manifest']['catalog_id']='f'*64
        self.assertEqual(self.post()[0],422)

    def test_page_and_health_identify_new_module(self):
        with self.opener.open(self.origin+'/api/health',timeout=5) as response:
            health=json.load(response)
        self.assertEqual((health['edition'],health['role_overview']),('E3f-2',True))
        with self.opener.open(self.origin+'/',timeout=5) as response:
            page=response.read().decode('utf-8')
        self.assertIn('E3f·2',page)
        self.assertIn('role-overview-slot',page)

    @unittest.skipUnless(packages.BASE_IMAGE.is_file(), 'private resource image absent')
    def test_public_and_local_products_reproduce(self):
        builder=load('e3f2_builder_tests','scripts/build-deskbot-studio-e3f2.py')
        for local,folder in ((False,builder.PUBLIC),(True,builder.LOCAL)):
            for name,raw in builder.products(local).items():
                self.assertTrue(product_matches((folder/name).read_bytes(),raw,builder.PUBLIC/name,local=local),name)


if __name__=='__main__':
    unittest.main()
