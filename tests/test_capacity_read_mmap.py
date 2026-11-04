import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol.storage_capacity_probe import (BOUNDARY, LOWER_ADDRESS, PHYSICAL_FLASH_BYTES,
                                             SAMPLE_BYTES, SCHEMA, UPPER_ADDRESS, parse_probe)


def valid_probe() -> dict:
    payload = {
        "schema": SCHEMA,
        "flash_size": PHYSICAL_FLASH_BYTES,
        "flash_size_matches_expected": True,
        "boundary": BOUNDARY,
        "sample_bytes": SAMPLE_BYTES,
        "lower_address": LOWER_ADDRESS,
        "upper_address": UPPER_ADDRESS,
        "lower_sample_hex": "ff" * SAMPLE_BYTES,
        "upper_sample_hex": "ff" * SAMPLE_BYTES,
        "lower_read_status": "ESP_OK",
        "upper_read_status": "ESP_OK",
        "out_of_range_read_status": "ESP_ERR_INVALID_ARG",
        "lower_mmap_status": "ESP_OK",
        "upper_mmap_status": "ESP_OK",
        "lower_read_mmap_equal": True,
        "upper_read_mmap_equal": True,
        "upper_read_all_ff": True,
        "out_of_range_read_rejected": True,
        "lower_mmap_released": True,
        "upper_mmap_released": True,
        "mmap_pages_before": 10,
        "mmap_pages_while_lower_mapped": 8,
        "mmap_pages_after_lower": 10,
        "mmap_pages_while_upper_mapped": 9,
        "mmap_pages_after_upper": 10,
        "lower_mmap_pages_restored": True,
        "upper_mmap_pages_restored": True,
        "mmap_pages_restored": True,
        "writes_performed": False,
        "probe_passed": True,
    }
    for prefix in ("before", "lower_mapped", "upper_mapped", "after"):
        for suffix in ("internal_free", "internal_largest", "internal_minimum",
                       "spiram_free", "spiram_largest", "spiram_minimum"):
            payload[f"{prefix}_{suffix}"] = 100
    return payload


class CapacityReadMmapTests(unittest.TestCase):
    def test_valid_probe_is_normalized(self):
        result = parse_probe(valid_probe())
        self.assertTrue(result["probe_passed"])
        self.assertFalse(result["writes_performed"])

    def test_write_claim_is_rejected(self):
        payload = valid_probe()
        payload["writes_performed"] = True
        with self.assertRaisesRegex(ValueError, "write"):
            parse_probe(payload)

    def test_inconsistent_pass_claim_is_rejected(self):
        payload = valid_probe()
        payload["upper_read_mmap_equal"] = False
        with self.assertRaisesRegex(ValueError, "probe_passed"):
            parse_probe(payload)

    def test_boundary_and_status_are_fail_closed(self):
        payload = valid_probe()
        payload["boundary"] = 15 * 1024 * 1024
        with self.assertRaisesRegex(ValueError, "boundary"):
            parse_probe(payload)
        payload = valid_probe()
        payload["upper_read_status"] = "ESP_ERR_INVALID_ARG"
        payload["probe_passed"] = False
        result = parse_probe(payload)
        self.assertFalse(result["probe_passed"])

    def test_malformed_sample_is_rejected(self):
        payload = valid_probe()
        payload["upper_sample_hex"] = "zz"
        with self.assertRaisesRegex(ValueError, "upper_sample_hex"):
            parse_probe(payload)

    def test_reference_samples_must_match_full_readback(self):
        payload = valid_probe()
        result = parse_probe(
            payload,
            expected_lower_sample=bytes(0xFF for _ in range(SAMPLE_BYTES)),
            expected_upper_sample=bytes(0xFF for _ in range(SAMPLE_BYTES)),
        )
        self.assertTrue(result["probe_passed"])
        with self.assertRaisesRegex(ValueError, "E1.40"):
            parse_probe(payload, expected_lower_sample=bytes(SAMPLE_BYTES))

    def test_status_and_page_claims_must_match_values(self):
        payload = valid_probe()
        payload["out_of_range_read_status"] = "ESP_ERR_INVALID_SIZE"
        with self.assertRaisesRegex(ValueError, "out-of-range"):
            parse_probe(payload)
        payload = valid_probe()
        payload["mmap_pages_after_lower"] = 9
        with self.assertRaisesRegex(ValueError, "lower mmap page-restoration"):
            parse_probe(payload)

    def test_upper_erased_claim_must_match_sample_bytes(self):
        payload = valid_probe()
        payload["upper_read_all_ff"] = False
        with self.assertRaisesRegex(ValueError, "upper sample"):
            parse_probe(payload)


if __name__ == "__main__":
    unittest.main()
