"""Current-baseline source assembly and off/on comparison boundaries."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stable_ladder_tests", ROOT / "scripts/build-studio-stable-ladder.py")
ladder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ladder)


class CandidateLadderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        report = json.loads(ladder.BASE.REFERENCE_REPORT.read_bytes())
        cls.baseline = {"source_sha256": report["source_sha256"]}

    def test_each_feature_retains_accepted_display_fixes_and_usb_schedule(self):
        for stage in ladder.STAGES:
            with self.subTest(stage=stage):
                source, _, _ = ladder.sources(stage, self.baseline)
                for name in (ladder.BASE.e3c3b_display_dma.BOARD,):
                    self.assertEqual(source[name], (ladder.BASE.FROZEN / name).read_bytes())
                usb = source[ladder.USB]
                self.assertEqual(usb.count(b"read(fd, input, sizeof(input))"), 1)
                self.assertEqual(usb.count(b"vTaskDelay(pdMS_TO_TICKS(10))"), 1)
                self.assertIn(b'"studio_session", 6144, nullptr, 1, nullptr', usb)
                self.assertNotIn(b"read_count", usb)
                self.assertIn(b'"/dev/secondary"', usb)

    def test_layer_changes_only_declared_inputs(self):
        previous = self.baseline["source_sha256"]
        for stage in ladder.STAGES:
            _, current, _ = ladder.sources(stage, self.baseline)
            changed = {name for name in previous.keys() | current.keys() if previous.get(name) != current.get(name)}
            self.assertEqual(changed, ladder.DELTAS[stage])
            previous = current

    def test_config_absent_new_boolean_means_off(self):
        for level in range(3):
            before = {flag: True for flag in ladder.FLAGS[:level]}
            off = {**before, ladder.FLAGS[level]: False}
            self.assertEqual(ladder.config_delta(before, off, level, False), [])
            self.assertEqual(ladder.config_delta(before, {**off, ladder.FLAGS[level]: True}, level, True), [ladder.FLAGS[level]])

    def test_config_rejects_unrelated_or_future_feature(self):
        for wrong in ({"SPIRAM": False}, {ladder.FLAGS[1]: True}):
            with self.assertRaises(ValueError):
                ladder.config_delta({}, wrong, 0, False)

    def test_report_rejects_missing_rows_metadata_or_redirected_paths(self):
        stage, reference = "display", ladder.BASE.REFERENCE_BUILD
        baseline = {"report_sha256": "c"*64}
        saved = dict(schema="deskbot.stable_candidate_ladder/v1", stage=stage, baseline="E3c-3b",
            result="off_on_builds_verified", source_delta=sorted(ladder.DELTAS[stage]), target=ladder.load_target(),
            reference_app=ladder.H.file_record(reference / "xiaozhi.bin"), stable_report_sha256="c"*64,
            usb=dict(reads_per_iteration=1, yield_ms=10, stack_bytes=6144, priority=1), new_tasks=0,
            catalog_snapshot_max_psram_bytes=0, device_accessed=False, device_written=False, hardware_acceptance="pending",
            source_sha256={}, donors={}, components={}, input_inventory={}, disabled_comparison={},
            builds=[dict(enabled=enabled, build_directory=(ladder.WORK/stage/label).relative_to(ROOT).as_posix())
                    for label, enabled in (("off", False), ("on", True))],
            steps=[dict(configuration=label, action=action, sha256="a"*64,
                        log=(ladder.WORK/stage/(label+'-'+action+'.log')).relative_to(ROOT).as_posix())
                   for label in ("off", "on") for action in ("reconfigure", "build")])
        ladder.validate_report_structure(saved, stage, reference, baseline)
        import copy
        for key, wrong in (("builds", []), ("steps", []), ("target", {}), ("result", "passed"),
                           ("stage", "catalog"), ("baseline", "E3c-3a"), ("new_tasks", 1)):
            value = copy.deepcopy(saved)
            value[key] = wrong
            with self.subTest(key=key), self.assertRaises(ValueError):
                ladder.validate_report_structure(value, stage, reference, baseline)
        for key, field in (("builds", "build_directory"), ("steps", "log")):
            value = copy.deepcopy(saved)
            value[key][0][field] = "tmp/unrelated"
            with self.assertRaises(ValueError):
                ladder.validate_report_structure(value, stage, reference, baseline)

    def test_reparse_output_is_rejected_before_inspection(self):
        with patch.object(ladder.H, "_is_reparse_point", side_effect=lambda path: Path(path).name == "off"):
            with self.assertRaisesRegex(ValueError, "reparse"):
                ladder.inspect(ladder.WORK/"display/off", ladder.BASE.REFERENCE_BUILD, 0, False, {})

    def test_app_comparison_rejects_code_description_or_layout_changes(self):
        original = (ladder.BASE.REFERENCE_BUILD / "xiaozhi.bin").read_bytes()
        facts = ladder.release.image_facts(original)
        first = facts["segments"][0]["file_offset"]
        self.assertTrue(ladder.compare_disabled(original, original)["byte_identical"])
        # Patch an ELF identity and recompute image checksum/hash via esptool.
        import hashlib
        changed = bytearray(original)
        changed[first + 144] ^= 1
        checksum = 0xef
        for segment in facts["segments"]:
            for value in changed[segment["file_offset"]:segment["file_offset"] + segment["bytes"]]:
                checksum ^= value
        changed[facts["checksum_offset"]] = checksum
        changed[-32:] = hashlib.sha256(changed[:-32]).digest()
        self.assertTrue(ladder.compare_disabled(original, bytes(changed))["executable_and_data_equal"])
        for index in (first + 24, first + 200, facts["segments"][1]["file_offset"] + 100):
            mutated = bytearray(original)
            mutated[index] ^= 1
            with self.assertRaises(ValueError):
                ladder.compare_disabled(original, bytes(mutated))


if __name__ == "__main__":
    unittest.main()
