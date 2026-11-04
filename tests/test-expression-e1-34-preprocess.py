from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "scripts/audit-expression-e1-34-preprocess.py"
BUILD = ROOT / "scripts/build-expression-e1-34-preprocess.py"
PUBLIC = ROOT / "artifacts/expression-e1-34-preprocess-20261029"
LOCAL = ROOT / "tmp/expression-e1-34-preprocess-20261029-preview"
SPEC = importlib.util.spec_from_file_location("e134_preprocess_audit", AUDIT_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class ExpressionE134PreprocessTests(unittest.TestCase):
    def report(self) -> dict:
        return json.loads((PUBLIC / "report.json").read_text(encoding="utf-8"))

    def payload(self, local: bool) -> dict:
        html = (LOCAL if local else PUBLIC).joinpath("index.html").read_text(encoding="utf-8")
        match = re.search(r'<script id="e134-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_report_offline_boundary_source_and_capacity(self):
        report = self.report()
        AUDIT.validate_report(report)
        self.assertEqual(report["edition"], "E1.34")
        self.assertFalse(report["hardware_accessed"])
        self.assertFalse(report["deployment_eligible"])
        self.assertEqual(len(report["lossless_variants"]), 13)
        self.assertEqual(report["source"]["frame_count"], 188)
        best = min(report["lossless_variants"], key=lambda row: row["eaf_bytes"])
        self.assertGreater(best["eaf_bytes"], report["source"]["bytes"])
        self.assertFalse(best["capacity"]["fits_partition"])

    def test_crop_is_pixel_preserving_within_region_but_visual_not_accepted(self):
        report = self.report()
        crop = report["crop_research"]
        self.assertEqual(crop["crop"], [10, 90, 160, 200])
        self.assertEqual(crop["frame_count"], 188)
        self.assertTrue(crop["pixel_comparison"]["exact_sequence"])
        self.assertFalse(crop["lossless_to_full_source"])
        self.assertFalse(crop["visual_accepted"])
        self.assertEqual(crop["capacity"]["remaining_bytes"], 162615)
        self.assertTrue(crop["capacity"]["fits_partition"])

    def test_native_render_count_and_product_default_tail_limit(self):
        report = self.report()
        total = 0
        for candidate in (next(row for row in report["lossless_variants"]
                               if row["id"] == report["smallest_lossless_id"]), report["crop_research"]):
            renderer = candidate["official_player"]["renderer"]
            total += renderer["rendered_frame_count"]
            self.assertEqual(renderer["rendered_frame_count"], 376)
            timer = candidate["official_player"]["default_product_timer"]
            self.assertFalse(timer["all_188_images_scheduled"])
            self.assertEqual(timer["clamped_end"], 187)
        self.assertEqual(total, 752)

    def test_fps_only_does_not_claim_savings(self):
        report = self.report()
        self.assertEqual([row["fps"] for row in report["timing_research"]], [15, 20, 25, 30])
        self.assertTrue(all(row["saved_bytes_by_fps_only"] == 0 for row in report["timing_research"]))
        self.assertTrue(all(row["frame_resampling_performed"] is False for row in report["timing_research"]))

    def test_mutated_capacity_native_timer_and_boundary_are_rejected(self):
        report = self.report()
        mutated = copy.deepcopy(report)
        mutated["crop_research"]["capacity"]["remaining_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "capacity"):
            AUDIT.validate_report(mutated)

        mutated = copy.deepcopy(report)
        candidate = next(row for row in mutated["lossless_variants"]
                         if row["id"] == mutated["smallest_lossless_id"])
        candidate["official_player"]["renderer"]["checks"][0]["mismatch_pixels"] = 1
        with self.assertRaisesRegex(ValueError, "per-frame"):
            AUDIT.validate_report(mutated)

        mutated = copy.deepcopy(report)
        mutated["crop_research"]["official_player"]["default_product_timer"]["clamped_end"] = 188
        with self.assertRaisesRegex(ValueError, "timer"):
            AUDIT.validate_report(mutated)

        mutated = copy.deepcopy(report)
        mutated["hardware_accessed"] = True
        with self.assertRaisesRegex(ValueError, "boundary"):
            AUDIT.validate_report(mutated)

    def test_public_and_local_pages_are_reproducible_and_media_scoped(self):
        args_list = [["--check"]]
        has_local = (LOCAL / "index.html").is_file()
        if has_local:
            args_list.append(["--check", "--local"])
        for args in args_list:
            result = subprocess.run([sys.executable, str(BUILD), *args], cwd=ROOT,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        public = self.payload(False)
        self.assertEqual(public["media_scope"], "public_metadata_only")
        for key in ("full", "eye"):
            self.assertIsNone(public["candidates"][key]["atlas"]["data_url"])
        if has_local:
            local = self.payload(True)
            self.assertEqual(local["media_scope"], "local_research")
            for key in ("full", "eye"):
                self.assertTrue(local["candidates"][key]["atlas"]["data_url"].startswith("data:image/png;base64,"))
        self.assertEqual(public["native"]["frame_layout_checks"], 752)
        self.assertFalse(public["deployment_eligible"])
        self.assertFalse(public["flash_authorized"])
        public_html = (PUBLIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("候选素材未嵌入公开副本", public_html)
        self.assertIn("connect-src 'none'", public_html)


if __name__ == "__main__":
    unittest.main()
