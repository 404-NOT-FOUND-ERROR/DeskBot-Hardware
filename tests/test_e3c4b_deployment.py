"""Review the display candidate and prepare fresh App-only transactions offline."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_e3c4b_deployment as reviewed

ROOT = reviewed.ROOT
spec = importlib.util.spec_from_file_location("e3c4b_deployment_test", ROOT / "scripts/deploy-e3c4b-display.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DisplayDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = reviewed.load_reference()
        cls.evidence = reviewed.check_build_evidence()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e3c4b-gate-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def offline(self):
        folder = self.folder / "offline"
        with patch.object(deploy.base, "MenuTestDevice") as device:
            result = deploy.base.prepare_offline(folder)
        device.assert_not_called()
        self.assertFalse(result["hardware_accessed"])
        return folder

    def test_reference_is_accepted_e3c3b_with_exact_app(self):
        self.assertEqual(len(self.reference["snapshot"]), tx.FLASH)
        self.assertEqual(self.reference["original_bytes"], reviewed.ORIGINAL_SIZE)
        self.assertEqual(tx.sha(self.reference["app_union"][:reviewed.ORIGINAL_SIZE]), reviewed.ORIGINAL_SHA)

    def test_current_reference_allows_only_runtime_nvs_changes(self):
        changed = bytearray(self.reference["snapshot"])
        changed[0x9000] ^= 1
        reviewed.check_current(bytes(changed), self.reference)
        for offset in (0, 0x8000, 0xD000, tx.START, 0xBC8000, 0x1000000):
            changed = bytearray(self.reference["snapshot"])
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                reviewed.check_current(bytes(changed), self.reference)

    def test_candidate_change_is_rejected(self):
        changed = bytearray(self.evidence["image"])
        changed[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "App changed"):
            reviewed.reviewed_candidate(bytes(changed))

    def test_offline_plan_keeps_e3c3b_rollback_and_app_only_geometry(self):
        folder = self.offline()
        plan = reviewed.check_plan(tx.load(folder), reference=self.reference)
        self.assertEqual(plan.metadata["offset"], 0x10000)
        self.assertEqual(plan.metadata["original_bytes"], reviewed.ORIGINAL_SIZE)
        self.assertEqual(tx.sha(plan.rollback[:reviewed.ORIGINAL_SIZE]), reviewed.ORIGINAL_SHA)
        self.assertEqual(plan.metadata["erase_bytes"], (max(reviewed.ORIGINAL_SIZE, reviewed.APP_SIZE)+4095)//4096*4096)

    def test_repeated_prepare_preserves_existing_files(self):
        folder = self.offline()
        original = (folder / "plan.json").read_bytes()
        with self.assertRaises(FileExistsError):
            deploy.base.prepare_offline(folder)
        self.assertEqual((folder / "plan.json").read_bytes(), original)

    def test_offline_plan_cannot_install_without_fresh_device_check(self):
        folder = self.offline()
        with patch.object(deploy.base.entry, "run") as run:
            with self.assertRaisesRegex(ValueError, "live preflight required"):
                deploy.base.run("install", folder)
        run.assert_not_called()

    def test_live_preparation_refreshes_nvs_and_preserves_candidate(self):
        source = self.offline()
        current = bytearray(self.reference["snapshot"])
        current[0x9000] ^= 1
        target = self.folder / "live"
        with patch.object(deploy.base, "MenuTestDevice") as factory:
            device = factory.return_value
            device.read_reference.return_value = bytes(current)
            result = deploy.base.prepare_live(target, source)
        self.assertEqual(result["phase"], "prepared_live")
        self.assertEqual(tx.load(target).snapshot, bytes(current))
        device.write_app.assert_not_called()
        saved = json.loads((target / "preflight.json").read_bytes())
        self.assertTrue(saved["hardware_accessed"])
        self.assertFalse(saved["device_written"])
        self.assertEqual(saved["rollback_target"], reviewed.ROLLBACK_TARGET)

    def test_changed_static_flash_stops_live_preparation(self):
        source = self.offline()
        changed = bytearray(self.reference["snapshot"])
        changed[0x1000000] ^= 1
        target = self.folder / "live"
        with patch.object(deploy.base, "MenuTestDevice") as factory:
            device = factory.return_value
            device.read_reference.return_value = bytes(changed)
            with self.assertRaisesRegex(ValueError, "current Flash differs"):
                deploy.base.prepare_live(target, source)
        device.boot.assert_called_once_with(False)
        self.assertFalse((target / "plan.json").exists())

    def test_recovery_needs_no_candidate_or_build_files(self):
        folder = self.offline()
        (folder / "candidate-app.bin").unlink()
        with patch.object(reviewed, "check_build_evidence", side_effect=AssertionError("build accessed")), \
             patch.object(reviewed, "load_reference", side_effect=AssertionError("reference accessed")):
            plan = deploy.base.validate_saved(folder, "recover")
        self.assertIsInstance(plan, tx.RecoveryPlan)
        self.assertEqual(tx.sha(plan.rollback[:reviewed.ORIGINAL_SIZE]), reviewed.ORIGINAL_SHA)

    def test_recovery_rejects_changed_original_backup(self):
        folder = self.offline()
        path = folder / "snapshot-32m.bin"
        original = bytearray(path.read_bytes())
        original[tx.START] ^= 1
        path.write_bytes(original)
        with self.assertRaises(ValueError):
            deploy.base.validate_saved(folder, "recover")

    def test_run_labels_stay_inside_project_tmp(self):
        label = "e3c-live-20261107-e3c4b-offline"
        self.assertEqual(deploy.folder_for(label).parent, ROOT / "tmp")
        for value in ("../outside", "e3c-live-20261107-e3c4b-../outside", "e3c-live-20261107-e3c3a-offline"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                deploy.folder_for(value)


if __name__ == "__main__":
    unittest.main()
