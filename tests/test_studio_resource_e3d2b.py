"""Current-version resource plans and boot evidence; all inputs are saved files."""
import copy
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from protocol import studio_resource_installation as r
from protocol.studio_e3d2b_deployment import APP_ELF_SHA
from protocol.studio_resource_e3d2b_esptool import inspect_boot
from protocol.storage_upper_experiment_esptool import _NORMAL_BOOT_MARKERS
from protocol.storage_upper_migration_esptool import ASSET_ERRORS
import test_studio_resource_installation as legacy

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "tmp/resource-read-20261108-live1/snapshot-32m.bin"
BOOT = ROOT / "tmp/resource-read-20261108-live1/captures/080-catalog-restored.log"
IDENTITY_CAPTURE = ROOT / "tmp/resource-read-20261108-live1/logs/001-flash-id.log"
FAKE_MAC = "02:00:00:43:c2:01"


def saved_mac():
    # Hardware identifiers stay in the local evidence rather than the source.
    values = set(re.findall(r"\bMAC:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b",
                            IDENTITY_CAPTURE.read_text(encoding="utf-8"), re.IGNORECASE))
    if len(values) != 1:
        raise ValueError("saved boot identity is ambiguous")
    return values.pop()


def periodic_boot(mac=FAKE_MAC, *, change=None, count=4):
    # Use the device-origin schema from saved evidence. The production boot
    # validator is exercised separately on the unmodified real capture below.
    line = next(line for line in BOOT.read_text(encoding="utf-8").splitlines()
                if "DeskBotLink: " in line)
    template = json.loads(line.split("DeskBotLink: ", 1)[1])
    template["device_id"] = "device-" + hashlib.sha256(
        b"deskbot-link-v1\0" + bytes.fromhex(mac.replace(":", ""))).digest()[:16].hex()
    frames = []
    for index in range(count):
        hello = copy.deepcopy(template)
        hello.update(sequence=index, uptime_ms=1105 + index * 10000)
        if change:
            change(index, hello)
        frames.append("I (%d) DeskBotLink: %s" %
                      (hello["uptime_ms"], json.dumps(hello, separators=(",", ":"))))
    return "\n".join((*_NORMAL_BOOT_MARKERS, "ELF file SHA256: " + APP_ELF_SHA, *frames)) + "\n"


class E3d2bTransactionTests(legacy.ResourceTests):
    """Run the same fault/recovery contract against the actual E3d-2b snapshot."""
    @classmethod
    def setUpClass(cls):
        cls.snapshot = SNAPSHOT.read_bytes()
        cls.image = (ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin").read_bytes()
        cls.plan = r.prepare(cls.snapshot, cls.image, "a" * 64, baseline="E3d-2b")

    def test_current_schema_and_unchanged_legacy_schema(self):
        self.assertEqual(self.plan.metadata["schema"], r.E3D2B_SCHEMA)
        self.assertEqual(self.plan.metadata["baseline"], "E3d-2b")
        old_snapshot = (ROOT / "tmp/e143b-e3c3b-20261107-live1/snapshot-32m.bin").read_bytes()
        original = r.prepare(old_snapshot, self.image, "a" * 64)
        self.assertEqual(original.metadata["schema"], r.SCHEMA)
        self.assertNotIn("baseline", original.metadata)
        self.assertEqual(r.plan_baseline(original), "E3c-3b")
        self.assertNotEqual(original.id, self.plan.id)

    def test_baseline_and_snapshot_must_match_both_directions(self):
        old_snapshot = (ROOT / "tmp/e143b-e3c3b-20261107-live1/snapshot-32m.bin").read_bytes()
        for snapshot, baseline in ((old_snapshot, "E3d-2b"), (self.snapshot, "E3c-3b")):
            with self.subTest(baseline=baseline), self.assertRaisesRegex(ValueError, "app_required"):
                r.prepare(snapshot, self.image, "a" * 64, baseline=baseline)

    def test_metadata_cannot_change_baseline_or_schema(self):
        for field, value in (("baseline", "E3c-3b"), ("baseline", None),
                             ("schema", r.SCHEMA), ("schema", "unknown")):
            metadata = {**self.plan.metadata, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                r.RecoveryPlan(metadata, self.snapshot).validate()
        missing = {key: value for key, value in self.plan.metadata.items() if key != "baseline"}
        with self.assertRaises(ValueError):
            r.RecoveryPlan(missing, self.snapshot).validate()

    def test_changing_schema_and_recomputing_all_digests_still_requires_right_app(self):
        metadata = {key: value for key, value in self.plan.metadata.items() if key != "baseline"}
        metadata["schema"] = r.SCHEMA
        disguised = r.RecoveryPlan(metadata, self.snapshot)
        # The ID is recomputed from metadata, so failure must be the App identity.
        self.assertNotEqual(disguised.id, self.plan.id)
        with self.assertRaisesRegex(ValueError, "app_required"):
            disguised.validate()

    def test_unknown_baseline_and_altered_app_are_rejected(self):
        for baseline in (None, [], "E3d-2", True):
            with self.subTest(baseline=baseline), self.assertRaisesRegex(ValueError, "unsupported_resource_baseline"):
                r.prepare(self.snapshot, self.image, "a" * 64, baseline=baseline)
        changed = bytearray(self.snapshot)
        changed[0x10010] ^= 1
        with self.assertRaisesRegex(ValueError, "app_required"):
            r.prepare(bytes(changed), self.image, "a" * 64, baseline="E3d-2b")

    def test_initial_intent_save_failure_boots_without_writing_for_both_baselines(self):
        old_snapshot = (ROOT / "tmp/e143b-e3c3b-20261107-live1/snapshot-32m.bin").read_bytes()
        old = r.prepare(old_snapshot, self.image, "a" * 64)
        for plan in (old, self.plan):
            folder = self.folder / r.plan_baseline(plan)
            folder.mkdir()
            device = legacy.FlashModel(plan)
            journal = r.Journal(folder, plan)
            with self.subTest(baseline=r.plan_baseline(plan)):
                with patch.object(journal, "save", side_effect=OSError("disk_full")) as save:
                    with self.assertRaisesRegex(OSError, "disk_full"):
                        r.install(plan, device, journal)
                    save.assert_called_once_with("write_intent")
                self.assertEqual((device.writes, device.boots), ([], 1))
                self.assertEqual(bytes(device.raw), plan.snapshot)
                self.assertIsNone(journal.load())

    def test_initial_intent_failure_reports_unconfirmed_boot_for_both_baselines(self):
        old_snapshot = (ROOT / "tmp/e143b-e3c3b-20261107-live1/snapshot-32m.bin").read_bytes()
        old = r.prepare(old_snapshot, self.image, "a" * 64)
        for plan in (old, self.plan):
            folder = self.folder / (r.plan_baseline(plan) + "-boot-failed")
            folder.mkdir()
            device = legacy.FlashModel(plan)
            device.fail_first_boot = True
            journal = r.Journal(folder, plan)
            with self.subTest(baseline=r.plan_baseline(plan)):
                with patch.object(journal, "save", side_effect=OSError("disk_full")):
                    with self.assertRaises(r.ResourcePreparationRecoveryError) as caught:
                        r.install(plan, device, journal)
                error = caught.exception
                self.assertEqual(error.stage, "write_intent_save_failed")
                self.assertIsInstance(error.preparation_error, OSError)
                self.assertEqual(error.boot_result, False)
                self.assertIsNone(error.boot_error)
                self.assertEqual((device.writes, device.boots), ([], 1))
                self.assertEqual(bytes(device.raw), plan.snapshot)
                self.assertIsNone(journal.load())

    def test_restore_intent_save_failure_retains_state_for_retry(self):
        r.install(self.plan, self.device, self.journal)
        prior = self.journal.load()
        raw = bytes(self.device.raw)
        with patch.object(self.journal, "save", side_effect=OSError("disk_full")):
            with self.assertRaisesRegex(OSError, "disk_full"):
                r.recover(self.plan, self.device, self.journal)
        self.assertEqual(self.journal.load(), prior)
        self.assertEqual(bytes(self.device.raw), raw)
        self.assertEqual((len(self.device.writes), self.device.boots), (1, 1))
        r.recover(self.plan, self.device, self.journal)
        self.assertEqual(bytes(self.device.raw), self.snapshot)
        self.assertEqual(self.journal.load()["phase"], "restored")


class E3d2bBootTests(unittest.TestCase):
    def test_saved_device_capture_and_periodic_frames_pass(self):
        self.assertTrue(inspect_boot(BOOT.read_text(encoding="utf-8"), saved_mac()))
        self.assertTrue(inspect_boot(periodic_boot(), FAKE_MAC))

    def test_ansi_and_host_timestamps_are_supported(self):
        log = "\n".join("2026-11-08T18:00:00+08:00\t\x1b[0;32m" + line + "\x1b[0m"
                        for line in periodic_boot().splitlines())
        self.assertTrue(inspect_boot(log, FAKE_MAC))

    def test_wrong_firmware_device_or_capacity_is_rejected(self):
        changes = (
            lambda _, h: h["firmware"].update(app_elf_sha256="a" * 64),
            lambda _, h: h["firmware"].update(version="2.0.4"),
            lambda _, h: h.update(device_id="device-" + "a" * 32),
            lambda _, h: h["hardware"].update(flash_bytes=0x1000000),
            lambda _, h: h["hardware"].update(psram_bytes=0x800000),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                self.assertFalse(inspect_boot(periodic_boot(change=change), FAKE_MAC))
        self.assertFalse(inspect_boot(periodic_boot(), saved_mac()))

    def test_reboot_gaps_duplicate_sequence_and_bad_intervals_are_rejected(self):
        def second_boot(i, h):
            if i == 2:
                h["boot_id"] = "boot-" + "b" * 32
        changes = (
            second_boot,
            lambda i, h: h.update(sequence=9) if i == 2 else None,
            lambda i, h: h.update(sequence=1) if i == 2 else None,
            lambda i, h: h.update(uptime_ms=11105) if i == 2 else None,
            lambda i, h: h.update(uptime_ms=25105) if i == 2 else None,
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                self.assertFalse(inspect_boot(periodic_boot(change=change), FAKE_MAC))

    def test_missing_malformed_or_duplicate_hello_fields_are_rejected(self):
        self.assertFalse(inspect_boot(periodic_boot(count=2), FAKE_MAC))
        self.assertFalse(inspect_boot(periodic_boot(change=lambda _, h: h.pop("limits")), FAKE_MAC))
        log = periodic_boot()
        for changed in (log.replace('"sequence":0', '"sequence":0,"sequence":0', 1),
                        re.sub(r'DeskBotLink: \{.*\}', 'DeskBotLink: {broken}', log, count=1)):
            self.assertFalse(inspect_boot(changed, FAKE_MAC))

    def test_panic_asset_errors_missing_boot_and_repeated_boot_are_rejected(self):
        log = periodic_boot()
        for changed in (log + "\nGuru Meditation", log + "\nassert failed",
                        log + "\nCalling app_main()", log.replace("LCD panel create success", ""),
                        *(log + "\n" + error for error in ASSET_ERRORS)):
            with self.subTest(log=changed[-30:]):
                self.assertFalse(inspect_boot(changed, FAKE_MAC))


if __name__ == "__main__":
    unittest.main()
