"""Acceptance entry and current-clock original client; no physical USB access."""
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from protocol.client_presentation import ClientHTTP, ClientHTTPError, ClientPresentationService, timestamp
from protocol.client_presentation_ledger import PresentationLedger
from protocol.studio_control_link import initial_snapshot
from tests.client_presentation_fixture import MemoryPresentationOwner, DEVICE
from tests.test_shell_workbench import service_module

ROOT=Path(__file__).resolve().parents[1]


class AcceptanceHTTPTests(unittest.TestCase):
    def test_json_and_non_json_http_errors_preserve_status(self):
        client=ClientHTTP('http://127.0.0.1:1')
        for raw,expected in [(b'<html>Unavailable</html>',{}),(b'{"error":"busy"}',{'error':'busy'})]:
            client.opener=Mock()
            client.opener.open.side_effect=HTTPError(client.base_url,503,'unavailable',{},BytesIO(raw))
            with self.assertRaises(ClientHTTPError) as raised:client.request('POST','/api/chat',{})
            self.assertEqual((raised.exception.status,raised.exception.body),(503,expected))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='4b1b-http-')
        self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name)
        bridge=type('MemoryBridge',(),{'control':lambda _:initial_snapshot(),'close':lambda _:None})()
        self.server=service_module().StudioServer(0,bridge=bridge,state_path=folder/'active.json',
            shell_root=folder/'shells',profiles_path=folder/'characters.json')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start();self.addCleanup(self.stop)
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        self.opener=build_opener(ProxyHandler({}))

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)

    def test_acceptance_page_uses_original_origin_gate(self):
        with self.opener.open(self.origin+'/client-expression') as response:
            self.assertIn('text/html',response.headers['Content-Type'])
            page=response.read().decode()
            self.assertIn('client-presentation-v1',page)
            self.assertIn('客户端保存这次交流',page)
        for headers in [{'Origin':'http://example.com'},{'Host':'example.com'}]:
            with self.assertRaises(HTTPError) as raised:
                self.opener.open(Request(self.origin+'/client-expression',headers=headers))
            self.assertEqual(raised.exception.code,403)

    def test_chat_uses_existing_service_and_rejects_wrong_fields(self):
        calls=[]
        class Service:
            def chat(self,**body):calls.append(body);return dict(accepted=True,turn=dict(reply='收到'))
            def state(self):return dict(schema='deskbot.client_presentation/v1')
            def close(self):pass
        self.server.client_presentation=Service()
        body=dict(event_id='acceptance-1',correlation_id='correlation-1',message='你好')
        headers={'Content-Type':'application/json','X-DeskBot-Studio':'client-presentation-v1'}
        def send(value,custom=headers):
            return self.opener.open(Request(self.origin+'/api/client-presentation/chat',
                data=json.dumps(value).encode(),headers=custom))
        with send(body) as response:self.assertTrue(json.load(response)['accepted'])
        self.assertEqual(calls,[body])
        for value,custom,code in [(dict(body,device_id='other'),headers,409),
                                  (body,dict(headers,Origin='http://example.com'),403)]:
            with self.assertRaises(HTTPError) as raised:send(value,custom)
            self.assertEqual(raised.exception.code,code)
        self.assertEqual(calls,[body])


@unittest.skipUnless(os.environ.get('DESKBOT_CLIENT_CHECKOUT') and os.environ.get('DESKBOT_NODE'),
                     'Set the pinned client and Node runtime')
class OriginalAcceptanceRuntimeTests(unittest.TestCase):
    def test_real_launcher_current_clock_chat_sqlite_and_receipt(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='4b1b-launch-') as directory:
            folder=Path(directory)
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            process=subprocess.Popen([os.environ['DESKBOT_NODE'],str(ROOT/'scripts/serve-client-expression-4b1b.mjs'),
                '--client-root',os.environ['DESKBOT_CLIENT_CHECKOUT'],'--database',str(folder/'client.sqlite'),
                '--port',str(port)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            ledger=PresentationLedger(folder/'ledger.sqlite')
            try:
                line=process.stdout.readline();self.assertTrue(line,'Acceptance runtime did not start')
                runtime=json.loads(line);self.assertEqual(runtime['mode'],'fixed_reply')
                client=ClientHTTP(runtime['url']);owner=MemoryPresentationOwner()
                service=ClientPresentationService(owner,client,ledger,device_id=DEVICE)
                before=time.time()
                for event,message,semantic in [('greet','你好，和你打个招呼。','neutral'),
                                               ('happy','今天做饭成功了，我很开心，想和你分享！','happy')]:
                    response=service.chat(event,'correlation-'+event,message)
                    self.assertTrue(response['accepted'])
                    self.assertIsInstance(response['turn']['reply'],str)
                    service.tick();service.tick()
                    row=next(r for r in ledger.all('command') if r['command']['source_event_id']=='reply-'+event)
                    self.assertEqual(row['command']['payload']['expression'],semantic)
                    self.assertGreaterEqual(timestamp(row['command']['queued_at']),before-1)
                    self.assertEqual(row['phase'],'acknowledged')
                    self.assertEqual(row['ack']['status'],'completed')
                self.assertEqual(len(owner.sent),2)
                self.assertTrue((folder/'client.sqlite').is_file())
                service.close()
            finally:
                ledger.close();process.terminate();process.communicate(timeout=15)


if __name__=='__main__':unittest.main()
