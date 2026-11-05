"""Workbench generation, HTTP boundaries and one-owner IPC regression."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tests import test_deskbot_studio_e3a as previous
from tests.test_studio_control_link import hello
from protocol.studio_control_bridge import ControlBridge
from protocol.studio_control_link import initial_snapshot
from protocol.studio_identity_observation import IdentityObservation

ROOT = Path(__file__).resolve().parents[1]
B = previous.module("e3c_builder_test", "scripts/build-deskbot-studio-e3c.py")
S = previous.module("e3c_server_test", "scripts/serve-deskbot-studio-e3c.py")


class BuildTests(unittest.TestCase):
    def test_public_and_local_products_reproduce(self):
        for local, directory in ((False, B.PUBLIC), (True, B.LOCAL)):
            if local and not directory.is_dir():
                continue
            for name, raw in B.products(local).items():
                self.assertEqual((directory / name).read_bytes(), raw, name)

    def test_editor_simulator_and_control_remain_independent(self):
        html = B.products()["index.html"].decode()
        for marker in ('id="studio-control-slot"', 'id="body-observer-slot"', 'data-view="editor"',
                       'data-view="publish"', 'control:()=>liveControl.state()', 'simulation:()=>simulator.state()'):
            self.assertIn(marker, html)
        report = json.loads(B.products()["report.json"])
        self.assertEqual(report["edition"], "E3c-6")
        self.assertEqual(report["date"], "2026-11-05")
        self.assertEqual(report["control_link"]["serial_owners"], 1)
        self.assertFalse(report["hardware_accessed"])
        self.assertFalse(report["firmware_changed"])


class ServiceTests(previous.ServiceTests):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e3c-http-")
        self.server = S.make_server(0, local=False, output=self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def test_new_page_and_old_packager_served(self):
        self.assertEqual(self.call("/")[1], (B.PUBLIC / "index.html").read_bytes())
        data = json.loads(self.call("/api/health")[1])
        self.assertEqual(data["edition"], "E3c-6")
        self.assertFalse(data["device_controls"])

    def test_state_is_passive_and_body_v2_contract_unchanged(self):
        for _ in range(3):
            status, raw = self.call("/api/studio/state")
            self.assertEqual(status, 200)
            row = json.loads(raw)
            self.assertFalse(row["enabled"])
            self.assertEqual(row["frames_sent"], 0)
            self.assertIsNone(row["host_command"])
        body = json.loads(self.call("/api/body/state")[1])
        self.assertEqual(body["schema"], "deskbot.body_observation/v2")
        self.assertTrue(body["read_only"])
        self.assertFalse(body["device_written"])
        self.assertIsNone(self.server.bridge.process)

    def test_control_headers_origin_length_and_scope(self):
        headers = {"X-DeskBot-Studio": "e3c6"}
        self.assertEqual(self.call("/api/studio/state", headers={"Origin": "https://other.example"})[0], 403)
        self.assertEqual(self.call("/api/studio/command", {})[0], 415)
        for value in ({}, {"operation": "reset"}, {"extra": "x"*600}, []):
            self.assertEqual(self.call("/api/studio/command", value, headers)[0], 422)
        self.assertEqual(self.call("/api/studio/command", {}, headers | {"Origin": "https://other.example"})[0], 403)

    def test_duplicate_json_keys_are_rejected_before_worker(self):
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        raw = b'{"operation":"enable","operation":"neutral"}'
        request = Request(self.origin+"/api/studio/command", data=raw,
            headers={"Content-Type": "application/json", "X-DeskBot-Studio": "e3c6"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 422)
        caught.exception.close()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.bridge = ControlBridge(clock=lambda: self.now)
        observation = IdentityObservation(clock=lambda: self.now)
        observation.link("connected", "COM4")
        observation.feed(("I (10000) DeskBotLink: "+json.dumps(hello())+"\n").encode())
        self.bridge._update(observation.snapshot())
        self.bridge.control_state = initial_snapshot("connection-"+"c"*32)
        class Process:
            def __init__(self): self.stdin = io.StringIO()
            def poll(self): return None
        self.process = Process()
        self.bridge.process = self.process

    def command(self):
        return dict(operation="enable", command_id="click-1", connection_id=self.bridge.control_state["connection_id"],
                    target_id=hello()["device_id"], boot_id=hello()["boot_id"], session_id=None)

    def test_one_ipc_command_duplicate_suppression_and_body_projection(self):
        before = self.bridge.snapshot()
        first = self.bridge.command(self.command())
        again = self.bridge.command(self.command())
        self.assertEqual(first["host_command"], again["host_command"])
        self.assertEqual(self.process.stdin.getvalue().count("\n"), 1)
        after = self.bridge.snapshot()
        before.pop("generated_at"); after.pop("generated_at")
        self.assertEqual(after, before)
        self.assertEqual(self.bridge.worker.name, "observe-studio-body-e3c.py")
        with self.assertRaises(ValueError):
            self.bridge.command(self.command() | {"command_id": "click-2"})

    def test_worker_timeout_is_explicit_and_does_not_send_a_second_command(self):
        self.bridge.command(self.command())
        self.now = 4
        self.assertEqual(self.bridge.control()["host_command"]["status"], "timeout")
        self.assertFalse(self.bridge.control()["can_submit"])
        self.assertEqual(self.process.stdin.getvalue().count("\n"), 1)

    def test_changed_connection_rejects_stale_browser_click(self):
        with self.assertRaises(ValueError):
            self.bridge.command(self.command() | {"connection_id": "connection-"+"d"*32})
        self.assertEqual(self.process.stdin.getvalue(), "")

    def test_worker_exit_closes_control_state(self):
        self.process.poll = lambda: 1
        row = self.bridge.control()
        self.assertFalse(row["enabled"])
        self.assertIsNone(row["session_id"])
        with self.assertRaises(ValueError): self.bridge.command(self.command())
