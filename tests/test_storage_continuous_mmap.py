"""Synthetic/offline E1.43b parser, reference and diagnostic boundary tests."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.capacity_query import UPPER_ASSETS_8M_PARTITIONS
from protocol.storage_continuous_mmap import (BLOCK, MEMORY, SIZE, START, extract_records,
                                               reference_for, validate_capture)


def encode(kind: str, payload: dict) -> str:
    text = json.dumps(payload, separators=(",", ":"))
    return f"[DESKBOT_E143B {kind} bytes={len(text.encode())}] {text}"


def fixture():
    raw = bytes(range(256)) * (SIZE // 256)
    reference = reference_for(raw, identity_sha256="1" * 64, run_label="synthetic",
                              source_sha256="2" * 64)
    def snapshot(phase, index, elapsed, hold=0):
        mapped = phase == "hold"
        return {"phase": phase, "index": index, "elapsed_ms": elapsed, "hold_elapsed_ms": hold,
                "state": 3, "audio_idle": True, "pages": 189 if phase in {"before", "after"} else 61,
                "stack_free_bytes": 4096, "internal_free": 60000, "internal_largest": 32000,
                "internal_minimum": 30000, "spiram_free": 15000000, "spiram_largest": 14000000,
                "spiram_minimum": 14000000, "head_hex": reference["head_hex"] if mapped else "",
                "tail_hex": reference["tail_hex"] if mapped else ""}
    rows = [
        ("capacity", {"flash_size": 33554432, "source_tool": "self.get_flash_capacity",
                      "partition_table": [{"label": label, **value} for label, value in UPPER_ASSETS_8M_PARTITIONS.items()]}),
        ("begin", {"schema": "deskbot.continuous_mmap.begin.v1", "start": START, "bytes": SIZE,
                   "page_bytes": 65536, "chunk_bytes": 1024, "hold_ms": 180000, "interval_ms": 10000,
                   "writes_performed": False}),
        ("snapshot", snapshot("before", 0, 0)), ("snapshot", snapshot("mapped", 0, 1))]
    rows += [("block", {"index": i, "address": START + i * BLOCK, "bytes": BLOCK,
                        "read_sha256": digest, "mmap_sha256": digest})
             for i, digest in enumerate(reference["block_sha256"])]
    rows += [("sample", snapshot("hold", i, 21000 + i * 10000, i * 10000)) for i in range(19)]
    rows += [("snapshot", snapshot("after", 0, 201001, 180000)),
             ("summary", {"schema": "deskbot.continuous_mmap.summary.v1", "error": "none",
                          "mapping_status": "ESP_OK", "mapping_acquired": True, "mapping_released": True,
                          "blocks": 8, "samples": 19, "held_ms": 180000, "pages_before": 189, "pages_after": 189,
                          "read_sha256": reference["sha256"], "mmap_sha256": reference["sha256"],
                          "memory_ok": True, "writes_performed": False, "probe_passed": True})]
    return [{"kind": kind, "payload": payload} for kind, payload in rows], reference


class ContinuousMmapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template, cls.reference = fixture()

    def setUp(self):
        self.records = copy.deepcopy(self.template)

    def checked(self):
        return validate_capture(self.records, self.reference)

    def test_synthetic_capture_passes_without_physical_claim(self):
        log = "boot noise\n" + "\n".join(encode(row["kind"], row["payload"]) for row in self.records)
        checked = validate_capture(extract_records(log), self.reference)
        self.assertTrue(checked["probe_passed"])
        self.assertEqual(len(checked["checks"]), 21)
        self.assertFalse(checked["physical_interaction_accepted"])

    def test_existing_assets_mapping_can_be_shared_without_new_pages(self):
        self.records[2]["payload"]["pages"] = 61
        self.records[-2]["payload"]["pages"] = 61
        self.records[-1]["payload"].update(pages_before=61, pages_after=61)
        checked = self.checked()
        self.assertTrue(checked["probe_passed"])
        self.assertEqual(checked["mapping_mode"], "shared_existing")

    def test_partial_page_sharing_and_growth_are_rejected(self):
        for value in (62, 190):
            self.records[3]["payload"]["pages"] = value
            self.assertFalse(self.checked()["probe_passed"])

    def test_legacy_layout_cannot_be_used_after_migration(self):
        from protocol.capacity_query import LEGACY_PARTITIONS
        self.records[0]["payload"]["partition_table"] = [
            {"label": label, **row} for label, row in LEGACY_PARTITIONS.items()
        ]
        with self.assertRaisesRegex(ValueError, "partition table"):
            self.checked()

    def test_references_require_exact_geometry_and_valid_binding(self):
        for raw, identity, label in ((b"", "1" * 64, "test"), (b"x" * SIZE, "bad", "test"),
                                     (b"x" * SIZE, "1" * 64, "../outside")):
            with self.assertRaises(ValueError):
                reference_for(raw, identity_sha256=identity, run_label=label, source_sha256="2" * 64)

    def test_missing_duplicate_reordered_and_excess_records_rejected(self):
        for records in (self.records[:-1], self.records + self.records, self.records[1:] + self.records[:1],
                        self.records[:5] + self.records[6:]):
            with self.assertRaises(ValueError):
                validate_capture(records, self.reference)
        with self.assertRaises(ValueError):
            extract_records("\n".join(encode("begin", {}) for _ in range(41)))

    def test_lengths_json_duplicates_nonfinite_unknown_and_truncation_rejected(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '[]', '{'):
            with self.assertRaises(ValueError):
                extract_records(f"[DESKBOT_E143B begin bytes={len(text)}] {text}")
        for text in ("[DESKBOT_E143B unknown bytes=2] {}", "[DESKBOT_E143B begin bytes=1] {}",
                     "[DESKBOT_E143B begin bytes=0] ", "[DESKBOT_E143B begin bytes=9999] {}", ""):
            with self.assertRaises(ValueError):
                extract_records(text)

    def test_begin_geometry_and_false_write_claims_are_strict(self):
        for key, value in (("start", START + 1), ("bytes", 65536), ("chunk_bytes", 65536),
                           ("hold_ms", 1000), ("writes_performed", 0)):
            rows = copy.deepcopy(self.records)
            rows[1]["payload"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_capture(rows, self.reference)

    def test_each_block_matches_saved_reference_not_just_other_reader(self):
        for index in range(8):
            rows = copy.deepcopy(self.records)
            rows[4 + index]["payload"].update(read_sha256="0" * 64, mmap_sha256="0" * 64)
            self.assertFalse(validate_capture(rows, self.reference)["probe_passed"])

    def test_wrong_address_index_and_size_are_not_accepted(self):
        for key, value in (("address", 0), ("index", 7), ("bytes", BLOCK - 1)):
            rows = copy.deepcopy(self.records)
            rows[4]["payload"][key] = value
            self.assertFalse(validate_capture(rows, self.reference)["probe_passed"])
        self.records[4]["payload"]["index"] = True
        with self.assertRaises(ValueError):
            self.checked()

    def test_full_digest_and_endpoint_tampering_fail(self):
        for position, key in ((-1, "read_sha256"), (-1, "mmap_sha256"), (12, "head_hex"), (30, "tail_hex")):
            rows = copy.deepcopy(self.records)
            rows[position]["payload"][key] = "0" * 64
            self.assertFalse(validate_capture(rows, self.reference)["probe_passed"])

    def test_page_reservation_leaks_and_release_are_checked(self):
        for position, key, value in ((2, "pages", 128), (3, "pages", 62), (12, "pages", 15),
                                      (-2, "pages", 188), (-1, "mapping_released", False),
                                      (-1, "mapping_status", "ESP_FAIL")):
            rows = copy.deepcopy(self.records)
            rows[position]["payload"][key] = value
            self.assertFalse(validate_capture(rows, self.reference)["probe_passed"])

    def test_memory_floors_and_stack_use_actual_snapshot(self):
        for key, value in (("internal_free", 14000), ("internal_largest", 8000),
                           ("spiram_free", 500000), ("spiram_largest", 200000), ("stack_free_bytes", 512)):
            rows = copy.deepcopy(self.records)
            rows[12]["payload"][key] = value
            if key.endswith("_free"):
                heap = key[:-5]
                rows[12]["payload"][heap + "_largest"] = value // 2
                rows[12]["payload"][heap + "_minimum"] = value // 2
            self.assertFalse(validate_capture(rows, self.reference)["probe_passed"])

    def test_invalid_memory_and_boolean_numbers_rejected(self):
        for key, value in (("internal_free", True), ("internal_largest", 70000), ("state", 11),
                           ("stack_free_bytes", 9000), ("audio_idle", 1), ("elapsed_ms", -1)):
            rows = copy.deepcopy(self.records)
            rows[12]["payload"][key] = value
            with self.assertRaises(ValueError):
                validate_capture(rows, self.reference)

    def test_timing_order_and_full_three_minute_hold_required(self):
        for position, key, value in ((13, "hold_elapsed_ms", 0), (12, "elapsed_ms", 999999),
                                      (30, "hold_elapsed_ms", 100000), (-1, "held_ms", 100000)):
            rows = copy.deepcopy(self.records)
            rows[position]["payload"][key] = value
            self.assertFalse(validate_capture(rows, self.reference)["probe_passed"])

    def test_capacity_requires_the_migrated_upper_assets_layout(self):
        for mutate in (lambda p: p.update(flash_size=16777216),
                       lambda p: p["partition_table"][0].update(size=32768),
                       lambda p: p["partition_table"].append({"label": "extra", "address": 0x1800000, "size": 65536}),
                       lambda p: p["partition_table"].__setitem__(4, {"label": "assets_prev", "address": 0x5ec000, "size": 0x5db000})):
            rows = copy.deepcopy(self.records)
            mutate(rows[0]["payload"])
            with self.assertRaises(ValueError):
                validate_capture(rows, self.reference)

    def test_unknown_fields_and_summary_boolean_substitution_rejected(self):
        for position, key, value in ((-1, "writes_performed", 0), (-1, "mapping_acquired", 1),
                                      (-1, "extra", 0), (12, "extra", 0)):
            rows = copy.deepcopy(self.records)
            rows[position]["payload"][key] = value
            with self.assertRaises(ValueError):
                validate_capture(rows, self.reference)

    def test_abort_preserves_failure(self):
        row = {"kind": "aborted", "payload": {"error": "quiet_state_timeout", "writes_performed": False}}
        self.assertFalse(validate_capture([row], self.reference)["probe_passed"])
        row["payload"]["writes_performed"] = 0
        with self.assertRaises(ValueError):
            validate_capture([row], self.reference)

    def test_audio_activity_is_observed_not_forced_by_probe(self):
        self.records[15]["payload"].update(audio_idle=False, state=6)
        result = self.checked()
        self.assertTrue(result["probe_passed"])
        self.assertTrue(result["audio_active_observed"])
        self.assertFalse(result["physical_interaction_accepted"])

    def test_cli_binds_full_saved_snapshot_and_preserves_existing_output(self):
        from protocol.storage_continuous_mmap import sha
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143b-cli-") as folder:
            work = Path(folder)
            snapshot = work / "snapshot.bin"
            with snapshot.open("wb") as stream:
                stream.truncate(32 * 1024 * 1024)
                stream.seek(START)
                stream.write(bytes(range(256)) * (SIZE // 256))
            digest = sha(snapshot.read_bytes())
            log = work / "capture.log"
            log.write_text("\n".join(encode(row["kind"], row["payload"]) for row in self.records), encoding="utf-8")
            output = work / "result.json"
            command = [sys.executable, str(ROOT / "scripts/validate-expression-e1-43b-probe.py"), str(log),
                       "--snapshot", str(snapshot), "--snapshot-sha256", digest,
                       "--identity-sha256", "1" * 64, "--run-label", "synthetic-cli", "--output", str(output)]
            run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(result["probe_passed"])
            self.assertEqual(result["reference"]["source_sha256"], digest)
            self.assertFalse(result["binding"]["log_identity_authenticated"])
            original = output.read_bytes()
            duplicate = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual(output.read_bytes(), original)

    def test_cli_rejects_wrong_snapshot_digest_without_creating_report(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143b-cli-") as folder:
            work = Path(folder)
            snapshot = work / "snapshot.bin"
            with snapshot.open("wb") as stream:
                stream.truncate(32 * 1024 * 1024)
            output = work / "result.json"
            run = subprocess.run([sys.executable, str(ROOT / "scripts/validate-expression-e1-43b-probe.py"),
                                  str(work / "unused.log"), "--snapshot", str(snapshot),
                                  "--snapshot-sha256", "0" * 64, "--identity-sha256", "1" * 64,
                                  "--run-label", "synthetic-cli", "--output", str(output)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("SHA-256 mismatch", run.stderr)
            self.assertFalse(output.exists())

    def test_default_off_exclusive_scope_and_small_buffer(self):
        config = (ROOT / "firmware/body/main/Kconfig.projbuild").read_text(encoding="utf-8")
        block = config.split("config DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT", 1)[1].split("\nconfig ", 1)[0]
        for expected in ("default n", "!DESKBOT_FLASH_PROBE_BOOT_EXPORT", "!DESKBOT_UPPER_PATTERN_BOOT_EXPORT"):
            self.assertIn(expected, block)
        source = (ROOT / "firmware/body/main/boards/esp_vocat/continuous_mmap_probe.h").read_text(encoding="utf-8")
        for forbidden in ("esp_flash_write(", "esp_flash_erase_region(", "nvs_set", "lv_", "GetDisplay(", "Schedule(", "AddTool("):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("spi_flash_mmap(kStart, kBytes"), 1)
        self.assertIn("constexpr uint32_t kChunk = 1024", source)
        self.assertIn("~Mapping() { Release(); }", source)


class ProductionControlFlowTests(unittest.TestCase):
    def test_actual_cpp_failure_paths_and_release(self):
        spec = importlib.util.spec_from_file_location("continuous_cpp_test", ROOT / "scripts/test-expression-e1-43b-cpp.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.run()
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["scenario_count"], 22)


if __name__ == "__main__":
    unittest.main()
