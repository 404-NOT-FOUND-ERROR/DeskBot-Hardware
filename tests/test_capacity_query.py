from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.capacity_query import (  # noqa: E402
    LEGACY_PARTITIONS,
    UPPER_ASSETS_8M_PARTITIONS,
    PHYSICAL_FLASH_BYTES,
    parse_system_info,
    validate_expected_capacity,
)


def system_info(flash_size: int = PHYSICAL_FLASH_BYTES) -> dict:
    return {
        "version": 2,
        "flash_size": flash_size,
        "partition_table": [
            {"label": label, "type": 1, "subtype": 2,
             "address": values["address"], "size": values["size"]}
            for label, values in LEGACY_PARTITIONS.items()
        ],
    }


class CapacityQueryTests(unittest.TestCase):
    def test_official_shape_normalizes_without_claiming_mmap(self):
        result = validate_expected_capacity(json.dumps(system_info()))
        self.assertEqual(result["source_tool"], "self.get_system_info")
        self.assertEqual(result["flash_size_bytes"], 32 * 1024 * 1024)
        self.assertTrue(result["legacy_partition_table_matches"])
        self.assertFalse(result["mmap_verified"])
        self.assertFalse(result["upper_assets_verified"])

    def test_bounded_assistant_tool_response_is_accepted_and_redacted(self):
        payload = system_info()
        payload["source_tool"] = "self.get_flash_capacity"
        payload["mmap_verified"] = False
        payload["upper_assets_verified"] = False
        payload["mac_address"] = "must-not-be-copied"
        payload["uuid"] = "must-not-be-copied"
        result = validate_expected_capacity(payload)
        self.assertEqual(result["source_tool"], "self.get_flash_capacity")
        self.assertNotIn("mac_address", result)
        self.assertNotIn("uuid", result)

    def test_upper_assets_layout_is_explicit_and_exact(self):
        payload = system_info()
        payload["partition_table"] = [
            {"label": label, "address": values["address"], "size": values["size"]}
            for label, values in UPPER_ASSETS_8M_PARTITIONS.items()
        ]
        result = validate_expected_capacity(payload, layout="upper-assets-8m")
        self.assertEqual(result["expected_partition_layout"], "upper-assets-8m")
        self.assertTrue(result["partition_table_matches_expected"])
        with self.assertRaisesRegex(ValueError, "partition table"):
            validate_expected_capacity(payload)

    def test_unknown_capacity_source_is_rejected(self):
        payload = system_info()
        payload["source_tool"] = "self.get_anything"
        with self.assertRaisesRegex(ValueError, "source tool"):
            parse_system_info(payload)

    def test_wrong_capacity_is_observable_but_fail_closed(self):
        result = parse_system_info(system_info(16 * 1024 * 1024))
        self.assertFalse(result["flash_size_matches_expected"])
        with self.assertRaisesRegex(ValueError, "32 MiB"):
            validate_expected_capacity(system_info(16 * 1024 * 1024))

    def test_partition_drift_is_rejected(self):
        payload = system_info()
        payload["partition_table"][4]["size"] += 0x1000
        with self.assertRaisesRegex(ValueError, "partition table"):
            validate_expected_capacity(payload)

    def test_duplicate_or_malformed_partition_is_rejected(self):
        payload = system_info()
        payload["partition_table"].append(copy.deepcopy(payload["partition_table"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            parse_system_info(payload)
        payload = system_info()
        payload["partition_table"][0]["address"] = True
        with self.assertRaisesRegex(ValueError, "address"):
            parse_system_info(payload)

    def test_missing_or_invalid_json_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "flash_size"):
            parse_system_info({"partition_table": []})
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            parse_system_info("not-json")


if __name__ == "__main__":
    unittest.main()
