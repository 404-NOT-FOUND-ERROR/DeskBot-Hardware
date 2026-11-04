from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit-role-expression-e1-32.py"
OUT = ROOT / "artifacts" / "role-expression-audit-e1-32-20261029"

SPEC = importlib.util.spec_from_file_location("role_expression_audit_e1_32", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def report_and_data() -> tuple[dict, dict]:
    return AUDIT.build_report()


class RoleExpressionAuditE132Tests(unittest.TestCase):
    def test_build_report_passes_and_preserves_offline_boundary(self):
        report, page_data = report_and_data()
        self.assertEqual(report["edition"], "E1.32")
        self.assertEqual(report["result"], "offline_role_expression_audit_passed")
        self.assertTrue(report["audit_pass"])
        self.assertFalse(report["hardware_accessed"])
        self.assertFalse(report["serial_accessed"])
        self.assertFalse(report["firmware_changed"])
        self.assertFalse(report["nvs_changed"])
        self.assertFalse(report["deployment_eligible"])
        self.assertFalse(report["flash_authorized"])
        self.assertEqual(page_data["inventory"]["semantic_count"], 29)
        self.assertEqual(page_data["inventory"]["official_resource_count"], 18)
        self.assertEqual(page_data["inventory"]["role_count"], 2)

    def test_all_positive_checks_and_negative_cases_are_explicit(self):
        report, _ = report_and_data()
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(len(report["negative_cases"]), 10)
        self.assertTrue(all(case["passed"] for case in report["negative_cases"]))
        self.assertEqual(
            {case["id"] for case in report["negative_cases"]},
            {
                "magnet_equals_shell", "magnet_equals_role", "unknown_expression_semantic",
                "unknown_resource", "wrong_layout_resource", "unsafe_action",
                "binding_version_mismatch", "asset_version_mismatch",
                "release_version_mismatch", "baseline_hash_mismatch",
            },
        )

    def test_baseline_lock_and_branch_separation_are_visible(self):
        report, _ = report_and_data()
        self.assertEqual(report["baseline"]["recomputed"], {
            "release_id": "E1.18",
            **AUDIT.E118_LOCK,
        })
        self.assertTrue(report["checks"]["official_E1.18_mapping_unchanged"])
        self.assertTrue(report["checks"]["official_EAF_hashes_sizes_unchanged"])
        for branch in report["branches"]:
            self.assertTrue(branch["official_branch"])
            self.assertTrue(branch["expression_branch"])
            self.assertTrue(branch["action_branch"])
            self.assertEqual(branch["binding_state"], "draft")
            self.assertNotEqual(branch["official_branch"], branch["expression_branch"])

    def test_identity_namespaces_are_pairwise_distinct(self):
        report, _ = report_and_data()
        for row in report["identity_isolation"]:
            self.assertTrue(row["all_distinct"])
            self.assertEqual(len({row["shell_id"], row["magnet_profile_id"], row["role_id"]}), 3)

    def test_generated_artifacts_are_current(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"], cwd=ROOT,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["edition"], "E1.32")
        self.assertEqual(report["next_gate"], "offline_asset_import_and_player_layout_audit")

    def test_mutating_baseline_or_role_does_not_change_accepted_inputs(self):
        e131, e119, e131_report = AUDIT.load_inputs()
        original = copy.deepcopy(e131)
        e131["baseline"]["package_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "baseline"):
            AUDIT.audit_payloads(e131, e119, e131_report)
        self.assertNotEqual(e131, original)


if __name__ == "__main__":
    unittest.main()
