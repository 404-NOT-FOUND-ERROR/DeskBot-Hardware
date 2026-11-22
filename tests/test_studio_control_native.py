"""Drive the host link with the production C++ USB endpoint and owner adapter."""
import json
import os
from pathlib import Path
import struct
import subprocess
import unittest
from unittest.mock import patch

from tests import test_studio_expression_control as native
from tests.test_studio_control_link import hello
from protocol.studio_control_link import ControlLink
from protocol import studio_role_expression as planner


class NativeLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The release check points this existing harness at the staged candidate.
        # Public CI keeps using the checked-in firmware sources.
        root = Path(os.environ["DESKBOT_NATIVE_SOURCE_ROOT"]) if "DESKBOT_NATIVE_SOURCE_ROOT" in os.environ else native.ROOT / "firmware/body"
        with patch.multiple(native, MAIN=root/"main", BOARD=root/"main/boards/esp_vocat"):
            native.ExpressionControlTests.setUpClass.__func__(cls)
        cls.role_catalog = planner.packages.load_catalog()
        cls.role_reference = json.loads((native.ROOT / "artifacts/deskbot-studio-e3d1-20261105/installed-directory.json").read_bytes())

    def setUp(self):
        self.link = ControlLink(nonce=lambda: "client-native", wall=lambda: "2026-11-05T00:00:00.000Z")
        self.link.observe(hello(), 0)
        self.trace = bytearray()
        self.read_count = 0

    def command(self, name, now):
        state = self.link.snapshot(now)
        self.link.command(dict(operation=name, command_id="click-"+str(now),
            connection_id=state["connection_id"], target_id=hello()["device_id"], boot_id=hello()["boot_id"],
            session_id=None if name == "enable" else state["session_id"]), now)

    def run_device(self, operations, now):
        for code, time, *values in operations:
            self.trace.extend(code.encode()+struct.pack("<Q", time))
            if code == "D":
                self.trace.extend(struct.pack("<I", len(values[0]))+values[0])
            elif values:
                self.trace.append(values[0])
        path = self.folder / "host-link.bin"
        path.write_bytes(self.trace)
        run = subprocess.run([str(self.exe), str(path)], capture_output=True, check=True, timeout=20)
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        for row in rows[self.read_count:]:
            self.link.accept(row, now)
        self.read_count = len(rows)
        self.assertEqual(self.link.rejected, 0)
        return rows, list(map(int, run.stderr.split()))

    def ready(self):
        self.command("enable", 0)
        self.run_device([("D", 0, self.link.tick(0))], 0)
        self.run_device([("D", 400, self.link.tick(400))], 400)
        self.assertTrue(self.link.snapshot(400)["can_submit"])

    def test_production_open_display_accepted_and_dispatched(self):
        self.ready(); self.command("neutral", 410)
        self.run_device([("D", 800, self.link.tick(800))], 800)
        self.assertEqual(self.link.snapshot(800)["pending"]["phase"], "accepted")
        rows, stats = self.run_device([("U", 810), ("Z", 820)], 820)
        self.assertEqual([r["status"] for r in self.link.receipts], ["accepted", "dispatched"])
        self.assertEqual(rows[-1]["reason"], "submitted_to_display")
        self.assertEqual(stats[:2], [1, 1])
        self.assertIsNone(self.link.snapshot(820)["pending"])

    def test_device_rechecks_page_between_acceptance_and_dispatch(self):
        self.ready(); self.command("neutral", 410)
        self.run_device([("D", 800, self.link.tick(800))], 800)
        rows, stats = self.run_device([("P", 810, 2), ("U", 820), ("Z", 830)], 830)
        self.assertEqual(rows[-1]["status"], "interrupted")
        self.assertEqual(stats[0], 0)
        self.assertIsNone(self.link.pending)

    def test_device_busy_receipt_reaches_host_when_voice_starts_after_query(self):
        self.ready(); self.command("neutral", 410)
        rows, stats = self.run_device([("A", 700, 6), ("D", 800, self.link.tick(800))], 800)
        self.assertEqual(rows[-1]["status"], "busy")
        self.assertEqual(stats[0], 0)
        self.assertIsNone(self.link.pending)

    def test_device_expiry_receipt_is_separate_from_host_timeout(self):
        self.ready(); self.command("neutral", 410)
        self.run_device([("D", 800, self.link.tick(800))], 800)
        rows, stats = self.run_device([("U", 2800), ("Z", 2810)], 2810)
        self.assertEqual(rows[-1]["status"], "expired")
        self.assertEqual(stats[0], 0)
        self.assertIsNone(self.link.local_result)

    def role_plan(self):
        self.ready()
        state = self.link.snapshot(410)
        self.link.catalog = dict(status="complete", reason="verified", manifest=self.role_reference,
            received_entries=len(self.role_reference["entries"]), total=len(self.role_reference["entries"]),
            next_cursor=None, **{k: state[k] for k in ("target_id", "boot_id", "session_id")})
        state = self.link.snapshot(410)
        context = {k: state[k] for k in planner.CONTEXT_KEYS}
        row = planner.resolve(self.role_catalog["initial"], "neutral", self.role_catalog, self.role_reference,
            control=state, source="device", context=context, request_id="native-role-route",
            issued_ms=410, now_ms=410)
        self.assertEqual(row["status"], "ready")
        return row["command"]

    def test_role_planner_to_native_display_with_duplicate_submission(self):
        value = self.role_plan()
        self.link.command(value, 410)
        self.link.command(value, 411)
        self.assertEqual(self.link.sequence, 1)
        self.run_device([("D", 800, self.link.tick(800))], 800)
        rows, stats = self.run_device([("U", 810), ("Z", 820)], 820)
        self.assertEqual(stats[:2], [1, 1])
        self.assertEqual([r["status"] for r in self.link.receipts], ["accepted", "dispatched"])
        self.assertEqual(rows[-1]["reason"], "submitted_to_display")
        self.assertIsNone(self.link.snapshot(820)["pending"])

    def test_role_planner_request_interrupted_when_menu_opens(self):
        self.link.command(self.role_plan(), 410)
        self.run_device([("D", 800, self.link.tick(800))], 800)
        rows, stats = self.run_device([("P", 805, 2), ("U", 810), ("Z", 820)], 820)
        self.assertEqual((rows[-1]["status"], stats[0]), ("interrupted", 0))
        self.assertIsNone(self.link.pending)

    def test_role_planner_request_reports_busy_when_voice_starts(self):
        self.link.command(self.role_plan(), 410)
        rows, stats = self.run_device([("A", 700, 6), ("D", 800, self.link.tick(800))], 800)
        self.assertEqual((rows[-1]["status"], stats[0]), ("busy", 0))
        self.assertIsNone(self.link.pending)
