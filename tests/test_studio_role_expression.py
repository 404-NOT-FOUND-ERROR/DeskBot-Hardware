"""Role draft -> installed bindings -> existing request boundary."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from protocol import studio_role_expression as p
from protocol import studio_installed_catalog as installed
from protocol.studio_control_link import ControlLink

ROOT = Path(__file__).resolve().parents[1]


class RoleExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = p.packages.load_catalog()
        cls.reference = json.loads((ROOT / "artifacts/deskbot-studio-e3d1-20261105/installed-directory.json").read_bytes())

    def setUp(self):
        self.draft = deepcopy(self.catalog["initial"])
        self.context = dict(connection_id="connection-role-test", target_id="device-"+"a"*32,
                            boot_id="boot-"+"b"*32, session_id="session-"+"b"*32+"-1")
        self.control = dict(**self.context, enabled=True, age_ms=50, can_submit=True,
            catalog=dict(status="complete", manifest=deepcopy(self.reference),
                         **{k: self.context[k] for k in ("target_id", "boot_id", "session_id")}))

    def resolve(self, semantic="neutral", source="device", **kw):
        return p.resolve(self.draft, semantic, self.catalog, self.reference, source=source,
                         control=self.control, context=self.context, request_id="intent-one",
                         issued_ms=100, now_ms=kw.pop("now_ms", 200), **kw)

    def test_snapshot_is_preview_and_has_no_device_command(self):
        value = self.resolve(source="saved_snapshot")
        self.assertEqual(value["status"], "planned")
        self.assertIsNone(value["command"])

    def test_neutral_matches_current_resources_and_builds_existing_command(self):
        row = self.resolve()
        self.assertEqual((row["status"], row["reason"]), ("ready", "request_ready"))
        self.assertEqual(row["command"]["operation"], "neutral")
        self.assertEqual(row["command"]["connection_id"], self.context["connection_id"])
        self.assertEqual(row["command"], self.resolve()["command"])

    def test_offline_role_name_does_not_change_firmware_role_contract(self):
        self.draft["roles"][0]["name"] = "新的厨师"
        row = self.resolve()
        self.assertEqual(row["role_name"], "新的厨师")
        self.assertNotIn("role_id", row["command"])

    def test_most_semantics_need_interface_even_when_installed(self):
        row = self.resolve("happy")
        self.assertEqual(row["reason"], "expression_interface_pending")
        self.assertIsNone(row["command"])

    def test_rewired_shy_needs_resource_publication(self):
        self.draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        row = self.resolve("embarrassed")
        self.assertEqual(row["status"], "needs_publish")
        self.assertEqual(row["installed_resource_id"], "Happy.eaf")

    def test_published_shy_matches_before_pending_interface(self):
        self.draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        asset = self.catalog["assets"]["shy_candidate.eaf"]
        entries = deepcopy(self.reference["entries"])
        row = next(r for r in entries if r["kind"] == "expression" and r["id"] == "embarrassed")
        row.update(resource_id="shy_candidate.eaf", resource_sha256=asset["deployed_sha256"], bytes=asset["bytes"], fps=25, loop=True)
        self.control["catalog"]["manifest"] = installed.build_manifest(entries, "c"*64, "d"*64)
        self.assertEqual(self.resolve("embarrassed")["reason"], "expression_interface_pending")

    def test_unconnected_binding_and_missing_semantic_are_distinct(self):
        self.draft["roles"][0]["bindings"]["neutral"] = None
        self.assertEqual(self.resolve()["reason"], "binding_missing")
        self.draft = deepcopy(self.catalog["initial"])
        entries = [r for r in self.reference["entries"] if (r["kind"], r["id"]) != ("expression", "neutral")]
        self.control["catalog"]["manifest"] = installed.build_manifest(entries, "c"*64, "d"*64)
        self.assertEqual(self.resolve()["reason"], "semantic_not_installed")

    def test_expiry_busy_and_age_boundaries(self):
        self.assertEqual(self.resolve(now_ms=2100)["reason"], "intent_expired")
        self.control["can_submit"] = False
        self.assertEqual(self.resolve()["status"], "busy")
        self.control.update(age_ms=2000, can_submit=True)
        self.assertEqual(self.resolve()["reason"], "display_state_expired")

    def test_reconnection_and_catalog_session_invalidated(self):
        self.control["connection_id"] = "connection-new"
        self.assertEqual(self.resolve()["reason"], "connection_changed")
        self.control["connection_id"] = self.context["connection_id"]
        self.control["catalog"]["session_id"] = "session-old"
        self.assertEqual(self.resolve()["reason"], "catalog_session_changed")
        self.control["session_id"] = None
        self.assertEqual(self.resolve()["reason"], "session_required")

    def test_version_tamper_or_incomplete_catalog_does_not_create_command(self):
        self.control["catalog"]["status"] = "reading"
        self.assertEqual(self.resolve()["reason"], "catalog_required")
        self.draft["catalog_id"] = "a"*64
        with self.assertRaises(ValueError):
            self.resolve()

    def test_layout_or_playback_change_requires_publication(self):
        for field, value in (("fps", 21), ("loop", True)):
            entries = deepcopy(self.reference["entries"])
            row = next(r for r in entries if (r["kind"], r["id"]) == ("expression", "neutral"))
            row[field] = value
            self.control["catalog"]["manifest"] = installed.build_manifest(entries, "c"*64, "d"*64)
            self.assertEqual(self.resolve()["reason"], "resource_binding_changed")
        entries = deepcopy(self.reference["entries"])
        next(r for r in entries if r["kind"] == "layout" and r["id"] == "eye_anim")["mirror"] = "false"
        self.control["catalog"]["manifest"] = installed.build_manifest(entries, "c"*64, "d"*64)
        self.assertEqual(self.resolve()["reason"], "layout_changed")

    def test_implicit_action_wire_change_requires_publication(self):
        entries = deepcopy(self.reference["entries"])
        actions = [r for r in entries if r["kind"] == "action" and r["id"] in ("beat_swing", "cat_nuzzle")]
        actions[0]["wire_value"], actions[1]["wire_value"] = actions[1]["wire_value"], actions[0]["wire_value"]
        self.control["catalog"]["manifest"] = installed.build_manifest(entries, "c"*64, "d"*64)
        self.assertEqual(self.resolve("angry")["reason"], "action_changed")


if __name__ == "__main__":
    unittest.main()
