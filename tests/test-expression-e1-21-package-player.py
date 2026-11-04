#!/usr/bin/env python3
"""Public contract checks for the E1.21 offline package/player report."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/expression-e1-21-package-player-20261026"


class ExpressionE121Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ARTIFACT / "report.json").read_text(encoding="utf-8"))
        cls.page = (ARTIFACT / "index.html").read_text(encoding="utf-8")

    def test_candidate_is_a_single_index_change_with_exact_rollback(self):
        report = self.report
        self.assertEqual(report["edition"], "E1.21")
        self.assertEqual(report["result"], "offline_package_player_rollback_passed")
        self.assertTrue(report["audit_pass"])
        self.assertFalse(report["deployment_eligible"])
        self.assertEqual(report["mapping"]["semantic"], "surprised")
        self.assertEqual((report["mapping"]["before"], report["mapping"]["after"]),
                         ("shocked.eaf", "Happy.eaf"))
        comparison = report["package_comparison"]
        self.assertEqual(comparison["changed_members"], ["index.json"])
        self.assertEqual(comparison["eaf_payloads_changed"], [])
        self.assertTrue(comparison["member_order_unchanged"])
        self.assertTrue(comparison["all_non_index_members_byte_identical"])
        self.assertTrue(comparison["candidate_matches_e113_exact"])
        self.assertEqual(report["candidate_package"]["bytes"], 5_538_349)
        self.assertEqual(report["candidate_package"]["remaining_bytes"], 605_651)
        self.assertTrue(report["rollback"]["candidate_to_e118_exact"])
        self.assertTrue(report["rollback"]["candidate_to_e113_exact"])
        self.assertFalse(report["rollback"]["device_rollback_performed"])

    def test_official_player_cases_and_safety_boundary(self):
        report = self.report
        self.assertEqual(set(report["player"]["cases"]), {"happy", "shocked"})
        for case in report["player"]["cases"].values():
            self.assertTrue(case["case_pass"])
            self.assertEqual(case["c_rendered_frames"], case["image_frame_count"])
            self.assertTrue(case["all_frame_pixels_match"])
            self.assertTrue(case["all_frame_guards_clean"])
            self.assertTrue(case["metadata_frame_rejected_safely"])
            self.assertTrue(case["stop_restart_starts_at_zero"])
            self.assertTrue(case["repeated_reload_cleanup_passed"])
            self.assertTrue(case["nonloop_timer_passed"])
            self.assertTrue(case["loop_timer_passed"])
        self.assertTrue(all(value is False for value in report["safety"].values()))
        self.assertEqual([gate["status"] for gate in report["gates"]],
                         ["passed"] * 5 + ["pending"])

    def test_offline_visualization_embeds_the_exact_report(self):
        match = re.search(r'<script id="e121-data" type="application/json">(.*?)</script>',
                          self.page, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match[1]), self.report)
        self.assertIn("connect-src 'none'", self.page)
        self.assertFalse(self.report["safety"]["hardware_accessed"])
        self.assertFalse(self.report["safety"]["serial_accessed"])
        self.assertIsNone(re.search(r"[A-Za-z]:[\\/].*(?:Codex Project|Users[\\/])", self.page, re.I))
        self.assertEqual(list(ARTIFACT.glob("*.bin")), [])


if __name__ == "__main__":
    unittest.main()
