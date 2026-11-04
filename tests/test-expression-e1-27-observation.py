from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build-expression-e1-27-observation.py"
SPEC = importlib.util.spec_from_file_location("e1_27_build", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILD
SPEC.loader.exec_module(BUILD)


class ExpressionObservationE127Tests(unittest.TestCase):
    def test_synthetic_snapshot_obeys_e126_contract(self):
        snapshot = BUILD.sample_snapshot()
        schema = json.loads(BUILD.SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(snapshot)
        self.assertEqual(len(snapshot["records"]), 7)
        self.assertEqual([row["sequence"] for row in snapshot["records"]], list(range(1, 8)))
        self.assertIn("other", [row["semantic"] for row in snapshot["records"]])

    def test_snapshot_rejects_nonascending_sequence_and_unknown_semantic(self):
        snapshot = BUILD.sample_snapshot()
        snapshot["records"][1]["sequence"] = 1
        with self.assertRaisesRegex(ValueError, "ascending"):
            BUILD.validate_snapshot(snapshot)
        snapshot = BUILD.sample_snapshot()
        snapshot["records"][0]["semantic"] = "untrusted value"
        with self.assertRaises(Exception):
            BUILD.validate_snapshot(snapshot)

    def test_oral_report_stays_qualitative_and_separate_from_raw_snapshot(self):
        report = BUILD.oral_summary()
        self.assertIs(report["raw_json_available"], False)
        self.assertEqual(report["reported_recent_record_count"], 24)
        self.assertEqual({row["semantic"] for row in report["reported_semantics"]},
                         {"happy", "embarrassed", "sleepy", "neutral"})
        self.assertNotIn("records", report)
        self.assertTrue(all("repeat_count" not in row for row in report["reported_semantics"]))

    def test_generated_page_is_self_contained_and_read_only(self):
        report = BUILD.build(check=True)
        self.assertEqual(report["edition"], "E1.27")
        page = (BUILD.OUT / "index.html").read_text(encoding="utf-8")
        for token in ("e127-data", "e127-core", "e127-js", "e127-file",
                      "e127-observed-list", "e127-records", "e127-oral-list",
                      "非原始 JSON", "connect-src 'none'"):
            self.assertIn(token, page)
        self.assertNotRegex(page, r"\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(")

    def test_machine_report_keeps_hardware_and_deployment_closed(self):
        report = json.loads((BUILD.OUT / "report.json").read_text(encoding="utf-8"))
        for key in ("deployment_eligible", "flash_authorized", "hardware_accessed",
                    "serial_accessed", "device_written", "nvs_changed"):
            self.assertIs(report[key], False)
        self.assertTrue(report["sample"]["offline_only"])
        self.assertIs(report["user_reported_oral_summary"]["raw_json_available"], False)


if __name__ == "__main__":
    unittest.main()
