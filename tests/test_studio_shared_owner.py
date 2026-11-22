"""Shared HTTP ownership preserves the original ControlLink and coordinator."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
import unittest

from protocol.studio_shared_owner import SharedWorkbenchBridge
import tests.test_shell_installation as installation
DEVICE=installation.DEVICE


class SharedOwnerTests(unittest.TestCase):
    def setUp(self):
        self.case=installation.InstallationTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        case=self.case;self.commands=[];commands=self.commands
        flags=self.flags=dict(automatic=False,bad=False,lost=False)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.end_headers()
                value=({'enabled':flags['automatic']} if self.path=='/api/shell-runtime/state'
                       else {} if flags['bad'] else case.owner.control())
                self.wfile.write(json.dumps(value).encode())
            def do_POST(self):
                assert self.headers['X-DeskBot-Studio']=='e3c6'
                command=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                commands.append(command)
                value=case.owner.command(command)
                if flags['lost'] and command['operation'].startswith('role.activate.'):
                    flags['lost']=False;self.close_connection=True;return
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(value).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
        self.bridge=SharedWorkbenchBridge('http://127.0.0.1:'+str(self.server.server_port),DEVICE)

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)

    def test_addresses_stay_on_explicit_local_owner(self):
        for value in ('https://127.0.0.1:8780','http://example.com:8780','http://127.0.0.1','http://user@127.0.0.1:8780','http://127.0.0.1:8780/path'):
            with self.subTest(value=value),self.assertRaises(ValueError):SharedWorkbenchBridge(value,DEVICE)

    def test_original_control_and_identity_are_checked(self):
        self.assertEqual(self.bridge.control()['target_id'],DEVICE)
        self.bridge.device_id='device-other'
        with self.assertRaises(ValueError):self.bridge.control()

    def test_old_session_sends_no_command(self):
        state=self.bridge.control()
        value=dict(command_id='shared-read',operation='read_roles',**{k:state[k] for k in ('connection_id','target_id','boot_id','session_id')})
        value['boot_id']='boot-other'
        with self.assertRaises(ValueError):self.bridge.command(value)
        self.assertFalse(self.commands)

    def test_close_preserves_original_connection(self):
        self.bridge.close()
        self.assertTrue(self.bridge.control()['enabled'])
        self.assertFalse(self.commands)
        with self.assertRaises(ValueError):self.bridge.disconnect()
        with self.assertRaises(ValueError):self.bridge.connect('COM4','anything')

    def test_real_installation_uses_original_coordinator_over_http(self):
        self.case.service.bridge=self.bridge
        self.case.confirm();self.case.run_to(7000)
        row=self.case.row()
        self.assertEqual(row['phase'],'saved')
        self.assertTrue(row['first_frame'] and row['saved'])
        self.assertEqual(len(self.case.owner.activations),1)
        self.assertEqual(row['completion_source'],'correlated_device_reply')

    def test_invalid_control_is_rejected(self):
        self.flags['bad']=True
        with self.assertRaises(ValueError):self.bridge.control()

    def test_upstream_automatic_blocks_manual_activation(self):
        self.flags['automatic']=True
        self.case.service.bridge=self.bridge
        self.case.confirm();self.case.run_to(2400)
        self.assertFalse(self.commands)
        self.assertFalse(self.case.owner.activations)

    def test_lost_http_return_queries_result_without_duplicate_activation(self):
        self.flags['lost']=True;self.case.service.bridge=self.bridge
        self.case.confirm();self.case.run_to(22000)
        self.assertEqual(self.case.row()['phase'],'saved')
        self.assertEqual(len(self.case.owner.activations),1)


if __name__=='__main__':unittest.main()
