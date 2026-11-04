"""Migrated-layout preparation and saved-evidence checks; no serial access."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import storage_continuous_mmap_baseline as baseline
from protocol.storage_continuous_mmap_experiment import FLASH_BYTES, sha


def module_at(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREPARE = module_at("continuous_upper_preparation_test", "scripts/prepare-expression-e1-43b-upper-deployment.py")
EXPERIMENT = module_at("continuous_upper_adapter_contract", "tests/test_storage_continuous_mmap_experiment.py")


class MigratedAdapterTests(EXPERIMENT.AdapterBoundaryTests):
    """Apply the existing App-only boundary/recovery contract to the new entry."""
    @classmethod
    def setUpClass(cls):
        cls.runner = module_at("continuous_upper_adapter_test", "scripts/run-expression-continuous-mmap-upper-e1-43b.py")

    def test_accepted_snapshot_rejects_static_drift_but_allows_nvs(self):
        raw = bytearray(b"\xff" * FLASH_BYTES)
        prep = {"baseline": {"layout": "upper-assets-8m", "protected_ranges": [
            {"name": "upper_assets", "offset": 0x1000000, "bytes": 16, "sha256": sha(b"\xff" * 16)}],
            "original_app": {"bytes": 1, "sha256": sha(b"\xff")}}}
        raw[0x9000] = 0
        self.runner.accepted_snapshot(bytes(raw), prep)
        raw[0x1000000] = 0
        with self.assertRaisesRegex(ValueError, "upper_assets"):
            self.runner.accepted_snapshot(bytes(raw), prep)


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = b"\xff" * FLASH_BYTES
        regions = (("partition_table", 0x8000, 0x1000), ("bootloader", 0, 0x8000),
                   ("model", 0xBC8000, 0xC8000), ("upper_assets", 0x1000000, 0x800000))
        cls.reference = {
            "layout": "upper-assets-8m", "original_app": {"bytes": 4097, "sha256": sha(b"\xff" * 4097)},
            "protected_ranges": [{"name": name, "offset": offset, "bytes": size,
                                  "sha256": sha(cls.raw[offset:offset + size])}
                                 for name, offset, size in regions],
        }

    def test_matching_snapshot_and_runtime_nvs_change_are_accepted(self):
        baseline.accepted_snapshot(self.raw, self.reference)
        raw = bytearray(self.raw)
        raw[0x9000] = 0
        baseline.accepted_snapshot(bytes(raw), self.reference)

    def test_table_boot_model_assets_and_app_drift_are_rejected(self):
        for offset in (0x8000, 0, 0xBC8000, 0x1000000, 0x10000):
            with self.subTest(offset=offset):
                raw = bytearray(self.raw)
                raw[offset] = 0
                with self.assertRaises(ValueError):
                    baseline.accepted_snapshot(bytes(raw), self.reference)

    def test_short_snapshot_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "32 MiB"):
            baseline.accepted_snapshot(self.raw[:-1], self.reference)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143b-upper-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.raw = b"\xe9" * 4100
        (self.folder / "xiaozhi.bin").write_bytes(self.raw)
        self.report = self.folder / "build.json"
        self.build = {"rounds": {"on": {"evidence_directory": self.folder.relative_to(ROOT).as_posix(),
                       "artifacts": {"xiaozhi.bin": {"bytes": len(self.raw), "sha256": sha(self.raw)}}}}}
        self.reference = {"layout": "upper-assets-8m", "identity_sha256": "1" * 64,
                          "original_app": {"bytes": 8193, "sha256": "2" * 64}, "protected_ranges": []}

    def prepare(self):
        self.report.write_text(json.dumps(self.build), encoding="utf-8")
        with patch.object(PREPARE, "audit_build", return_value={"result": "passed"}), \
                patch.object(PREPARE, "load_migrated_baseline", return_value=self.reference):
            return PREPARE.prepare(self.report)

    def test_preparation_uses_new_layout_and_exact_app_union(self):
        result = self.prepare()
        self.assertEqual(result["baseline"]["layout"], "upper-assets-8m")
        self.assertEqual(result["write_range"]["erase_bytes"], 12288)
        self.assertEqual(result["write_range"]["offset"], 0x10000)
        self.assertEqual(result["mapping"]["allowed_page_costs"], [0, 128])
        self.assertFalse(result["device_accessed"])
        self.assertFalse(result["fresh_live_backup_ready"])

    def test_candidate_changed_after_build_audit_is_rejected(self):
        (self.folder / "xiaozhi.bin").write_bytes(b"x" * len(self.raw))
        with self.assertRaisesRegex(ValueError, "App differs"):
            self.prepare()

    def test_live_preparation_keeps_historical_entry_separate(self):
        runner = module_at("continuous_upper_runner_test", "scripts/run-expression-continuous-mmap-upper-e1-43b.py")
        self.assertTrue(runner.load_preparation().__file__.endswith("prepare-expression-e1-43b-upper-deployment.py"))
        with self.assertRaisesRegex(ValueError, "upper-assets-8m"):
            runner.accepted_snapshot(b"", {"baseline": {"layout": "legacy"}})


class RunnerPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = module_at("continuous_upper_pipeline_test", "scripts/run-expression-continuous-mmap-upper-e1-43b.py")
        cls.fixture = module_at("continuous_upper_fixture_test", "tests/test_storage_continuous_mmap.py")
        raw = bytearray(b"\xff" * FLASH_BYTES)
        raw[0x1000000:0x1800000] = bytes(range(256)) * (0x800000 // 256)
        cls.raw = bytes(raw)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143b-upper-pipeline-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.candidate = b"\xe9" * 8193
        path = self.folder / "xiaozhi.bin"
        path.write_bytes(self.candidate)
        self.report = self.folder / "build.json"
        self.report.write_text("{}", encoding="utf-8")
        self.reference = {
            "layout": "upper-assets-8m", "identity_sha256": "1" * 64,
            "report": {"path": "saved-migration-report", "sha256": "3" * 64},
            "original_app": {"bytes": 4097, "sha256": sha(b"\xff" * 4097)},
            "protected_ranges": [
                {"name": name, "offset": offset, "bytes": size, "sha256": sha(self.raw[offset:offset + size])}
                for name, offset, size in (("partition_table", 0x8000, 0x1000),
                                          ("upper_assets", 0x1000000, 0x800000))],
        }
        self.prep = {"baseline": self.reference, "original_app": self.reference["original_app"],
                     "candidate_app": {"path": path.relative_to(ROOT).as_posix(),
                                       "bytes": len(self.candidate), "sha256": sha(self.candidate)}}
        rows, _ = self.fixture.fixture()
        capture = "\n".join(self.fixture.encode(row["kind"], row["payload"]) for row in rows)
        self.device = SimpleNamespace(data=bytearray(self.raw), writes=[], boots=0, plan=None)
        self.device.identity = lambda: "1" * 64
        self.device.read_full = lambda: bytes(self.device.data)
        self.device.read_app = lambda size: bytes(self.device.data[0x10000:0x10000 + size])
        def write(raw):
            self.device.writes.append(raw)
            self.device.data[0x10000:0x10000 + len(raw)] = raw
        def boot():
            self.device.boots += 1
            return True
        self.device.write_app, self.device.boot_original = write, boot
        self.device.capture = lambda: capture

    def run_case(self):
        with patch.object(self.runner, "load_preparation", return_value=SimpleNamespace(prepare=lambda _: self.prep)), \
                patch.object(self.runner, "AppDevice", return_value=self.device):
            return self.runner.run_new(self.folder / "e143b-synthetic-upper", self.report)

    def test_migrated_pipeline_restores_app_and_preserves_upper_resources(self):
        result = self.run_case()
        self.assertTrue(result["probe_passed"])
        self.assertTrue(result["restored"])
        self.assertEqual(bytes(self.device.data), self.raw)
        self.assertEqual(len(self.device.writes), 2)
        saved = self.folder / "e143b-synthetic-upper/deployment-preparation.json"
        self.assertEqual(json.loads(saved.read_bytes())["baseline"]["layout"], "upper-assets-8m")

    def test_changed_table_stops_before_app_write_and_boots_original(self):
        self.device.data[0x8000] = 0
        with self.assertRaisesRegex(ValueError, "partition_table"):
            self.run_case()
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.device.boots, 1)

    def test_wrong_body_stops_before_app_write(self):
        self.device.identity = lambda: "0" * 64
        with self.assertRaisesRegex(ValueError, "migrated body"):
            self.run_case()
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.device.boots, 0)


@unittest.skipUnless((ROOT / baseline.REPORT).is_file()
                     and (ROOT / "tmp/e143c-live-20261103-direct1/plan.json").is_file(),
                     "retained private migration readbacks required")
class RetainedEvidenceTests(unittest.TestCase):
    def test_saved_migration_blocks_table_assets_model_and_app_verify(self):
        result = baseline.load_migrated_baseline()
        self.assertEqual(result["layout"], "upper-assets-8m")
        rows = {row["name"]: row for row in result["protected_ranges"]}
        self.assertEqual(rows["upper_assets"]["bytes"], 0x800000)
        self.assertEqual(rows["partition_table"]["offset"], 0x8000)
        self.assertEqual(rows["model"]["sha256"], baseline.LIVE_MODEL_SHA256)
        self.assertEqual(result["original_app"]["sha256"], baseline.APP_SHA256)

    def test_report_digest_drift_is_rejected(self):
        with patch.object(baseline, "REPORT_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "report differs"):
                baseline.load_migrated_baseline()

    def test_migration_restoration_state_is_not_a_migrated_baseline(self):
        with patch.object(baseline.migration.Journal, "load", return_value={"phase": "restored"}):
            with self.assertRaisesRegex(ValueError, "completion state"):
                baseline.load_migrated_baseline()

    def test_saved_readback_tampering_is_rejected(self):
        read = Path.read_bytes
        def changed(path):
            raw = read(path)
            if path.name.endswith("-migration-full-backup-01000000-00100000.bin"):
                return bytes([raw[0] ^ 1]) + raw[1:]
            return raw
        with patch.object(Path, "read_bytes", changed):
            with self.assertRaisesRegex(ValueError, "readback block differs"):
                baseline.load_migrated_baseline()


if __name__ == "__main__":
    unittest.main()
