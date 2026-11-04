"""E1.43a CPU parser and isolation regression tests, all synthetic/offline."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.capacity_query import LEGACY_PARTITIONS
from protocol.storage_upper_pattern import build_pattern, manifest_for
from protocol.storage_upper_pattern_runtime import MEMORY_SUFFIXES, extract_records, validate_capture


def record(kind, payload):
    body = json.dumps(payload, separators=(",", ":"))
    return f"[DESKBOT_E143A {kind} bytes={len(body.encode())}] {body}"


def synthetic_records():
    raw = build_pattern()
    manifest = manifest_for(raw)
    capacity = {"flash_size": 33554432, "source_tool": "self.get_flash_capacity",
                "partition_table": [{"label": label, **value} for label, value in LEGACY_PARTITIONS.items()]}
    begin = {"schema": "deskbot.upper_pattern.begin.v1", "start": 16777216, "bytes": 65536,
             "sector_bytes": 4096, "chunk_bytes": 1024, "writes_performed": False}
    blocks = [{"index": row["index"], "address": row["absolute_offset"], "bytes": 4096,
               "read_sha256": row["sha256"], "mmap_sha256": row["sha256"],
               "read_header_hex": row["header_hex"], "mmap_header_hex": row["header_hex"]}
              for row in manifest["blocks"]]
    boundary = "ff" * 16 + raw[:16].hex()
    summary = {"schema": "deskbot.upper_pattern.summary.v1", "read_sha256": manifest["sha256"],
               "mmap_sha256": manifest["sha256"], "low_alias_read_sha256": "1" * 64,
               "low_protected_read_sha256": "3" * 64,
               "boundary_address": 16777200, "boundary_read_hex": boundary, "boundary_mmap_hex": boundary,
               "boundary_read_status": "ESP_OK", "boundary_mmap_status": "ESP_OK",
               "pages_before": 189, "pages_while_upper": 188, "pages_after_upper": 189,
               "pages_while_boundary": 187, "pages_after_boundary": 189,
               "upper_released": True, "boundary_released": True, "writes_performed": False, "probe_passed": True}
    for prefix in ("before", "upper_mapped", "boundary_mapped", "after"):
        for suffix in MEMORY_SUFFIXES:
            summary[f"{prefix}_{suffix}"] = 20000 if suffix.endswith("free") else 10000
    return {"capacity": capacity, "begin": begin, "blocks": blocks, "summary": summary}, manifest, boundary


def log_for(records):
    parts = [record("capacity", records["capacity"]), record("begin", records["begin"])]
    parts += [record("block", row) for row in records["blocks"]]
    parts += [record("summary", records["summary"])] if "summary" in records else [record("aborted", records["aborted"])]
    return "boot noise\n" + "\n".join(parts) + "\nunrelated log"


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.records, self.manifest, self.boundary = synthetic_records()

    def checked(self, records=None):
        return validate_capture(self.records if records is None else records, self.manifest, "1" * 64, self.boundary, "3" * 64)

    def test_complete_synthetic_capture_passes_without_claiming_hardware(self):
        result = self.checked(extract_records(log_for(self.records)))
        self.assertTrue(result["probe_passed"])
        self.assertEqual(len(result["records"]["blocks"]), 16)
        self.assertTrue(all(result["checks"].values()))

    def test_missing_duplicate_truncated_and_unknown_records_rejected(self):
        log = log_for(self.records)
        for altered in ("", log.replace(record("block", self.records["blocks"][5]), ""),
                        log + "\n" + record("begin", self.records["begin"]),
                        log.replace(record("block", self.records["blocks"][3]), record("block", self.records["blocks"][3])[:-1]),
                        log + "\n[DESKBOT_E143A other bytes=2] {}"):
            with self.assertRaises(ValueError):
                extract_records(altered)

    def test_record_order_not_ignored(self):
        records = self.records
        parts = [record("begin", records["begin"]), record("capacity", records["capacity"])]
        parts += [record("block", row) for row in records["blocks"]]
        parts += [record("summary", records["summary"])]
        with self.assertRaisesRegex(ValueError, "order"):
            extract_records("\n".join(parts))

    def test_duplicate_blocks_and_boolean_indices_rejected(self):
        for value in (True, 0, -1, 16):
            bad = copy.deepcopy(self.records)
            bad["blocks"][1]["index"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                extract_records(log_for(bad))

    def test_bad_length_garbled_or_nonobject_json_rejected(self):
        for line in ("[DESKBOT_E143A begin bytes=1] {", "[DESKBOT_E143A begin bytes=0] ",
                     "[DESKBOT_E143A begin bytes=2] []", "[DESKBOT_E143A begin bytes=9999] {}"):
            with self.assertRaises(ValueError):
                extract_records(line)

    def test_duplicate_json_fields_and_nonfinite_numbers_rejected(self):
        for payload in ('{"start":16777216,"start":0}', '{"value":NaN}', '{"value":Infinity}'):
            with self.assertRaises(ValueError):
                extract_records(f"[DESKBOT_E143A begin bytes={len(payload)}] {payload}")

    def test_page_digest_and_each_sector_marker_must_match_reference(self):
        for key in ("read_sha256", "mmap_sha256", "read_header_hex", "mmap_header_hex"):
            bad = copy.deepcopy(self.records)
            bad["blocks"][15][key] = "00" * (64 if key.endswith("hex") else 32)
            with self.subTest(key=key):
                self.assertFalse(self.checked(bad)["probe_passed"])
        for key in ("read_sha256", "mmap_sha256"):
            bad = copy.deepcopy(self.records)
            bad["summary"][key] = "0" * 64
            self.assertFalse(self.checked(bad)["probe_passed"])

    def test_alias_and_boundary_substitution_fail_closed(self):
        for key, value in (("low_alias_read_sha256", self.manifest["sha256"]),
                           ("low_protected_read_sha256", "2" * 64), ("boundary_read_hex", "ff" * 32),
                           ("boundary_mmap_hex", "00" * 32), ("boundary_address", 16777216)):
            bad = copy.deepcopy(self.records)
            bad["summary"][key] = value
            self.assertFalse(self.checked(bad)["probe_passed"], key)

    def test_release_page_leaks_and_flash_errors_fail_closed(self):
        for key, value in (("upper_released", False), ("boundary_released", False),
                           ("pages_after_upper", 188), ("pages_after_boundary", 188),
                           ("pages_while_upper", 189), ("pages_while_boundary", 188),
                           ("boundary_read_status", "ESP_FAIL"), ("boundary_mmap_status", "ESP_ERR_NO_MEM"),
                           ("probe_passed", False), ("writes_performed", True)):
            bad = copy.deepcopy(self.records)
            bad["summary"][key] = value
            self.assertFalse(self.checked(bad)["probe_passed"], key)

    def test_schema_missing_unknown_boolean_and_invalid_memory_fields_rejected(self):
        for key, value in (("before_internal_free", True), ("pages_before", -1),
                           ("upper_released", 1), ("unknown", 0), ("before_internal_largest", 30000)):
            bad = copy.deepcopy(self.records)
            bad["summary"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.checked(bad)
        bad = copy.deepcopy(self.records)
        del bad["summary"]["after_spiram_minimum"]
        with self.assertRaises(ValueError):
            self.checked(bad)

    def test_wrong_capacity_partition_drift_or_new_upper_partition_rejected(self):
        bad = copy.deepcopy(self.records)
        bad["capacity"]["flash_size"] //= 2
        with self.assertRaises(ValueError):
            self.checked(bad)
        bad = copy.deepcopy(self.records)
        bad["capacity"]["partition_table"].append({"label": "upper", "address": 16777216, "size": 65536})
        with self.assertRaises(ValueError):
            self.checked(bad)
        bad = copy.deepcopy(self.records)
        bad["capacity"]["partition_table"][4]["size"] += 4096
        with self.assertRaises(ValueError):
            self.checked(bad)

    def test_geometry_write_claim_and_wrong_expected_reference_rejected(self):
        for key, value in (("start", 16777217), ("bytes", 131072), ("chunk_bytes", 65536),
                           ("writes_performed", 0)):
            bad = copy.deepcopy(self.records)
            bad["begin"][key] = value
            with self.assertRaises(ValueError):
                self.checked(bad)
        for low, boundary in (("bad", self.boundary), ("1" * 64, "ff" * 32)):
            with self.assertRaises(ValueError):
                validate_capture(self.records, self.manifest, low, boundary, "3" * 64)

    def test_runtime_nvs_change_is_observation_but_immutable_prefix_drift_fails(self):
        bad = copy.deepcopy(self.records)
        bad["summary"]["low_alias_read_sha256"] = "4" * 64
        result = self.checked(bad)
        self.assertTrue(result["probe_passed"])
        self.assertFalse(result["observations"]["nvs_containing_low_page_matches_historical_reference"])
        bad["summary"]["low_protected_read_sha256"] = "4" * 64
        self.assertFalse(self.checked(bad)["probe_passed"])

    def test_abort_is_failure_and_cannot_mix_with_success(self):
        bad = copy.deepcopy(self.records)
        del bad["summary"]
        bad["blocks"] = bad["blocks"][:3]
        bad["aborted"] = {"error": "sector_hash_read_or_quiet_state_failed", "writes_performed": False}
        parsed = extract_records(log_for(bad))
        self.assertFalse(self.checked(parsed)["probe_passed"])
        with self.assertRaises(ValueError):
            extract_records(log_for(self.records) + "\n" + record("aborted", bad["aborted"]))

    def test_probe_is_default_off_mutually_exclusive_and_has_no_write_or_ui_entry(self):
        config = (ROOT / "firmware/body/main/Kconfig.projbuild").read_text(encoding="utf-8")
        block = config.split("config DESKBOT_UPPER_PATTERN_BOOT_EXPORT", 1)[1].split("\nconfig ", 1)[0]
        self.assertIn("default n", block)
        self.assertIn("!DESKBOT_FLASH_PROBE_BOOT_EXPORT", block)
        source = (ROOT / "firmware/body/main/boards/esp_vocat/upper_pattern_probe.h").read_text(encoding="utf-8")
        for forbidden in ("esp_flash_write(", "esp_flash_erase_region(", "nvs_set", "lv_", "GetDisplay(", "Schedule(", "AddTool("):
            self.assertNotIn(forbidden, source)
        self.assertIn("constexpr uint32_t kChunk = 1024", source)
        self.assertIn("~Mapping() { Release(); }", source)
        self.assertIn("seconds < 120", source)
        self.assertIn("vTaskDelay(1)", source)


if __name__ == "__main__":
    unittest.main()
