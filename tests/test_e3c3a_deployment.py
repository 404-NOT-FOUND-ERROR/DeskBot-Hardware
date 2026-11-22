"""Offline validation and live-preflight gates for the E3c-3a menu test."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_e3c3a_deployment as reviewed

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "e3c3a_deploy_test", ROOT / "scripts/deploy-deskbot-studio-e3c3a.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class E3c3aDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = reviewed.load_reference()
        cls.evidence = reviewed.check_build_evidence()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e3c3a-live-gate-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.offline = self.root / "offline"
        deploy.prepare_offline(self.offline)

    def make_live_plan(self, live):
        with patch.object(deploy, "MenuTestDevice") as factory:
            factory.return_value.identity.return_value = self.reference["identity_sha256"]
            factory.return_value.read_reference.return_value = self.reference["snapshot"]
            return deploy.prepare_live(live, self.offline)

    def test_query_off_candidate_is_pinned_to_the_audited_build(self):
        image = reviewed.reviewed_candidate(self.evidence["image"])
        self.assertEqual(len(image), 4488224)
        self.assertEqual(tx.sha(image), "a67aaec69b359039db52e1987ed6d9f2dc726243f2871d2b9718b6095c0d8375")
        self.assertEqual(self.evidence["candidate"]["app_elf_sha256"], reviewed.APP_ELF_SHA)

    def test_offline_plan_keeps_e3c2_and_limits_write_to_app(self):
        plan = tx.load(self.offline)
        self.assertEqual(plan.metadata["edition"], "E3c")
        self.assertEqual(plan.rollback, self.reference["app_union"])
        self.assertEqual(plan.metadata["offset"], 0x10000)
        self.assertEqual(plan.metadata["erase_bytes"], 4489216)
        self.assertEqual(plan.metadata["candidate_bytes"], 4488224)
        self.assertEqual(plan.metadata["candidate_sha256"], reviewed.APP_SHA)

    def test_offline_prepare_persists_recovery_before_device_access(self):
        with patch.object(deploy, "MenuTestDevice") as factory:
            folder = self.root / "another-plan"
            result = deploy.prepare_offline(folder)
            factory.assert_not_called()
        self.assertEqual(result["phase"], "prepared_offline")
        self.assertEqual(tx.load(folder).id, result["plan_id"])
        receipt = json.loads((folder / "preflight.json").read_bytes())
        self.assertEqual(receipt["result"], "offline_plan_ready")
        self.assertFalse(receipt["hardware_accessed"])
        self.assertFalse(receipt["device_written"])
        self.assertTrue(receipt["fresh_device_check_required_before_install"])
        self.assertEqual(tx.load_recovery(folder).rollback, self.reference["app_union"])

    def test_existing_run_directory_is_never_overwritten(self):
        marker = self.offline / "keep.txt"
        marker.write_text("user data", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            deploy.prepare_offline(self.offline)
        with patch.object(deploy, "MenuTestDevice") as factory:
            with self.assertRaises(FileExistsError):
                deploy.prepare_live(self.offline, self.offline)
            factory.assert_not_called()
        self.assertEqual(marker.read_text(encoding="utf-8"), "user data")

    def test_bad_plan_or_receipt_is_rejected_before_runner(self):
        receipt_path = self.offline / "preflight.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["plan_id"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with patch.object(deploy.entry, "run") as run:
            with self.assertRaisesRegex(ValueError, "preflight receipt differs"):
                deploy.run("check", self.offline)
            run.assert_not_called()

    def test_install_dispatches_only_a_valid_live_plan(self):
        live = self.root / "live"
        self.make_live_plan(live)
        expected = {"result": "candidate_installed"}
        original_device = deploy.entry.Device
        def install(phase, folder):
            self.assertIs(deploy.entry.Device, deploy.MenuTestDevice)
            return expected
        with patch.object(deploy.entry, "run", side_effect=install) as run:
            self.assertIs(deploy.run("install", live), expected)
        run.assert_called_once_with("install", live)
        self.assertIs(deploy.entry.Device, original_device)

    def test_bad_candidate_digest_is_rejected(self):
        changed = bytearray(self.evidence["image"])
        changed[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "reviewed E3c-3a menu-test App changed"):
            reviewed.reviewed_candidate(bytes(changed))

    def test_offline_check_never_dispatches_to_device(self):
        with patch.object(deploy.entry, "run") as run:
            result = deploy.run("check", self.offline)
            run.assert_not_called()
        self.assertEqual(result["phase"], "checked_offline")
        self.assertFalse(result["hardware_accessed"])

    def test_offline_plan_cannot_be_installed_before_live_preflight(self):
        with patch.object(deploy.entry, "run") as run:
            with self.assertRaisesRegex(ValueError, "live preflight required"):
                deploy.run("install", self.offline)
            run.assert_not_called()

    def test_live_preflight_refreshes_the_saved_nvs_snapshot_without_writing(self):
        reference = reviewed.load_reference()
        fresh = bytearray(reference["snapshot"])
        fresh[0x9000] ^= 1
        live = self.root / "live"
        with patch.object(deploy, "MenuTestDevice") as factory:
            device = factory.return_value
            device.identity.return_value = reference["identity_sha256"]
            device.read_reference.return_value = bytes(fresh)
            result = deploy.prepare_live(live, self.offline)
            device.identity.assert_called_once_with()
            device.read_reference.assert_called_once()
            device.boot.assert_not_called()
            device.write_app.assert_not_called()
        plan = tx.load(live)
        receipt = json.loads((live / "preflight.json").read_bytes())
        self.assertEqual(result["phase"], "prepared_live")
        self.assertNotEqual(plan.id, tx.load(self.offline).id)
        self.assertEqual(plan.snapshot, bytes(fresh))
        self.assertEqual(plan.rollback, reference["app_union"])
        self.assertEqual(receipt["result"], "device_preflight_ready")
        self.assertTrue(receipt["hardware_accessed"])
        self.assertFalse(receipt["device_written"])
        self.assertFalse(receipt["fresh_device_check_required_before_install"])
        self.assertEqual(deploy.validate_saved(live, "install").id, plan.id)

    def test_bad_live_receipt_is_rejected_before_install_dispatch(self):
        live = self.root / "live"
        self.make_live_plan(live)
        receipt_path = live / "preflight.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["target"] = "base_COM8"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with patch.object(deploy.entry, "run") as run:
            with self.assertRaisesRegex(ValueError, "preflight receipt differs"):
                deploy.run("install", live)
            run.assert_not_called()

    def test_recovery_load_does_not_need_candidate_or_build_reference(self):
        plan = tx.load_recovery(self.offline)
        tx.Journal(self.offline, plan).save("restore_required")
        (self.offline / "candidate-app.bin").unlink()
        (self.offline / "preflight.json").unlink()
        with patch.object(reviewed, "check_build_evidence",
                          side_effect=AssertionError("build inputs not needed for recovery")), \
                patch.object(reviewed, "load_reference",
                             side_effect=AssertionError("baseline not needed for recovery")), \
                patch.object(deploy, "MenuTestDevice") as factory:
            recovered = deploy.validate_saved(self.offline, "recover")
            device = factory.return_value
            device.identity.return_value = plan.metadata["identity_sha256"]
            device.verify_image.return_value = {"app_byte_equal": True}
            device.boot.return_value = {"normal_boot": True}
            result = deploy.run("recover", self.offline)
            device.write_app.assert_called_once_with(plan.rollback)
            device.boot.assert_called_once_with(False)
        self.assertEqual(result["result"], "original_restored")
        self.assertEqual(tx.Journal(self.offline, plan).load()["phase"], "restored")
        self.assertEqual(recovered.rollback, tx.load_recovery(self.offline).rollback)

    def test_changed_app_or_protected_bytes_stop_live_preflight(self):
        for offset in (tx.START, 0x8000, 0xBC8000, 0x1000000):
            with self.subTest(offset=offset), patch.object(deploy, "MenuTestDevice") as factory:
                fresh = bytearray(self.reference["snapshot"])
                fresh[offset] ^= 1
                device = factory.return_value
                device.identity.return_value = self.reference["identity_sha256"]
                device.read_reference.return_value = bytes(fresh)
                folder = self.root / str(offset)
                with self.assertRaisesRegex(ValueError, "differs from the verified E3c-2 reference"):
                    deploy.prepare_live(folder, self.offline)
                device.boot.assert_called_once_with(False)
                device.write_app.assert_not_called()
                self.assertFalse((folder / "preflight.json").exists())

    def test_live_device_checks_nvs_before_any_app_write(self):
        plan = tx.load(self.offline)
        device = deploy.MenuTestDevice.__new__(deploy.MenuTestDevice)
        device.deployment_plan = plan
        device.identity = lambda: plan.metadata["identity_sha256"]
        with patch.object(device, "verify_image", side_effect=ValueError("NVS changed")) as verify, \
                patch.object(device, "write_app") as write:
            with self.assertRaisesRegex(ValueError, "NVS changed"):
                tx.install(plan, device, tx.Journal(self.offline, plan))
            verify.assert_called_once_with(plan.rollback, allow_runtime_nvs=False)
            write.assert_not_called()
        self.assertIsNone(tx.Journal(self.offline, plan).load())


if __name__ == "__main__":
    unittest.main()
