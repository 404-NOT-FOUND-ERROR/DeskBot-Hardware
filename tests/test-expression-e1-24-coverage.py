#!/usr/bin/env python3
"""Offline tests for the E1.24 static mapping-coverage audit."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit-expression-e1-24-coverage.py"
DECISION = ROOT / "artifacts/expression-e1-22-candidate-preview-20261026/selection-record.json"


class ExpressionE124CoverageTests(unittest.TestCase):
    def run_audit(self, decision: Path = DECISION, output: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(SCRIPT), "--decision", str(decision)]
        if output is not None:
            command.extend(("--output", str(output)))
        return subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                              capture_output=True, check=False)

    def test_report_records_static_coverage_without_deployment(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            output = Path(directory) / "e124-report.json"
            result = self.run_audit(output=output)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "deskbot.expression_e1_24_coverage_audit/v1")
        self.assertEqual(report["static_inventory"]["route_kinds"],
                         {"behavior": 20, "semantic": 29, "unmapped": 7})
        self.assertEqual(report["static_inventory"]["semantic_reachability"]["counts"],
                         {"dynamic_entry_only_in_scope": 23, "literal_local_entry": 5, "status_entry": 1})
        self.assertEqual(report["static_inventory"]["current_e118_mapping"]["semantic_count"], 29)
        self.assertEqual(report["static_inventory"]["current_e118_mapping"]["official_resource_count"], 18)
        self.assertEqual(report["static_inventory"]["unmapped_calls"]["call_site_count"], 9)
        self.assertEqual(report["static_inventory"]["unmapped_calls"]["distinct_name_count"], 7)
        self.assertEqual(report["static_inventory"]["resource_protection_kinds"],
                         {"core": 5, "offline_candidate": 1, "protected_shared": 3, "protected_single": 10})
        self.assertEqual(report["visual_decision"]["selected_resource"], "shocked.eaf")
        self.assertFalse(report["visual_decision"]["mapping_changed"])
        self.assertTrue(report["sources"]["decision"]["sha256"])
        self.assertEqual(report["runtime_observation"]["status"], "not_collected")
        self.assertEqual(report["next_gate"],
                         "design_bounded_passive_runtime_observation_before_any_mapping_cleanup_or_firmware_change")
        self.assertFalse(report["deployment_eligible"])
        self.assertFalse(report["flash_authorized"])

    def test_candidate_choice_is_not_silently_accepted_as_current(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            directory = Path(directory)
            decision = json.loads(DECISION.read_text(encoding="utf-8"))
            decision["selected_resource"] = "Happy.eaf"
            tampered = directory / "decision.json"
            output = directory / "report.json"
            tampered.write_text(json.dumps(decision), encoding="utf-8")
            result = self.run_audit(tampered, output)
            self.assertEqual(result.returncode, 2)
            self.assertIn("choice must retain", result.stderr)


if __name__ == "__main__":
    unittest.main()
