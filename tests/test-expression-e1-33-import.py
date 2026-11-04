from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit-expression-e1-33-import.py"
BUILD = ROOT / "scripts/build-expression-e1-33-import.py"
OUT = ROOT / "artifacts/expression-e1-33-import-20261029"
SPEC = importlib.util.spec_from_file_location("e133_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class ExpressionE133ImportTests(unittest.TestCase):
    def report(self) -> dict:
        return json.loads((OUT / "report.json").read_text(encoding="utf-8"))

    def test_report_passes_offline_boundary_and_capacity_gate(self):
        report = self.report()
        AUDIT.validate_report(report)
        self.assertEqual(report["edition"], "E1.33")
        self.assertTrue(report["audit_pass"])
        self.assertFalse(report["hardware_accessed"])
        self.assertFalse(report["deployment_eligible"])
        self.assertFalse(report["capacity"]["append_candidate"]["fits_partition"])
        self.assertFalse(report["capacity"]["replace_candidates"]["Happy.eaf"]["fits_partition"])

    def test_candidate_structure_and_c_player_evidence(self):
        report = self.report()
        candidate = report["candidate"]
        self.assertEqual(candidate["structure"]["encodings"], [0, 1, 3])
        self.assertEqual(candidate["source_loop"], [50, 150])
        renderer = report["official_player"]["renderer"]
        self.assertEqual(renderer["rendered_frame_count"], 376)
        self.assertTrue(renderer["all_reference_frames_equal"])
        self.assertTrue(renderer["all_guards_clean"])
        self.assertTrue(renderer["explicit_range"]["renderer_accepts_range"])

    def test_negative_cases_reject_and_mutation_fails(self):
        report = self.report()
        self.assertTrue(all(item["rejected"] for item in report["negative_cases"].values()))
        mutated = copy.deepcopy(report)
        mutated["capacity"]["partition_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "capacity"):
            AUDIT.validate_report(mutated)
        mutated = copy.deepcopy(report)
        mutated["official_player"]["renderer"]["checks"][0]["mismatch_pixels"] = 1
        with self.assertRaisesRegex(ValueError, "per-frame"):
            AUDIT.validate_report(mutated)

    def test_public_generator_is_reproducible_and_withholds_candidate_media(self):
        result = subprocess.run([sys.executable, str(BUILD), "--check"], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        html = (OUT / "index.html").read_text(encoding="utf-8")
        payload = json.loads(__import__("re").search(
            r'<script id="e133-data" type="application/json">(.*?)</script>', html, __import__("re").S).group(1))
        self.assertEqual(payload["media_scope"], "public_metadata_only")
        self.assertIsNone(payload["candidate"]["atlas_data_url"])
        self.assertIsNone(payload["official_player"]["preview_atlases"]["full_screen"]["data_url"])
        self.assertIn("候选素材未嵌入公开副本", html)


if __name__ == "__main__":
    unittest.main()
