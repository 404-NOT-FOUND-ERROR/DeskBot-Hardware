from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_addressing import (
    CONFIG_EXPECTED, MIB, bounded_image, capacity_case, compare_boot_metadata, effective_config,
    flash_evidence, mmap_guard, rollback_ranges, sector_range, validate_report, validate_rollback_files,
)

FLASH_LOG = """Manufacturer: c2
Device: 8039
Detected flash size: 32MB
Flash type set in eFuse: octal (8 data lines)
Flash voltage set by eFuse to 3.3V
"""
BOOT_LOG = """Octal Flash Mode Enabled
For OPI Flash, Use Default Flash Boot Mode
Found 16MB PSRAM device
Octal flash chip is using but qio mode is selected, will automatically
 switch to Octal mode
detected chip: mxic (opi)
flash io: opi_str
Detected size(32768k) larger than the size in the binary image header(
16384k). Using the size in the binary image header.
"""


def test_image(capacity: int = 4, date: bytes = b"", body: bytes = b"body-code") -> bytes:
    descriptor = bytearray(80)
    descriptor[0] = 80
    descriptor[8:14] = b"v5.5.5"
    descriptor[40:40 + len(date)] = date
    payload = bytes(descriptor) + body
    header = bytearray(24)
    header[0], header[1], header[2], header[3], header[23] = 0xE9, 1, 2, capacity << 4 | 15, 1
    raw = bytes(header) + struct.pack("<II", 0x3FCE2820, len(payload)) + payload
    checksum = 0xEF
    for byte in payload:
        checksum ^= byte
    raw += bytes((len(raw) | 15) - len(raw)) + bytes((checksum,))
    return raw + hashlib.sha256(raw).digest()


class AddressingEvidenceTests(unittest.TestCase):
    def test_opi_runtime_is_separate_from_dio_header_and_qio_compile_selection(self):
        facts = flash_evidence(FLASH_LOG, BOOT_LOG)
        self.assertEqual(facts["runtime_io"], "opi_str")
        self.assertEqual(facts["efuse_flash_type"], "octal")
        self.assertIsNone(facts["commercial_part_number"])
        self.assertEqual(facts["physical_flash_bytes"], 32 * MIB)
        self.assertEqual(facts["runtime_configured_flash_bytes"], 16 * MIB)

    def test_wrong_device_efuse_or_duplicate_identity_cannot_reuse_the_capture_claim(self):
        for log in (FLASH_LOG.replace("c2", "ef"), FLASH_LOG.replace("8039", "8038"),
                    FLASH_LOG.replace("32MB", "16MB"), FLASH_LOG.replace("octal", "quad"),
                    FLASH_LOG + "Device: 8039\n"):
            with self.subTest(log=log), self.assertRaises(ValueError):
                flash_evidence(log, BOOT_LOG)

    def test_absent_or_conflicting_runtime_mode_is_rejected(self):
        for log in (BOOT_LOG.replace("opi_str", "qio"), BOOT_LOG.replace("mxic (opi)", "generic"),
                    BOOT_LOG + "flash io: dio\n", BOOT_LOG + BOOT_LOG):
            with self.subTest(log=log), self.assertRaises(ValueError):
                flash_evidence(FLASH_LOG, log)

    def test_invisible_kconfig_bools_default_false_without_following_runtime_auto_detect(self):
        config = dict(CONFIG_EXPECTED)
        del config["BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH"]
        del config["BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH"]
        self.assertEqual(effective_config(config), CONFIG_EXPECTED)
        config["SPI_FLASH_SIZE_OVERRIDE"] = "false"
        with self.assertRaises(ValueError):
            effective_config(config)

    def test_app_only_32m_still_uses_16m_bootloader_without_explicit_override(self):
        self.assertEqual(capacity_case(16 * MIB, 32 * MIB, False)["expected_driver_limit_bytes"], 16 * MIB)
        self.assertEqual(capacity_case(32 * MIB, 32 * MIB, False)["expected_driver_limit_bytes"], 32 * MIB)
        result = capacity_case(16 * MIB, 32 * MIB, True)
        self.assertEqual(result["expected_driver_limit_bytes"], 32 * MIB)
        self.assertFalse(result["runtime_verified"])
        for args in ((True, 32 * MIB, False), (16 * MIB, 32 * MIB, "false")):
            with self.assertRaises(ValueError):
                capacity_case(*args)

    def test_mmap_guard_rejects_start_and_cross_boundary_but_not_the_exact_16m_end(self):
        for start, size in ((16 * MIB, 4096), (16 * MIB - 65536, 131072)):
            self.assertTrue(mmap_guard(start, size, CONFIG_EXPECTED)["rejected_by_24bit_guard"])
        self.assertFalse(mmap_guard(16 * MIB - 65536, 65536, CONFIG_EXPECTED)["rejected_by_24bit_guard"])
        with_cache = dict(CONFIG_EXPECTED, BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH=True)
        result = mmap_guard(16 * MIB, 8 * MIB, with_cache)
        self.assertFalse(result["rejected_by_24bit_guard"])
        self.assertFalse(result["runtime_mmap_verified"])

    def test_mmap_source_model_rejects_wrong_implementation_or_physical_overflow(self):
        with self.assertRaises(ValueError):
            mmap_guard(16 * MIB, MIB, dict(CONFIG_EXPECTED, SPI_FLASH_ROM_IMPL=True))
        for start, size in ((32 * MIB, 1), (-1, 4096), (0, 0), (False, 4096)):
            with self.assertRaises(ValueError):
                mmap_guard(start, size, CONFIG_EXPECTED)


class ExactRollbackTests(unittest.TestCase):
    def test_image_extraction_stops_before_partition_padding(self):
        image = test_image()
        capture = b"\xff" * 4096 + image + b"\xff" * 4096
        self.assertEqual(bounded_image(capture, 4096, len(image) + 4096), image)
        for bound in (24, len(image) - 1, len(capture)):
            with self.assertRaises(ValueError):
                bounded_image(capture, 4096, bound)

    def test_boot_timestamp_and_capacity_are_classified_but_never_exact_rollback(self):
        live = test_image(date=b"Sep 19 2026 12:34:56")
        result = compare_boot_metadata(live, test_image(5))
        self.assertEqual(result["date_field"], {"start": 72, "bytes": 24})
        self.assertTrue(result["only_reviewed_metadata_changed"])
        self.assertTrue(result["capacity_header_changed"])
        self.assertFalse(result["byte_identical"])
        self.assertFalse(result["build_is_exact_rollback"])

    def test_boot_code_change_cannot_be_hidden_by_timestamp_normalization(self):
        result = compare_boot_metadata(test_image(body=b"body-code"), test_image(body=b"body-evil"))
        self.assertFalse(result["only_reviewed_metadata_changed"])
        self.assertTrue(result["other_changed_offsets"])

    def test_bad_boot_date_or_image_checksum_is_not_accepted_as_metadata(self):
        with self.assertRaises(ValueError):
            compare_boot_metadata(test_image(date=b"unreviewed text"), test_image())
        corrupt = bytearray(test_image())
        corrupt[-33] ^= 1
        corrupt[-32:] = hashlib.sha256(corrupt[:-32]).digest()
        with self.assertRaises(ValueError):
            compare_boot_metadata(bytes(corrupt), test_image())

    def test_sector_ranges_include_unaligned_prefix_and_trailing_erase_bytes(self):
        self.assertEqual(sector_range(0, 16240), {"offset": 0, "bytes": 16384, "end_exclusive": 16384})
        self.assertEqual(sector_range(4095, 2), {"offset": 0, "bytes": 8192, "end_exclusive": 8192})
        app = sector_range(0x10000, 4477280)
        self.assertEqual(app["end_exclusive"] % 4096, 0)
        self.assertGreater(app["bytes"], 4477280)
        for offset, size in ((0, 0), (32 * MIB - 1, 2), (-1, 1), (False, 1)):
            with self.assertRaises(ValueError):
                sector_range(offset, size)

    def test_exact_live_trailing_bytes_are_required_instead_of_ff_padding_or_build_payload(self):
        raw = b"\xa5" * (32 * MIB)
        images = [{"id": "bootloader", "offset": 0, "payload_bytes": 16240}]
        plan = {"images": images, "ranges": rollback_ranges(raw, images)}
        correct = raw[:16384]
        validate_rollback_files(raw, plan, {"bootloader": correct})
        for files in ({}, {"bootloader": raw[:16240]}, {"bootloader": raw[:16240] + b"\xff" * 144}):
            with self.assertRaises(ValueError):
                validate_rollback_files(raw, plan, files)
        plan["ranges"][0]["bytes"] = 16240
        with self.assertRaises(ValueError):
            validate_rollback_files(raw, plan, {"bootloader": correct})

    def test_overlapping_erase_sectors_and_partial_device_capture_are_rejected(self):
        images = [{"id": "a", "offset": 0, "payload_bytes": 1}, {"id": "b", "offset": 1, "payload_bytes": 1}]
        with self.assertRaises(ValueError):
            rollback_ranges(b"\xff" * (32 * MIB), images)
        with self.assertRaises(ValueError):
            rollback_ranges(b"\xff" * (16 * MIB), images[:1])


class AddressingReportTests(unittest.TestCase):
    def report(self):
        return json.loads((ROOT / "artifacts/expression-addressing-e1-38-20261030/report.json").read_text(encoding="utf-8"))

    def test_published_report_stays_offline_and_separates_hardware_config_and_runtime(self):
        validate_report(self.report())

    def test_forged_runtime_authorization_or_realtime_state_is_rejected(self):
        for key in ("deployment_eligible", "flash_authorized", "runtime_verified", "hardware_accepted", "live_snapshot_current"):
            report = self.report()
            report[key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_report(report)

    def test_hardware_and_cache_inference_cannot_be_promoted_from_header_or_autodetection(self):
        mutations = ((["hardware", "efuse_flash_type"], "quad"),
                     (["hardware", "commercial_part_number"], "invented-part"),
                     (["hardware", "runtime_io"], "qio"),
                     (["candidate_configs", 0, "effective", "BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH"], True),
                     (["capacity_cases", 1, "expected_driver_limit_bytes"], 32 * MIB),
                     (["mmap_checks", 2, "rejected_by_24bit_guard"], False))
        for path, value in mutations:
            report = self.report()
            target = report
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_report(report)

    def test_missing_rollback_sector_or_unsafe_path_is_rejected(self):
        for path in ("../outside.bin", "/tmp/elsewhere.bin", "artifacts/public.bin", "tmp/e138-addressing/..\\..\\outside.bin"):
            report = self.report()
            report["rollback"]["ranges"][0]["private_file"] = path
            with self.assertRaises(ValueError):
                validate_report(report)
        for mode in ("drop", "shorten", "stale_ok"):
            report = self.report()
            if mode == "drop":
                report["rollback"]["ranges"].pop()
            elif mode == "shorten":
                report["rollback"]["ranges"][1]["bytes"] -= 4096
            else:
                report["rollback"]["prewrite_identity_and_digest_refresh_required"] = False
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_report(report)

    def test_missing_digest_or_escaped_source_path_cannot_pass_public_metadata_checks(self):
        for key in ("candidate_configs", "protected_ranges"):
            report = self.report()
            del report[key][0]["sha256"]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_report(report)
        report = self.report()
        report["source_evidence"][-1]["path"] = "firmware/..\\..\\outside.cc"
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_live_boot_difference_and_protected_boundaries_cannot_be_concealed(self):
        mutations = ((["live_boot_comparison", 0, "build_is_exact_rollback"], True),
                     (["live_boot_comparison", 0, "other_changed_offsets"], [1024]),
                     (["protected_ranges", 2, "offset"], 0xA000),
                     (["evidence", "capture_chunks_verified"], 16))
        for path, value in mutations:
            report = copy.deepcopy(self.report())
            target = report
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_report(report)


if __name__ == "__main__":
    unittest.main()
