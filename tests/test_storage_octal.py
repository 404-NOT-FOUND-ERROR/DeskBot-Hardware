from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_addressing import sector_range
from protocol.storage_octal import (
    MIB, candidate_mmap, compare_images, effective_octal_config, expected_explicit_config,
    validate_record, validate_relative, validate_report,
)


def esp_image(capacity: int = 5, mode: int = 3, body: bytes = b"boot-and-flash-code") -> bytes:
    header = bytearray(24)
    header[0], header[1], header[2], header[3], header[23] = 0xE9, 1, mode, capacity << 4 | 15, 1
    raw = bytes(header) + struct.pack("<II", 0x40370000, len(body)) + body
    checksum = 0xEF
    for value in body:
        checksum ^= value
    raw += bytes((len(raw) | 15) - len(raw)) + bytes((checksum,))
    return raw + hashlib.sha256(raw).digest()


class ExplicitOctalConfigTests(unittest.TestCase):
    def test_octal_dout_header_does_not_select_quad_runtime(self):
        config = expected_explicit_config(32)
        effective_octal_config(config, 32)
        facts = compare_images(esp_image(), esp_image())
        self.assertEqual(facts["candidate"]["flash_mode"], 3)
        self.assertTrue(config["ESPTOOLPY_FLASHMODE_OPI"])
        self.assertTrue(config["ESPTOOLPY_FLASH_SAMPLE_MODE_STR"])
        self.assertFalse(config["ESPTOOLPY_FLASH_SAMPLE_MODE_DTR"])

    def test_derived_cache32_is_capacity_specific(self):
        self.assertFalse(expected_explicit_config(16)["BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH"])
        self.assertTrue(expected_explicit_config(32)["BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH"])
        with self.assertRaises(ValueError):
            effective_octal_config(expected_explicit_config(16), 32)

    def test_auto_detection_or_dtr_cannot_hide_in_explicit_str_candidate(self):
        for key in ("ESPTOOLPY_FLASH_MODE_AUTO_DETECT", "ESPTOOLPY_FLASH_SAMPLE_MODE_DTR",
                    "BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH", "IDF_EXPERIMENTAL_FEATURES",
                    "SPI_FLASH_SIZE_OVERRIDE", "SPI_FLASH_ROM_IMPL"):
            config = expected_explicit_config(32)
            config[key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                effective_octal_config(config, 32)

    def test_driver_mode_and_frequency_cannot_drift(self):
        for key, value in (("ESPTOOLPY_OCT_FLASH", False), ("SPI_FLASH_SUPPORT_MXIC_OPI_CHIP", False),
                           ("ESPTOOLPY_FLASHFREQ", "120m"), ("ESPTOOLPY_FLASHMODE", "dio")):
            config = expected_explicit_config(32)
            config[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                effective_octal_config(config, 32)

    def test_string_and_integer_booleans_are_rejected(self):
        for value in ("false", "true", 0, 1):
            config = expected_explicit_config(32)
            config["ESPTOOLPY_OCT_FLASH"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                effective_octal_config(config, 32)

    def test_guard_removal_does_not_claim_runtime_mmap(self):
        self.assertTrue(candidate_mmap(16)["rejected_by_24bit_guard"])
        candidate = candidate_mmap(32)
        self.assertFalse(candidate["rejected_by_24bit_guard"])
        self.assertFalse(candidate["runtime_mmap_verified"])
        self.assertFalse(candidate["runtime_verified"])

    def test_unsupported_capacity_is_not_implicitly_32m(self):
        for capacity in (8, 64, "32", True, None):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                candidate_mmap(capacity)


class ExplicitOctalImageTests(unittest.TestCase):
    def test_code_and_geometry_changes_are_reported_instead_of_assuming_header_only(self):
        result = compare_images(esp_image(mode=2), esp_image(body=b"different-and-longer-payload"))
        self.assertFalse(result["segments_equal"])
        self.assertFalse(result["segment_comparison"][0]["payload_equal"])
        self.assertFalse(result["segment_comparison"][0]["geometry_equal"])
        self.assertIn(2, result["header_changed_offsets"])
        self.assertGreater(result["differing_bytes_in_shared_range"], 0)

    def test_same_size_code_change_is_detected(self):
        a, b = esp_image(body=b"flash-code-A"), esp_image(body=b"flash-code-B")
        comparison = compare_images(a, b)
        self.assertEqual(comparison["length_delta_bytes"], 0)
        self.assertFalse(comparison["segment_comparison"][0]["payload_equal"])
        self.assertTrue(comparison["segment_comparison"][0]["geometry_equal"])

    def test_bad_image_digest_is_rejected(self):
        raw = esp_image()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            compare_images(raw, raw[:-1] + bytes((raw[-1] ^ 1,)))

    def test_bad_checksum_cannot_be_hidden_by_new_digest(self):
        raw = bytearray(esp_image())
        raw[-33] ^= 1
        raw[-32:] = hashlib.sha256(raw[:-32]).digest()
        with self.assertRaisesRegex(ValueError, "checksum"):
            compare_images(esp_image(), bytes(raw))

    def test_image_truncation_or_signature_is_not_silently_ignored(self):
        for raw in (esp_image()[:-10], esp_image() + b"unreviewed-trailer"):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                compare_images(esp_image(), raw)


class ExplicitOctalReportTests(unittest.TestCase):
    @staticmethod
    def report():
        return json.loads((ROOT / "artifacts/expression-octal-e1-39-20261030/report.json").read_bytes())

    def mutate(self, path, value):
        report = copy.deepcopy(self.report())
        node = report
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        return report

    def test_public_evidence_is_coherent_without_private_device_files(self):
        validate_report(self.report())

    def test_offline_build_cannot_be_promoted_to_current_device_or_accepted_state(self):
        for key in ("deployment_eligible", "flash_authorized", "runtime_verified", "hardware_accepted", "live_snapshot_current"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_report(self.mutate([key], True))

    def test_app_and_bootloader_must_both_use_explicit_octal(self):
        for target in ("effective_app_config", "effective_bootloader_config"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_report(self.mutate(["builds", 1, target, "ESPTOOLPY_OCT_FLASH"], False))

    def test_missing_control_or_reversed_capacity_pair_is_rejected(self):
        for builds in (self.report()["builds"][:1], list(reversed(self.report()["builds"]))):
            with self.subTest(count=len(builds)), self.assertRaises(ValueError):
                validate_report(self.mutate(["builds"], builds))

    def test_unchanged_flag_cannot_hide_model_or_asset_hash_drift(self):
        for name in ("expression_assets.bin", "srmodels/srmodels.bin", "partition_table/partition-table.bin"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_report(self.mutate(["builds", 1, "artifacts", name, "sha256"], "0" * 64))

    def test_required_elf_or_generated_config_cannot_be_omitted(self):
        for name in ("bootloader/bootloader.elf", "config/sdkconfig.json", "compile_commands.json"):
            report = self.report()
            del report["builds"][1]["artifacts"][name]
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_report(report)

    def test_image_metadata_must_link_to_artifact_and_segment_extent(self):
        changes = ((["builds", 1, "app_image", "sha256"], "0" * 64),
                   (["builds", 1, "bootloader_image", "configured_flash_bytes"], 16 * MIB),
                   (["builds", 1, "app_image", "segments", 0, "file_offset"], 0),
                   (["builds", 1, "app_image", "checksum_verified"], False))
        for path, value in changes:
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_report(self.mutate(path, value))

    def test_failed_build_and_false_headroom_cannot_pass_public_review(self):
        for path, value in ((["builds", 0, "steps", 1, "returncode"], 1),
                            (["builds", 1, "app_partition_free_bytes"], 6000 * 1024),
                            (["formal_source_unchanged"], False),
                            (["components", "actual_contents_match_lock"], False)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_report(self.mutate(path, value))

    def test_partial_sector_rollback_is_rejected(self):
        report = self.report()
        report["rollback"]["ranges"][1]["bytes"] -= 1
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_previous_candidate_range_cannot_replace_new_payload_geometry(self):
        report = self.report()
        report["rollback"]["images"][1]["payload_bytes"] += 4096
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_rollback_cannot_be_an_executable_or_claim_fresh_capture(self):
        for path, value in ((["rollback", "executable_flash_script_generated"], True),
                            (["rollback", "prewrite_identity_and_digest_refresh_required"], False),
                            (["rollback", "source_capture_sha256"], "0" * 64)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_report(self.mutate(path, value))

    def test_path_escape_and_unbounded_public_artifact_records_are_rejected(self):
        for path in ("tmp/../flash.bin", "D:/outside/file.bin", "/tmp/flash.bin", "tmp\\flash.bin"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_relative(path, "tmp/")
        for record in ({"bytes": 0, "sha256": "0" * 64}, {"bytes": 1, "sha256": "unknown"}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate_record(record)

    def test_real_candidate_rollback_covers_actual_last_erase_sector(self):
        report = self.report()
        for image, row in zip(report["rollback"]["images"], report["rollback"]["ranges"]):
            expected = sector_range(image["offset"], image["payload_bytes"])
            self.assertEqual({key: row[key] for key in expected}, expected)
            self.assertEqual(row["end_exclusive"] % 4096, 0)


class ExplicitOctalPublicReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("e139_review_tests", ROOT / "scripts/audit-expression-e1-39-octal.py")
        cls.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.audit)

    def evidence(self):
        public = ROOT / "artifacts/expression-octal-e1-39-20261030"
        return (json.loads((public / "report.json").read_bytes()),
                json.loads((public / "review.json").read_bytes()))

    def test_public_review_needs_no_toolchain_or_private_files(self):
        self.audit.validate_public_review(*self.evidence())

    def test_generated_config_value_cannot_hide_behind_correct_key(self):
        build, review = self.evidence()
        review["config_deltas"]["32m_vs_e137"]["generated"]["bootloader"]["ESPTOOLPY_OCT_FLASH"]["candidate"] = False
        with self.assertRaises(ValueError):
            self.audit.validate_public_review(build, review)

    def test_elf_and_paired_image_metadata_must_match_candidate(self):
        for label in ("elf", "image"):
            build, review = self.evidence()
            if label == "elf":
                review["elf_checks"]["app_vs_e137_32m"]["candidate"]["sha256"] = "0" * 64
            else:
                review["capacity_image_pair"]["xiaozhi.bin"]["candidate"]["segments"][0]["bytes"] += 4
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.audit.validate_public_review(build, review)

    def test_source_excerpt_extent_must_match_claimed_lines(self):
        build, review = self.evidence()
        review["source_anchors"][0]["end_line"] += 1
        with self.assertRaises(ValueError):
            self.audit.validate_public_review(build, review)


if __name__ == "__main__":
    unittest.main()
