"""Public contract checks for E1.23 exact assets deployment staging."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "artifacts/expression-e1-23-deployment-preflight-20261026/report.json"
OUTPUT = ROOT / "tmp/expression-e1-23-deployment-preflight-20261026"


class ExpressionE123Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    def test_full_partition_candidate_and_rollback_match_accepted_hashes(self):
        report = self.report
        self.assertEqual(report["edition"], "E1.23")
        self.assertEqual(report["result"], "offline_deployment_artifacts_prepared")
        self.assertTrue(report["audit_pass"])
        self.assertEqual(report["partition"]["offset"], 0x5EC000)
        self.assertEqual(report["partition"]["length_bytes"], 0x5DC000)
        for key, expected_sha in (
            ("candidate", "6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205"),
            ("rollback", "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6"),
        ):
            item = report[key]
            image = (ROOT / item["path"]).read_bytes()
            self.assertEqual(len(image), item["partition_bytes"])
            self.assertEqual(len(image), 0x5DC000)
            self.assertEqual(hashlib.sha256(image).hexdigest(), expected_sha)
            self.assertEqual(item["partition_sha256"], expected_sha)

    def test_preflight_does_not_authorize_or_claim_device_access(self):
        report = self.report
        self.assertFalse(report["deployment_eligible"])
        self.assertFalse(report["flash_authorized"])
        self.assertFalse(report["hardware_accessed"])
        self.assertFalse(report["serial_accessed"])
        self.assertFalse(report["device_written"])
        self.assertEqual(report["mapping"]["semantic"], "surprised")
        self.assertEqual(report["mapping"]["before"], "shocked.eaf")
        self.assertEqual(report["mapping"]["after"], "Happy.eaf")
        self.assertGreaterEqual(len(report["pending_gates"]), 5)

    def test_only_full_partition_images_are_kept_in_private_tmp(self):
        self.assertEqual(self.report["candidate"]["partition_bytes"], 0x5DC000)
        self.assertEqual(self.report["rollback"]["partition_bytes"], 0x5DC000)
        self.assertTrue((OUTPUT / "candidate-assets-6mb-do-not-flash.bin").is_file())
        self.assertTrue((OUTPUT / "rollback-e1-18-assets-6mb.bin").is_file())
        self.assertFalse((ROOT / "artifacts/expression-e1-23-deployment-preflight-20261026/candidate-assets.bin").exists())


if __name__ == "__main__":
    unittest.main()
