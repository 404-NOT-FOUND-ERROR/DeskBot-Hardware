"""Capability-aware shy requests through the existing host and C++ owners."""
from copy import deepcopy
import json
import unittest

from tests import test_studio_control_link as host
from tests import test_studio_control_native as integration
from tests import test_studio_expression_control as native
from tests import test_studio_role_expression as roles
from protocol import studio_display_query as display
from protocol import studio_control_bridge as bridge
from protocol import studio_control_link as link


class ShyHostTests(unittest.TestCase):
    setUp = host.LinkTests.setUp
    command = host.LinkTests.command
    open = host.LinkTests.open
    ready = host.LinkTests.ready

    def capable(self):
        self.ready()
        self.link.client._cached["value"]["expression_semantics"] = ["neutral", "embarrassed"]

    def test_old_reply_supports_only_neutral(self):
        row = host.display("r-1")
        self.assertTrue(display.supports(display.validate_reply(row), "neutral"))
        self.assertFalse(display.supports(row, "embarrassed"))
        self.ready()
        self.command("embarrassed", now=410, session_id=host.SESSION)
        self.assertEqual(self.link.last_command["reason"], "expression_interface_pending")
        self.assertIsNone(self.link.outgoing)

    def test_empty_and_invalid_capabilities(self):
        row = host.display("r-1")
        row["expression_semantics"] = []
        self.assertFalse(display.supports(display.validate_reply(row), "neutral"))
        for value in (None, "embarrassed", ["neutral", "neutral"], ["happy"], [{}], ["neutral", "embarrassed", "happy"]):
            with self.subTest(value=value):
                row["expression_semantics"] = value
                with self.assertRaises(ValueError):
                    display.validate_reply(row)
                self.assertFalse(display.supports(row, "embarrassed"))

    def test_shy_command_preserves_fixed_command_catalog(self):
        self.capable()
        self.command("embarrassed", now=410, session_id=host.SESSION)
        wire = json.loads(self.link.tick(800))
        self.assertEqual(wire["command"]["semantic_id"], "embarrassed")
        self.assertEqual(wire["catalog_id"], link.CATALOG)
        self.assertTrue(bridge.validate_control(self.link.snapshot(800)))

    def test_capability_removed_between_click_and_send(self):
        self.capable()
        self.command("embarrassed", now=410, session_id=host.SESSION)
        self.link.client._cached["value"]["expression_semantics"] = ["neutral"]
        self.assertIsNone(self.link.tick(800))
        self.assertEqual(self.link.local_result["reason"], "expression_interface_pending")
        self.assertIsNone(self.link.pending)

    def test_stale_reply_and_directory_reading_cancel_click(self):
        for case in ("stale", "reading"):
            self.setUp()
            self.capable()
            if case == "reading":
                self.link.catalog.update(status="reading", target_id=self.link.target,
                                         boot_id=self.link.boot, session_id=host.SESSION)
            self.command("embarrassed", now=2410 if case == "stale" else 410, session_id=host.SESSION)
            self.assertEqual(self.link.last_command["reason"], "request_not_ready")
            self.assertIsNone(self.link.outgoing)

    def test_directory_request_waits_for_queued_or_pending_expression(self):
        self.capable()
        self.command("embarrassed", now=410, session_id=host.SESSION)
        self.command("read_catalog", now=411, session_id=host.SESSION)
        self.assertEqual(self.link.last_command["reason"], "pending_request")
        self.assertEqual(json.loads(self.link.tick(800))["command"]["semantic_id"], "embarrassed")
        self.command("read_catalog", now=810, session_id=host.SESSION, command_id="read-again")
        self.assertEqual(self.link.last_command["reason"], "pending_request")

    def test_directory_started_before_send_cancels_queued_expression(self):
        self.capable()
        self.command("embarrassed", now=410, session_id=host.SESSION)
        self.link.catalog.update(status="reading", target_id=self.link.target,
                                 boot_id=self.link.boot, session_id=host.SESSION)
        self.assertIsNone(self.link.tick(800))
        self.assertEqual(self.link.local_result["reason"], "request_not_ready")


class ShyRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        roles.RoleExpressionTests.setUpClass.__func__(cls)
    setUp = roles.RoleExpressionTests.setUp
    resolve = roles.RoleExpressionTests.resolve

    def test_current_binding_and_firmware_capability_are_independent(self):
        self.control["display"] = {"expression_semantics": ["neutral", "embarrassed"]}
        row = self.resolve("embarrassed")
        self.assertEqual((row["status"], row["installed_resource_id"]), ("ready", "Happy.eaf"))
        self.assertEqual(row["command"]["operation"], "embarrassed")
        self.draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        self.assertEqual(self.resolve("embarrassed")["status"], "needs_publish")

    def test_capability_loss_and_wrong_catalog_session(self):
        self.control["display"] = {"expression_semantics": ["neutral", "embarrassed"]}
        self.assertEqual(self.resolve("embarrassed")["status"], "ready")
        self.control["display"]["expression_semantics"] = []
        self.assertIsNone(self.resolve("embarrassed")["command"])
        self.assertIsNone(self.resolve("neutral")["command"])
        self.control["catalog"]["session_id"] = "other-session"
        self.assertEqual(self.resolve("embarrassed")["reason"], "catalog_session_changed")

    def test_published_e145_binding_is_requestable_with_current_capability(self):
        self.control["display"] = {"expression_semantics": ["neutral", "embarrassed"]}
        self.draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        asset = self.catalog["assets"]["shy_candidate.eaf"]
        entries = deepcopy(self.reference["entries"])
        row = next(r for r in entries if r["kind"] == "expression" and r["id"] == "embarrassed")
        row.update(resource_id="shy_candidate.eaf", resource_sha256=asset["deployed_sha256"],
                   bytes=asset["bytes"], fps=25, loop=True)
        self.control["catalog"]["manifest"] = roles.installed.build_manifest(entries, "c"*64, "d"*64)
        result = self.resolve("embarrassed")
        self.assertEqual((result["status"], result["installed_resource_id"]), ("ready", "shy_candidate.eaf"))
        self.assertEqual(result["command"]["operation"], "embarrassed")


class ShyNativeTests(native.ExpressionControlTests):
    """Also rerun the existing parser, cancellation and concurrency coverage."""
    SHY_ENABLED = True

    def shy_wire(self, **changes):
        return self.wire(command=dict(kind="play_expression", semantic_id="embarrassed"), **changes)

    def test_shy_dispatches_the_accepted_semantic(self):
        rows, stats = self.trace(("D", 500, self.shy_wire()), ("U", 510), ("Z", 520))
        self.assertEqual([r["status"] for r in rows], ["accepted", "dispatched"])
        self.assertEqual((stats[0], stats[3]), (1, 1))

    def test_later_input_does_not_replace_accepted_semantic(self):
        rows, stats = self.trace(("D", 500, self.shy_wire()),
                                ("D", 800, self.wire(request_id="expr-other", sequence=2)),
                                ("U", 810), ("Z", 820))
        self.assertEqual([r["status"] for r in rows], ["accepted", "busy", "dispatched"])
        self.assertEqual((stats[0], stats[3]), (1, 1))

    def test_same_id_different_semantic_conflicts(self):
        rows, stats = self.trace(("D", 500, self.shy_wire()), ("D", 800, self.wire()), ("U", 810), ("Z", 820))
        self.assertEqual(rows[1]["reason"], "request_id_conflict")
        self.assertEqual((stats[0], stats[3]), (1, 1))

    def test_shy_replay_runs_once(self):
        rows, stats = self.trace(("D", 500, self.shy_wire()), ("U", 510), ("Z", 520), ("D", 800, self.shy_wire()))
        self.assertEqual(rows[-1]["status"], "duplicate")
        self.assertEqual((stats[0], stats[3]), (1, 1))

    def test_shy_menu_voice_disconnect_and_deadline_before_dispatch(self):
        for change in (("P", 510, 2), ("A", 510, 6), ("X", 510), ("T", 3500)):
            with self.subTest(change=change):
                rows, stats = self.trace(("D", 500, self.shy_wire()), change, ("U", 3510), ("Z", 3520))
                self.assertIn(rows[-1]["status"], ("interrupted", "expired"))
                self.assertEqual((stats[0], stats[3]), (0, 0))


class ShyNativeLinkTests(integration.NativeLinkTests):
    SHY_ENABLED = True

    def test_host_to_cpp_shy_capability_and_receipts(self):
        self.ready()
        self.assertEqual(self.link.snapshot(410)["display"]["expression_semantics"], ["neutral", "embarrassed"])
        self.command("embarrassed", 410)
        wire = self.link.tick(800)
        self.assertEqual(json.loads(wire)["command"]["semantic_id"], "embarrassed")
        self.run_device([("D", 800, wire)], 800)
        rows, stats = self.run_device([("U", 810), ("Z", 820)], 820)
        self.assertEqual([r["status"] for r in self.link.receipts], ["accepted", "dispatched"])
        self.assertEqual((stats[0], stats[3]), (1, 1))


if __name__ == "__main__":
    unittest.main()
