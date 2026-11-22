"""Production role HTTP and workbench integration, using an in-memory USB bridge."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class RoleHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = module("runtime_roles_http", "scripts/serve-deskbot-studio-e3f8.py")
        cls.helpers = module("runtime_roles_bridge", "tests/test_deskbot_studio_e3e.py")
        cls.manifest = json.loads((ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json").read_bytes())
        cls.builder = module("runtime_roles_builder", "scripts/build-deskbot-studio-e3f8.py")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="roles-http-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "active.json"
        self.bridge = self.helpers.FakeBridge(deepcopy(self.manifest))
        self.opener = build_opener(ProxyHandler({}))
        self.start()
        self.addCleanup(self.stop)
        self.state = self.get("/api/roles/state")[1]
        self.a, self.b = [row["id"] for row in self.state["catalog"]["roles"]]

    def start(self):
        self.server = self.service.StudioServer(0, bridge=self.bridge, output=self.temp.name, state_path=self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.server = None

    def get(self, path, headers=None):
        try:
            with self.opener.open(Request(self.origin+path, headers=headers or {}), timeout=5) as response:
                raw = response.read()
                return response.status, json.loads(raw) if response.headers["Content-Type"].startswith("application/json") else raw
        except HTTPError as error:
            return error.code, json.load(error)

    def post(self, path, value=None, *, raw=None, headers=None):
        request = Request(self.origin+path, data=json.dumps(value).encode() if raw is None else raw,
            headers=headers or {"Content-Type":"application/json", "X-DeskBot-Studio":"e3f8"})
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def choice(self, role=None, revision=0):
        return dict(role_id=role or self.b, expected_revision=revision, catalog_id=self.state["catalog_id"])

    def test_real_save_restart_and_resolve_without_usb_reads_or_commands(self):
        self.assertFalse(self.path.exists())
        status, saved = self.post("/api/roles/activate", self.choice())
        self.assertEqual(status, 200)
        self.stop()
        self.start()
        self.assertEqual(self.get("/api/roles/state")[1], saved)
        status, plan = self.post("/api/roles/expression", dict(semantic="embarrassed", expected_revision=1,
                                                            catalog_id=self.state["catalog_id"]))
        self.assertEqual((status, plan["expression"]["state"], plan["expression"]["mirror"]), (200,"shy",False))
        self.assertEqual((self.bridge.control_calls, self.bridge.commands), (0,[]))

    def test_stale_save_and_expression_return_latest_state(self):
        self.post("/api/roles/activate", self.choice())
        for path, value in (("/api/roles/activate", self.choice(self.a)),
                            ("/api/roles/expression", dict(semantic="happy", expected_revision=0,
                                                           catalog_id=self.state["catalog_id"]))):
            status, body = self.post(path, value)
            self.assertEqual((status,body["state"]["activation"]["role_id"]),(409,self.b))
        self.assertEqual(self.server.roles.snapshot()["activation"]["revision"],1)

    def test_boundaries_unknown_role_duplicate_keys_and_extra_fields(self):
        requests = [self.choice("missing"), {**self.choice(), "source":"shell"},
                    {**self.choice(),"expected_revision":True}, {**self.choice(),"shell_id":"fake"}]
        for value in requests:
            self.assertEqual(self.post("/api/roles/activate",value)[0],422)
        self.assertEqual(self.post("/api/roles/activate",raw=b'{"role_id":"a","role_id":"b"}')[0],422)
        self.assertEqual(self.post("/api/roles/activate",raw=b'x'*1025)[0],413)
        self.assertFalse(self.path.exists())

    def test_cross_origin_wrong_header_and_chunked_requests_leave_state(self):
        headers={"Content-Type":"application/json","X-DeskBot-Studio":"e3f8","Origin":"https://example.com"}
        self.assertEqual(self.post("/api/roles/activate", self.choice(), headers=headers)[0],403)
        headers.pop("Origin")
        headers["X-DeskBot-Studio"]="e3e"
        self.assertEqual(self.post("/api/roles/activate", self.choice(), headers=headers)[0],415)
        self.assertEqual(self.get("/api/roles/state", {"Host":"localhost:1"})[0],403)
        connection=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        connection.request("POST","/api/roles/activate",body=b"0\r\n\r\n",headers={
            "Content-Type":"application/json","X-DeskBot-Studio":"e3f8","Transfer-Encoding":"chunked"})
        self.assertEqual(connection.getresponse().status,415)
        connection.close()
        self.assertFalse(self.path.exists())

    def test_storage_failure_preserves_live_state(self):
        with patch.object(self.server.roles,"writer",side_effect=OSError("simulated full disk")):
            status, body=self.post("/api/roles/activate",self.choice())
        self.assertEqual(status,503)
        self.assertIn("保存",body["error"])
        self.assertEqual(self.get("/api/roles/state")[1]["activation"]["role_id"],self.a)
        self.assertFalse(self.path.exists())

    def test_explicit_recovery_retains_invalid_file(self):
        self.stop()
        raw=b'{"bad":true}'
        self.path.write_bytes(raw)
        self.start()
        self.assertIsNone(self.get("/api/roles/state")[1]["activation"])
        self.assertEqual(self.post("/api/roles/activate",self.choice())[0],503)
        self.assertEqual(self.post("/api/roles/recover",self.choice())[0],200)
        backups=list(self.path.parent.glob("active.previous-*.json"))
        self.assertEqual(backups[0].read_bytes(),raw)

    def test_media_whitelist_and_content_hash(self):
        status, raw=self.get("/role-media/shy.png")
        self.assertEqual((status,raw[:8]),(200,b'\x89PNG\r\n\x1a\n'))
        for path in ("/role-media/../report.json","/role-media/%2e%2e/report.json","/role-media/missing.png"):
            self.assertEqual(self.get(path)[0],404)
        original=self.server.role_media["role-media/shy.png"]
        self.server.role_media["role-media/shy.png"]=(original[0],"f"*64)
        self.assertEqual(self.get("/role-media/shy.png")[0],503)

    def test_page_health_and_old_planner_remain_available(self):
        status, page=self.get("/")
        self.assertEqual(status,200)
        for needle in (b"runtime-role-slot",b"DeskBotStudioE3EUI",b"DeskBotStudioE3F2UI",b"E3f\xc2\xb78"):
            self.assertIn(needle,page)
        self.assertEqual(self.get("/api/health")[1]["edition"],"E3f-8")
        before=self.bridge.control_calls
        value=dict(draft=deepcopy(self.server.catalog["initial"]),semantic="neutral",source="saved_snapshot",
                   context=None,request_id="legacy-planner")
        status, result=self.post("/api/role-expression/resolve",value,headers={
            "Content-Type":"application/json","X-DeskBot-Studio":"e3e"})
        self.assertEqual((status,result["status"]),(200,"planned"))
        self.assertEqual(self.bridge.control_calls,before)

    def test_no_second_service_writes_the_same_state(self):
        with self.assertRaises(OSError):
            self.service.StudioServer(0,bridge=self.bridge,output=self.temp.name,state_path=self.path)

    def test_service_close_keeps_owner_until_pending_commit_finishes(self):
        entered,release=threading.Event(),threading.Event()
        writer=self.server.roles.writer
        def delayed(path,value):
            entered.set()
            if not release.wait(3):
                raise OSError("test writer not released")
            writer(path,value)
        self.server.roles.writer=delayed
        with ThreadPoolExecutor(2) as executor:
            saving=executor.submit(self.post,"/api/roles/activate",self.choice())
            self.assertTrue(entered.wait(2))
            self.server.shutdown()
            closing=executor.submit(self.server.server_close)
            self.assertFalse(closing.done())
            with self.assertRaises(OSError):
                self.service.StudioServer(0,bridge=self.bridge,output=self.temp.name,state_path=self.path)
            release.set()
            self.assertEqual(saving.result()[0],200)
            closing.result()
        self.server=None
        self.start()
        self.assertEqual(self.get("/api/roles/state")[1]["activation"]["role_id"],self.b)

    def test_local_and_public_generation_reproduces(self):
        for local, folder in ((True,self.builder.LOCAL),(False,self.builder.PUBLIC)):
            for name, raw in self.builder.products(local).items():
                self.assertEqual((folder/name).read_bytes(),raw,name)


if __name__=="__main__":
    unittest.main()
