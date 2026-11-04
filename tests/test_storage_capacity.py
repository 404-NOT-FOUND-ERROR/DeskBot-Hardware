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
from protocol.storage_capacity import compare_images, image_facts, validate_build_report
from protocol.storage_n32r16 import FORMAL_CSV_SHA256


def image(size_nibble: int, payload: bytes = b"abcd", mode: int = 2) -> bytes:
    header = bytearray(24)
    header[0], header[1], header[2], header[3], header[23] = 0xE9, 1, mode, size_nibble << 4, 1
    raw = bytes(header) + struct.pack("<II", 0x3C000020, len(payload)) + payload
    checksum = 0xEF
    for value in payload:
        checksum ^= value
    raw += bytes((len(raw) | 15) - len(raw)) + bytes((checksum,))
    return raw + hashlib.sha256(raw).digest()


class CapacityImageTests(unittest.TestCase):
    def test_capacity_header_and_digest_are_validated_separately_from_segments(self):
        result = compare_images(image(4), image(5))
        self.assertTrue(result["only_header_capacity_and_digest_changed"])
        self.assertTrue(result["segment_payloads_equal"])
        self.assertEqual(result["control"]["configured_flash_bytes"], 16 * 1024 * 1024)
        self.assertEqual(result["candidate"]["configured_flash_bytes"], 32 * 1024 * 1024)
        self.assertEqual(result["first_differing_offset"], 3)

    def test_payload_change_cannot_be_mislabeled_as_header_only(self):
        result = compare_images(image(4), image(5, b"different code"))
        self.assertFalse(result["only_header_capacity_and_digest_changed"])
        self.assertFalse(result["segment_payloads_equal"])

    def test_bad_checksum_and_recomputed_digest_are_rejected(self):
        raw = bytearray(image(4))
        raw[-33] ^= 1
        raw[-32:] = hashlib.sha256(raw[:-32]).digest()
        with self.assertRaisesRegex(ValueError, "checksum"):
            image_facts(bytes(raw))

    def test_wrong_digest_truncation_and_unreviewed_trailers_are_rejected(self):
        raw = image(4)
        for invalid in (raw[:-1], raw + b"signature", raw[:-32] + bytes(32), b"invalid"):
            with self.assertRaises(ValueError):
                image_facts(invalid)

    def test_frequency_mode_and_wrong_capacity_pair_are_rejected(self):
        for candidate in (image(5, mode=0), image(4)):
            with self.assertRaises(ValueError):
                compare_images(image(4), candidate)

    def test_formal_partition_csv_is_measured_instead_of_trusting_an_invented_digest(self):
        raw = (ROOT / "firmware/body/partitions/v2/16m.csv").read_bytes()
        self.assertEqual(FORMAL_CSV_SHA256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(FORMAL_CSV_SHA256), 64)


class CapacityBuildReportTests(unittest.TestCase):
    def report(self):
        path = ROOT / "artifacts/expression-capacity-build-e1-37b-20261030/build-report.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_recorded_build_preserves_offline_and_protected_boundaries(self):
        validate_build_report(self.report())

    def test_capacity_change_cannot_hide_ui_audio_or_partition_changes(self):
        mutations = (
            (["comparison", "config_changes", "CONFIG_USE_EMOTE_MESSAGE_STYLE"], {"old": "y", "candidate": "n"}),
            (["comparison", "control_app_matches_accepted_e126"], False),
            (["comparison", "unchanged_outputs", "expression_assets.bin"], False),
            (["builds", 1, "artifacts", "partition_table/partition-table.bin", "sha256"], "0" * 64),
            (["builds", 1, "artifacts", "srmodels/srmodels.bin", "sha256"], "0" * 64),
            (["comparison", "config_changes", "CONFIG_BOOTLOADER_FLASH_32BIT_ADDR", "candidate"], "n"),
            (["comparison", "config_changes", "CONFIG_IDF_EXPERIMENTAL_FEATURES"], {"old": "n", "candidate": "y"}),
        )
        for path, value in mutations:
            report = copy.deepcopy(self.report())
            target = report
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_build_report(report)

    def test_build_success_cannot_be_promoted_to_deployment_or_hardware_acceptance(self):
        for key in ("deployment_eligible", "flash_authorized", "runtime_verified", "hardware_accepted"):
            report = copy.deepcopy(self.report())
            report[key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_build_report(report)

    def test_incomplete_build_pair_and_fabricated_headroom_are_rejected(self):
        report = self.report()
        report["builds"] = report["builds"][:1]
        with self.assertRaises(ValueError):
            validate_build_report(report)
        report = self.report()
        report["comparison"]["candidate_app_partition_free_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "arithmetic"):
            validate_build_report(report)

    def test_header_capacity_cannot_claim_upper_half_cached_access_is_ready(self):
        for key in ("quad_cache_enabled", "octal_cache_enabled", "experimental_features_enabled",
                    "upper_assets_deployment_ready"):
            report = self.report()
            report["upper_half_cache"][key] = True
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "upper-half"):
                validate_build_report(report)


if __name__ == "__main__":
    unittest.main()
