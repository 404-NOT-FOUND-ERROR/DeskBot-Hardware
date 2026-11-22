import importlib.util
import contextlib
import io
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_e3c3_deployment as reviewed
from tests.test_storage_capacity import image as synthetic_esp_image

ROOT = reviewed.ROOT
spec = importlib.util.spec_from_file_location(
    "e3c3_menu_deploy_test", ROOT / "scripts/deploy-deskbot-studio-e3c3.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class E3c3SavedDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reference_path = ROOT / "tmp" / reviewed.PREVIOUS_RUN / "plan.json"
        if not reference_path.is_file() or not (ROOT / reviewed.SOURCE).is_file():
            raise unittest.SkipTest("local E3c-2 Flash backup or E3c-3 App is absent")
        cls.reference = reviewed.load_reference()
        cls.image = reviewed.reviewed_candidate((reviewed.ROOT / reviewed.SOURCE).read_bytes())

    def test_reference_is_verified_installed_e3c2(self):
        self.assertEqual(len(self.reference["snapshot"]), tx.FLASH)
        self.assertEqual(len(self.reference["app_union"]), 4489216)
        self.assertEqual(tx.sha(self.reference["app_union"][:reviewed.ORIGINAL_SIZE]),
                         reviewed.ORIGINAL_SHA)

    def test_current_flash_allows_runtime_nvs_changes(self):
        current = bytearray(self.reference["snapshot"])
        current[0x9000] ^= 0x01
        reviewed.check_current(bytes(current), self.reference)

    def test_current_flash_rejects_app_or_static_changes(self):
        for offset in (tx.START, tx.START + len(self.reference["app_union"]) + 16):
            with self.subTest(offset=offset):
                current = bytearray(self.reference["snapshot"])
                current[offset] ^= 0x01
                with self.assertRaises(ValueError):
                    reviewed.check_current(bytes(current), self.reference)

    def test_plan_is_app_only_and_keeps_current_e3c2_as_rollback(self):
        plan = tx.prepare(self.reference["snapshot"], self.image,
                          self.reference["identity_sha256"], reviewed.ORIGINAL_SIZE,
                          edition="E3c")
        reviewed.check_plan(plan)
        self.assertEqual(plan.metadata["offset"], 0x10000)
        self.assertEqual(plan.rollback[:reviewed.ORIGINAL_SIZE],
                         self.reference["app_union"][:reviewed.ORIGINAL_SIZE])
        self.assertEqual(plan.metadata["erase_bytes"], 4489216)


class E3c3DeploymentTests(unittest.TestCase):
    def setUp(self):
        # The historical candidate is private. Use a valid unsigned image to
        # exercise the production digest check and boot log rules on every host.
        self.image = synthetic_esp_image(5, b"synthetic E3c-3 App")
        pinned = patch.multiple(reviewed, APP_SIZE=len(self.image), APP_SHA=tx.sha(self.image))
        pinned.start()
        self.addCleanup(pinned.stop)

    def test_candidate_digest_is_pinned(self):
        self.assertEqual(reviewed.reviewed_candidate(self.image), self.image)
        changed = bytearray(self.image)
        changed[-1] ^= 0x01
        with self.assertRaises(ValueError):
            reviewed.reviewed_candidate(bytes(changed))

    def menu_device(self, log):
        device = deploy.MenuComparisonDevice.__new__(deploy.MenuComparisonDevice)
        device.deployment_plan = type("Plan", (), {"image": self.image})()
        synthetic_mac = ":".join(("00",) * 5 + ("01",))
        device._identity = type("Identity", (), {"mac": synthetic_mac})()
        device._serial = lambda label, duration: log
        return device

    def test_menu_comparison_accepts_only_known_legacy_usb_unavailable_log(self):
        log = "I (1288) DeskBotLink: boot\nW (1288) DeskBotSession: USB input unavailable\n"
        device = self.menu_device(log)
        expected = {"normal_boot": True}
        with patch.object(deploy.entry.parent, "inspect_boot", return_value=expected) as inspect:
            result = device.boot(True)
        self.assertEqual(inspect.call_args.args[1], self.image)
        self.assertTrue(inspect.call_args.kwargs["identity_required"])
        self.assertEqual(result["usb_input"], "historical_path_unavailable")
        self.assertEqual(result["usb_query_validation"], "pending")
        self.assertEqual(result["validation_scope"], "historical_menu_comparison")

    def test_menu_comparison_rejects_unexpected_usb_or_missing_log(self):
        for log in ("boot log", "USB input configuration unavailable",
                    "USB input unavailable\nUSB input ready: /dev/secondary"):
            with self.subTest(log=log):
                device = self.menu_device(log)
                with patch.object(deploy.entry.parent, "inspect_boot") as inspect:
                    with self.assertRaisesRegex(ValueError, "historical USB task exit required"):
                        device.boot(True)
                    inspect.assert_not_called()

    def test_menu_comparison_rejects_changed_candidate_before_boot_check(self):
        changed = bytearray(self.image)
        changed[-1] ^= 0x01
        device = self.menu_device("USB input unavailable\n")
        device.deployment_plan = type("Plan", (), {"image": bytes(changed)})()
        with patch.object(deploy.entry.parent, "inspect_boot") as inspect:
            with self.assertRaisesRegex(ValueError, "reviewed E3c-3 candidate changed"):
                device.boot(True)
            inspect.assert_not_called()

    def test_install_requires_explicit_menu_comparison_mode(self):
        with patch.object(deploy.entry, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                deploy.main(["install", "--run", "e3c-live-20261106-test"])
        run.assert_not_called()

    def test_cli_forwards_reused_backup_and_scopes_device_override(self):
        original_device = deploy.entry.Device

        def fake_run(phase, folder, *, reuse_reference=False):
            self.assertEqual(phase, "prepare")
            self.assertEqual(folder.name, "e3c-live-20261106-test")
            self.assertTrue(reuse_reference)
            self.assertIs(deploy.entry.Device, deploy.MenuComparisonDevice)
            return {"phase": "prepared", "plan_id": "test", "erase_bytes": 4489216}

        with patch.object(deploy.entry, "run", side_effect=fake_run):
            deploy.main(["prepare", "--run", "e3c-live-20261106-test",
                         "--reuse-installed-reference", "--menu-comparison"])
        self.assertIs(deploy.entry.Device, original_device)


if __name__ == "__main__":
    unittest.main()
