#!/usr/bin/env python3
"""Validate the published E1.16 report without requiring private raw inputs."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/expression-e1-16-repack-20261024"


class ExpressionE116Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ARTIFACT / "report.json").read_text(encoding="utf-8"))
        cls.page = (ARTIFACT / "index.html").read_text(encoding="utf-8")

    def test_actual_capacity_and_single_index_change(self):
        report = self.report
        self.assertEqual(report["edition"], "E1.16")
        self.assertEqual(report["result"], "offline_repack_verified")
        self.assertFalse(report["deployment_eligible"])
        self.assertEqual((report["baseline"]["bytes"], report["candidate"]["bytes"], report["capacity"]["delta_bytes"]),
                         (5_538_349, 5_538_351, 2))
        self.assertEqual(report["candidate"]["remaining_bytes"], 605_649)
        self.assertEqual(report["member_diff"]["changed"], ["index.json"])
        self.assertEqual(report["member_diff"]["added"], [])
        self.assertEqual(report["member_diff"]["removed"], [])
        self.assertEqual(report["member_diff"]["eaf_payloads_changed"], [])
        self.assertTrue(report["rollback"]["package_byte_exact"])
        self.assertTrue(report["rollback"]["full_partition_byte_exact"])

    def test_baseline_and_candidate_hashes_are_fixed(self):
        self.assertEqual(self.report["baseline"]["sha256"],
                         "5e342db35da13531836339a98c25d6e74ac6fdff446ad4e515fcf39f28b7e81f")
        self.assertEqual(self.report["candidate"]["sha256"],
                         "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1")
        self.assertEqual(self.report["partition"]["archived_readback_sha256"],
                         "6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205")
        self.assertEqual(self.report["index_patch"]["delta_bytes"], 2)
        self.assertTrue(self.report["index_patch"]["all_other_index_bytes_unchanged"])

    def test_player_and_hardware_boundaries_remain_locked(self):
        self.assertFalse(self.report["safety"]["hardware_accessed"])
        self.assertFalse(self.report["safety"]["firmware_built"])
        self.assertFalse(self.report["safety"]["device_written"])
        self.assertFalse(self.report["safety"]["c61_changed"])
        self.assertTrue(self.report["preserved"]["timing"])
        self.assertTrue(self.report["preserved"]["layout"])
        self.assertEqual(self.report["preserved"]["surprised_action"], "look_around")
        self.assertEqual([x["status"] for x in self.report["gates"]],
                         ["passed", "pending", "pending", "pending", "pending"])

    def test_workbench_payload_and_offline_csp(self):
        match = re.search(r'<script id="e116-data" type="application/json">(.*?)</script>', self.page, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match[1]), self.report)
        self.assertIn("connect-src 'none'", self.page)
        self.assertNotIn("COM4", self.page)
        self.assertNotIn("9c:cc:01:fb:a8:78", self.page)


if __name__ == "__main__":
    unittest.main()
