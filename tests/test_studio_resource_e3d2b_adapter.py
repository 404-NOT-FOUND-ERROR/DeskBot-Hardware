"""Production E3d-2b CLI/transport with an in-memory runner, never a USB port."""
import importlib.util
from pathlib import Path
import unittest
import uuid
from unittest.mock import Mock, patch

from protocol import studio_resource_installation as r
from protocol.studio_resource_e3d2b_esptool import ResourceDevice
from protocol.studio_resource_esptool import ResourceDevice as LegacyDevice
from protocol.storage_upper_experiment_esptool import CAPTURE_SCRIPT, parse_identity
from test_studio_resource_adapter import (
    CommandRunner as LegacyRunner, FLASH_LOG, SECURITY_LOG, TRANSFER_LOG,
)
import test_studio_resource_adapter as legacy
from test_studio_resource_e3d2b import SNAPSHOT, periodic_boot

ROOT = Path(__file__).resolve().parents[1]


class CommandRunner(LegacyRunner):
    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.fail_writes = set()
        self.fail_boots = set()
        self.boots = 0
        self.corrupt_first_readback = False

    def run(self, argv, *, timeout):
        if str(CAPTURE_SCRIPT) in argv:
            self.calls.append(list(argv))
            self.boots += 1
            log = periodic_boot()
            if self.boots in self.fail_boots:
                log += "\nGuru Meditation\n"
            Path(argv[argv.index("--output") + 1]).write_text(log, encoding="utf-8")
            return 0, "Capture complete\n"
        if "write_flash" in argv and len(self.writes) + 1 in self.fail_writes:
            self.assert_com4(argv)
            self.calls.append(list(argv))
            start, raw = int(argv[-2], 0), Path(argv[-1]).read_bytes()
            self.writes.append((start, raw))
            self.data[start:start + len(raw) // 2] = raw[:len(raw) // 2]
            return 1, TRANSFER_LOG + "interrupted write\n"
        result = super().run(argv, timeout=timeout)
        if ("read_flash" in argv and self.corrupt_first_readback and len(self.writes) == 1
                and int(argv[-3], 0) == r.START):
            self.corrupt_first_readback = False
            path = Path(argv[-1])
            raw = bytearray(path.read_bytes())
            raw[100] ^= 1
            path.write_bytes(raw)
        return result


def load_cli(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class E3d2bAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = SNAPSHOT.read_bytes()
        cls.legacy_snapshot = (ROOT / "tmp/e143b-e3c3b-20261107-live1/snapshot-32m.bin").read_bytes()
        cls.image = (ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin").read_bytes()
        cls.identity = parse_identity(FLASH_LOG, SECURITY_LOG).digest
        cls.cli = load_cli("deploy-e3d2b-resources.py", "current_resource_cli_tests")
        cls.legacy_cli = load_cli("deploy-studio-resources.py", "legacy_resource_cli_cross_tests")

    def setUp(self):
        self.folder = self.run_folder()
        self.plan = r.prepare(self.snapshot, self.image, self.identity, mode="live", baseline="E3d-2b")
        r.persist(self.folder, self.plan)
        self.runner = CommandRunner(self.snapshot)

    def run_folder(self, *, create=True):
        folder = ROOT / "tmp" / ("e145-assets-20261108-test-" + uuid.uuid4().hex[:16])
        self.addCleanup(legacy.ResourceAdapterTests.remove_run, folder)
        if create:
            folder.mkdir()
        return folder

    def device(self, *, plan=None, write_enabled=True):
        return ResourceDevice(self.folder, plan=plan or self.plan, runner=self.runner, write_enabled=write_enabled)

    def factory(self):
        return Mock(side_effect=lambda folder, **kw: ResourceDevice(folder, runner=self.runner, **kw))

    def assert_resource_writes(self):
        self.assertTrue(self.runner.writes)
        self.assertTrue(all((offset, len(raw)) == (r.START, r.SIZE) for offset, raw in self.runner.writes))
        self.assertTrue(all(call[call.index("--port") + 1] == "COM4" for call in self.runner.calls))
        self.assertFalse(any(arg in ("erase_flash", "erase_region", "--erase-all")
                             for call in self.runner.calls for arg in call))

    def test_current_cli_full_install_and_recovery_independent_of_candidate_and_builds(self):
        folder = self.run_folder(create=False)
        factory = self.factory()
        prepared = self.cli.run("prepare-live", folder, identity=self.identity, device_factory=factory)
        plan = r.load(folder)
        self.assertEqual((prepared["mode"], plan.metadata["baseline"]), ("live", "E3d-2b"))
        self.assertEqual((self.runner.writes, bytes(self.runner.data)), ([], self.snapshot))
        self.assertEqual(self.cli.run("check", folder, device_factory=factory)["plan_id"], plan.id)
        self.assertEqual(factory.call_count, 1)
        installed = self.cli.run("install", folder, confirm_plan=plan.id, device_factory=factory)
        self.assertEqual(installed["result"], "candidate_resources_installed")
        self.assertEqual(bytes(self.runner.data[r.START:r.START + r.SIZE]), self.image)
        self.assertEqual(bytes(self.runner.data[:r.START]), self.snapshot[:r.START])
        self.assertEqual(bytes(self.runner.data[r.START + r.SIZE:]), self.snapshot[r.START + r.SIZE:])
        (folder / "candidate-assets-8m.bin").unlink()
        # Recovery may use saved plans, captures and payloads; build products or
        # the staging candidate are deliberately unavailable to every read.
        original_read = Path.read_bytes
        def saved_materials_only(path):
            if (path.name in {"candidate-assets-8m.bin", "xiaozhi.bin"}
                    or any(part in {"build", "on", "off"} for part in path.parts)):
                raise AssertionError("recovery attempted candidate/build dependency")
            return original_read(path)
        with patch.object(Path, "read_bytes", saved_materials_only):
            restored = self.cli.run("recover", folder, confirm_plan=plan.id, device_factory=factory)
        self.assertEqual(restored["result"], "original_resources_restored")
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(r.Journal(folder, plan).load()["phase"], "restored")
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(self.runner.boots, 3)
        self.assert_resource_writes()

    def test_cross_version_check_install_and_recover_rejected_before_factory(self):
        old = r.prepare(self.legacy_snapshot, self.image, self.identity, mode="live")
        old_folder = self.run_folder()
        r.persist(old_folder, old)
        for cli, folder, plan in ((self.cli, old_folder, old),
                                  (self.legacy_cli, self.folder, self.plan)):
            for phase in ("check", "install", "recover"):
                with self.subTest(phase=phase, schema=plan.metadata["schema"]):
                    factory = Mock()
                    with self.assertRaisesRegex(ValueError, "entry_baseline"):
                        cli.run(phase, folder, confirm_plan=plan.id, device_factory=factory)
                    factory.assert_not_called()
        self.assertEqual(self.runner.calls, [])

    def test_adapter_rejects_cross_version_plan_before_any_io(self):
        old = r.prepare(self.legacy_snapshot, self.image, self.identity, mode="live")
        for adapter, plan in ((ResourceDevice, old), (LegacyDevice, self.plan)):
            with self.assertRaisesRegex(ValueError, "adapter_baseline"):
                adapter(self.folder, plan=plan, runner=self.runner, write_enabled=True)
        self.assertEqual(self.runner.calls, [])

    def test_default_transport_selection_matches_entry_baseline(self):
        self.assertIs(self.cli.entry.transport_for("E3d-2b"), ResourceDevice)
        self.assertIs(self.legacy_cli.transport_for("E3c-3b"), LegacyDevice)
        self.assertEqual(self.runner.calls, [])

    def test_offline_prepare_check_install_and_recover_never_construct_transport(self):
        folder = self.run_folder(create=False)
        factory = Mock()
        with patch.object(self.cli.entry, "transport_for", side_effect=AssertionError("loaded transport")):
            prepared = self.cli.run("prepare-offline", folder, snapshot=SNAPSHOT,
                                    identity=self.identity, device_factory=factory)
            self.assertEqual(prepared["mode"], "offline")
            self.assertEqual(self.cli.run("check", folder, device_factory=factory)["plan_id"], prepared["plan_id"])
            for phase in ("install", "recover"):
                with self.assertRaisesRegex(ValueError, "matching_live_resource_plan"):
                    self.cli.run(phase, folder, confirm_plan=prepared["plan_id"], device_factory=factory)
        factory.assert_not_called()
        self.assertEqual(self.runner.calls, [])

    def test_wrong_confirmation_and_unprepared_recovery_never_write(self):
        factory = self.factory()
        with self.assertRaisesRegex(ValueError, "matching_live_resource_plan"):
            self.cli.run("install", self.folder, confirm_plan="0" * 64, device_factory=factory)
        factory.assert_not_called()
        with self.assertRaisesRegex(ValueError, "recovery_state"):
            self.cli.run("recover", self.folder, confirm_plan=self.plan.id, device_factory=factory)
        self.assertEqual(self.runner.calls, [])

    def test_partial_write_is_restored_by_production_adapter(self):
        self.runner.fail_writes = {1}
        with self.assertRaises(OSError):
            r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(r.Journal(self.folder, self.plan).load()["phase"], "restored")
        self.assert_resource_writes()

    def test_initial_intent_failure_reboots_current_app_without_flash_write(self):
        journal = r.Journal(self.folder, self.plan)
        with patch.object(journal, "save", side_effect=OSError("disk_full")):
            with self.assertRaisesRegex(OSError, "disk_full"):
                r.install(self.plan, self.device(), journal)
        self.assertEqual((self.runner.writes, self.runner.boots), ([], 1))
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertIsNone(journal.load())

    def test_initial_intent_failure_reports_unconfirmed_app_boot(self):
        for mode in ("false", "raise"):
            with self.subTest(mode=mode):
                runner = CommandRunner(self.snapshot)
                self.runner = runner
                device = self.device()
                journal = r.Journal(self.folder, self.plan)
                if mode == "false":
                    runner.fail_boots = {1}
                    boot_patch = patch.object(device, "boot", wraps=device.boot)
                else:
                    boot_patch = patch.object(device, "boot", side_effect=OSError("serial_unavailable"))
                with patch.object(journal, "save", side_effect=OSError("disk_full")), boot_patch:
                    with self.assertRaises(r.ResourcePreparationRecoveryError) as caught:
                        r.install(self.plan, device, journal)
                error = caught.exception
                self.assertEqual(error.stage, "write_intent_save_failed")
                self.assertIsInstance(error.preparation_error, OSError)
                if mode == "false":
                    self.assertEqual((error.boot_result, error.boot_error), (False, None))
                    self.assertEqual(runner.boots, 1)
                else:
                    self.assertIsNone(error.boot_result)
                    self.assertIsInstance(error.boot_error, OSError)
                    self.assertEqual(str(error.boot_error), "serial_unavailable")
                self.assertEqual((runner.writes, bytes(runner.data)), ([], self.snapshot))
                self.assertFalse(any("write_flash" in call for call in runner.calls))
                self.assertIsNone(journal.load())

    def test_readback_failure_is_restored_by_production_adapter(self):
        self.runner.corrupt_first_readback = True
        with self.assertRaisesRegex(ValueError, "candidate_readback"):
            r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(len(self.runner.writes), 2)
        self.assert_resource_writes()

    def test_boot_failure_is_restored_by_production_adapter(self):
        self.runner.fail_boots = {1}
        with self.assertRaisesRegex(ValueError, "candidate_boot"):
            r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(self.runner.boots, 2)
        self.assert_resource_writes()

    def test_recovery_interrupted_then_restarted_without_candidate(self):
        self.runner.fail_writes = {1, 2}
        factory = self.factory()
        with self.assertRaises(OSError):
            self.cli.run("install", self.folder, confirm_plan=self.plan.id, device_factory=factory)
        self.assertEqual(r.Journal(self.folder, self.plan).load()["phase"], "needs_recovery")
        (self.folder / "candidate-assets-8m.bin").unlink()
        self.assertEqual(self.cli.run("recover", self.folder, confirm_plan=self.plan.id,
                                    device_factory=factory)["result"], "original_resources_restored")
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(len(self.runner.writes), 3)
        self.assert_resource_writes()

    def test_later_recovery_retains_current_runtime_nvs(self):
        r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.runner.data[r.NVS_START + 100] ^= 1
        current_nvs = bytes(self.runner.data[r.NVS_START:r.NVS_END])
        r.recover(r.load(self.folder, recovery=True), self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(bytes(self.runner.data[r.NVS_START:r.NVS_END]), current_nvs)
        self.assertEqual(bytes(self.runner.data[r.START:r.START + r.SIZE]), self.plan.rollback)
        self.assert_resource_writes()

    def test_default_permissions_payload_and_intent_limits(self):
        with self.assertRaises(PermissionError):
            self.device(write_enabled=False).write_assets(self.image)
        with self.assertRaisesRegex(ValueError, "intent"):
            self.device().write_assets(self.image)
        r.Journal(self.folder, self.plan).save("write_intent")
        for raw in (self.image[:-1], self.plan.rollback):
            with self.assertRaises(ValueError):
                self.device().write_assets(raw)
        with self.assertRaises(ValueError):
            self.device().write_flash(0x10000, self.image)
        self.assertEqual(self.runner.writes, [])
        self.assertEqual(self.runner.calls, [])

    def test_identity_or_protected_bytes_change_stops_before_resource_write(self):
        self.runner.flash_log = FLASH_LOG.replace("02:00:00:43:c2:01", "02:00:00:43:c2:02")
        with self.assertRaises(ValueError):
            r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(self.runner.writes, [])
        self.runner.flash_log = FLASH_LOG
        self.runner.data[0x800000] ^= 1
        with self.assertRaisesRegex(ValueError, "snapshot_changed"):
            r.install(self.plan, self.device(), r.Journal(self.folder, self.plan))
        self.assertEqual(self.runner.writes, [])


if __name__ == "__main__":
    unittest.main()
