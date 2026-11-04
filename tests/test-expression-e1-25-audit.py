from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit-expression-e1-25-observation.py"


class ExpressionObservationE125AuditTests(unittest.TestCase):
    def test_audit_report_is_bounded_and_non_deployable(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            output = Path(directory) / "e125-report.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output)], cwd=ROOT,
                text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(report["audit_pass"])
        self.assertFalse(report["observer_implemented"])
        self.assertFalse(report["runtime_data_collected"])
        self.assertFalse(report["deployment_eligible"])
        self.assertFalse(report["flash_authorized"])
        self.assertEqual(report["bounds"]["capacity_records"], 24)
        self.assertEqual(report["bounds"]["coalesce_window_ms"], 250)
        self.assertFalse(report["bounds"]["default_enabled"])
        self.assertFalse(report["safety"]["firmware_changed"])
        self.assertFalse(report["safety"]["device_written"])
        self.assertEqual(report["firmware_baseline_commit"], "747b0d918538eedaf936fda0bb6c9340fc7658cc")
        self.assertEqual(report["sources"]["display"]["sha256"], "8a1daa3501c3d6e68efe88546b3820de9ad93c991d357167352b58f692ca96a4")
        self.assertIn("application_scheduled_emotion", report["observation"]["static_call_contexts"])
        self.assertIn("base_uart_callback", report["observation"]["static_call_contexts"])

    def test_audit_rejects_invalid_output_scope(self):
        outside = ROOT / "e125-invalid-output.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(outside)], cwd=ROOT,
            text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("inside artifacts/ or tmp/", result.stderr)


if __name__ == "__main__":
    unittest.main()
