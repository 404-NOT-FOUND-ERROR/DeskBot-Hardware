"""Exercise HTTP parsing, real decoding/builds, revision and delivery boundaries."""
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode

sys.path.insert(0,str(Path.home()/'AppData/Local/DeskBot/expression-runtime/site-packages'))
from protocol.expression_pipeline import ExpressionProjects,ROOT
from protocol.expression_pipeline_http import ExpressionRoutes
from tests.test_expression_pipeline import sample_gif, RUN


class Handler(ExpressionRoutes,BaseHTTPRequestHandler):
    def allowed(self):return True
    def log_message(self,*args):pass
    def reply(self,status,raw,content_type='application/json',filename=None):
        self.send_response(status);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def json(self,status,value):return self.reply(status,json.dumps(value).encode())


class ExpressionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=ExpressionProjects(self.temp.name,
            ROOT/'artifacts/layered-face-20261124-r8',ROOT/'tmp/layered-face-live-20261124-insert')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.expression_projects=self.store
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.store.close();self.temp.cleanup()

    def request(self,path,value=None,raw=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        body=json.dumps(value).encode() if value is not None else raw
        method='POST' if body is not None else 'GET'
        default={'X-DeskBot-Studio':'expression-pipeline-v1','Content-Type':'application/json'}
        if headers is not None:default=headers
        connection.request(method,'/api/expressions/'+path,body=body,headers=default)
        result=connection.getresponse();status=result.status;data=result.read();connection.close()
        return status,data

    def test_invalid_upload_and_request_types_return_errors(self):
        status,data=self.request('create',{'name':'http'});self.assertEqual(status,200);p=json.loads(data)
        query='upload?'+urlencode({'project':p['id'],'revision':1,'name':'broken.gif'})
        self.assertEqual(self.request(query,raw=b'not a GIF')[0],422)
        self.assertEqual(self.request('create',{'name':'bad'},headers={'Content-Type':'application/json'})[0],415)
        self.assertEqual(self.request('bind',raw=b'{"ident":"x","ident":"y"}')[0],422)
        self.assertEqual(self.request('file?'+urlencode({'project':'../escape','revision':1,'name':'xiaozhi.bin'}))[0],404)

    @unittest.skipUnless((RUN/'plan.json').is_file(), 'requires local installed App/resource backup')
    def test_real_upload_build_download_and_reject_stale_or_incomplete_files(self):
        _,raw=self.request('create',{'name':'http'});p=json.loads(raw)
        incomplete=self.store.folder(p['id'])/'build-1';incomplete.mkdir();(incomplete/'xiaozhi.bin').write_bytes(b'partial')
        path=lambda revision,name:'file?'+urlencode({'project':p['id'],'revision':revision,'name':name})
        self.assertEqual(self.request(path(1,'xiaozhi.bin'))[0],404)
        query='upload?'+urlencode({'project':p['id'],'revision':1,'name':'../../safe.gif'})
        status,raw=self.request(query,raw=sample_gif());self.assertEqual(status,200);p=json.loads(raw)
        self.assertEqual(p['assets'][0]['name'],'safe.gif')
        asset=p['assets'][0]['id']
        bind={'ident':p['id'],'revision':2,'semantic':'happy','asset':asset,'mirror':True,'fps':25}
        status,raw=self.request('bind',bind);self.assertEqual(status,200);p=json.loads(raw)
        self.assertEqual(self.request('bind',bind)[0],422)
        self.assertEqual(self.request('build',{'ident':p['id'],'revision':3})[0],200)
        self.store.worker.join(timeout=90);self.assertEqual(self.store.job['status'],'completed',self.store.job)
        for name in ('delivery.json','deskbot-expression.zip','face-motion.dfm'):
            self.assertEqual(self.request(path(3,name))[0],200)
        self.assertEqual(self.request(path(2,'xiaozhi.bin'))[0],404)
        self.assertEqual(self.request(path(3,'../project.json'))[0],404)
        output=self.store.folder(p['id'])/'build-3/xiaozhi.bin';output.write_bytes(b'tampered')
        self.assertEqual(self.request(path(3,'xiaozhi.bin'))[0],404)
        bind.update(revision=3,mirror=False);self.assertEqual(self.request('bind',bind)[0],200)
        self.assertEqual(self.request(path(3,'deskbot-expression.zip'))[0],404)


if __name__=='__main__':unittest.main()
