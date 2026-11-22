"""E3c-3a snapshot baseline checks; never opens a device or serial port."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol import storage_continuous_mmap_e3c3a_baseline as baseline
from protocol import storage_upper_migration_transaction as migration
from protocol.storage_continuous_mmap_experiment import APP_START, FLASH_BYTES
from protocol.storage_upper_migration_preflight import UPPER_BYTES, UPPER_START


class E3c3aSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = b"current-e3c3a-app" * 4
        raw = bytearray(b"\xff" * FLASH_BYTES)
        raw[APP_START:APP_START + len(cls.app)] = cls.app
        cls.snapshot = bytes(raw)
        rows = []
        for name, offset, size in migration.STATIC_GEOMETRY:
            digest = baseline.sha256(cls.snapshot[offset:offset + size])
            if name == "app":
                digest = baseline.sha256(b"historical-e1.42-app")
            rows.append({"name": name, "offset": offset, "bytes": size, "sha256": digest})
        rows.extend([
            {"name": "partition_table", "offset": migration.TABLE_START,
             "bytes": migration.TABLE_BYTES,
             "sha256": baseline.sha256(cls.snapshot[migration.TABLE_START:
                                                       migration.TABLE_START + migration.TABLE_BYTES])},
            {"name": "upper_assets", "offset": UPPER_START, "bytes": UPPER_BYTES,
             "sha256": baseline.sha256(cls.snapshot[UPPER_START:UPPER_START + UPPER_BYTES])},
        ])
        cls.identity = "a" * 64
        cls.migration_reference = {
            "layout": "upper-assets-8m", "identity_sha256": cls.identity,
            "report": {"path": "saved-e1.43c-3-report", "sha256": "b" * 64},
            "migration_plan_id": "c" * 64, "protected_ranges": rows,
        }
        cls.build_reference = {
            "layout": "upper-assets-8m", "report": {"path": "saved-e3c3a-report", "sha256": "d" * 64},
            "app": {"offset": APP_START, "bytes": len(cls.app),
                    "sha256": baseline.sha256(cls.app), "elf_sha256": "e" * 64},
        }

    def validation(self, snapshot=None, identity=None, migration_reference=None, build_reference=None):
        expected = self.snapshot if snapshot is None else snapshot
        observed = self.identity if identity is None else identity
        old_reference = self.migration_reference if migration_reference is None else migration_reference
        app_reference = self.build_reference if build_reference is None else build_reference
        with patch.object(baseline.migrated, "load_migrated_baseline", return_value=old_reference) as load_old, \
                patch.object(baseline, "load_e3c3a_build_reference", return_value=app_reference) as load_build, \
                patch.object(baseline, "APP_BYTES_EXPECTED", len(self.app)), \
                patch.object(baseline, "APP_SHA256_EXPECTED", baseline.sha256(self.app)), \
                patch.object(baseline, "APP_ELF_SHA256_EXPECTED", "e" * 64), \
                patch.object(baseline, "IMAGE_SHA256", self.migration_reference["protected_ranges"][-1]["sha256"]), \
                patch.object(baseline, "LIVE_MODEL_SHA256", next(
                    row["sha256"] for row in self.migration_reference["protected_ranges"]
                    if row["name"] == "model")), \
                patch.object(migration, "TABLE_SECTOR_SHA256",
                             next(row["sha256"] for row in self.migration_reference["protected_ranges"]
                                  if row["name"] == "partition_table")):
            result = baseline.validate_e3c3a_snapshot(expected, observed)
            load_old.assert_called_once_with()
            load_build.assert_called_once_with()
            return result

    def test_current_app_is_accepted_when_historical_app_digest_is_different(self):
        result = self.validation()
        self.assertTrue(result["historical_app_range_skipped"])
        self.assertEqual(result["original_app"]["sha256"], baseline.sha256(self.app))
        self.assertEqual(result["nvs"]["offset"], 0x9000)
        self.assertNotIn("app", {row["name"] for row in result["protected_ranges"]})

    def test_nvs_is_recorded_but_not_compared_with_historical_value(self):
        raw = bytearray(self.snapshot)
        raw[0x9000] ^= 1
        result = self.validation(snapshot=bytes(raw))
        self.assertEqual(result["nvs"]["sha256"], baseline.sha256(bytes(raw[0x9000:0xD000])))

    def test_identity_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "device identity"):
            self.validation(identity="f" * 64)

    def test_current_app_mismatch_is_rejected(self):
        raw = bytearray(self.snapshot)
        raw[APP_START] ^= 1
        with self.assertRaisesRegex(ValueError, "current App BIN"):
            self.validation(snapshot=bytes(raw))

    def test_each_non_app_historical_range_is_checked(self):
        for row in self.migration_reference["protected_ranges"]:
            if row["name"] == "app":
                continue
            with self.subTest(name=row["name"]):
                raw = bytearray(self.snapshot)
                raw[row["offset"]] ^= 1
                with self.assertRaisesRegex(ValueError, "migrated static baseline"):
                    self.validation(snapshot=bytes(raw))

    def test_historical_app_record_geometry_is_required(self):
        reference = {**self.migration_reference,
                     "protected_ranges": [row for row in self.migration_reference["protected_ranges"]
                                          if row["name"] != "app"]}
        with self.assertRaisesRegex(ValueError, "complete migrated static reference"):
            baseline._validate_e3c3a_snapshot_with_references(
                self.snapshot, self.identity, reference, self.build_reference)

    def test_historical_app_digest_is_not_used_as_current_app_digest(self):
        reference = {**self.migration_reference, "protected_ranges": [
            {**row, "sha256": "0" * 64} if row["name"] == "app" else row
            for row in self.migration_reference["protected_ranges"]]}
        result = self.validation(migration_reference=reference)
        self.assertTrue(result["historical_app_range_skipped"])

    def test_short_snapshot_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "complete 32 MiB"):
            self.validation(snapshot=self.snapshot[:-1])

    def test_e3c3a_app_reference_must_match_the_accepted_build(self):
        reference = {**self.build_reference, "app": {
            **self.build_reference["app"], "sha256": "0" * 64}}
        with self.assertRaisesRegex(ValueError, "accepted version"):
            baseline._validate_e3c3a_snapshot_with_references(
                self.snapshot, self.identity, self.migration_reference, reference)

    def test_public_validator_has_no_reference_override(self):
        with self.assertRaises(TypeError):
            baseline.validate_e3c3a_snapshot(
                self.snapshot, self.identity,
                migration_reference=self.migration_reference,
                build_reference=self.build_reference,
            )


class RetainedBuildEvidenceTests(unittest.TestCase):
    def test_saved_query_on_build_and_elf_identity_are_verified(self):
        reference = baseline.load_e3c3a_build_reference()
        self.assertEqual(reference["layout"], "upper-assets-8m")
        self.assertEqual(reference["app"]["bytes"], 4_489_232)
        self.assertEqual(reference["app"]["sha256"], baseline.APP_SHA256_EXPECTED)
        self.assertEqual(reference["app"]["elf_sha256"], baseline.APP_ELF_SHA256_EXPECTED)

    def test_saved_build_report_digest_is_pinned(self):
        with patch.object(baseline, "BUILD_REPORT_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "report digest"):
                baseline.load_e3c3a_build_reference()

    def test_non_object_report_is_rejected_as_value_error(self):
        self.assert_bad_report([], "JSON object")

    def test_non_object_asset_target_is_rejected_as_value_error(self):
        self.assert_bad_report({"target": {"assets": None}}, "asset target")

    def assert_bad_report(self, document, message):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / baseline.BUILD_REPORT
            path.parent.mkdir(parents=True)
            raw = json.dumps(document).encode("utf-8")
            path.write_bytes(raw)
            with patch.object(baseline, "BUILD_REPORT_SHA256", baseline.sha256(raw)):
                with self.assertRaisesRegex(ValueError, message):
                    baseline.load_e3c3a_build_reference(root)


if __name__ == "__main__":
    unittest.main()
