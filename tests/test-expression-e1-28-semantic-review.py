from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build-expression-e1-28-semantic-review.py"
SPEC = importlib.util.spec_from_file_location("expression_e1_28_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


class ExpressionSemanticReviewE128Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tmp").mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e128-review-")
        cls.addClassCleanup(folder.cleanup)
        files = dict(BUILDER.SOURCE_FILES)
        for name in ("application", "emote"):
            files[name] = ROOT / "tests/fixtures/expression-e1-28" / files[name].name
        for name, value in (("SOURCE_FILES", files), ("OUT", Path(folder.name))):
            p = patch.object(BUILDER, name, value)
            p.start()
            cls.addClassCleanup(p.stop)

    def test_four_bindings_and_independent_action_branches_are_locked(self):
        data, report = BUILDER.build_data()
        self.assertEqual(set(data["bindings"]), {"happy", "embarrassed", "sleepy", "neutral"})
        self.assertEqual(data["bindings"]["happy"]["resource_id"], "Happy.eaf")
        self.assertEqual(data["bindings"]["embarrassed"]["resource_id"], "Happy.eaf")
        self.assertEqual(data["bindings"]["happy"]["action_id"], "shark_head")
        self.assertEqual(data["bindings"]["embarrassed"]["action_id"], "cat_nuzzle")
        self.assertEqual(data["bindings"]["sleepy"]["resource_id"], "sleep.eaf")
        self.assertEqual(data["bindings"]["neutral"]["resource_id"], "winking.eaf")
        self.assertEqual(len(data["semantics"]), 4)
        self.assertIs(report["flash_authorized"], False)
        for name in ("Happy.eaf", "sleep.eaf", "winking.eaf"):
            atlas = data["assets"][name]
            self.assertIs(atlas["atlas_verified"], True)
            self.assertGreater(atlas["atlas_bytes"], 0)
            self.assertEqual(report["asset_selection"]["hashes"][name]["atlas_bytes"], atlas["atlas_bytes"])
            self.assertIs(report["asset_selection"]["hashes"][name]["atlas_verified"], True)
        self.assertIn("独立支路", " ".join(report["checks"]))

    def test_touch_and_oral_summary_do_not_become_fabricated_emotion_counts(self):
        data, report = BUILDER.build_data()
        embarrassed = next(row for row in data["semantics"] if row["id"] == "embarrassed")
        self.assertIn("WakeWordInvoke", " ".join(item["detail"] for item in embarrassed["sources"]))
        self.assertNotIn("SetEmotion(\"embarrassed\")", " ".join(item["detail"] for item in embarrassed["sources"]))
        self.assertEqual(data["oral_summary"]["reported_recent_record_count"], 24)
        self.assertIs(data["oral_summary"]["raw_json_available"], False)
        self.assertEqual(len(data["oral_summary"]["reported_semantics"]), 4)
        self.assertIn("精确次数", " ".join(report["limits"]))
        self.assertIn("仅在 Idle 时", embarrassed["trigger_summary"])
        self.assertIn("SetPreviewImage", data["source_refs"]["power_save"]["snippet"])

    def test_self_contained_page_keeps_reference_and_hardware_boundaries(self):
        BUILDER.build(check=False)
        page = (BUILDER.OUT / "index.html").read_text(encoding="utf-8")
        report = json.loads((BUILDER.OUT / "report.json").read_text(encoding="utf-8"))
        for token in ("e128-data", "e128-js", "happy", "embarrassed", "sleepy", "neutral",
                      "connect-src 'none'", "参考合成", "不模拟底座执行结果", "两条独立支路", "branch-line"):
            self.assertTrue(token in page, f"missing page contract: {token}")
        self.assertNotIn("EAF → 动作分支", page)
        self.assertNotIn("__E128_", page)
        self.assertNotRegex(page, r"\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(")
        for key in ("deployment_eligible", "flash_authorized", "hardware_accessed",
                    "serial_accessed", "device_written", "nvs_changed"):
            self.assertIs(report[key], False)

    def test_check_mode_rejects_stale_output(self):
        BUILDER.build(check=False)
        path = BUILDER.OUT / "README.txt"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"stale")
            with self.assertRaisesRegex(ValueError, "stale generated artifact"):
                BUILDER.build(check=True)
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
