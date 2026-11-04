"""Portable E1.43a negative tests. No hardware and no private image required."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jsonschema
from protocol.storage_n32r16 import legacy_rows
from protocol.storage_upper_pattern import (
    HEADER, MAGIC, PAGE_BYTES, PAGE_START, PHYSICAL_FLASH_BYTES, PSRAM_BYTES, REFERENCE_SHA256,
    PATTERN_SHA256, SECTOR_BYTES, build_pattern, manifest_for, offline_report, parse_legacy_table, sha,
    validate_manifest, validate_pattern, validate_span, validate_target_context,
)


class PatternTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = build_pattern()
        cls.manifest = manifest_for(cls.raw)
        cls.schema = json.loads((ROOT / "protocol/storage-upper-pattern-e1-43a.schema.json").read_text(encoding="utf-8"))

    def context(self):
        return {"port": "COM4", "chip": "esp32s3", "physical_flash_bytes": PHYSICAL_FLASH_BYTES,
                "psram_bytes": PSRAM_BYTES, "bootloader_header_bytes": PHYSICAL_FLASH_BYTES,
                "driver_flash_bytes": PHYSICAL_FLASH_BYTES}

    def test_reproducible_exact_page_and_unique_sectors(self):
        self.assertEqual(self.raw, build_pattern())
        self.assertEqual(len(self.raw), PAGE_BYTES)
        self.assertEqual(sha(self.raw), PATTERN_SHA256)
        self.assertNotEqual(self.raw, b"\xff" * PAGE_BYTES)
        self.assertEqual(len({self.raw[i:i + SECTOR_BYTES] for i in range(0, PAGE_BYTES, SECTOR_BYTES)}), 16)
        self.assertEqual(len({row["sha256"] for row in self.manifest["blocks"]}), 16)

    def test_every_marker_has_correct_absolute_and_relative_address(self):
        for index, row in enumerate(self.manifest["blocks"]):
            relative = index * SECTOR_BYTES
            fields = HEADER.unpack_from(self.raw, relative)
            self.assertEqual(fields[:9], (MAGIC, 1, PAGE_START, PAGE_BYTES, index,
                                         PAGE_START + relative, relative, SECTOR_BYTES, SECTOR_BYTES - 64))
            self.assertEqual(row["sha256"], sha(self.raw[relative:relative + SECTOR_BYTES]))

    def test_schema_and_semantic_validation(self):
        jsonschema.Draft202012Validator.check_schema(self.schema)
        jsonschema.validate(self.manifest, self.schema)
        validate_manifest(self.manifest, self.raw)

    def test_truncation_extra_bytes_and_erased_page_rejected(self):
        for raw in (self.raw[:-1], self.raw + b"\0", b"\xff" * PAGE_BYTES, b"\0" * PAGE_BYTES):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                validate_pattern(raw)

    def test_header_payload_and_last_byte_tampering_rejected(self):
        for offset in (0, 8, 12, 20, 63, 64, 4095, 4096, 32768, PAGE_BYTES - 1):
            changed = bytearray(self.raw)
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_pattern(bytes(changed))

    def test_sector_alias_and_reordering_rejected(self):
        for raw in (self.raw[:4096] * 16, self.raw[4096:] + self.raw[:4096],
                    self.raw[:32768] * 2):
            with self.assertRaises(ValueError):
                validate_pattern(raw)

    def test_manifest_tamper_and_forged_authorization_rejected(self):
        for key, value in (("sha256", "0" * 64), ("deployment_eligible", True),
                           ("start", PAGE_START + SECTOR_BYTES), ("write_command", "forbidden"),
                           ("format_version", True)):
            bad = copy.deepcopy(self.manifest)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_manifest(bad, self.raw)

    def test_missing_duplicate_swapped_and_corrupt_block_records_rejected(self):
        for mode in ("missing", "duplicate", "swapped", "offset", "header", "bool"):
            bad = copy.deepcopy(self.manifest)
            if mode == "missing":
                bad["blocks"].pop()
            elif mode == "duplicate":
                bad["blocks"][1] = bad["blocks"][0]
            elif mode == "swapped":
                bad["blocks"][0], bad["blocks"][1] = bad["blocks"][1], bad["blocks"][0]
            elif mode == "offset":
                bad["blocks"][15]["absolute_offset"] -= 4096
            elif mode == "header":
                bad["blocks"][15]["header_hex"] = "00" * 64
            else:
                bad["blocks"][1]["index"] = True
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_manifest(bad, self.raw)

    def test_schema_rejects_booleans_extra_fields_and_unsafe_geometry(self):
        for key, value in (("format_version", True), ("bytes", 65535), ("start", 0),
                           ("deployment_eligible", True), ("write_command", "forbidden")):
            bad = copy.deepcopy(self.manifest)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(bad, self.schema)

    def test_only_expected_intended_body_tuple_allowed(self):
        validate_target_context(self.context(), legacy_rows())
        for key, value in (("port", "COM8"), ("port", "COM13"), ("chip", "esp32c61"),
                           ("physical_flash_bytes", 16 * 1024 * 1024), ("psram_bytes", 8 * 1024 * 1024),
                           ("driver_flash_bytes", 16 * 1024 * 1024), ("bootloader_header_bytes", 16 * 1024 * 1024)):
            bad = self.context()
            bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_target_context(bad, legacy_rows())

    def test_reserved_overlap_unaligned_overflow_and_boolean_spans_rejected(self):
        for start, size, capacity in ((0, PAGE_BYTES, PHYSICAL_FLASH_BYTES),
                                     (0x8000, 4096, PHYSICAL_FLASH_BYTES),
                                     (0x10000, 4096, PHYSICAL_FLASH_BYTES),
                                     (0x5EC000, 4096, PHYSICAL_FLASH_BYTES),
                                     (0xBC8000, 4096, PHYSICAL_FLASH_BYTES),
                                     (PAGE_START + 1, PAGE_BYTES, PHYSICAL_FLASH_BYTES),
                                     (PAGE_START, PAGE_BYTES - 1, PHYSICAL_FLASH_BYTES),
                                     (PHYSICAL_FLASH_BYTES, 4096, PHYSICAL_FLASH_BYTES),
                                     (PAGE_START, 0, PHYSICAL_FLASH_BYTES),
                                     (PAGE_START, True, PHYSICAL_FLASH_BYTES),
                                     (PAGE_START, PAGE_BYTES, 16 * 1024 * 1024)):
            with self.subTest(start=start, size=size), self.assertRaises(ValueError):
                validate_span(start, size, capacity, legacy_rows())

    def test_neighbor_page_or_expanded_test_range_rejected(self):
        for start, size in ((PAGE_START + PAGE_BYTES, PAGE_BYTES), (PAGE_START, PAGE_BYTES * 2)):
            with self.assertRaises(ValueError):
                validate_target_context(self.context(), legacy_rows(), start=start, size=size)

    def test_partition_drift_and_extra_upper_allocation_rejected(self):
        rows = legacy_rows()
        rows.append({"name": "future", "type": 1, "subtype": 0x82, "flags": 0,
                     "start": PAGE_START, "size_bytes": PAGE_BYTES, "end_exclusive": PAGE_START + PAGE_BYTES})
        with self.assertRaises(ValueError):
            validate_target_context(self.context(), rows)
        rows = legacy_rows()
        rows[-1]["flags"] = 1
        with self.assertRaises(ValueError):
            validate_target_context(self.context(), rows)
        rows = legacy_rows()
        rows[-1]["flags"] = False
        with self.assertRaises(ValueError):
            validate_target_context(self.context(), rows)

    def test_corrupt_table_or_wrong_length_rejected(self):
        for raw in (b"\xff" * 4096, b"\0" * 4096, b"\xff" * 3072):
            with self.assertRaises(ValueError):
                parse_legacy_table(raw)

    def test_untrusted_full_reference_rejected(self):
        for raw in (b"", b"\xff" * PHYSICAL_FLASH_BYTES):
            with self.assertRaises(ValueError):
                offline_report(self.raw, self.manifest, raw)

    def test_public_report_is_offline_and_reproducible(self):
        public = ROOT / "artifacts/expression-upper-pattern-e1-43a-20261102"
        manifest = json.loads((public / "pattern-manifest.json").read_text(encoding="utf-8"))
        report = json.loads((public / "report.json").read_text(encoding="utf-8"))
        validate_manifest(manifest, self.raw)
        self.assertEqual(report["pattern"], manifest)
        self.assertTrue(all(value is False for value in report["safety"].values()))
        self.assertEqual(report["reference"]["sha256"], REFERENCE_SHA256)
        self.assertEqual(report["reference"]["role"], "historical_only_not_current_backup")
        self.assertEqual(report["result"], "offline_material_ready_device_gates_pending")
        self.assertIn("restored_body_physical_regression", report["pending_gates"])
        self.assertNotIn("commands", report)


class GeneratorSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tmp").mkdir(exist_ok=True)

    def module(self):
        spec = importlib.util.spec_from_file_location("e143a_pattern_generator", ROOT / "scripts/prepare-expression-e1-43a-pattern.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_bad_reference_produces_no_output_directory(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as folder:
            base = Path(folder)
            reference = base / "invalid-reference.bin"
            reference.write_bytes(b"invalid")
            with (patch.object(module, "REFERENCE", reference), patch.object(module, "PRIVATE", base / "private"),
                  patch.object(module, "PUBLIC", base / "public"), patch.object(sys, "argv", ["prepare", "--run-label", "test"]),
                  self.assertRaises(ValueError)):
                module.main()
            self.assertFalse((base / "private").exists())
            self.assertFalse((base / "public").exists())

    def test_existing_evidence_is_not_overwritten(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as folder:
            base = Path(folder)
            destination = base / "existing"
            destination.mkdir()
            sentinel = destination / "do-not-change.txt"
            sentinel.write_bytes(b"retained evidence")
            with (patch.object(module, "PRIVATE", base), patch.object(sys, "argv", ["prepare", "--run-label", "existing"]),
                  redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure):
                module.main()
            self.assertEqual(failure.exception.code, 2)
            self.assertEqual(sentinel.read_bytes(), b"retained evidence")

    def test_run_label_cannot_escape_evidence_directory(self):
        module = self.module()
        for label in ("../elsewhere", "..", "a/b", "a\\b", "D:\\", "UPPER", ""):
            with (self.subTest(label=label), patch.object(sys, "argv", ["prepare", "--run-label", label]),
                  redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure):
                module.main()
            self.assertEqual(failure.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
