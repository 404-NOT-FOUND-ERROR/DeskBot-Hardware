"""Accepted layout App resource transaction, CLI and real package regression."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from protocol import studio_resource_installation as r
from protocol import studio_app_deployment as app
from protocol import studio_resource_package as packages
from protocol import studio_resource_e3f4c_reference as reference
from protocol.studio_resource_e3f4c_esptool import ResourceDevice
from protocol.studio_resource_e3d2b_esptool import ResourceDevice as OlderDevice
from protocol.studio_resource_e3d2b_esptool import inspect_boot
from protocol.storage_upper_experiment_esptool import CAPTURE_SCRIPT, parse_identity
import test_studio_resource_installation as transaction
import test_studio_resource_adapter as legacy
import test_studio_resource_e3d2b_adapter as older
from test_studio_resource_e3d2b import periodic_boot, FAKE_MAC

ROOT = Path(__file__).resolve().parents[1]


def current_boot():
    return periodic_boot(change=lambda _, hello: hello["firmware"].update(
        app_elf_sha256=reference.APP_ELF_SHA)) + "USB input ready: /dev/secondary\n"


class CommandRunner(older.CommandRunner):
    def run(self, argv, *, timeout):
        if str(CAPTURE_SCRIPT) in argv:
            self.calls.append(list(argv))
            self.boots += 1
            log = current_boot() + ("Guru Meditation\n" if self.boots in self.fail_boots else "")
            Path(argv[argv.index("--output") + 1]).write_text(log, encoding="utf-8")
            return 0, "Capture complete\n"
        if "verify_flash" in argv:
            self.assert_com4(argv)
            self.calls.append(list(argv))
            raw = Path(argv[-1]).read_bytes()
            offset = int(argv[-2], 0)
            equal = bytes(self.data[offset:offset + len(raw)]) == raw
            return (0, legacy.TRANSFER_LOG + "-- verify OK (digest matched)\n") if equal else (
                1, legacy.TRANSFER_LOG + "verify failed\n")
        return super().run(argv, timeout=timeout)


class E3f4cTransactionTests(transaction.ResourceTests):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = reference.load_reference()["snapshot"]
        cls.image = (ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin").read_bytes()
        cls.plan = r.prepare(cls.snapshot, cls.image, "a" * 64, baseline="E3f-4c")

    def test_truncated_or_unexpected_images_rejected(self):
        for raw in (self.image[:-1], bytes(r.SIZE)):
            with self.assertRaises(ValueError):
                r.prepare(self.snapshot, raw, "a" * 64, baseline="E3f-4c")
        with self.assertRaises(ValueError):
            r.prepare(self.snapshot[:-1], self.image, "a" * 64, baseline="E3f-4c")

    def test_accepted_reference_overlays_installed_app_not_old_snapshot(self):
        saved = app.load(ROOT / reference.RUN)
        self.assertNotEqual(saved.snapshot, self.snapshot)
        self.assertEqual(self.snapshot[app.START:app.START + len(saved.candidate)], saved.candidate)
        self.assertEqual(r.sha(self.snapshot[app.START:app.START + r.E3F4C_APP_BYTES]), r.E3F4C_APP_SHA)

    def test_schema_and_app_binding_cannot_be_changed(self):
        self.assertEqual((self.plan.metadata["schema"], self.plan.metadata["baseline"]),
                         (r.E3F4C_SCHEMA, "E3f-4c"))
        for key, value in (("schema", r.E3D2B_SCHEMA), ("baseline", "E3d-2b"),
                           ("schema", r.SCHEMA), ("baseline", None)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                r.RecoveryPlan(self.plan.metadata | {key: value}, self.snapshot).validate()
        with self.assertRaises(ValueError):
            r.prepare(self.snapshot, self.image, "a" * 64, baseline="E3d-2b")

    def test_real_package_adds_only_shy_and_preserves_existing_layout_and_files(self):
        parser, _ = packages.load_legacy_tools()
        def members(raw):
            return parser.parse_package_bytes(packages.extract_package(raw, r.SIZE), modern=False)["members"]
        old, new = members(self.plan.rollback), members(self.image)
        self.assertEqual(set(new), set(old) | {"shy_candidate.eaf"})
        self.assertTrue(all(new[name] == raw for name, raw in old.items() if name != "index.json"))
        expected = json.loads(old["index.json"])
        shy = next(row for row in expected["emoji_collection"] if row["name"] == "embarrassed")
        shy["file"], shy["eaf"]["fps"] = "shy_candidate.eaf", 25
        self.assertEqual(json.loads(new["index.json"]), expected)
        self.assertEqual(packages.sha(new["shy_candidate.eaf"]),
                         "46ac913fa186e033cba78b11ef6e7da580ab337528f80a7e33b686019aadc93c")


class E3f4cAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = reference.load_reference()["snapshot"]
        cls.image = (ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin").read_bytes()
        cls.identity = parse_identity(legacy.FLASH_LOG, legacy.SECURITY_LOG).digest
        cls.cli = older.load_cli("deploy-e3f4c-resources.py", "layout_resource_cli_tests")
        cls.old_cli = older.load_cli("deploy-e3d2b-resources.py", "older_layout_resource_cli_tests")

    def setUp(self):
        self.folder = self.run_folder()
        self.plan = r.prepare(self.snapshot, self.image, self.identity, mode="live", baseline="E3f-4c")
        r.persist(self.folder, self.plan)
        self.runner = CommandRunner(self.snapshot)

    run_folder = legacy.ResourceAdapterTests.run_folder
    remove_run = staticmethod(legacy.ResourceAdapterTests.remove_run)

    def device(self, *, plan=None, write_enabled=True):
        return ResourceDevice(self.folder, plan=plan or self.plan, runner=self.runner, write_enabled=write_enabled)

    def factory(self):
        return Mock(side_effect=lambda folder, **kw: ResourceDevice(
            folder, reference=self.snapshot, runner=self.runner, **kw))

    assert_resource_writes = older.E3d2bAdapterTests.assert_resource_writes

    def test_live_prepare_stays_in_downloader_and_install_boots_same_app(self):
        folder = self.run_folder(create=False)
        factory = self.factory()
        prepared = self.cli.run("prepare-live", folder, identity=self.identity, device_factory=factory)
        self.assertEqual((self.runner.boots, self.runner.writes), (0, []))
        self.assertEqual(prepared["mode"], "live")
        self.assertIs(prepared["device_accessed"], True)
        self.assertIs(prepared["download_mode_retained"], True)
        plan = r.load(folder)
        result = self.cli.run("install", folder, confirm_plan=plan.id, device_factory=factory)
        self.assertEqual(result["result"], "candidate_resources_installed")
        self.assertEqual(self.runner.boots, 1)
        reads = [(int(c[-3], 0), int(c[-2], 0)) for c in self.runner.calls if "read_flash" in c]
        self.assertEqual(sum(size for offset, size in reads if offset >= r.START), r.SIZE)
        self.assertEqual(sum("verify_flash" in c for c in self.runner.calls), 3)
        self.assert_resource_writes()

    def test_preparation_failure_boots_original_and_never_writes(self):
        folder = self.run_folder(create=False)
        self.runner.data[0x900000] ^= 1
        with self.assertRaises(OSError):
            self.cli.run("prepare-live", folder, identity=self.identity, device_factory=self.factory())
        self.assertEqual((self.runner.boots, self.runner.writes), (1, []))

    def test_old_entry_and_adapter_reject_layout_plan_before_io(self):
        factory = self.factory()
        for phase in ("check", "install", "recover"):
            with self.assertRaisesRegex(ValueError, "entry_baseline"):
                self.old_cli.run(phase, self.folder, confirm_plan=self.plan.id, device_factory=factory)
        with self.assertRaisesRegex(ValueError, "adapter_baseline"):
            OlderDevice(self.folder, plan=self.plan, runner=self.runner, write_enabled=True)
        factory.assert_not_called()
        self.assertEqual(self.runner.calls, [])

    def test_layout_entry_rejects_old_plan_before_io(self):
        old_snapshot = (ROOT / "tmp/resource-read-20261108-live1/snapshot-32m.bin").read_bytes()
        old = r.prepare(old_snapshot, self.image, self.identity, mode="live", baseline="E3d-2b")
        folder = self.run_folder()
        r.persist(folder, old)
        factory = self.factory()
        for phase in ("check", "install", "recover"):
            with self.assertRaisesRegex(ValueError, "entry_baseline"):
                self.cli.run(phase, folder, confirm_plan=old.id, device_factory=factory)
        with self.assertRaisesRegex(ValueError, "adapter_baseline"):
            ResourceDevice(folder, plan=old, runner=self.runner, write_enabled=True)
        factory.assert_not_called()

    def test_offline_default_reference_and_transport_selection(self):
        folder = self.run_folder(create=False)
        factory = Mock()
        prepared = self.cli.run("prepare-offline", folder, device_factory=factory)
        self.assertEqual(prepared["mode"], "offline")
        self.assertEqual(self.cli.run("check", folder)["plan_id"], prepared["plan_id"])
        with self.assertRaisesRegex(ValueError, "matching_live"):
            self.cli.run("install", folder, confirm_plan=prepared["plan_id"], device_factory=factory)
        factory.assert_not_called()
        self.assertIs(self.cli.entry.transport_for("E3f-4c"), ResourceDevice)

    def test_wrong_plan_id_and_unprepared_recovery_never_touch_device(self):
        factory = self.factory()
        with self.assertRaisesRegex(ValueError, "matching_live"):
            self.cli.run("install", self.folder, confirm_plan="0" * 64, device_factory=factory)
        factory.assert_not_called()
        with self.assertRaisesRegex(ValueError, "recovery_state"):
            self.cli.run("recover", self.folder, confirm_plan=self.plan.id, device_factory=factory)
        self.assertEqual(self.runner.calls, [])

    def test_outside_bytes_and_fresh_nvs_checked_before_write(self):
        for offset in (0x800000, r.NVS_START):
            self.runner = CommandRunner(self.snapshot)
            self.runner.data[offset] ^= 1
            with self.assertRaises((OSError, ValueError)):
                r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
            self.assertEqual((self.runner.writes, self.runner.boots), ([], 1))

    def test_partial_write_recovers_actual_current_resources(self):
        self.runner.fail_writes = {1}
        with self.assertRaises(OSError):
            r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(r.Journal(self.folder, self.plan).load()["phase"], "restored")
        self.assert_resource_writes()

    def test_corrupt_resource_readback_recovers(self):
        self.runner.corrupt_first_readback = True
        with self.assertRaises(OSError):
            r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(len(self.runner.writes), 2)

    def test_boot_failure_recovers(self):
        self.runner.fail_boots = {1}
        with self.assertRaisesRegex(ValueError, "candidate_boot"):
            r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(self.runner.boots, 2)

    def test_restarted_recovery_uses_saved_materials_and_current_nvs_only(self):
        self.runner.fail_writes = {1, 2}
        factory = self.factory()
        with self.assertRaises(OSError):
            self.cli.run("install", self.folder, confirm_plan=self.plan.id, device_factory=factory)
        self.assertEqual(r.Journal(self.folder, self.plan).load()["phase"], "needs_recovery")
        (self.folder / "candidate-assets-8m.bin").unlink()
        self.runner.data[r.NVS_START + 10] ^= 1
        nvs = bytes(self.runner.data[r.NVS_START:r.NVS_END])
        with patch("protocol.studio_resource_e3f4c_esptool.load_reference", side_effect=AssertionError("old reference read")):
            result = self.cli.run("recover", self.folder, confirm_plan=self.plan.id, device_factory=factory)
        self.assertEqual(result["result"], "original_resources_restored")
        self.assertEqual(bytes(self.runner.data[r.NVS_START:r.NVS_END]), nvs)
        self.assertEqual(bytes(self.runner.data[r.START:r.START + r.SIZE]), self.plan.rollback)
        self.assert_resource_writes()

    def test_intent_failure_restarts_without_writing(self):
        journal = r.Journal(self.folder, self.plan)
        with patch.object(journal, "save", side_effect=OSError("disk_full")):
            with self.assertRaisesRegex(OSError, "disk_full"):
                r.install(self.plan, self.device(), journal)
        self.assertEqual((self.runner.boots, self.runner.writes), (1, []))

    def test_transfer_identity_and_missing_digest_fail(self):
        device = self.device()
        real_run = self.runner.run
        def altered(argv, *, timeout):
            code, output = real_run(argv, timeout=timeout)
            return code, output.replace("digest matched", "no digest") if "verify_flash" in argv else output
        with patch.object(self.runner, "run", side_effect=altered), self.assertRaisesRegex(ValueError, "digest_unconfirmed"):
            device.read_full()
        self.assertEqual(self.runner.writes, [])

    def test_exact_payload_intent_and_read_only_limits(self):
        with self.assertRaises(PermissionError):
            self.device(write_enabled=False).write_assets(self.image)
        with self.assertRaisesRegex(ValueError, "intent"):
            self.device().write_assets(self.image)
        r.Journal(self.folder, self.plan).save("write_intent")
        with self.assertRaises(ValueError):
            self.device().write_assets(self.image[:-1])
        with self.assertRaises(ValueError):
            self.device().write_flash(app.START, self.image)
        self.assertEqual(self.runner.calls, [])

    def test_boot_identity_usb_and_periodic_frames(self):
        log = current_boot()
        self.assertTrue(inspect_boot(log, FAKE_MAC, expected_elf=reference.APP_ELF_SHA))
        self.assertFalse(inspect_boot(log, FAKE_MAC))
        self.assertFalse(inspect_boot(log, FAKE_MAC[:-1] + "2", expected_elf=reference.APP_ELF_SHA))
        device = self.device()
        device._identity = parse_identity(legacy.FLASH_LOG, legacy.SECURITY_LOG)
        for text in (log, log.replace("USB input ready: /dev/secondary", ""), log + "USB input owner changed"):
            with patch.object(device, "_capture_serial", return_value=text):
                self.assertEqual(device.boot(), text == log)


if __name__ == "__main__":
    unittest.main()
