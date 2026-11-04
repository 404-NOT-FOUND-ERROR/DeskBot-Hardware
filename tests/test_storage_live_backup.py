from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_live_backup import (  # noqa: E402
    MIB, configured_flash_bytes, require_independent_verify, sha, verify_capture_files,
)


class StorageLiveBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tmp").mkdir(exist_ok=True)
        cls.work = tempfile.TemporaryDirectory(prefix="e135c-test-", dir=ROOT / "tmp")
        cls.directory = Path(cls.work.name)
        cls.mac = ":".join(["02", "00", "00", "00", "00", "01"])
        chunk = bytes([0xFF]) * MIB
        cls.index = {"edition": "E1.35c", "chip": "esp32s3", "flash_written": False,
                     "capture_complete": True, "physical_flash_bytes": 16 * MIB,
                     "mac": cls.mac, "chunks": [], "attempts": []}
        for i in range(16):
            offset = i * MIB
            path = cls.directory / f"chunk-{i}.bin"
            path.write_bytes(chunk)
            log = cls.directory / f"read-{i}.log"
            log.write_text(f"esptool.py v4.12.0\nMAC: {cls.mac}\nRead {MIB} bytes at {offset:#010x} in 1 seconds...\n",
                           encoding="utf-8")
            cls.index["chunks"].append({"offset": offset, "bytes": MIB, "path": path.name,
                "sha256": sha(chunk), "transport_digest_verified": True, "log": log.name})
            argv = ["python", "-m", "esptool", "--before", "usb_reset", "--after", "no_reset",
                    "read_flash", "--no-progress", "--flash_size", "16MB", hex(offset), hex(MIB), str(path)]
            cls.index["attempts"].append({"offset": offset, "bytes": MIB, "log": log.name,
                                         "returncode": 0, "argv": argv})
        full = cls.directory / "whole.bin"
        full.write_bytes(chunk * 16)
        cls.index["full_image"] = {"path": full.name, "bytes": 16 * MIB, "sha256": sha(full.read_bytes())}

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_headers_distinguish_configured_16_and_physical_32(self):
        self.assertEqual(configured_flash_bytes(bytes.fromhex("e903024f")), 16 * MIB)
        self.assertEqual(configured_flash_bytes(bytes.fromhex("e903025f")), 32 * MIB)
        for header in (b"", bytes.fromhex("0000024f"), bytes.fromhex("e90302ff")):
            with self.subTest(header=header), self.assertRaises(ValueError):
                configured_flash_bytes(header)

    def test_complete_capture_is_independently_assembled(self):
        self.assertEqual(len(verify_capture_files(self.directory, self.index)), 16 * MIB)

    def test_missing_duplicate_and_wrong_capacity_chunks_are_rejected(self):
        for mutation in ("missing", "duplicate", "wrong_size"):
            index = copy.deepcopy(self.index)
            if mutation == "missing":
                index["chunks"].pop()
            elif mutation == "duplicate":
                index["chunks"].append(copy.deepcopy(index["chunks"][0]))
            else:
                index["physical_flash_bytes"] = 32 * MIB
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_capture_files(self.directory, index)

    def test_hash_corruption_and_path_traversal_are_rejected(self):
        for field, value in (("sha256", "0" * 64), ("path", "../whole.bin")):
            index = copy.deepcopy(self.index)
            index["chunks"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_capture_files(self.directory, index)

    def test_write_command_and_failed_read_provenance_are_rejected(self):
        for mutation in ("write", "failure", "wrong_mac", "incomplete"):
            index = copy.deepcopy(self.index)
            if mutation == "write":
                index["attempts"][0]["argv"][index["attempts"][0]["argv"].index("read_flash")] = "write_flash"
            elif mutation == "failure":
                index["attempts"][0]["returncode"] = 2
            elif mutation == "wrong_mac":
                index["mac"] = ":".join(["02", "11", "00", "00", "00", "01"])
            else:
                index["capture_complete"] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_capture_files(self.directory, index)

    def test_assembled_image_hash_is_required(self):
        index = copy.deepcopy(self.index)
        index["full_image"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "assembled"):
            verify_capture_files(self.directory, index)

    def test_independent_verify_rejects_header_transform_and_partial_range(self):
        filename = "whole.bin"
        size = 32 * MIB
        log = (f"MAC: {self.mac}\nVerifying 0x{size:x} ({size}) bytes @ 0x00000000 in flash against "
               f"{filename}...\n-- verify OK (digest matched)\n")
        require_independent_verify(log, self.mac, size, filename)
        for bad in (log.replace("verify OK", "verify FAILED"),
                    log.replace(self.mac, "wrong"),
                    log + "Flash params set to 0x025f\n",
                    log + "SHA digest in image updated\n",
                    log.replace("0x2000000", "0x1000000")):
            with self.subTest(bad=bad[-40:]), self.assertRaises(ValueError):
                require_independent_verify(bad, self.mac, size, filename)

    def test_public_report_separates_backup_from_deployment(self):
        report = json.loads((ROOT / "artifacts/expression-storage-live-e1-35c-20261029/report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["device"]["physical_flash_bytes"], 32 * MIB)
        self.assertEqual(report["device"]["configured_flash_bytes"], 16 * MIB)
        self.assertEqual(report["device"]["psram_bytes"], 16 * MIB)
        self.assertTrue(report["capture"]["independent_verify_passed"])
        self.assertFalse(report["flash_authorized"])
        self.assertFalse(report["device_written"])
        self.assertFalse(report["runtime_mmap_verified"])
        self.assertFalse(report["physical_interaction_accepted"])
        ranges = sorted(report["required_ranges"], key=lambda r: r["offset"])
        self.assertEqual(ranges[0]["offset"], 0)
        self.assertEqual(ranges[-1]["end_exclusive"], 0xEB4000)
        self.assertTrue(all(a["end_exclusive"] == b["offset"] for a, b in zip(ranges, ranges[1:])))


if __name__ == "__main__":
    unittest.main()
