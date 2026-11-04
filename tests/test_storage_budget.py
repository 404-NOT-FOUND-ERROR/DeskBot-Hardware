from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_budget import (compare_layouts, mapped_image_segments, page_span,
                                     reserve_projection, valid_build_checkpoint, validate_layout)


class StorageBudgetTests(unittest.TestCase):
    def layouts(self):
        data = json.loads((ROOT / "artifacts/expression-storage-strategy-e1-35-20261029/report.json").read_text(encoding="utf-8"))
        return data["current"]["partitions"], data["proposal"]["partitions"]

    def test_unaligned_mapping_includes_boundary_page(self):
        self.assertEqual(page_span(0x5EC000, 0x5DC000), 95)
        self.assertEqual(page_span(0x5EC000, 0x800000), 129)
        self.assertEqual(page_span(0xBC8000, 0xC8000), 13)
        self.assertEqual(page_span(0xDEC000, 0xC8000), 14)
        self.assertEqual(page_span(0, 65536), 1)
        for start, size, page in ((-1, 1, 65536), (0, 0, 65536), (0, 1, 3), (True, 1, 65536)):
            with self.assertRaises(ValueError):
                page_span(start, size, page)

    def test_layout_overlap_flash_overflow_duplicate_and_wrong_end_rejected(self):
        old, _ = self.layouts()
        for field, value in (("start", old[-2]["start"]), ("size_bytes", 0x1000000), ("name", "assets"),
                             ("end_exclusive", old[-1]["end_exclusive"] + 4096)):
            mutated = copy.deepcopy(old)
            mutated[-1][field] = value
            with self.assertRaises(ValueError):
                validate_layout(mutated)

    def test_protected_nvs_and_app_not_shrunk(self):
        old, new = self.layouts()
        for index in (0, 3):
            changed = copy.deepcopy(new)
            changed[index]["size_bytes"] -= 4096
            changed[index]["end_exclusive"] -= 4096
            with self.assertRaisesRegex(ValueError, "protected"):
                compare_layouts(old, changed)

    def test_future_reserve_does_not_claim_runtime_or_dual_bank(self):
        old, new = self.layouts()
        facts = compare_layouts(old, new)
        self.assertEqual(facts["candidate_tail_bytes"], 1359872)
        self.assertTrue(facts["new_assets_overwrites_entire_old_model"])
        self.assertFalse(facts["runtime_mmap_proven"])
        report = json.loads((ROOT / "artifacts/expression-storage-preflight-e1-35a-20261029/report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["mmap"]["component_guard_minimum_reported_free_pages"], 129)
        self.assertIsNone(report["mmap"]["runtime_probe_pass"])
        self.assertNotIn("arithmetic_remainder_pages_excluding_other_reservations", report["mmap"])
        budget = reserve_projection(5538351, 4477280)
        self.assertEqual(budget["assets_free_bytes"], 2850257)
        self.assertEqual(budget["app_free_bytes"], 1666720)
        self.assertEqual(budget["growth_to_soft_limit_bytes"], 1801681)
        self.assertLess(facts["candidate_tail_bytes"], budget["app_bytes"])
        self.assertLess(facts["candidate_tail_bytes"], budget["package_bytes"])
        self.assertEqual(budget["policy_status"], "planning_only_not_firmware_enforced")

    def test_soft_ceiling_is_not_physical_overflow(self):
        self.assertFalse(reserve_projection(7500000, 4477280)["within_proposed_soft_limit"])
        for arguments in ((9000000, 4477280), (5538351, 7000000), (-1, 1)):
            with self.assertRaises(ValueError):
                reserve_projection(*arguments)

    def test_image_segment_range_parser_rejects_truncation_and_window_overrun(self):
        header = bytes((0xE9, 1)) + bytes(22)
        image = header + struct.pack("<II", 0x3C1E0020, 48) + bytes(48)
        self.assertEqual(mapped_image_segments(image)[0]["span_pages"], 1)
        with self.assertRaisesRegex(ValueError, "truncated"):
            mapped_image_segments(image[:-1])
        with self.assertRaisesRegex(ValueError, "window"):
            mapped_image_segments(header + struct.pack("<II", 0x3DFFFFF0, 48) + bytes(48))
        with self.assertRaisesRegex(ValueError, "header"):
            mapped_image_segments(b"invalid")


class StorageBuildResumeTests(unittest.TestCase):
    def test_resume_requires_exact_artifact_log_and_path(self):
        def sha(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        files = ("xiaozhi.bin", "partition_table/partition-table.bin")
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e135a-test-") as temporary:
            work = Path(temporary)
            archive = work / "outputs/off-old"
            archive.mkdir(parents=True)
            for name in files:
                target = archive / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"test")
            table = archive / "partition_table/partition-table.bin"
            table_sha = sha(table)
            steps = []
            for suffix in ("configure", "build"):
                log = work / f"off-old-{suffix}.log"
                log.write_text("success", encoding="utf-8")
                steps.append({"log": log.relative_to(ROOT).as_posix(), "log_sha256": sha(log), "returncode": 0})
            record = {"observer_enabled": False, "layout": "old", "steps": steps,
                      "archive": archive.relative_to(ROOT).as_posix(), "observer_source_members": 0,
                      "artifacts": {name: {"bytes": (archive / name).stat().st_size, "sha256": sha(archive / name)} for name in files}}
            self.assertTrue(valid_build_checkpoint(record, ROOT, work, False, "old", files, table_sha))
            for field, value in (("archive", "../outside"), ("observer_source_members", 1), ("steps", [])):
                corrupted = copy.deepcopy(record)
                corrupted[field] = value
                self.assertFalse(valid_build_checkpoint(corrupted, ROOT, work, False, "old", files, table_sha))
            (archive / "xiaozhi.bin").write_bytes(b"changed")
            self.assertFalse(valid_build_checkpoint(record, ROOT, work, False, "old", files, table_sha))


class StoragePreflightReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("storage_gate_tests", ROOT / "scripts/audit-expression-e1-35a-storage.py")
        cls.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.audit)

    def report(self):
        return json.loads((ROOT / "artifacts/expression-storage-preflight-e1-35a-20261029/report.json").read_text(encoding="utf-8"))

    def test_report_does_not_allow_missing_boundary_or_fake_runtime(self):
        report = self.report()
        self.audit.validate_report(report)
        modified = copy.deepcopy(report)
        modified["safety"] = {}
        with self.assertRaisesRegex(ValueError, "boundary"):
            self.audit.validate_report(modified)
        modified = copy.deepcopy(report)
        modified["mmap"]["live_free_mmap_pages"] = 200
        with self.assertRaisesRegex(ValueError, "not measured"):
            self.audit.validate_report(modified)
        modified = copy.deepcopy(report)
        modified["flash_authorized"] = True
        with self.assertRaisesRegex(ValueError, "offline"):
            self.audit.validate_report(modified)

    def test_changed_budget_and_app_identity_rejected(self):
        report = self.report()
        changed = copy.deepcopy(report)
        changed["future_budget"]["app_free_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "budget"):
            self.audit.validate_report(changed)
        changed = copy.deepcopy(report)
        changed["build_summary"][2]["app"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            self.audit.validate_report(changed)
        changed = copy.deepcopy(report)
        changed["partitions"]["8m"][-1]["end_exclusive"] += 4096
        with self.assertRaises(ValueError):
            self.audit.validate_report(changed)


if __name__ == "__main__":
    unittest.main()
