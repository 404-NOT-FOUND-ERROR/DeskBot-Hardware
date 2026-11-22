"""Runtime synchronization uses production installations and the original client HTTP/SQLite."""
from pathlib import Path
from http.server import ThreadingHTTPServer
import threading
import unittest

from protocol.client_installation_runtime import ClientInstallationRuntime
from protocol.role_activation import RoleStorageError
from tests import test_client_installation_sync as integration
from tests.test_client_installation_sync import CHARACTER, SOURCE
from tests.test_shell_installation import DEVICE


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.case = integration.ClientInstallationSyncTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.folder = Path(self.case.client.folder)
        self.url = self.case.client.http.url
        self.path = self.folder/'sync.json'
        self.runtime = self.make()
        self.addCleanup(lambda: self.runtime.close())

    def make(self, **options):
        return ClientInstallationRuntime(self.case.f.service, self.path,
            url=self.url, source_id=SOURCE, character_id=CHARACTER,
            device_id=DEVICE, background=False, **options)

    def test_waiting_installation_submits_nothing(self):
        self.runtime.tick()
        self.assertEqual(self.runtime.snapshot()['status'], 'waiting')
        self.assertIsNone(self.case.current()['current'])

    def test_a_b_a_automatic_sync_and_original_life_retained(self):
        world = self.case.client.http.request('GET', '/api/world/state')
        for shell, ident in [('a','runtime-a'),('b','runtime-b'),('a','runtime-return')]:
            self.case.confirm(shell, ident)
            self.runtime.tick()
            state = self.runtime.snapshot()
            self.assertTrue(state['verified'])
            self.assertEqual(state['installation_id'], ident)
            self.assertEqual(state['last_receipt']['installation_id'], ident)
        self.assertEqual(self.case.client.http.request('GET', '/api/world/state'), world)
        self.assertEqual(len(self.case.f.owner.activations), 3)

    def test_reopen_requires_remote_check_and_reuses_original_receipt(self):
        self.case.confirm('a','reopen-a'); self.runtime.tick()
        receipt = self.runtime.snapshot()['last_receipt']
        self.runtime.close(); self.runtime = self.make()
        self.assertFalse(self.runtime.snapshot()['verified'])
        self.case.client.restart()
        self.runtime.client = self.case.client.http
        self.runtime.tick()
        self.assertTrue(self.runtime.snapshot()['verified'])
        self.assertEqual(self.runtime.snapshot()['last_receipt'], receipt)

    def test_unreachable_client_retains_installation_and_resumes(self):
        self.case.confirm('b','offline-b')
        client = self.runtime.client
        class Offline:
            def request(self, *args): raise OSError('offline')
        self.runtime.client = Offline(); self.runtime.tick()
        self.assertEqual(self.runtime.snapshot()['status'], 'retrying')
        self.assertFalse(self.runtime.snapshot()['verified'])
        self.assertEqual(self.case.f.row()['phase'], 'saved')
        self.runtime.client = client; self.runtime.tick()
        self.assertTrue(self.runtime.snapshot()['verified'])
        self.assertEqual(len(self.case.f.owner.activations),1)

    def test_lost_post_reply_uses_remote_committed_receipt_without_resend(self):
        self.case.confirm('a','reply-lost')
        original = self.runtime.client
        calls = []
        class LostReply:
            def request(self, method, route, value=None):
                result = original.request(method,route,value)
                if method == 'POST':
                    calls.append(value['installation']['installation_id'])
                    raise OSError('response_lost')
                return result
        self.runtime.client = LostReply(); self.runtime.tick()
        self.assertFalse(self.runtime.snapshot()['verified'])
        self.runtime.tick()
        self.assertTrue(self.runtime.snapshot()['verified'])
        self.assertEqual(calls,['reply-lost'])

    def test_slow_post_late_reply_never_marks_new_installation_synchronized(self):
        self.case.confirm('a','slow-a')
        original = self.runtime.client
        class ChangedTarget:
            def request(inner,method,route,value=None):
                result = original.request(method,route,value)
                if method == 'POST': self.case.confirm('b','new-b')
                return result
        self.runtime.client = ChangedTarget(); self.runtime.tick()
        state = self.runtime.snapshot()
        self.assertFalse(state['verified'])
        self.assertEqual(state['installation_id'],'new-b')
        self.assertEqual(state['last_receipt']['installation_id'],'slow-a')
        self.runtime.client = original; self.runtime.tick()
        self.assertTrue(self.runtime.snapshot()['verified'])
        self.assertEqual(self.case.current()['current']['installation_id'],'new-b')

    def test_target_change_after_get_prevents_old_post(self):
        self.case.confirm('a','before-get')
        original = self.runtime.client
        calls = []
        class ChangedTarget:
            def request(inner,method,route,value=None):
                calls.append(method)
                result = original.request(method,route,value)
                self.case.confirm('b','after-get')
                return result
        self.runtime.client = ChangedTarget(); self.runtime.tick()
        self.assertEqual(calls,['GET'])
        self.assertIsNone(self.case.current()['current'])

    def test_cached_receipt_does_not_claim_sync_after_client_store_removed(self):
        self.case.confirm('b','store-b'); self.runtime.tick()
        receipt = self.runtime.snapshot()['last_receipt']
        self.runtime.close()
        self.case.client.stop()
        for suffix in ('','-wal','-shm'):
            (self.folder/('client.sqlite'+suffix)).unlink(missing_ok=True)
        self.case.client.start()
        self.runtime = self.make()
        self.runtime.client = self.case.client.http
        self.runtime.tick()
        self.assertFalse(self.runtime.snapshot()['verified'])
        self.assertEqual(self.runtime.snapshot()['last_receipt'],receipt)
        self.case.client.http.request('POST','/api/devices/hello',dict(device_id=DEVICE,character_id=CHARACTER,
            hardware='esp32-s3',firmware='memory',capabilities={'display.expression':True}))
        self.runtime.tick()
        self.assertTrue(self.runtime.snapshot()['verified'])

    def test_single_owner_and_fixed_source_configuration(self):
        with self.assertRaises((OSError,RoleStorageError)):
            self.make()
        self.runtime.close()
        import json
        doc = json.loads(self.path.read_bytes()); doc['config']['source_id'] = 'another'
        self.path.write_text(json.dumps(doc),encoding='utf-8')
        with self.assertRaises(ValueError): self.make()

    def test_close_waits_for_inflight_task_before_releasing_owner(self):
        self.case.confirm('a','closing-a')
        started, release, closed = threading.Event(), threading.Event(), threading.Event()
        original = self.runtime.client
        class Slow:
            def request(inner,*args):
                started.set()
                if not release.wait(5): raise OSError('fixture_timeout')
                return original.request(*args)
        self.runtime.client = Slow()
        tick = threading.Thread(target=self.runtime.tick); tick.start()
        self.assertTrue(started.wait(5))
        close = threading.Thread(target=lambda:(self.runtime.close(),closed.set())); close.start()
        self.assertFalse(closed.wait(.05))
        with self.assertRaises((OSError,RoleStorageError)): self.make()
        release.set(); tick.join(5); close.join(5)
        self.assertTrue(closed.is_set())
        self.assertFalse(self.runtime.snapshot()['verified'])
        self.assertIsNone(self.case.current()['current'])

    def test_storage_failure_does_not_show_verified_then_retry_recovers(self):
        self.case.confirm('b','write-fail')
        original = self.runtime.writer
        def failed(*args): raise OSError('storage_unavailable')
        self.runtime.writer = failed; self.runtime.tick()
        self.assertFalse(self.runtime.snapshot()['verified'])
        self.runtime.writer = original; self.runtime.tick()
        self.assertTrue(self.runtime.snapshot()['verified'])

    def test_wrong_receipt_digest_is_rejected(self):
        self.case.confirm('a','bad-receipt')
        original = self.runtime.client
        class Corrupt:
            def request(inner,method,route,value=None):
                result = original.request(method,route,value)
                if method=='POST': result['receipt']['installation_sha256']='0'*64
                return result
        self.runtime.client = Corrupt(); self.runtime.tick()
        self.assertFalse(self.runtime.snapshot()['verified'])

    def test_truncated_http_response_retries_original_installation(self):
        from http.client import IncompleteRead
        self.case.confirm('a','truncated-a')
        original = self.runtime.client
        class Truncated:
            calls = 0
            def request(inner,method,route,value=None):
                inner.calls += 1
                if inner.calls == 1: raise IncompleteRead(b'partial',10)
                return original.request(method,route,value)
        self.runtime.client=Truncated();self.runtime.tick()
        self.assertEqual(self.runtime.snapshot()['status'],'retrying')
        self.runtime.tick()
        self.assertTrue(self.runtime.snapshot()['verified'])
        self.assertEqual(self.runtime.snapshot()['last_receipt']['installation_id'],'truncated-a')

    def test_nonobject_remote_response_is_visible_and_recoverable(self):
        self.case.confirm('b','invalid-json-b')
        original=self.runtime.client
        for result in ([],{'scope':'installation_facts','live_device_state':False,
            'character_id':CHARACTER,'device_id':DEVICE,'current':[]}):
            class Malformed:
                def request(inner,*args): return result
            self.runtime.client=Malformed();self.runtime.tick()
            self.assertEqual(self.runtime.snapshot()['status'],'retrying')
        self.runtime.client=original;self.runtime.tick()
        self.assertTrue(self.runtime.snapshot()['verified'])

    def test_original_installation_http_includes_current_client_sync(self):
        from tests.test_character_profiles import service_module
        from protocol.client_installation_sync import InstallationHTTP
        server = ThreadingHTTPServer(('127.0.0.1',0),service_module().Handler)
        server.shell_installation=self.case.f.service
        server.client_installation=self.runtime
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            http=InstallationHTTP(f'http://127.0.0.1:{server.server_port}')
            self.case.confirm('a','http-sync');self.runtime.tick()
            state=http.request('GET','/api/shell-installation/state')
            self.assertTrue(state['client_sync']['verified'])
            self.assertEqual(state['client_sync']['installation_id'],'http-sync')
            request=self.case.f.request('b','new-http');request['character_id']=CHARACTER
            from urllib.request import Request, build_opener, ProxyHandler
            import json
            submit=Request(f'http://127.0.0.1:{server.server_port}/api/shell-installation/confirm',
                data=json.dumps(request).encode(),headers={'Content-Type':'application/json',
                    'X-DeskBot-Studio':'shell-installation-v1'})
            with build_opener(ProxyHandler({})).open(submit,timeout=5) as result:
                response=json.load(result)
            self.assertFalse(response['client_sync']['verified'])
            self.assertEqual(response['client_sync']['installation_id'],'new-http')
            self.assertEqual(response['client_sync']['last_receipt']['installation_id'],'http-sync')
        finally:
            server.shutdown();server.server_close();thread.join(5)

    def test_background_loop_syncs_without_an_http_reader(self):
        self.case.confirm('b','background-b')
        self.runtime.close()
        self.runtime=ClientInstallationRuntime(self.case.f.service,self.path,url=self.url,source_id=SOURCE,
            character_id=CHARACTER,device_id=DEVICE,client=self.case.client.http,interval=.05)
        import time
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if self.case.current()['current'] is not None: break
            time.sleep(.02)
        self.assertEqual(self.case.current()['current']['installation_id'],'background-b')


if __name__=='__main__': unittest.main()
