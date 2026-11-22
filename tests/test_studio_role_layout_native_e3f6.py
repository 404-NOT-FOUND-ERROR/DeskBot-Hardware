"""Real E1.45 directory -> role plan -> production C++ request dispatch."""
from copy import deepcopy
import json
import unittest

from protocol import studio_role_expression as planner
from protocol import studio_role_layout as layouts
from protocol import studio_role_overview as overview
from tests import test_studio_reflow_e3f4e as reflow
from tests.test_studio_shy_e3f5 import E145LinkTests


class NativeCatalogRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reflow.ReflowTests.setUpClass.__func__(cls)
        cls.catalog = planner.packages.load_catalog()
        cls.requirements = layouts.load_requirements(cls.catalog)

    fixture = reflow.ReflowTests.fixture
    run_player = reflow.ReflowTests.run_player
    directory_manifest = reflow.ReflowTests.directory_manifest
    assert_frames = reflow.ReflowTests.assert_frames

    def test_native_37_directory_matches_entire_current_role(self):
        actual = self.directory_manifest()
        expected = json.loads((reflow.ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json").read_bytes())
        self.assertEqual(actual, expected)
        draft = deepcopy(self.catalog["initial"])
        draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        result = overview.overview(draft, self.catalog, actual, request_id="native-directory-role",
            issued_ms=100, now_ms=150, layout_requirements=self.requirements)
        self.assertEqual((result["resource_matches"], result["counts"]["needs_publish"]), (29, 0))

    def test_native_mixed_geometry_matches_node_layout_profiles(self):
        rows = self.run_player("eat,neutral,embarrassed,eat,embarrassed,neutral")
        self.assert_frames(rows)
        self.assertEqual([r["full"] for r in rows], [True, False, False, True, False, False])
        self.assertEqual([r["height"] for r in rows], [360, 160, 200, 360, 200, 160])


class NativeRoleRequestTests(E145LinkTests):
    def test_role_plans_use_current_layouts_and_dispatch_each_click_once(self):
        self.ready()
        draft = deepcopy(self.role_catalog["initial"])
        draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        requirements = layouts.load_requirements(self.role_catalog)
        for index, semantic in enumerate(("eat", "neutral", "embarrassed", "eat", "embarrassed", "neutral")):
            at = 1200 + index * 1600
            query = self.link.tick(at - 400)
            if query:
                self.run_device([("D", at - 400, query)], at - 400)
            state = self.link.snapshot(at)
            plan = planner.resolve(draft, semantic, self.role_catalog, self.role_reference, control=state,
                source="device", context={k: state[k] for k in planner.CONTEXT_KEYS},
                request_id="node-click-"+str(index), issued_ms=at, now_ms=at, layout_requirements=requirements)
            self.assertEqual((plan["status"], plan["playback"]["layout_id"]),
                             ("ready", "full_frame" if semantic == "eat" else "eye_anim"))
            self.link.command(plan["command"], at)
            self.link.command(plan["command"], at + 1)
            wire = self.link.tick(at + 1)
            self.assertIsNotNone(wire)
            _, stats = self.run_device([("D", at + 1, wire), ("U", at + 10), ("Z", at + 20)], at + 20)
            self.assertEqual(stats[0], index + 1)
            self.assertEqual(self.link.receipts[-1]["status"], "dispatched")
            self.assertIsNone(self.link.pending)


if __name__ == "__main__":
    unittest.main()
