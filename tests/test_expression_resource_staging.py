from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deskbot_e145_tests", ROOT / "scripts/prepare-expression-e1-45-staging.py")
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)


class ResourceStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((STAGE.PUBLIC / "report.json").read_bytes())

    def members(self):
        index = {"version": 1, "srmodels": "model.bin", "layout": [{"name": "eye_anim", "mirror": "auto"}],
                 "emoji_collection": [{"name": "embarrassed", "file": "Happy.eaf", "eaf": {"loop": True, "fps": 20}},
                     {"name": "happy", "file": "Happy.eaf", "eaf": {"loop": True, "fps": 20}},
                     {"name": "surprised", "file": "shocked.eaf", "eaf": {"loop": True, "fps": 20}}]}
        before = {"Happy.eaf": b"Happy", "shocked.eaf": b"shocked", "index.json": json.dumps(index).encode()}
        index["emoji_collection"][0]["file"] = STAGE.MEMBER
        index["emoji_collection"][0]["eaf"]["fps"] = 25
        after = {**before, STAGE.MEMBER: b"selected", "index.json": json.dumps(index).encode()}
        return before, after

    def test_exact_single_semantic_delta_keeps_shared_resources_and_layout(self):
        before, after = self.members()
        result = STAGE.audit_member_delta(before, after, b"selected")
        self.assertEqual(result["changed"], ["index.json"])
        self.assertEqual(result["other_happy_references"], ["happy"])
        self.assertEqual(result["surprised_resource"], "shocked.eaf")

    def test_other_member_changes_and_extra_or_missing_members_are_rejected(self):
        before, after = self.members()
        for bad in ({**after, "Happy.eaf": b"changed"}, {**after, "extra.bin": b"x"},
                    {key: value for key, value in after.items() if key != "shocked.eaf"},
                    {**after, STAGE.MEMBER: b"different"}):
            with self.subTest(keys=sorted(bad)), self.assertRaises(ValueError):
                STAGE.audit_member_delta(before, bad, b"selected")

    def test_index_changes_to_other_semantic_layout_or_model_are_rejected(self):
        before, after = self.members()
        index = json.loads(after["index.json"])
        cases = []
        changed = copy.deepcopy(index)
        changed["emoji_collection"][1]["file"] = STAGE.MEMBER
        cases.append(changed)
        changed = copy.deepcopy(index)
        changed["layout"][0]["mirror"] = "false"
        cases.append(changed)
        cases.append({**index, "srmodels": "different.bin"})
        for value in cases:
            with self.subTest(index=value), self.assertRaises(ValueError):
                STAGE.audit_member_delta(before, {**after, "index.json": json.dumps(value).encode()}, b"selected")

    def test_wrong_target_timing_or_duplicate_target_is_rejected(self):
        before, after = self.members()
        index = json.loads(after["index.json"])
        index["emoji_collection"][0]["eaf"]["fps"] = 20
        with self.assertRaises(ValueError):
            STAGE.audit_member_delta(before, {**after, "index.json": json.dumps(index).encode()}, b"selected")
        index = json.loads(before["index.json"])
        index["emoji_collection"].append(index["emoji_collection"][0])
        with self.assertRaises(ValueError):
            STAGE.audit_member_delta({**before, "index.json": json.dumps(index).encode()}, after, b"selected")

    def test_padding_and_extraction_keep_package_and_erased_remainder(self):
        package = struct.pack("<III", 1, 0, 4) + b"data"
        image = STAGE.pad_package(package)
        self.assertEqual(len(image), 0x800000)
        self.assertEqual(image[:16], package)
        self.assertEqual(image[16:], b"\xff" * (len(image) - 16))
        self.assertEqual(STAGE.extract_package(image), package)

    def test_oversized_packages_invalid_image_length_and_non_ff_padding_are_rejected(self):
        with self.assertRaises(ValueError):
            STAGE.pad_package(b"x" * (STAGE.SOFT_LIMIT + 1))
        for bad in (b"", b"\xff" * (STAGE.SIZE - 1), b"\xff" * STAGE.SIZE):
            with self.assertRaises(ValueError):
                STAGE.extract_package(bad)
        package = struct.pack("<III", 1, 0, 4) + b"data"
        image = STAGE.pad_package(package)
        with self.assertRaises(ValueError):
            STAGE.extract_package(image[:-1] + b"\0")

    def test_full_and_interrupted_resource_restore_preserve_entire_synthetic_flash(self):
        rollback = b"r" * STAGE.SIZE
        snapshot = b"p" * STAGE.START + rollback + b"t" * (STAGE.FLASH_SIZE - STAGE.START - STAGE.SIZE)
        result = STAGE.simulate_restore(snapshot, b"c" * STAGE.SIZE, rollback)
        self.assertEqual(result["snapshot_sha256"], STAGE.sha(snapshot))
        self.assertEqual([row["written_bytes"] for row in result["scenarios"]], [STAGE.SIZE, STAGE.SIZE // 2])
        self.assertTrue(all(row["full_32MiB_restore_byte_exact"] for row in result["scenarios"]))
        self.assertFalse(result["device_io"])
        self.assertFalse(result["physical_power_loss_tested"])

    def test_restore_rejects_wrong_snapshot_and_recovery_bytes(self):
        candidate, rollback = b"c" * STAGE.SIZE, b"r" * STAGE.SIZE
        snapshot = b"\xff" * STAGE.FLASH_SIZE
        for full, new, old in ((snapshot[:-1], candidate, rollback),
                               (snapshot, candidate, rollback), (snapshot, candidate[:-1], rollback)):
            with self.assertRaises(ValueError):
                STAGE.simulate_restore(full, new, old)

    def test_report_has_selected_eye_only_and_reference_recovery(self):
        STAGE.validate_report(self.report)
        self.assertEqual(self.report["selection"]["selected_id"], "eye")
        self.assertEqual(self.report["rollback_reference"]["partition_sha256"], STAGE.ROLLBACK_SHA)
        self.assertFalse(self.report["ready_for_device_write"])
        self.assertEqual(self.report["partition"]["offset"], 0x1000000)

    def test_report_rejects_wrong_partition_capacity_choice_and_extra_scope(self):
        for mutate in (lambda d: d["partition"].__setitem__("offset", 0x5EC000),
                       lambda d: d["budget"].__setitem__("partition_bytes", 6144000),
                       lambda d: d["selection"].__setitem__("selected_id", "full"),
                       lambda d: d.__setitem__("ready_for_device_write", True),
                       lambda d: d["member_delta"]["changed"].append("Happy.eaf"),
                       lambda d: d["simulation"]["scenarios"][0].__setitem__("restored_flash_sha256", "0" * 64)):
            bad = copy.deepcopy(self.report)
            mutate(bad)
            with self.assertRaises(ValueError):
                STAGE.validate_report(bad)

    def test_private_images_reproduce_report_when_retained(self):
        if not STAGE.OUTPUT.exists():
            self.skipTest("local staged images are not distributed")
        STAGE.check_saved(self.report)


if __name__ == "__main__":
    unittest.main()
