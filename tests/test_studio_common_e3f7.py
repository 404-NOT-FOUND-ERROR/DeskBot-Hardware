"""Eight semantics through real endpoints, host/schema compatibility and roles."""
from copy import deepcopy
import json
import subprocess
import unittest

import jsonschema
from protocol import studio_control_bridge as bridge
from protocol import studio_control_link as link
from protocol import studio_display_query as display
from protocol import studio_role_expression as planner
from protocol import studio_role_layout as layouts
from tests import test_studio_control_native as integration
from tests import test_studio_expression_control as native
from tests.test_studio_control_link import display as display_fixture
from tests import test_studio_reflow_e3f4e as reflow

COMMON = ("happy", "sad", "sleepy", "confused", "surprised")
ALL = ("neutral", "embarrassed", "eat", *COMMON)
LEGACY = ALL[:3]
BINDINGS = {
    "happy": ("Happy.eaf", "shark_head"), "sad": ("Sad.eaf", "shark_head_decay"),
    "sleepy": ("sleep.eaf", "shark_head_decay"), "confused": ("confused.eaf", "look_around"),
    "surprised": ("shocked.eaf", "look_around"),
}
ROOT = native.ROOT
DIRECTORY = ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json"


class CommonEnabledTests(native.ExpressionControlTests):
    SHY_ENABLED = EAT_ENABLED = COMMON_ENABLED = True

    def test_each_common_semantic_dispatches_the_captured_request(self):
        for index, semantic in enumerate(COMMON):
            with self.subTest(semantic=semantic):
                wire = self.wire(command=dict(kind="play_expression", semantic_id=semantic))
                later = self.wire(request_id="later", sequence=2)
                rows, stats = self.trace(("D", 500, wire), ("D", 800, later), ("U", 810), ("Z", 820))
                self.assertEqual([r["status"] for r in rows], ["accepted", "busy", "dispatched"])
                self.assertEqual(stats[:2], [1, 1])
                self.assertEqual(stats[-5:], [int(i == index) for i in range(5)])

    def test_new_semantics_replay_once_and_changed_semantic_conflicts(self):
        for semantic in COMMON:
            with self.subTest(semantic=semantic):
                wire = self.wire(command=dict(kind="play_expression", semantic_id=semantic))
                rows, stats = self.trace(("D", 500, wire), ("D", 800, self.wire()),
                    ("U", 810), ("Z", 820), ("D", 1100, wire))
                self.assertEqual(rows[1]["reason"], "request_id_conflict")
                self.assertEqual((rows[-1]["status"], rows[-1]["original_status"]), ("duplicate", "dispatched"))
                self.assertEqual((stats[0], sum(stats[-5:])), (1, 1))

    def test_new_semantics_cancel_before_dispatch_on_owner_or_session_change(self):
        for semantic in COMMON:
            for change in (("P", 510, 2), ("A", 510, 6), ("X", 510), ("T", 3500)):
                with self.subTest(semantic=semantic, change=change):
                    wire = self.wire(command=dict(kind="play_expression", semantic_id=semantic))
                    rows, stats = self.trace(("D", 500, wire), change, ("U", 3510), ("Z", 3520))
                    self.assertIn(rows[-1]["status"], ("interrupted", "expired"))
                    self.assertEqual((stats[0], sum(stats[-5:])), (0, 0))

    def test_maximum_display_identifiers_and_counters_fit_one_kib(self):
        run = subprocess.run([str(self.exe), "display-boundary"], capture_output=True, check=True, timeout=20)
        row = json.loads(run.stdout)
        self.assertLess(len(run.stdout), 1024)
        self.assertEqual(row["expression_semantics"], list(ALL))
        display.validate_reply(row)
        jsonschema.validate(row, json.loads((ROOT / "protocol/deskbot-studio-display-v1.schema.json").read_bytes()))


class CommonDisabledTests(native.ExpressionControlTests):
    SHY_ENABLED = EAT_ENABLED = True

    def test_common_off_rejects_new_semantics_and_keeps_three_old_ones(self):
        for semantic in ALL:
            wire = self.wire(command=dict(kind="play_expression", semantic_id=semantic))
            rows, stats = self.trace(("D", 500, wire), ("U", 510), ("Z", 520))
            if semantic in COMMON:
                self.assertEqual((rows[0]["reason"], stats[0]), ("unsupported_expression_or_action", 0))
            else:
                self.assertEqual(([r["status"] for r in rows], stats[0]), (["accepted", "dispatched"], 1))

    def test_common_off_advertises_exact_E5_capabilities(self):
        run = subprocess.run([str(self.exe), "display-boundary"], capture_output=True, check=True, timeout=20)
        self.assertEqual(json.loads(run.stdout)["expression_semantics"], list(LEGACY))


class CommonOnlyTests(native.ExpressionControlTests):
    COMMON_ENABLED = True

    def test_common_only_dispatches_all_five_without_eat_or_shy(self):
        for index, semantic in enumerate(COMMON):
            wire = self.wire(command=dict(kind="play_expression", semantic_id=semantic))
            rows, stats = self.trace(("D", 500, wire), ("U", 510), ("Z", 520))
            self.assertEqual([r["status"] for r in rows], ["accepted", "dispatched"])
            self.assertEqual(stats[-5:], [int(i == index) for i in range(5)])
        run = subprocess.run([str(self.exe), "display-boundary"], capture_output=True, check=True, timeout=20)
        self.assertEqual(json.loads(run.stdout)["expression_semantics"], ["neutral", *COMMON])


class CapabilityContractTests(unittest.TestCase):
    def test_host_and_both_schemas_share_the_same_semantics(self):
        self.assertEqual(display.EXPRESSION_SEMANTICS[:8], ALL)
        ds = json.loads((ROOT / "protocol/deskbot-studio-display-v1.schema.json").read_bytes())["properties"]["expression_semantics"]
        cs = json.loads((ROOT / "protocol/deskbot-studio-control-link-v1.schema.json").read_bytes())
        self.assertEqual((ds["items"]["enum"], ds["maxItems"]), (list(display.EXPRESSION_SEMANTICS), 29))
        # Role activations carry the installed graph digest as part of the operation.
        self.assertEqual(cs["properties"]["last_command"]["oneOf"][1]["properties"]["operation"]["maxLength"], 96)

    def test_absent_empty_legacy_and_eight_are_distinct(self):
        for caps, expected in ((None, ["neutral"]), ([], []), (list(LEGACY), list(LEGACY)), (list(ALL), list(ALL))):
            row = display_fixture("query-one")
            if caps is not None:
                row["expression_semantics"] = caps
            display.validate_reply(row)
            self.assertEqual([s for s in ALL if display.supports(row, s)], expected)

    def test_malformed_capabilities_rejected_by_python_and_schema(self):
        schema = json.loads((ROOT / "protocol/deskbot-studio-display-v1.schema.json").read_bytes())
        for invalid in (["happy", "happy"], ["unregistered"], [*ALL, "happy"], [1], "happy", None):
            row = display_fixture("query-one")
            row["expression_semantics"] = invalid
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError): display.validate_reply(row)
                with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(row, schema)
                self.assertFalse(display.supports(row, "happy"))

    def test_host_accepts_eight_operations_but_unknown_is_rejected(self):
        command = dict(operation="neutral", command_id="click-one", connection_id="connection-one",
                       target_id=None, boot_id=None, session_id=None)
        for semantic in ALL:
            self.assertEqual(link.validate_command(command | dict(operation=semantic))["operation"], semantic)
        with self.assertRaisesRegex(ValueError, "invalid_host_command"):
            link.validate_command(command | dict(operation="listen"))


class CommonRoleLinkTests(integration.NativeLinkTests):
    SHY_ENABLED = EAT_ENABLED = COMMON_ENABLED = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.role_reference = json.loads(DIRECTORY.read_bytes())
        cls.requirements = layouts.load_requirements(cls.role_catalog)

    def ready(self):
        super().ready()
        state = self.link.snapshot(400)
        self.link.catalog = dict(status="complete", reason="verified", manifest=deepcopy(self.role_reference),
            received_entries=37, total=37, next_cursor=None,
            **{k: state[k] for k in ("target_id", "boot_id", "session_id")})

    def test_current_directory_and_eight_capabilities_reach_host_bridge(self):
        self.ready()
        state = self.link.snapshot(410)
        self.assertEqual(state["display"]["expression_semantics"], list(ALL))
        self.assertTrue(bridge.validate_control(state))

    def test_eight_role_nodes_mix_with_legacy_and_dispatch_once(self):
        self.ready()
        draft = deepcopy(self.role_catalog["initial"])
        draft["roles"][0]["bindings"]["embarrassed"] = "shy_candidate.eaf"
        sequence = ("eat", "neutral", "happy", "sad", "sleepy", "confused", "surprised", "embarrassed", "eat")
        for index, semantic in enumerate(sequence):
            at = 1200 + index * 1600
            query = self.link.tick(at - 400)
            if query:
                self.run_device([("D", at - 400, query)], at - 400)
            state = self.link.snapshot(at)
            plan = planner.resolve(draft, semantic, self.role_catalog, self.role_reference, control=state,
                source="device", context={k: state[k] for k in planner.CONTEXT_KEYS}, request_id=f"common-node-{index}",
                issued_ms=at, now_ms=at, layout_requirements=self.requirements)
            self.assertEqual(plan["status"], "ready")
            if semantic in BINDINGS:
                installed = next(r for r in self.role_reference["entries"] if r["kind"] == "expression" and r["id"] == semantic)
                self.assertEqual((plan["resource_id"], installed["implicit_action_id"]), BINDINGS[semantic])
                self.assertEqual((plan["playback"]["layout_id"], plan["playback"]["fps"], plan["playback"]["loop"]), ("eye_anim", 20, True))
            self.link.command(plan["command"], at)
            self.link.command(plan["command"], at + 1)
            wire = self.link.tick(at + 1)
            self.assertEqual(json.loads(wire)["command"]["semantic_id"], semantic)
            _, stats = self.run_device([("D", at + 1, wire), ("U", at + 10), ("Z", at + 20)], at + 20)
            self.assertEqual(stats[0], index + 1)
            self.assertIsNone(self.link.pending)
            self.assertEqual(self.link.receipts[-1]["status"], "dispatched")
            self.assertTrue(bridge.validate_control(self.link.snapshot(at + 20)))
        self.assertEqual(stats[3:], [1, 2, 1, 1, 1, 1, 1])

    def test_old_device_capabilities_keep_five_nodes_unavailable(self):
        self.ready()
        draft = deepcopy(self.role_catalog["initial"])
        state = self.link.snapshot(410)
        for caps in (list(LEGACY), [], None):
            if caps is None:
                state["display"].pop("expression_semantics", None)
            else:
                state["display"]["expression_semantics"] = caps
            for semantic in COMMON:
                plan = planner.resolve(draft, semantic, self.role_catalog, self.role_reference, control=state,
                    source="device", context={k: state[k] for k in planner.CONTEXT_KEYS}, request_id="legacy-check",
                    issued_ms=410, now_ms=410, layout_requirements=self.requirements)
                self.assertEqual(plan["reason"], "expression_interface_pending")
                self.assertIsNone(plan["command"])


class CommonPlayerGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reflow.ReflowTests.setUpClass.__func__(cls)

    fixture = reflow.ReflowTests.fixture
    run_player = reflow.ReflowTests.run_player
    assert_frames = reflow.ReflowTests.assert_frames

    def test_real_e145_common_animations_mix_with_full_frame_and_new_eye_size(self):
        sequence = "eat,happy,sad,sleepy,confused,surprised,embarrassed,neutral,eat"
        rows = self.run_player(sequence)
        self.assert_frames(rows)
        self.assertEqual([r["full"] for r in rows], [True, False, False, False, False, False, False, False, True])


if __name__ == "__main__":
    unittest.main()
