from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deskbot_e144_tests", ROOT / "scripts/audit-expression-e1-44-adaptation.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class AdaptationReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((AUDIT.PUBLIC / "report.json").read_bytes())

    def rejected(self, mutate):
        data = copy.deepcopy(self.report)
        mutate(data)
        with self.assertRaises(ValueError):
            AUDIT.validate_report(data)

    def test_complete_portable_report(self):
        AUDIT.validate_report(self.report)
        self.assertEqual(sum(row["native"]["full_frame_render"]["rendered_frame_count"]
                             for row in self.report["variants"]), 752)

    def test_old_capacity_and_changed_baseline_are_rejected(self):
        for key, value in (("partition_bytes", 6144000), ("layout", "legacy"),
                           ("package_sha256", "0" * 64), ("free_bytes", 0)):
            with self.subTest(key=key):
                self.rejected(lambda d: d["baseline"].__setitem__(key, value))

    def test_offline_scope_and_unknown_marker_are_rejected(self):
        for key in ("hardware_accessed", "firmware_changed", "package_emitted"):
            self.rejected(lambda d: d.__setitem__(key, True))
        self.rejected(lambda d: d["terminal"].__setitem__("bytes", 4))

    def test_claimed_pass_does_not_hide_missing_or_bad_native_frames(self):
        self.rejected(lambda d: d["variants"][0]["native"]["full_frame_render"]["checks"].pop())
        self.rejected(lambda d: d["variants"][0]["native"]["full_frame_render"]["checks"][0]
                      .__setitem__("mismatch_pixels", 1))
        self.rejected(lambda d: d["variants"][1]["native"]["full_frame_render"]["checks"][-1]
                      .__setitem__("actual_sha256", "0" * 64))

    def test_claimed_pass_does_not_hide_missing_last_frame_or_timer_corruption(self):
        self.rejected(lambda d: d["variants"][0]["native"]["default_timers"][0]["trace"][-4]
                      .__setitem__("one_done", 187))
        self.rejected(lambda d: d["variants"][1]["native"]["default_timers"][1]["trace"][188]
                      .__setitem__("current_frame", 188))
        self.rejected(lambda d: d["variants"][0]["native"]["default_timers"][2]
                      .__setitem__("last_frame_rgb565_sha256", "0" * 64))
        self.rejected(lambda d: d["variants"][0]["native"]["default_timers"].pop())

    def test_extra_mapping_change_and_lost_shared_reference_are_rejected(self):
        self.rejected(lambda d: d["variants"][0]["capacity"]["semantic_changes"].append({"name": "happy"}))
        self.rejected(lambda d: d["variants"][0]["capacity"]["other_happy_references"].pop())
        self.rejected(lambda d: d["variants"][0]["capacity"].__setitem__("surprised_resource", "Happy.eaf"))
        self.rejected(lambda d: d["variants"][0]["capacity"]["semantic_changes"][0]["after"]["eaf"]
                      .__setitem__("fps", 20))
        self.rejected(lambda d: d["semantic_draft"].__setitem__("action_branch_changed", True))

    def test_both_variants_fit_new_soft_limit_but_only_eye_uses_current_layout(self):
        for row in self.report["variants"]:
            self.assertGreater(row["capacity"]["soft_limit_remaining_bytes"], 0)
            self.assertEqual(row["layout"]["current_eye_route_compatible"], row["id"] == "eye")
            self.assertEqual(row["layout"]["full_composition_preserved"], row["id"] == "full")
            self.assertFalse(row["layout"]["visual_accepted"])


if __name__ == "__main__":
    unittest.main()
