from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_n32r16 import (  # noqa: E402
    LEGACY_FLASH_BYTES,
    PHYSICAL_FLASH_BYTES,
    PACKAGE_BYTES,
    proposals,
    summarize,
    validate_proposal,
    validate_report,
)


class N32R16LayoutTests(unittest.TestCase):
    def test_capacity_only_keeps_the_legacy_table_byte_for_byte(self):
        plans = {plan["id"]: plan for plan in proposals()}
        self.assertEqual(plans["current-16m"]["configured_flash_bytes"], LEGACY_FLASH_BYTES)
        self.assertEqual(plans["capacity-only-32m"]["configured_flash_bytes"], PHYSICAL_FLASH_BYTES)
        self.assertEqual(plans["capacity-only-32m"]["rows"], plans["current-16m"]["rows"])
        self.assertEqual(validate_proposal(plans["capacity-only-32m"],),
                         validate_proposal(plans["current-16m"]))

    def test_upper_slot_preserves_old_resources_and_model(self):
        plans = {plan["id"]: plan for plan in proposals()}
        old = {row["name"]: row for row in plans["current-16m"]["rows"]}
        upper = {row["name"]: row for row in plans["upper-assets-8m"]["rows"]}
        self.assertEqual(upper["assets_prev"]["start"], old["assets"]["start"])
        self.assertEqual(upper["assets_prev"]["size_bytes"], old["assets"]["size_bytes"])
        self.assertEqual(upper["model"], old["model"])
        self.assertEqual(upper["assets"]["start"], 16 * 1024 * 1024)
        self.assertEqual(upper["assets"]["size_bytes"], 8 * 1024 * 1024)
        self.assertLessEqual(upper["assets"]["end_exclusive"], PHYSICAL_FLASH_BYTES)

    def test_headroom_and_mmap_projection_are_explicit(self):
        upper = next(plan for plan in proposals() if plan["id"] == "upper-assets-8m")
        result = summarize(upper)
        self.assertEqual(result["assets"]["package_bytes"], PACKAGE_BYTES)
        self.assertEqual(result["assets"]["free_bytes"], 8 * 1024 * 1024 - PACKAGE_BYTES)
        self.assertEqual(result["assets"]["mmap_span_pages"], 128)
        self.assertTrue(result["assets"]["meets_proposed_headroom"])
        self.assertFalse(result["runtime_mmap_verified"])
        self.assertFalse(result["deployment_eligible"])

    def test_protected_and_version_fields_reject_tampering(self):
        plan = next(plan for plan in proposals() if plan["id"] == "capacity-only-32m")
        for field, value in (("configured_flash_bytes", 16 * 1024 * 1024),
                             ("next_build", False), ("purpose", "resource_candidate")):
            mutated = copy.deepcopy(plan)
            mutated[field] = value
            with self.assertRaises(ValueError):
                validate_proposal(mutated)
        mutated = copy.deepcopy(plan)
        mutated["rows"][0]["subtype"] += 1
        with self.assertRaisesRegex(ValueError, "protected"):
            validate_proposal(mutated)


class N32R16ReportTests(unittest.TestCase):
    def test_generated_report_is_offline_and_complete(self):
        report_path = ROOT / "artifacts/expression-storage-n32r16-e1-36-20261030/report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        validate_report(report)
        self.assertFalse(report["deployment_eligible"])
        self.assertFalse(report["flash_authorized"])
        self.assertFalse(report["safety"]["serial_accessed"])
        self.assertFalse(report["safety"]["c61_accessed"])

    def test_report_cannot_be_promoted_to_a_flash_manifest(self):
        report_path = ROOT / "artifacts/expression-storage-n32r16-e1-36-20261030/report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for path, value in ((["deployment_eligible"], True), (['safety', 'device_written'], True),
                            (["candidates", 1, "idf_roundtrip_verified"], False)):
            mutated = copy.deepcopy(report)
            target = mutated
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.assertRaises(ValueError):
                validate_report(mutated)


if __name__ == "__main__":
    unittest.main()
