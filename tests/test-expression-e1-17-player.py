#!/usr/bin/env python3
"""Public, private-input-free checks for the E1.17 player report."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/expression-e1-17-player-20261024"


class ExpressionE117Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ARTIFACT / "report.json").read_text(encoding="utf-8"))
        cls.page = (ARTIFACT / "index.html").read_text(encoding="utf-8")

    def test_player_contract_and_boundaries(self):
        report = self.report
        self.assertEqual(report["edition"], "E1.17")
        self.assertEqual(report["result"], "offline_c_player_compatibility_passed")
        self.assertTrue(report["audit_pass"])
        self.assertFalse(report["deployment_eligible"])
        self.assertFalse(report["safety"]["hardware_accessed"])
        self.assertFalse(report["safety"]["device_written"])
        self.assertEqual(report["semantic_contract"]["surprised_resource"], "shocked.eaf")
        self.assertTrue(report["semantic_contract"]["happy_resource_preserved"])

    def test_both_resources_cover_all_contracts(self):
        self.assertEqual(set(self.report["cases"]), {"happy", "shocked"})
        for case in self.report["cases"].values():
            self.assertTrue(case["case_pass"])
            self.assertEqual(case["c_rendered_frames"], case["image_frame_count"])
            self.assertTrue(case["all_frame_pixels_match"])
            self.assertTrue(case["all_frame_guards_clean"])
            self.assertTrue(case["metadata_frame_rejected_safely"])
            self.assertTrue(case["nonloop_timer_passed"])
            self.assertTrue(case["loop_timer_passed"])

    def test_workbench_payload_and_offline_csp(self):
        match = re.search(r'<script id="e117-data" type="application/json">(.*?)</script>', self.page, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match[1]), self.report)
        self.assertIn("connect-src 'none'", self.page)
        self.assertNotIn("COM4", self.page)
        self.assertNotIn("COM8", self.page)


if __name__ == "__main__":
    unittest.main()
