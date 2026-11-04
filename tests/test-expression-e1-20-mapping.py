#!/usr/bin/env python3
"""Offline contract tests for the E1.20 mapping audit gate."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit-expression-e1-20-mapping.py"
DRAFT = ROOT / "artifacts/expression-e1-20-mapping-audit-20261025/draft.json"
REPORT = ROOT / "artifacts/expression-e1-20-mapping-audit-20261025/report.json"


class ExpressionE120Tests(unittest.TestCase):
    def run_audit(self, draft: Path = DRAFT, output: Path = REPORT) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--draft", str(draft), "--output", str(output)],
            cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
        )

    def test_report_is_reproducible_and_safe(self):
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "deskbot.expression_mapping_audit/v1")
        self.assertEqual(report["edition"], "E1.20")
        self.assertEqual(report["result"], "offline_mapping_audit_passed")
        self.assertFalse(report["deployment_eligible"])
        self.assertFalse(report["hardware_accessed"])
        self.assertEqual(report["draft"]["changed_semantics"], ["surprised"])
        self.assertEqual(report["mapping_comparison"]["changes"][0]["disconnected"], "shocked.eaf")
        self.assertEqual(report["mapping_comparison"]["changes"][0]["connected"], "Happy.eaf")
        self.assertTrue(report["mapping_comparison"]["action_map_unchanged"])
        self.assertTrue(report["mapping_comparison"]["timing_unchanged"])
        self.assertTrue(report["mapping_comparison"]["layout_unchanged"])
        self.assertTrue(report["mapping_comparison"]["rollback_exact"])
        self.assertEqual(report["capacity"]["payload_delta_bytes"], 0)
        self.assertEqual(report["capacity"]["remaining_bytes"], 605649)
        self.assertEqual(report["shared_resource_impact"][0]["resource_id"], "Happy.eaf")
        self.assertEqual([gate["status"] for gate in report["gates"]], ["passed"] * 4 + ["pending"] * 2)

    def test_action_change_is_rejected(self):
        draft = json.loads(DRAFT.read_text(encoding="utf-8"))
        draft["changes"][0]["action_id"] = "shark_head"
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            draft_path = Path(directory) / "tampered.json"
            output = Path(directory) / "report.json"
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            result = self.run_audit(draft_path, output)
            self.assertEqual(result.returncode, 2)
            rejected = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(rejected["result"], "rejected")
            self.assertIn("protected action_id", rejected["error"])

    def test_unknown_resource_is_rejected(self):
        draft = json.loads(DRAFT.read_text(encoding="utf-8"))
        draft["changes"][0]["after"] = "not-official.eaf"
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            draft_path = Path(directory) / "tampered.json"
            output = Path(directory) / "report.json"
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            result = self.run_audit(draft_path, output)
            self.assertEqual(result.returncode, 2)
            rejected = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("official EAF whitelist", rejected["error"])


if __name__ == "__main__":
    unittest.main()
