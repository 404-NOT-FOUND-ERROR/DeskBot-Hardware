"""Portable tests for the E1.40 read-only paired-capacity preflight."""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_capacity_boot import (
    CANDIDATE_APP_BYTES,
    CANDIDATE_BOOTLOADER_BYTES,
    PHYSICAL_FLASH_BYTES,
    parse_identity,
    read_log_is_safe,
    sector_span,
)


class StorageCapacityBootTests(unittest.TestCase):
    def test_candidate_lengths_and_erase_spans_are_exact(self) -> None:
        self.assertEqual(CANDIDATE_BOOTLOADER_BYTES, 15504)
        self.assertEqual(CANDIDATE_APP_BYTES, 4476944)
        self.assertEqual(sector_span(0, CANDIDATE_BOOTLOADER_BYTES),
                         {"offset": 0, "bytes": 16384, "end_exclusive": 16384})
        self.assertEqual(sector_span(0x10000, CANDIDATE_APP_BYTES),
                         {"offset": 0x10000, "bytes": 4481024, "end_exclusive": 0x456000})

    def test_physical_capacity_is_32_mib(self) -> None:
        self.assertEqual(PHYSICAL_FLASH_BYTES, 32 * 1024 * 1024)

    def test_identity_requires_fresh_mxic_octal_evidence(self) -> None:
        flash = "\n".join((
            "MAC: 9c:cc:01:fb:a8:78", "Manufacturer: c2", "Device: 8039",
            "Detected flash size: 32MB", "Flash type set in eFuse: octal (8 data lines)",
            "Flash voltage set by eFuse to 3.3V"))
        chip = "MAC: 9c:cc:01:fb:a8:78"
        facts = parse_identity(flash, chip)
        self.assertEqual(facts["physical_flash_bytes"], 32 * 1024 * 1024)
        self.assertEqual(facts["efuse_flash_type"], "octal")

    def test_write_or_erase_log_is_rejected(self) -> None:
        log = "Read 33554432 bytes at 0x00000000\nStaying in bootloader.\nwrite_flash"
        self.assertFalse(read_log_is_safe(log, "current-full-32MiB.bin"))

    def test_successful_read_log_is_accepted(self) -> None:
        log = "Read 33554432 bytes at 0x00000000\nStaying in bootloader.\n"
        self.assertTrue(read_log_is_safe(log, "current-full-32MiB.bin"))


if __name__ == "__main__":
    unittest.main()
