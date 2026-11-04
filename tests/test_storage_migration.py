from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_migration import (
    MODEL_PAYLOAD_BYTES, NEW_MODEL_END, NEW_MODEL_START, OLD_MODEL_END,
    OLD_TAIL_OVERWRITTEN_END, OLD_TABLE_SHA256, json_canonical, padded_partition,
    safe_relative_path, sha256_bytes, validate_flash_map, validate_layout_segments,
    validate_live_backup_requirements, validate_manifest, validate_member_records,
    validate_model_identity, validate_schema,
)


class StorageMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        report = ROOT / "artifacts/expression-storage-migration-e1-35b-20261029/report.json"
        cls.report = json.loads(report.read_text(encoding="utf-8"))
        cls.manifest = {key: value for key, value in cls.report.items() if key != "manifest_sha256"}
        spec = importlib.util.spec_from_file_location("e135b_builder_test", ROOT / "scripts/build-expression-e1-35b-migration.py")
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def test_public_manifest_schema_and_metadata_are_valid(self):
        validate_schema(self.manifest)
        validate_manifest(self.manifest, ROOT, require_files=False)
        raw = (json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.assertEqual(sha256_bytes(raw), self.report["manifest_sha256"])

    def test_old_tail_reaches_end_of_relocated_model(self):
        self.assertEqual(OLD_TAIL_OVERWRITTEN_END, NEW_MODEL_END)
        self.assertEqual(NEW_MODEL_END, 0xEB4000)
        live = self.manifest["live_backup_requirements"]
        tail = next(row for row in live["required_before_write"] if row["id"] == "old_tail_extension")
        self.assertEqual((tail["offset"], tail["bytes"]), (0xC90000, 2_244_608))
        ranges = sorted((row["offset"], row["offset"] + row["bytes"]) for row in live["required_before_write"])
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], NEW_MODEL_END)
        self.assertTrue(all(left[1] == right[0] for left, right in zip(ranges, ranges[1:])))

    def test_incomplete_or_missing_tail_and_live_model_are_rejected(self):
        for target in ("old_tail_extension", "current_model", "nvs"):
            changed = copy.deepcopy(self.manifest["live_backup_requirements"])
            changed["required_before_write"] = [r for r in changed["required_before_write"] if r["id"] != target]
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "incomplete"):
                validate_live_backup_requirements(changed)
        changed = copy.deepcopy(self.manifest["live_backup_requirements"])
        tail = next(r for r in changed["required_before_write"] if r["id"] == "old_tail_extension")
        tail["bytes"] = NEW_MODEL_START - OLD_MODEL_END
        with self.assertRaisesRegex(ValueError, "old_tail_extension"):
            validate_live_backup_requirements(changed)

    def test_duplicate_range_and_build_reference_claiming_live_backup_rejected(self):
        changed = copy.deepcopy(self.manifest["live_backup_requirements"])
        changed["required_before_write"].append(copy.deepcopy(changed["required_before_write"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_live_backup_requirements(changed)
        changed = copy.deepcopy(self.manifest)
        changed["rollback"]["device_complete"] = True
        with self.assertRaisesRegex(ValueError, "completeness"):
            validate_manifest(changed, ROOT, require_files=False)
        changed = copy.deepcopy(self.manifest)
        changed["live_backup_requirements"]["complete_device_backup"] = True
        with self.assertRaisesRegex(ValueError, "device backup"):
            validate_manifest(changed, ROOT, require_files=False)

    def test_wrong_partition_table_and_model_hash_are_rejected(self):
        for name in ("partition_table", "model"):
            changed = copy.deepcopy(self.manifest)
            changed["layouts"]["candidate"]["segments"][name]["payload_sha256"] = "0" * 64
            with self.subTest(segment=name), self.assertRaisesRegex(ValueError, "identity|table"):
                validate_manifest(changed, ROOT, require_files=False)

    def test_missing_old_model_and_swapped_layouts_are_rejected(self):
        changed = copy.deepcopy(self.manifest)
        del changed["layouts"]["old"]["segments"]["model"]
        with self.assertRaisesRegex(ValueError, "cover"):
            validate_manifest(changed, ROOT, require_files=False)
        changed = copy.deepcopy(self.manifest)
        changed["layouts"]["old"], changed["layouts"]["candidate"] = changed["layouts"]["candidate"], changed["layouts"]["old"]
        with self.assertRaisesRegex(ValueError, "swapped"):
            validate_manifest(changed, ROOT, require_files=False)

    def test_old_flash_map_cannot_be_used_for_candidate_layout(self):
        legacy = {"layout_id": "old-6m", "partition_table_sha256": OLD_TABLE_SHA256, "model_offset": 0xBC8000}
        validate_flash_map("old-6m", legacy)
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_flash_map("candidate-8m", legacy)
        changed = copy.deepcopy(self.manifest)
        changed["flash_maps"]["legacy_map_allowed_for_candidate"] = True
        with self.assertRaisesRegex(ValueError, "legacy"):
            validate_manifest(changed, ROOT, require_files=False)

    def test_authorization_and_hardware_claims_are_rejected(self):
        for field in ("deployment_eligible", "flash_authorized"):
            changed = copy.deepcopy(self.manifest)
            changed[field] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "non-deployable"):
                validate_manifest(changed, ROOT, require_files=False)
            with self.assertRaisesRegex(ValueError, "schema"):
                validate_schema(changed)
        changed = copy.deepcopy(self.manifest)
        changed["safety"]["serial_accessed"] = True
        with self.assertRaisesRegex(ValueError, "offline"):
            validate_manifest(changed, ROOT, require_files=False)

    def test_write_plan_rejects_app_nvs_and_unsafe_commit_order(self):
        for name in ("app", "nvs", "otadata", "bootloader"):
            changed = copy.deepcopy(self.manifest)
            changed["write_plan"].append({"id": name, "segment": name, "offset": 0, "bytes": 4096, "sha256": "0" * 64})
            with self.subTest(segment=name), self.assertRaisesRegex(ValueError, "write plan"):
                validate_manifest(changed, ROOT, require_files=False)
        changed = copy.deepcopy(self.manifest)
        changed["write_order"] = list(reversed(changed["write_order"]))
        with self.assertRaisesRegex(ValueError, "write order"):
            validate_manifest(changed, ROOT, require_files=False)

    def test_incomplete_protected_set_and_fake_model_origin_rejected(self):
        changed = copy.deepcopy(self.manifest)
        changed["untouched_partitions"].pop()
        with self.assertRaisesRegex(ValueError, "protected"):
            validate_manifest(changed, ROOT, require_files=False)
        changed = copy.deepcopy(self.manifest)
        changed["layouts"]["old"]["segments"]["model"]["source_kind"] = "device_readback"
        with self.assertRaisesRegex(ValueError, "provenance"):
            validate_manifest(changed, ROOT, require_files=False)

    def test_member_payload_and_inventory_tampering_rejected(self):
        actual = {"a.eaf": b"a", "index.json": b"{}"}
        rows = [{"name": name, "bytes": len(raw), "sha256": sha256_bytes(raw)} for name, raw in actual.items()]
        validate_member_records(rows, actual)
        for payloads in ({"a.eaf": b"b", "index.json": b"{}"}, {"a.eaf": b"a"}):
            with self.assertRaisesRegex(ValueError, "member"):
                validate_member_records(rows, payloads)
        changed = copy.deepcopy(self.manifest)
        changed["source_inputs"]["asset_members"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "member manifest"):
            validate_manifest(changed, ROOT, require_files=False)

    def test_traversal_absolute_windows_paths_and_streams_rejected(self):
        for relative in ("../escape", "tmp/../escape", "/escape", "C:/escape", "\\\\server\\share", "tmp/a.bin:stream", "tmp//a.bin"):
            with self.subTest(path=relative), self.assertRaises(ValueError):
                safe_relative_path(ROOT, relative)
        self.assertEqual(safe_relative_path(ROOT, "tmp/test.bin"), (ROOT / "tmp/test.bin").resolve())

    def test_schema_does_not_accept_missing_or_extra_fields(self):
        for field in ("source_inputs", "write_plan", "rollback", "flash_maps"):
            changed = copy.deepcopy(self.manifest)
            del changed[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "schema"):
                validate_schema(changed)
        changed = copy.deepcopy(self.manifest)
        changed["deploy_now"] = True
        with self.assertRaisesRegex(ValueError, "schema"):
            validate_schema(changed)

    def test_model_bytes_must_match_accepted_source_not_only_length(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_model_identity(bytes(MODEL_PAYLOAD_BYTES))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_model_identity(bytes(MODEL_PAYLOAD_BYTES - 1))

    def test_partition_padding_and_file_corruption_are_checked(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e135b-negative-") as temporary:
            base = Path(temporary)
            layout = copy.deepcopy(self.manifest["layouts"]["old"])
            boot = layout["segments"]["bootloader"]
            payload = b"boot"
            raw = padded_partition(payload, 0x8000)
            path = base / "boot.bin"
            boot.update(path="boot.bin", payload_bytes=len(payload), payload_sha256=sha256_bytes(payload), sha256=sha256_bytes(raw))
            for broken in (raw[:-1], b"X" + raw[1:]):
                path.write_bytes(broken)
                with self.assertRaisesRegex(ValueError, "missing or changed"):
                    validate_layout_segments(layout, base)
            broken = raw[:-1] + b"\x00"
            path.write_bytes(broken)
            boot["sha256"] = sha256_bytes(broken)
            with self.assertRaisesRegex(ValueError, "padding"):
                validate_layout_segments(layout, base)
            path.write_bytes(raw)
            boot["sha256"] = sha256_bytes(raw)
            boot["payload_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "payload"):
                validate_layout_segments(layout, base)

    def test_check_mode_never_creates_missing_images_or_reports(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e135b-check-") as temporary:
            folder = Path(temporary) / "absent"
            with patch.object(self.builder, "WORK", folder):
                with self.assertRaisesRegex(ValueError, "missing staged"):
                    self.builder.stage_image("old/x.bin", b"x", 4096, check=True)
            with self.assertRaisesRegex(ValueError, "stale or missing"):
                self.builder.write_or_check(folder / "report.json", b"{}", True)
            self.assertFalse(folder.exists())

    def test_existing_image_cannot_be_silently_replaced(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e135b-immutable-") as temporary:
            path = Path(temporary) / "image.bin"
            self.builder.immutable_write(path, b"first")
            self.builder.immutable_write(path, b"first")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                self.builder.immutable_write(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")


if __name__ == "__main__":
    unittest.main()
