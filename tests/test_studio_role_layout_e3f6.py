"""Current layouts, legacy snapshots and production HTTP with a memory bridge."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler, Request

from protocol import studio_installed_catalog as installed
from protocol import studio_role_expression as expression
from protocol import studio_role_layout as layouts
from protocol import studio_role_overview as overview
from tests.test_deskbot_studio_e3e import FakeBridge, module

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json"


class LayoutRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = expression.packages.load_catalog()
        cls.requirements = layouts.load_requirements(cls.catalog)
        cls.reference = installed.validate_manifest(json.loads(DIRECTORY.read_bytes()))
        cls.legacy = json.loads((ROOT / "artifacts/deskbot-studio-e3d1-20261105/installed-directory.json").read_bytes())

    def setUp(self):
        self.draft = deepcopy(self.catalog["initial"])
        self.draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        self.bridge = FakeBridge(deepcopy(self.reference))
        self.bridge.state["display"] = dict(expression_semantics=["neutral", "eat", "embarrassed"])

    def resolve(self, semantic="eat", **kwargs):
        return expression.resolve(self.draft, semantic, self.catalog, kwargs.pop("reference", self.reference),
            control=self.bridge.state, context=self.bridge.context, source=kwargs.pop("source", "device"),
            request_id=kwargs.pop("request_id", "node-one"), issued_ms=100, now_ms=150,
            layout_requirements=kwargs.pop("layout_requirements", self.requirements), **kwargs)

    def mutate(self, kind, identity, **changes):
        rows = deepcopy(self.bridge.state["catalog"]["manifest"]["entries"])
        next(r for r in rows if (r["kind"], r["id"]) == (kind, identity)).update(changes)
        self.bridge.state["catalog"]["manifest"] = installed.build_manifest(rows, "c"*64, "d"*64)

    def test_current_eat_full_frame_and_shy_mirror(self):
        for semantic, mode, resource in (("eat", "full_frame", "eat.eaf"), ("neutral", "eye_anim", "winking.eaf"),
                                         ("embarrassed", "eye_anim", "shy_candidate.eaf")):
            with self.subTest(semantic=semantic):
                row = self.resolve(semantic)
                self.assertEqual((row["status"], row["playback"]["layout_id"], row["resource_id"]), ("ready", mode, resource))
                self.assertEqual(row["command"]["operation"], semantic)

    def test_saved_current_reference_has_29_matches_without_command_construction(self):
        with patch.object(expression, "validate_command", side_effect=AssertionError("constructed command")):
            row = overview.overview(self.draft, self.catalog, self.reference, source="saved_snapshot",
                request_id="overview-one", issued_ms=100, now_ms=150, layout_requirements=self.requirements)
        self.assertEqual(row["resource_matches"], 29)
        self.assertEqual(next(r for r in row["entries"] if r["semantic_id"] == "eat")["playback"]["layout_id"], "full_frame")

    def test_default_draft_reports_only_shy_change_in_current_reference(self):
        self.draft = deepcopy(self.catalog["initial"])
        row = overview.overview(self.draft, self.catalog, self.reference, source="saved_snapshot",
            request_id="overview-one", issued_ms=100, now_ms=150, layout_requirements=self.requirements)
        self.assertEqual((row["resource_matches"], row["counts"]["needs_publish"]), (28, 1))

    def test_legacy_without_registry_retains_original_eat_geometry(self):
        row = self.resolve(reference=self.legacy, source="saved_snapshot", layout_requirements=None)
        self.assertEqual((row["status"], row["playback"]["layout_id"]), ("planned", "eye_anim"))
        row = self.resolve(reference=self.legacy, source="saved_snapshot")
        self.assertEqual(row["reason"], "resource_binding_changed")

    def test_explicit_layout_parameters_are_checked_against_profile(self):
        for changes in ({"mirror": "auto"}, {"x": 10}, {"align": "GFX_ALIGN_LEFT_MID"}):
            self.bridge.state["catalog"]["manifest"] = deepcopy(self.reference)
            with self.assertRaisesRegex(ValueError, "invalid_catalog_layout"):
                self.mutate("layout", "full_frame", **changes)
        self.mutate("layout", "eye_anim", mirror="false")
        self.assertEqual(self.resolve("neutral")["reason"], "layout_changed")

    def test_registered_content_and_dimensions_cannot_fall_back(self):
        for key, value in (("resource_sha256", "e"*64), ("width", 359), ("resource_id", "Happy.eaf")):
            changed = deepcopy(self.requirements)
            changed["eat.eaf"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "content_changed"):
                self.resolve(layout_requirements=changed)

    def test_unregistered_asset_keeps_existing_layout(self):
        row = layouts.playback(self.catalog, "angry", "angry.eaf", self.requirements)
        self.assertEqual(row["layout_id"], "eye_anim")

    def test_alias_inherits_asset_layout_and_retains_semantic_action(self):
        self.draft["roles"][0]["bindings"]["embarrassed"] = "eat.eaf"
        asset = self.catalog["assets"]["eat.eaf"]
        self.mutate("expression", "embarrassed", resource_id="eat.eaf", resource_sha256=asset["deployed_sha256"],
                    bytes=asset["bytes"], layout_id="full_frame", fps=20, loop=True)
        row = self.resolve("embarrassed")
        self.assertEqual((row["status"], row["playback"]["layout_id"]), ("ready", "full_frame"))
        self.mutate("expression", "embarrassed", implicit_action_id=None)
        self.assertEqual(self.resolve("embarrassed")["reason"], "resource_binding_changed")

    def test_hash_playback_or_resource_change_requires_publish(self):
        for changes in ({"resource_sha256": "e"*64}, {"fps": 25}, {"loop": True}, {"layout_id": "eye_anim"}):
            self.bridge.state["catalog"]["manifest"] = deepcopy(self.reference)
            self.mutate("expression", "eat", **changes)
            self.assertEqual(self.resolve()["reason"], "resource_binding_changed")

    def test_current_firmware_keeps_shy_interface_pending(self):
        self.bridge.state["display"]["expression_semantics"] = ["neutral", "eat"]
        self.assertEqual(self.resolve("embarrassed")["reason"], "expression_interface_pending")

    def test_repeated_clicks_have_new_ids_and_same_intent_is_deterministic(self):
        first = self.resolve(request_id="click-one")["command"]
        self.assertEqual(first, self.resolve(request_id="click-one")["command"])
        self.assertNotEqual(first["command_id"], self.resolve(request_id="click-two")["command"]["command_id"])

    def test_registry_duplicate_or_content_change_is_rejected(self):
        raw = json.loads(layouts.PATH.read_bytes())
        with tempfile.TemporaryDirectory(prefix="role-layout-", dir=ROOT / "tmp") as folder:
            path = Path(folder) / "registry.json"
            for rows, reason in ((raw["assets"] + raw["assets"][:1], "duplicate"),
                                 ([{**raw["assets"][0], "resource_sha256": "e"*64}], "content_changed")):
                path.write_text(json.dumps({**raw, "assets": rows}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, reason):
                    layouts.load_requirements(self.catalog, path)


class HTTPRoleLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = module("e3f6_http_tests", "scripts/serve-deskbot-studio-e3f6.py")
        cls.catalog = expression.packages.load_catalog()
        cls.manifest = json.loads(DIRECTORY.read_bytes())

    def setUp(self):
        self.bridge = FakeBridge(deepcopy(self.manifest))
        self.bridge.state["display"] = dict(expression_semantics=["neutral", "eat", "embarrassed"])
        self.temp = tempfile.TemporaryDirectory(prefix="e3f6-http-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.server = self.service.StudioServer(0, bridge=self.bridge, output=self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(ProxyHandler({}))
        self.draft = deepcopy(self.catalog["initial"])
        self.draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def post(self, path, value, header):
        request = Request(self.origin + path, data=expression.packages.canonical(value),
            headers={"Content-Type": "application/json", "X-DeskBot-Studio": header})
        with self.opener.open(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def plan(self, semantic="eat", source="device"):
        return self.post("/api/role-expression/resolve", dict(draft=self.draft, semantic=semantic, source=source,
            context=self.bridge.context if source == "device" else None, request_id="click-http-one"), "e3e")

    def test_http_current_full_frame_then_explicit_send(self):
        plan = self.plan()
        self.assertEqual((plan["status"], plan["playback"]["layout_id"]), ("ready", "full_frame"))
        self.assertEqual(self.bridge.commands, [])
        self.post("/api/studio/command", plan["command"], "e3c6")
        self.assertEqual(self.bridge.commands, [plan["command"]])

    def test_snapshot_uses_37_reference_without_bridge_read(self):
        before = self.bridge.control_calls
        self.assertEqual(self.plan(source="saved_snapshot")["status"], "planned")
        self.assertEqual(self.bridge.control_calls, before)

    def test_overview_uses_registry_and_one_snapshot(self):
        before = self.bridge.control_calls
        row = self.post("/api/role-expression/overview", dict(draft=self.draft, source="device",
            context=self.bridge.context, request_id="overview-http"), "e3f2")
        self.assertEqual((row["resource_matches"], row["counts"]["ready"]), (29, 3))
        self.assertEqual(self.bridge.control_calls, before + 1)
        self.assertEqual(self.bridge.commands, [])

    def test_reconnection_during_analysis_creates_no_command(self):
        self.bridge.state["boot_id"] = "boot-" + "c"*32
        self.assertEqual(self.plan()["reason"], "connection_changed")
        self.assertEqual(self.bridge.commands, [])

    def test_health_and_page_identify_new_edition(self):
        with self.opener.open(self.origin + "/api/health", timeout=5) as response:
            row = json.load(response)
            self.assertEqual(row["edition"], "E3f-6")
            self.assertFalse(row["hardware_accessed"])
        with self.opener.open(self.origin + "/", timeout=5) as response:
            self.assertIn(b"data-e3f6-node", response.read())


if __name__ == "__main__":
    unittest.main()
