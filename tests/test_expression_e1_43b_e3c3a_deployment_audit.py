"""Offline tests for the E1.43b prepared-snapshot audit."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "expression_e143b_e3c3a_audit_tests",
    ROOT / "scripts/audit-expression-e1-43b-e3c3a-deployment.py",
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

FLASH_ID = """esptool.py v4.12.0
Serial port COM4
Chip is ESP32-S3 (QFN56) (revision v0.2)
Features: WiFi, BLE, Embedded PSRAM 16MB (AP_1v8)
MAC: 02:00:00:43:a5:01
Detected flash size: 32MB
Flash type set in eFuse: octal (8 data lines)
"""
SECURITY = """esptool.py v4.12.0
Serial port COM4
MAC: 02:00:00:43:a5:01
Secure Boot: Disabled
Flash Encryption: Disabled
"""


class SnapshotChunkAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        (self.folder / "captures").mkdir()
        self.snapshot = b"".join(bytes([index]) * 0x100000 for index in range(4))
        self.previous_size = audit.FLASH_BYTES
        audit.FLASH_BYTES = len(self.snapshot)
        for index in range(4):
            offset = index * 0x100000
            (self.folder / "captures" /
             f"{index + 1:03d}-full-readback-{offset:08x}-00100000.bin").write_bytes(
                 self.snapshot[offset:offset + 0x100000])

    def tearDown(self):
        audit.FLASH_BYTES = self.previous_size
        self.temporary.cleanup()

    def test_exact_ordered_chunks_match_snapshot(self):
        rows = audit._check_snapshot_chunks(self.folder, self.snapshot)
        self.assertEqual([row["offset"] for row in rows], [0, 0x100000, 0x200000, 0x300000])
        self.assertEqual([row["bytes"] for row in rows], [0x100000] * 4)
        self.assertEqual(rows[0]["capture_file"], "001-full-readback-00000000-00100000.bin")

    def test_capture_is_bound_to_the_following_com4_log(self):
        transfers = [{"log": "002-full-readback.log", "log_sequence": 2,
                      "offset": 0, "bytes": 0x100000, "log_sha256": "a" * 64}]
        captures = [{"capture_file": "001-full-readback-00000000-00100000.bin",
                     "capture_sequence": 1, "offset": 0, "bytes": 0x100000,
                     "sha256": "b" * 64}]
        bound = audit._bind_read_transfers_to_captures(transfers, captures)
        self.assertEqual(bound[0]["capture_sha256"], "b" * 64)

    def test_unpaired_capture_log_sequence_is_rejected(self):
        transfers = [{"log": "003-full-readback.log", "log_sequence": 3,
                      "offset": 0, "bytes": 0x100000, "log_sha256": "a" * 64}]
        captures = [{"capture_file": "001-full-readback-00000000-00100000.bin",
                     "capture_sequence": 1, "offset": 0, "bytes": 0x100000,
                     "sha256": "b" * 64}]
        with self.assertRaisesRegex(ValueError, "does not follow"):
            audit._bind_read_transfers_to_captures(transfers, captures)

    def test_missing_chunk_is_rejected(self):
        (self.folder / "captures" / "004-full-readback-00300000-00100000.bin").unlink()
        with self.assertRaisesRegex(ValueError, "ordered one-MiB"):
            audit._check_snapshot_chunks(self.folder, self.snapshot)

    def test_chunk_content_mismatch_is_rejected(self):
        path = self.folder / "captures" / "002-full-readback-00100000-00100000.bin"
        path.write_bytes(b"\x00" * 0x100000)
        with self.assertRaisesRegex(ValueError, "differs from the complete snapshot"):
            audit._check_snapshot_chunks(self.folder, self.snapshot)


class ReadTransferAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        (self.folder / "logs").mkdir()
        self.previous_size = audit.FLASH_BYTES
        audit.FLASH_BYTES = 4 * 0x100000
        self.identity = audit.parse_identity(FLASH_ID, SECURITY).digest
        (self.folder / "logs/001-flash-id.log").write_text(FLASH_ID, encoding="utf-8")
        (self.folder / "logs/002-security-info.log").write_text(SECURITY, encoding="utf-8")
        for index in range(4):
            offset = index * 0x100000
            text = ("esptool.py v4.12.0\nSerial port COM4\n"
                    "MAC: 02:00:00:43:a5:01\n"
                    f"Read 1048576 bytes at 0x{offset:08x} in 25.4 seconds\n")
            (self.folder / "logs" / f"{index + 3:03d}-full-readback.log").write_text(
                text, encoding="utf-8")
        (self.folder / "logs/007-normal-boot-capture.log").write_text(
            "Capture complete: boot.log (lines=10)\n", encoding="utf-8")

    def tearDown(self):
        audit.FLASH_BYTES = self.previous_size
        self.temporary.cleanup()

    def test_read_logs_bind_each_block_to_the_same_com4_identity(self):
        rows = audit._check_read_transfer_logs(self.folder, self.identity)
        self.assertEqual([row["offset"] for row in rows], [0, 0x100000, 0x200000, 0x300000])
        self.assertNotIn("mac", rows[0])

    def test_read_log_with_other_mac_is_rejected(self):
        path = self.folder / "logs/004-full-readback.log"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "02:00:00:43:a5:01", "02:00:00:43:a5:02"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different COM4 device"):
            audit._check_read_transfer_logs(self.folder, self.identity)

    def test_missing_or_write_operation_log_is_rejected(self):
        (self.folder / "logs/006-full-readback.log").unlink()
        with self.assertRaisesRegex(ValueError, "complete Flash"):
            audit._check_read_transfer_logs(self.folder, self.identity)
        (self.folder / "logs/006-full-readback.log").write_text(
            "esptool.py v4.12.0\nSerial port COM4\nMAC: 02:00:00:43:a5:01\n"
            "Read 1048576 bytes at 0x00300000 in 25.4 seconds\n", encoding="utf-8")
        (self.folder / "logs/008-write-app.log").write_text("write", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "write or erase"):
            audit._check_read_transfer_logs(self.folder, self.identity)

    def test_log_inventory_rejects_nested_directory(self):
        (self.folder / "logs/nested").mkdir()
        with self.assertRaisesRegex(ValueError, "only regular files"):
            audit._check_read_transfer_logs(self.folder, self.identity)

    def test_identity_digest_must_match_receipt(self):
        with self.assertRaisesRegex(ValueError, "migrated device"):
            audit._check_read_transfer_logs(self.folder, "f" * 64)

    def test_repo_file_rejects_traversal(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            audit._repo_file(Path("../outside.bin"))

if __name__ == "__main__":
    unittest.main()
