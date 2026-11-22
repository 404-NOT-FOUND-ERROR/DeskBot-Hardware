"""Current E1.45 bindings and both request configurations on the E4e player."""
from copy import deepcopy
import json
import unittest

from tests import test_studio_control_native as integration
from tests import test_studio_expression_control as native
from tests import test_studio_role_expression as roles

ROOT = native.ROOT
CAPABILITIES = ["neutral", "embarrassed", "eat"]
DIRECTORY = ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json"


class EnabledRequestTests(native.ExpressionControlTests):
    SHY_ENABLED = EAT_ENABLED = True

    def test_busy_eat_cannot_replace_queued_shy(self):
        shy = self.wire(command=dict(kind="play_expression", semantic_id="embarrassed"))
        eat = self.wire(request_id="eat-next", sequence=2,
                        command=dict(kind="play_expression", semantic_id="eat"))
        rows, stats = self.trace(("D", 500, shy), ("D", 800, eat), ("U", 810), ("Z", 820))
        self.assertEqual([r["status"] for r in rows], ["accepted", "busy", "dispatched"])
        self.assertEqual((stats[0], stats[3], stats[4]), (1, 1, 0))

    def test_same_id_different_enabled_semantic_conflicts(self):
        shy = self.wire(command=dict(kind="play_expression", semantic_id="embarrassed"))
        eat = self.wire(command=dict(kind="play_expression", semantic_id="eat"))
        rows, stats = self.trace(("D", 500, shy), ("D", 800, eat), ("U", 810), ("Z", 820))
        self.assertEqual(rows[1]["reason"], "request_id_conflict")
        self.assertEqual((stats[0], stats[3], stats[4]), (1, 1, 0))

    def test_shy_is_cancelled_by_menu_voice_disconnect_and_expiry(self):
        for change in (("P", 510, 2), ("A", 510, 6), ("X", 510), ("T", 3500)):
            with self.subTest(change=change):
                shy = self.wire(command=dict(kind="play_expression", semantic_id="embarrassed"))
                rows, stats = self.trace(("D", 500, shy), change, ("U", 3510), ("Z", 3520))
                self.assertIn(rows[-1]["status"], ("interrupted", "expired"))
                self.assertEqual((stats[0], stats[3], stats[4]), (0, 0, 0))

    def test_shy_replay_retains_single_dispatch(self):
        shy = self.wire(command=dict(kind="play_expression", semantic_id="embarrassed"))
        rows, stats = self.trace(("D", 500, shy), ("U", 510), ("Z", 520), ("D", 800, shy))
        self.assertEqual(rows[-1]["status"], "duplicate")
        self.assertEqual((stats[0], stats[3], stats[4]), (1, 1, 0))


class DisabledRequestTests(native.ExpressionControlTests):
    SHY_ENABLED = False
    EAT_ENABLED = True

    def test_disabled_shy_still_keeps_eat(self):
        shy = self.wire(command=dict(kind="play_expression", semantic_id="embarrassed"))
        eat = self.wire(request_id="eat-next", sequence=2,
                        command=dict(kind="play_expression", semantic_id="eat"))
        rows, stats = self.trace(("D", 500, shy), ("D", 800, eat), ("U", 810), ("Z", 820))
        self.assertEqual(rows[0]["reason"], "unsupported_expression_or_action")
        self.assertEqual([r["status"] for r in rows[1:]], ["accepted", "dispatched"])
        self.assertEqual((stats[0], stats[-1]), (1, 1))


class E145LinkTests(integration.NativeLinkTests):
    SHY_ENABLED = EAT_ENABLED = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.role_reference = json.loads(DIRECTORY.read_bytes())

    def ready(self):
        super().ready()
        state = self.link.snapshot(400)
        self.link.catalog = dict(status="complete", reason="verified", manifest=deepcopy(self.role_reference),
            received_entries=37, total=37, next_cursor=None,
            **{k: state[k] for k in ("target_id", "boot_id", "session_id")})

    def test_three_capabilities_and_current_directory(self):
        self.ready()
        state = self.link.snapshot(410)
        self.assertEqual(state["display"]["expression_semantics"], CAPABILITIES)
        self.assertEqual(len(state["catalog"]["manifest"]["entries"]), 37)
        shy = next(r for r in state["catalog"]["manifest"]["entries"] if r["id"] == "embarrassed")
        self.assertEqual((shy["resource_id"], shy["fps"], shy["implicit_action_id"]),
                         ("shy_candidate.eaf", 25, "cat_nuzzle"))

    def test_mixed_requests_release_queue_after_each_dispatch(self):
        self.ready()
        semantics = ("eat", "neutral", "embarrassed", "eat", "embarrassed", "neutral")
        for index, semantic in enumerate(semantics):
            at = 1200 + index * 1600
            # Refresh through the real USB display endpoint before each click.
            query = self.link.tick(at - 400)
            if query:
                self.assertEqual(json.loads(query)["operation"], "display")
                self.run_device([("D", at - 400, query)], at - 400)
            self.command(semantic, at)
            wire = self.link.tick(at)
            self.assertIsNotNone(wire)
            self.assertEqual(json.loads(wire)["command"]["semantic_id"], semantic)
            _, stats = self.run_device([("D", at, wire), ("U", at + 10), ("Z", at + 20)], at + 20)
            self.assertEqual(stats[0], index + 1)
            self.assertIsNone(self.link.pending)
            self.assertEqual(self.link.receipts[-1]["status"], "dispatched")
        self.assertEqual((stats[3], stats[4]), (2, 2))


class E145BindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        roles.RoleExpressionTests.setUpClass.__func__(cls)
        cls.reference = json.loads(DIRECTORY.read_bytes())

    def setUp(self):
        roles.RoleExpressionTests.setUp(self)
        self.draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        self.control["display"] = {"expression_semantics": CAPABILITIES.copy()}

    resolve = roles.RoleExpressionTests.resolve

    def test_published_shy_resolves_against_full_current_catalog(self):
        row = self.resolve("embarrassed")
        self.assertEqual((row["status"], row["installed_resource_id"]), ("ready", "shy_candidate.eaf"))
        self.assertEqual(row["command"]["operation"], "embarrassed")

    def test_current_e4e_capabilities_keep_shy_unavailable(self):
        self.control["display"]["expression_semantics"] = ["neutral", "eat"]
        row = self.resolve("embarrassed")
        self.assertEqual(row["reason"], "expression_interface_pending")
        self.assertIsNone(row["command"])

    def test_directory_session_change_cancels_shy_plan(self):
        self.control["catalog"]["session_id"] = "old-session"
        self.assertEqual(self.resolve("embarrassed")["reason"], "catalog_session_changed")


if __name__ == "__main__":
    unittest.main()
