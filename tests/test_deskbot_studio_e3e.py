"""Role analysis through the real HTTP handler; all device state is in memory."""
from copy import deepcopy
import importlib.util
import json
import http.client
from pathlib import Path
import tempfile
import threading
import unittest
from scripts.archived_products import product_matches
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

from protocol import studio_resource_package as packages
from protocol import studio_installed_catalog as installed

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class FakeBridge:
    """Explicit injected boundary: a test can never spawn a serial worker."""
    def __init__(self, manifest):
        self.context = dict(connection_id="connection-http-test", target_id="device-"+"a"*32,
            boot_id="boot-"+"b"*32, session_id="session-"+"b"*32+"-1")
        self.state = dict(**self.context, enabled=True, age_ms=20, can_submit=True, status="ready",
            catalog=dict(status="complete", manifest=manifest,
                **{k: self.context[k] for k in ("target_id", "boot_id", "session_id")}))
        self.commands, self.control_calls = [], 0

    def control(self):
        self.control_calls += 1
        return deepcopy(self.state)

    def snapshot(self):
        return dict(hardware_accessed=False)

    def command(self, value):
        self.commands.append(value)
        return self.control()

    def close(self):
        pass


class RoleHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = module("e3e_http_test", "scripts/serve-deskbot-studio-e3e.py")
        cls.catalog = packages.load_catalog()
        cls.manifest = json.loads((ROOT / "artifacts/deskbot-studio-e3d1-20261105/installed-directory.json").read_bytes())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e3e-http-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.bridge = FakeBridge(deepcopy(self.manifest))
        self.server = self.service.make_server(0, local=False, output=self.temp.name, bridge=self.bridge)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(ProxyHandler({}))
        self.value = dict(draft=deepcopy(self.catalog["initial"]), semantic="neutral", source="device",
                          context=self.bridge.context, request_id="analysis-http-test")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def post(self, value=None, *, headers=None, raw=None, path="/api/role-expression/resolve"):
        request = Request(self.origin+path, data=raw if raw is not None else packages.canonical(value or self.value),
            headers=headers or {"Content-Type": "application/json", "X-DeskBot-Studio": "e3e"})
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_analysis_returns_existing_command_without_sending_it(self):
        status, row = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["command"]["operation"], "neutral")
        self.assertEqual(row["command"], self.post()[1]["command"])
        self.assertEqual(self.bridge.commands, [])
        # Sending remains a separate, explicit request to the existing bridge.
        status, _ = self.post(row["command"], path="/api/studio/command",
            headers={"Content-Type": "application/json", "X-DeskBot-Studio": "e3c6"})
        self.assertEqual(status, 200)
        self.assertEqual(self.bridge.commands, [row["command"]])

    def test_snapshot_analysis_does_not_read_bridge(self):
        self.value.update(source="saved_snapshot", context=None)
        calls = self.bridge.control_calls
        status, row = self.post()
        self.assertEqual((status, row["status"]), (200, "planned"))
        self.assertIsNone(row["command"])
        self.assertEqual(self.bridge.control_calls, calls)

    def test_edit_busy_reconnect_and_missing_catalog_return_next_step(self):
        self.value["semantic"] = "embarrassed"
        self.value["draft"]["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        self.assertEqual(self.post()[1]["status"], "needs_publish")
        self.value["semantic"] = "neutral"
        self.bridge.state["can_submit"] = False
        self.assertEqual(self.post()[1]["status"], "busy")
        self.bridge.state["connection_id"] = "connection-new"
        self.assertEqual(self.post()[1]["reason"], "connection_changed")
        self.bridge.state["connection_id"] = self.bridge.context["connection_id"]
        self.bridge.state["catalog"]["status"] = "reading"
        self.assertEqual(self.post()[1]["reason"], "catalog_required")
        self.assertEqual(self.bridge.commands, [])

    def test_malformed_duplicate_and_wrong_version_are_rejected(self):
        for raw in (b'{"semantic":"neutral","semantic":"happy"}', b'{', b'[]'):
            self.assertEqual(self.post(raw=raw)[0], 422)
        value = deepcopy(self.value)
        value["draft"]["catalog_id"] = "a"*64
        self.assertEqual(self.post(value)[0], 422)
        self.assertEqual(self.post(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post(headers={"Content-Type": "application/json", "X-DeskBot-Studio": "e3e", "Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.bridge.commands, [])

    def test_oversize_is_rejected_before_parsing(self):
        self.assertEqual(self.post(raw=b" "*(packages.MAX_DRAFT_BYTES+1))[0], 413)

    def test_rejected_post_receipts_survive_unread_body_and_do_not_read_device(self):
        before = self.bridge.control_calls
        for _ in range(5):
            self.assertEqual(self.post(headers={"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(self.post(headers={"Content-Type": "application/json",
                "X-DeskBot-Studio": "e3e", "Origin": "https://example.org"})[0], 403)
            self.assertEqual(self.post(raw=b" "*(packages.MAX_DRAFT_BYTES+1))[0], 413)
        self.assertEqual(self.bridge.control_calls, before)
        self.assertEqual(self.bridge.commands, [])

    def test_rejected_incomplete_body_returns_status_before_waiting_for_body(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.putrequest("POST", "/api/role-expression/resolve")
        connection.putheader("Content-Type", "text/plain")
        connection.putheader("Content-Length", str(packages.MAX_DRAFT_BYTES))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 415)
        self.assertIn("error", json.loads(response.read()))
        self.assertEqual(self.bridge.commands, [])

    def test_page_and_health_show_current_edition(self):
        with self.opener.open(self.origin+"/", timeout=5) as response:
            self.assertIn(b"role-expression-slot", response.read())
        with self.opener.open(self.origin+"/api/health", timeout=5) as response:
            value = json.load(response)
            self.assertEqual(value["edition"], "E3e")
            self.assertFalse(value["hardware_accessed"])


class GenerationTests(unittest.TestCase):
    @unittest.skipUnless(packages.BASE_IMAGE.is_file(), "private resource image absent")
    def test_public_and_local_generation_reproduce(self):
        builder = module("e3e_generation_test", "scripts/build-deskbot-studio-e3e.py")
        for local, folder in ((False, builder.PUBLIC), (True, builder.LOCAL)):
            for name, raw in builder.products(local).items():
                self.assertTrue(product_matches((folder / name).read_bytes(), raw, builder.PUBLIC / name, local=local), name)


if __name__ == "__main__":
    unittest.main()
