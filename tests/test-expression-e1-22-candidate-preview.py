"""Public contract tests for the E1.22 offline before/after preview."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/expression-e1-22-candidate-preview-20261026"


class ExpressionE122Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ARTIFACT / "report.json").read_text(encoding="utf-8"))
        cls.page = (ARTIFACT / "index.html").read_text(encoding="utf-8")
        match = re.search(r'<script id="e122-data" type="application/json">(.*?)</script>', cls.page, re.S)
        if not match:
            raise AssertionError("missing embedded E1.22 payload")
        cls.payload = json.loads(match.group(1))

    def test_only_the_expected_route_changes_and_candidate_is_not_deployable(self):
        report = self.report
        self.assertEqual(report["edition"], "E1.22")
        self.assertEqual(report["result"], "offline_before_after_preview_ready")
        self.assertTrue(report["audit_pass"])
        self.assertFalse(report["deployment_eligible"])
        comparison = report["comparison"]
        self.assertEqual(comparison["semantic"], "surprised")
        self.assertEqual(comparison["before_resource"], "shocked.eaf")
        self.assertEqual(comparison["candidate_resource"], "Happy.eaf")
        self.assertEqual(comparison["old_edge"]["operation"], "disconnect")
        self.assertEqual(comparison["candidate_edge"]["operation"], "connect")
        self.assertTrue(comparison["action_unchanged"])
        self.assertTrue(comparison["layout_unchanged"])
        self.assertTrue(comparison["timing_unchanged"])
        self.assertEqual(comparison["action_id"], "look_around")
        self.assertEqual(comparison["layout_id"], "eye_anim")
        self.assertEqual(comparison["fps"], 20)
        self.assertTrue(comparison["loop"])

    def test_preview_assets_are_anchored_official_decoded_atlases(self):
        for resource, frames, digest in (
            ("shocked.eaf", 34, "bf97df5acdb7b64a34bb1f32ee2d5fd98a1117c6f06daad4a30e99ec44b47cf8"),
            ("Happy.eaf", 42, "2ed3a64ecdb7a78e2c9959bfc3758c2742d5447d4a2bcdc6d2bf072763deba5c"),
        ):
            asset = self.payload["previews"][resource]
            self.assertEqual(asset["origin"], "official")
            self.assertEqual(asset["sha256"], digest)
            self.assertEqual(asset["frame_count"], frames)
            self.assertEqual(asset["fps"], 20)
            self.assertTrue(asset["loop"])
            self.assertTrue(asset["atlas"].startswith("data:image/png;base64,"))
            self.assertIn("official decoded source frames", asset["preview_basis"])
            self.assertEqual(self.report["previews"][resource]["sha256"], digest)

    def test_payload_safety_and_standalone_page_contract(self):
        self.assertEqual(self.payload["report"], self.report)
        self.assertIn("connect-src 'none'", self.page)
        self.assertNotRegex(self.page, r'<(?:script|link)[^>]+(?:src|href)=["\']https?://')
        self.assertIn("不是设备实拍", self.page)
        self.assertIn("断开旧边", self.page)
        self.assertIn("新建连接", self.page)
        self.assertIn("<canvas id=\"before\"", self.page)
        self.assertIn("<canvas id=\"candidate\"", self.page)
        self.assertTrue(all(value is False for value in self.report["safety"].values()))
        self.assertFalse(list(ARTIFACT.glob("*.bin")))


if __name__ == "__main__":
    unittest.main()
