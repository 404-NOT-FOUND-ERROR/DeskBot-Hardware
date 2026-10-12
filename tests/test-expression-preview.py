"""Offline tests for the fake expression/action workbench."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "expression_preview", ROOT / "tools/preview-expression-workbench.py"
)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)


def capabilities() -> dict:
    return {
        "schema": "deskbot.expression_capabilities/v1",
        "expressions": [
            {"id": "happy", "src": "Happy.eaf", "loop": True, "fps": 20,
             "eaf": {"frames": 20}},
            {"id": "chef_smile", "src": "Chef.eaf", "loop": False, "fps": 20,
             "eaf": {"frames": 10}},
        ],
        "bindings": [{"id": "joy", "target": "chef_smile"}],
        "actions": [
            {"id": "shark_head", "protocol_value": 0},
            {"id": "look_around", "protocol_value": 2},
            {"id": "greet", "steps": [{"action": "look_around", "duration_ms": 400},
                                           {"action": "shark_head", "duration_ms": 200}]},
        ],
        "emotion_action_map": {"happy": "shark_head", "joy": "greet"},
    }


class PreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "capabilities.json"
        self.path.write_text(json.dumps(capabilities()), encoding="utf-8")
        self.caps = MOD.load_capabilities(self.path)

    def test_visual_only_never_emits_device_action(self) -> None:
        out = MOD.build_preview(self.caps,
                                mode="visual_only_preview", expression="happy")
        self.assertEqual(out["result"], "simulated")
        self.assertFalse(out["hardware_accessed"])
        self.assertEqual([e["channel"] for e in out["timeline"]], ["render.expression"])
        self.assertEqual(out["timeline"][0]["type"], "expression.preview")

    def test_emotion_perform_separates_render_and_action(self) -> None:
        out = MOD.build_preview(self.caps,
                                mode="emotion_perform", expression="happy")
        self.assertEqual([e["channel"] for e in out["timeline"]],
                         ["render.expression", "device.action"])
        self.assertEqual(out["timeline"][1]["payload"]["action"], "shark_head")
        self.assertEqual(out["timeline"][1]["at_ms"], 0)
        self.assertEqual(out["timeline"][1]["payload"]["physical_completion"], "unobserved")

    def test_action_execute_expands_recipe_without_expression(self) -> None:
        out = MOD.build_preview(self.caps,
                                mode="action_execute", action="greet")
        self.assertEqual(len(out["timeline"]), 2)
        self.assertEqual([e["payload"]["action"] for e in out["timeline"]],
                         ["look_around", "shark_head"])
        self.assertEqual(out["simulation_window_ms"], 600)
        self.assertFalse(out["physical_action_timing_verified"])

    def test_binding_and_unknown_inputs(self) -> None:
        out = MOD.build_preview(self.caps,
                                mode="emotion_perform", expression="joy")
        self.assertEqual(out["timeline"][0]["payload"]["resolved"], "chef_smile")
        with self.assertRaises(MOD.PreviewError):
            MOD.build_preview(self.caps,
                              mode="visual_only_preview", expression="missing")
        with self.assertRaises(MOD.PreviewError):
            MOD.build_preview(self.caps,
                              mode="action_execute", action="raw_uart")

    def test_manifest_can_be_loaded_and_output_is_deterministic(self) -> None:
        first = MOD.build_preview(self.caps, mode="visual_only_preview", expression="happy")
        second = MOD.build_preview(self.caps, mode="visual_only_preview", expression="happy")
        self.assertEqual(first, second)

    def test_modes_reject_mixed_expression_and_action_inputs(self) -> None:
        with self.assertRaises(MOD.PreviewError):
            MOD.build_preview(self.caps, mode="visual_only_preview",
                              expression="happy", action="shark_head")
        with self.assertRaises(MOD.PreviewError):
            MOD.build_preview(self.caps, mode="action_execute",
                              expression="happy", action="shark_head")
        with self.assertRaises(MOD.PreviewError):
            MOD.build_preview(self.caps, mode="action_execute",
                              action="shark_head", duration_ms=100)

    def test_manifest_recipe_uses_fixed_official_whitelist(self) -> None:
        data = capabilities()
        data.pop("schema")
        data["actions"] = [data["actions"][-1]]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        caps = MOD.load_capabilities(self.path)
        output = MOD.build_preview(caps, mode="action_execute", action="greet")
        self.assertEqual(len(output["timeline"]), 2)
        caps["actions"]["greet"]["steps"][0]["action"] = "raw_uart"
        with self.assertRaises(MOD.PreviewError):
            MOD.build_preview(caps, mode="action_execute", action="greet")
        caps["actions"]["greet"]["steps"][0]["action"] = "calibrate"
        with self.assertRaises(MOD.PreviewError):
            MOD.build_preview(caps, mode="action_execute", action="greet")

    def test_untrusted_action_and_incompatible_version_are_rejected(self) -> None:
        data = capabilities()
        data["actions"].append({"id": "raw_uart"})
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(MOD.PreviewError):
            MOD.load_capabilities(self.path)
        data = capabilities()
        data["schema"] = "deskbot.expression_capabilities/v99"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(MOD.PreviewError):
            MOD.load_capabilities(self.path)

    def test_expression_without_motion_remains_display_only(self) -> None:
        output = MOD.build_preview(self.caps, mode="emotion_perform", expression="chef_smile")
        self.assertEqual([event["channel"] for event in output["timeline"]],
                         ["render.expression"])

    def test_invalid_simulation_durations_are_rejected(self) -> None:
        for duration in (0, -1, 120001, True, 1.5):
            with self.assertRaises(MOD.PreviewError):
                MOD.build_preview(self.caps, mode="visual_only_preview",
                                  expression="happy", duration_ms=duration)
        self.caps["actions"]["greet"]["steps"][0]["duration_ms"] = 0.5
        with self.assertRaises(MOD.PreviewError):
            MOD.build_preview(self.caps, mode="action_execute", action="greet")


if __name__ == "__main__":
    unittest.main()
