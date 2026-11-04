"""Offline checks for the A7 retained-evidence auditor; no device I/O."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("upper_live_audit", ROOT / "scripts/audit-expression-e1-43a7-live.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class CaptureAuditTests(unittest.TestCase):
    def test_contiguous_chunks_join(self):
        self.assertEqual(AUDIT.join_chunks([(4, b"cd"), (2, b"ab")], 2, 4), b"abcd")

    def test_gap_overlap_and_wrong_extent_rejected(self):
        for chunks, size in (([(0, b"ab"), (3, b"c")], 4),
                             ([(0, b"ab"), (1, b"c")], 3),
                             ([(0, b"ab")], 3), ([(0, b"")], 0)):
            with self.subTest(chunks=chunks), self.assertRaises(ValueError):
                AUDIT.join_chunks(chunks, 0, size)

    def fixture(self, run, raw=b"data", log=None):
        (run / "captures").mkdir()
        (run / "logs").mkdir()
        (run / "captures/003-snapshot-app-00010000-00000004.bin").write_bytes(raw)
        (run / "logs/004-snapshot-app.log").write_text(log or
            "Serial port COM4\nRead 4 bytes at 0x00010000 in 0.1 seconds\n", encoding="utf-8")

    def test_complete_capture_requires_success_log(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            self.fixture(run)
            with patch.object(AUDIT, "RUN", run):
                rows, retries = AUDIT.captures()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["bytes"], b"data")
            self.assertFalse(retries)

    def test_unexplained_truncation_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            self.fixture(run, raw=b"da")
            with patch.object(AUDIT, "RUN", run), self.assertRaises(ValueError):
                AUDIT.captures()

    def test_failed_transfer_retained_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            self.fixture(run, raw=b"da", log="Serial port COM4\nA fatal error occurred: Corrupt data, expected 0x4 bytes but received 0x2 bytes\n")
            with patch.object(AUDIT, "RUN", run):
                rows, retries = AUDIT.captures()
            self.assertFalse(rows)
            self.assertEqual(retries[0]["retained_bytes"], 2)

    def test_wrong_port_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            self.fixture(run, log="Serial port COM8\nRead 4 bytes at 0x00010000 in 0.1 seconds\n")
            with patch.object(AUDIT, "RUN", run), self.assertRaises(ValueError):
                AUDIT.captures()

    def test_missing_or_extra_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            AUDIT.snapshots([])


class RestorationScopeTests(unittest.TestCase):
    geometry = {"app": (0, 3), "page": (3, 3), "assets": (6, 3), "nvs": (9, 3)}

    def fixture(self):
        return {"app": b"app", "page": b"xyz", "assets": b"abc", "nvs": b"aaa"}

    def check(self, before, after, observation=None):
        if observation is None:
            observation = {"before_sha256": AUDIT.sha(before["nvs"]),
                           "latest_sha256": AUDIT.sha(after["nvs"]),
                           "changed": before["nvs"] != after["nvs"]}
        with patch.object(AUDIT, "GEOMETRY", self.geometry):
            return AUDIT.audit_restored_ranges(before, after, observation)

    def test_equal_ranges_remain_byte_equal(self):
        before = self.fixture()
        self.assertTrue(all(row["byte_equal"] for row in self.check(before, before).values()))

    def test_nvs_difference_is_reported_as_difference(self):
        before = self.fixture()
        after = {**before, "nvs": b"bbb"}
        report = self.check(before, after)
        self.assertFalse(report["nvs"]["byte_equal"])
        self.assertNotEqual(report["nvs"]["sha256"], report["nvs"]["after_sha256"])
        self.assertTrue(all(row["byte_equal"] for name, row in report.items() if name != "nvs"))

    def test_all_static_differences_still_fail(self):
        before = self.fixture()
        for name in ("app", "page", "assets"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check(before, {**before, name: b"bad"})

    def test_unreported_nvs_difference_fails(self):
        before = self.fixture()
        observation = {"before_sha256": AUDIT.sha(b"aaa"), "latest_sha256": AUDIT.sha(b"aaa"), "changed": False}
        with self.assertRaises(ValueError):
            self.check(before, {**before, "nvs": b"bbb"}, observation)

    def test_wrong_nvs_digest_fails(self):
        before = self.fixture()
        observation = {"before_sha256": "0" * 64, "latest_sha256": AUDIT.sha(b"aaa"), "changed": False}
        with self.assertRaises(ValueError):
            self.check(before, before, observation)

    def test_missing_region_fails(self):
        before = self.fixture()
        with self.assertRaises(ValueError):
            self.check(before, {name: raw for name, raw in before.items() if name != "assets"})

    def test_truncated_nvs_is_not_accepted(self):
        before = self.fixture()
        with self.assertRaises(ValueError):
            self.check(before, {**before, "nvs": b"bb"})

    def test_changed_flag_requires_boolean(self):
        before = self.fixture()
        observation = {"before_sha256": AUDIT.sha(b"aaa"), "latest_sha256": AUDIT.sha(b"aaa"), "changed": 0}
        with self.assertRaises(ValueError):
            self.check(before, before, observation)


if __name__ == "__main__":
    unittest.main()
