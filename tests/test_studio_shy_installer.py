"""Real E3f-1 CLI and App transport with an in-memory command runner."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from protocol import studio_app_deployment as app
from protocol import studio_e3d2b_deployment as baseline
from protocol import studio_e3f1_deployment as release
from protocol import studio_resource_installation as resources
from protocol import studio_shy_installer as installer
from protocol.studio_shy_esptool import ShyDevice
from protocol.storage_upper_experiment_esptool import CAPTURE_SCRIPT, _NORMAL_BOOT_MARKERS, parse_identity

ROOT = installer.ROOT


class CommandRunner:
    """Emulate the existing esptool/capture command boundary; never spawn a process."""
    def __init__(self, snapshot):
        self.data = bytearray(snapshot)
        self.calls, self.writes = [], []
        evidence = ROOT / "tmp/resource-read-20261108-live1"
        self.flash_log = (evidence / "logs/001-flash-id.log").read_text(encoding="utf-8")
        self.security_log = (evidence / "logs/002-security-info.log").read_text(encoding="utf-8")
        self.identity = parse_identity(self.flash_log, self.security_log)
        self.transfer = "Serial port COM4\nMAC: " + self.identity.mac + "\n"
        boot = (evidence / "captures/080-catalog-restored.log").read_text(encoding="utf-8")
        line = next(line for line in boot.splitlines() if "DeskBotLink: " in line)
        self.hello = json.loads(line.split("DeskBotLink: ", 1)[1])
        self.fail_writes, self.fail_boots = set(), set()
        self.corrupt_readback = False
        self.boots = 0
        self.update_nvs_on_boot = False

    def run(self, argv, *, timeout):
        self.calls.append(list(argv))
        if argv[argv.index("--port") + 1] != "COM4":
            raise AssertionError("unexpected port")
        if str(CAPTURE_SCRIPT) in argv:
            self.boots += 1
            header = self.data[app.START:app.START + baseline.APP_SIZE]
            candidate = bytes(self.data[app.START:app.START + release.APP_BYTES])
            elf = release.APP_ELF_SHA if app.sha(candidate) == release.APP_SHA else baseline.APP_ELF_SHA
            if elf == baseline.APP_ELF_SHA and app.sha(bytes(header)) != baseline.APP_SHA:
                raise AssertionError("booting an unexpected image")
            frames = []
            for index in range(4):
                hello = copy.deepcopy(self.hello)
                hello["firmware"]["app_elf_sha256"] = elf
                hello.update(sequence=index, uptime_ms=1105 + index * 10000,
                             boot_id="boot-" + format(self.boots, "032x"))
                frames.append("I (%d) DeskBotLink: %s" %
                              (hello["uptime_ms"], json.dumps(hello, separators=(",", ":"))))
            log = "\n".join((*_NORMAL_BOOT_MARKERS, "USB input ready: /dev/secondary", *frames)) + "\n"
            if self.boots in self.fail_boots:
                log += "Guru Meditation\n"
            if self.update_nvs_on_boot:
                self.data[0x9000] ^= 1
            Path(argv[argv.index("--output") + 1]).write_text(log, encoding="utf-8")
            return 0, "Capture complete\n"
        if "flash_id" in argv:
            return 0, self.flash_log
        if "get_security_info" in argv:
            return 0, self.security_log
        if "read_flash" in argv:
            start, size = int(argv[-3], 0), int(argv[-2], 0)
            raw = bytes(self.data[start:start + size])
            if self.corrupt_readback and len(self.writes) == 1 and start == app.START:
                self.corrupt_readback = False
                raw = bytes([raw[0] ^ 1]) + raw[1:]
            Path(argv[-1]).write_bytes(raw)
            return 0, self.transfer
        if "verify_flash" in argv:
            args = argv[argv.index("verify_flash") + 7:]
            if len(args) % 2:
                raise AssertionError("unexpected verification args")
            for offset, filename in zip(args[::2], args[1::2]):
                raw = Path(filename).read_bytes()
                start = int(offset, 0)
                if bytes(self.data[start:start + len(raw)]) != raw:
                    return 2, self.transfer + "Verification failed\n"
            return 0, self.transfer + "-- verify OK (digest matched)\n" * (len(args) // 2)
        if "write_flash" in argv:
            start, raw = int(argv[-2], 0), Path(argv[-1]).read_bytes()
            self.writes.append((start, raw))
            if (start, len(raw)) != (app.START, release.APP_ERASE_END - app.START):
                raise AssertionError("write outside the App union")
            if len(self.writes) in self.fail_writes:
                self.data[start:start + 128] = raw[:128]
                return 1, self.transfer + "injected partial write\n"
            self.data[start:start + len(raw)] = raw
            return 0, self.transfer + "Hash of data verified\n"
        raise AssertionError("unexpected command: " + repr(argv))


class ShyInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resource = release.resources_ready()
        cls.snapshot = release.simulate_resource_install(cls.resource)
        cls.candidate = release.inputs()[2]
        spec = importlib.util.spec_from_file_location("shy_installer_cli_tests", ROOT / "scripts/deploy-studio-shy-e3f1.py")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e3f1-installer-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        # Named direct children are production-validated; the temporary parent
        # owns cleanup, while tests patch ROOT to that isolated worktree only.
        self.test_root = Path(self.temp.name)
        (self.test_root / "tmp").mkdir()
        self.root_patch = patch.object(installer, "ROOT", self.test_root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.transport_root = patch("protocol.storage_upper_experiment_esptool.ROOT", self.test_root)
        self.transport_root.start()
        self.addCleanup(self.transport_root.stop)
        # Existing App transport uses its script root for directory ownership.
        from protocol.studio_shy_esptool import entry
        self.script_root = patch.object(entry.parent.transport, "ROOT", self.test_root)
        self.script_root.start()
        self.addCleanup(self.script_root.stop)
        self.runner = CommandRunner(self.snapshot)
        self.assertEqual(self.runner.identity.digest, baseline.IDENTITY_SHA)
        self.folder = installer.folder_for("e3f1-app-20261109-test")
        self.source = self.test_root / "tmp/e145-assets-20261109-test"
        self.source.mkdir()
        plan = resources.prepare(self.resource.snapshot, self.resource.image, baseline.IDENTITY_SHA,
                                 mode="live", baseline="E3d-2b")
        resources.persist(self.source, plan)
        resources.Journal(self.source, plan).save("installed", readback_sha256=app.sha(self.snapshot))
        self.factory = Mock(side_effect=lambda folder, identity, **kw:
                            ShyDevice(folder, identity, runner=self.runner, **kw))

    def prepare(self):
        return installer.run("prepare-live", self.folder, resource_folder=self.source, device_factory=self.factory)

    def call(self, phase):
        plan = app.load_recovery(self.folder)
        return installer.run(phase, self.folder, confirm_plan=plan.id, device_factory=self.factory)

    def test_prepare_install_and_candidate_independent_recovery(self):
        prepared = self.prepare()
        self.assertEqual((prepared["mode"], self.runner.writes), ("live", []))
        self.assertEqual(installer.run("check", self.folder)["plan_id"], prepared["plan_id"])
        result = self.call("install")
        self.assertEqual(result["result"], "candidate_installed")
        self.assertEqual(app.sha(bytes(self.runner.data[app.START:app.START + release.APP_BYTES])), release.APP_SHA)
        self.runner.data[0x9000] ^= 1
        (self.folder / "candidate-app.bin").unlink()
        with patch.object(release, "inputs", side_effect=AssertionError("candidate read during recovery")), \
             patch.object(installer, "_resource_source", side_effect=AssertionError("source read during recovery")):
            self.assertEqual(self.call("recover")["result"], "original_restored")
            self.assertEqual(self.call("recover")["result"], "original_restored")
        expected = bytearray(self.snapshot)
        expected[0x9000] ^= 1
        self.assertEqual(bytes(self.runner.data), bytes(expected))
        self.assertEqual(len(self.runner.writes), 3)

    def test_faults_restore_the_original_App(self):
        for failure in ("write", "readback", "boot"):
            with self.subTest(failure=failure):
                self.folder = installer.folder_for("e3f1-app-20261109-" + failure)
                self.runner = CommandRunner(self.snapshot)
                self.prepare()
                if failure == "write":
                    self.runner.fail_writes = {1}
                elif failure == "readback":
                    self.runner.corrupt_readback = True
                else:
                    self.runner.fail_boots = {1}
                with self.assertRaises((OSError, ValueError)):
                    self.call("install")
                self.assertEqual(bytes(self.runner.data), self.snapshot)
                self.assertEqual(installer.Journal(self.folder, app.load_recovery(self.folder)).load()["phase"], "restored")

    def test_interrupted_recovery_resumes_without_candidate(self):
        self.prepare()
        self.runner.fail_writes = {1, 2}
        with self.assertRaises(OSError):
            self.call("install")
        self.assertEqual(installer.Journal(self.folder, app.load_recovery(self.folder)).load()["phase"], "needs_recovery")
        (self.folder / "candidate-app.bin").unlink()
        self.call("recover")
        self.assertEqual(bytes(self.runner.data), self.snapshot)

    def test_offline_prepare_and_check_do_not_load_transport_or_allow_install(self):
        with patch.object(installer, "_factory", side_effect=AssertionError("transport loaded")):
            prepared = installer.run("prepare-offline", self.folder)
            self.assertEqual(installer.run("check", self.folder)["mode"], "offline")
            for phase in ("install", "recover"):
                with self.assertRaisesRegex(ValueError, "matching_live_shy_plan"):
                    installer.run(phase, self.folder, confirm_plan=prepared["plan_id"])
        self.factory.assert_not_called()
        self.assertEqual(self.runner.calls, [])

    def test_source_must_be_live_installed_and_same_device(self):
        original = json.loads((self.source / "resource-state.json").read_bytes())
        for change in ({"phase": "restored"}, {"readback_sha256": "0" * 64}):
            (self.source / "resource-state.json").write_bytes(app.canonical({**original, **change}))
            with self.assertRaisesRegex(ValueError, "installed_live_E1.45"):
                self.prepare()
        self.factory.assert_not_called()

    def test_old_resources_and_changed_static_bytes_abort_preparation(self):
        for offset in (resources.START, 0x800000):
            self.runner = CommandRunner(self.snapshot)
            self.runner.data[offset] ^= 1
            self.folder = installer.folder_for("e3f1-app-20261109-bad" + hex(offset)[2:])
            with self.assertRaisesRegex(ValueError, "E1.45_must_be_installed|outside_resource_bytes_changed"):
                self.prepare()
            self.assertEqual(self.runner.writes, [])
            self.assertEqual(self.runner.boots, 1)

    def test_current_nvs_is_saved_at_live_preparation(self):
        self.runner.data[0x9000] ^= 1
        self.prepare()
        self.assertEqual(app.load(self.folder).snapshot[0x9000], self.runner.data[0x9000])
        self.call("install")
        self.call("recover")
        self.assertEqual(self.runner.data[0x9000], self.snapshot[0x9000] ^ 1)

    def test_stale_nvs_or_static_change_rejects_install_before_write(self):
        self.prepare()
        for offset in (0x9000, 0x800000):
            self.runner.data[offset] ^= 1
            with self.assertRaises((OSError, ValueError)):
                self.call("install")
            self.assertEqual(self.runner.writes, [])
            self.runner.data[offset] ^= 1
        self.assertEqual(len(list(self.folder.glob("install-result-*.json"))), 2)

    def test_wrong_confirmation_and_duplicate_preparation_do_not_construct_device(self):
        self.prepare()
        self.factory.reset_mock()
        with self.assertRaisesRegex(FileExistsError, "choose_new"):
            self.prepare()
        with self.assertRaisesRegex(ValueError, "matching_live_shy_plan"):
            installer.run("install", self.folder, confirm_plan="0" * 64, device_factory=self.factory)
        self.factory.assert_not_called()

    def test_unprepared_recovery_does_not_construct_device(self):
        self.prepare()
        self.factory.reset_mock()
        with self.assertRaisesRegex(ValueError, "recovery_state"):
            self.call("recover")
        self.factory.assert_not_called()

    def test_transport_initialization_failure_is_recorded_and_can_retry(self):
        self.prepare()
        calls = len(self.runner.calls)
        with patch.object(installer, "_factory", side_effect=OSError("transport unavailable")):
            with self.assertRaisesRegex(OSError, "transport unavailable"):
                installer.run("install", self.folder, confirm_plan=app.load_recovery(self.folder).id)
        self.assertEqual(len(self.runner.calls), calls)
        record = json.loads((self.folder / "install-result-001.json").read_bytes())
        self.assertEqual((record["result"], record["error_type"], record["state"]), ("failed", "OSError", None))
        self.assertEqual(self.call("install")["result"], "candidate_installed")
        self.assertTrue((self.folder / "install-result-002.json").is_file())

    def test_range_intent_and_write_switch_are_enforced_by_transport(self):
        self.prepare()
        plan = app.load(self.folder)
        disabled = ShyDevice(self.folder, baseline.IDENTITY_SHA, plan=plan, runner=self.runner)
        for method, args in ((disabled.write_flash, (0x1000000, b"x")),
                             (disabled.write_app, (plan.candidate,))):
            with self.assertRaises((ValueError, PermissionError)):
                method(*args)
        installer.Journal(self.folder, plan).save("restore_required")
        with self.assertRaises(PermissionError):
            disabled.write_app(plan.candidate)
        enabled = self.factory(self.folder, baseline.IDENTITY_SHA, plan=plan, write_enabled=True)
        with self.assertRaises(ValueError):
            enabled.write_app(plan.candidate[:-1])
        self.assertEqual(self.runner.writes, [])

    def test_failed_durable_write_intent_leaves_original_App_running(self):
        self.prepare()
        save = installer.Journal.save

        def fail_intent(journal, phase, **details):
            if phase == "restore_required":
                raise OSError("injected journal persistence failure")
            return save(journal, phase, **details)

        with patch.object(installer.Journal, "save", fail_intent):
            with self.assertRaisesRegex(OSError, "journal persistence"):
                self.call("install")
        self.assertEqual(self.runner.writes, [])
        self.assertEqual(self.runner.boots, 1)
        self.assertEqual(installer.Journal(self.folder, app.load_recovery(self.folder)).load()["phase"], "prepared")
        result_path = next(self.folder.glob("install-result-*.json"))
        self.assertEqual(json.loads(result_path.read_bytes())["error_type"], "OSError")
        self.assertEqual(self.call("resume-original")["result"], "original_started")
        self.assertEqual(self.runner.writes, [])

    def test_invalid_paths_and_receipts_fail_before_device_access(self):
        for label in ("../outside", "e3f1-app-20261109-../out", "e3c-live-20261109-test"):
            with self.assertRaises(ValueError):
                installer.folder_for(label)
        self.prepare()
        receipt_path = self.folder / "installation.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["plan_id"] = "0" * 64
        receipt_path.write_bytes(app.canonical(receipt))
        self.factory.reset_mock()
        with self.assertRaisesRegex(ValueError, "receipt_changed"):
            self.call("install")
        self.factory.assert_not_called()

    def test_cli_arguments_route_to_the_same_installer(self):
        with patch.object(self.cli.installer, "run", return_value={"result": "checked"}) as run:
            self.cli.main(["check", "--run", self.folder.name])
            self.assertEqual(run.call_args.args, ("check", self.folder))
        for argv in (["install", "--run", self.folder.name],
                     ["prepare-live", "--run", self.folder.name],
                     ["check", "--run", self.folder.name, "--confirm-plan", "0" * 64]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.cli.main(argv)

    def test_live_preparation_keeps_download_mode_and_handles_runtime_nvs_at_boot(self):
        self.runner.update_nvs_on_boot = True
        self.prepare()
        self.assertEqual(self.runner.boots, 0)
        self.call("install")
        self.assertEqual(self.runner.boots, 1)
        self.call("recover")
        self.assertEqual(self.runner.writes[1][1], app.load_recovery(self.folder).rollback)
        # Recovery preserves NVS at the write boundary; the following normal
        # boot may update it again and does not write any old NVS page.
        self.assertEqual(bytes(self.runner.data[resources.START:resources.START + resources.SIZE]), self.resource.image)

    def test_resume_original_and_later_prepare_refreshes_nvs(self):
        self.prepare()
        self.runner.update_nvs_on_boot = True
        self.assertEqual(self.call("resume-original")["result"], "original_started")
        self.assertEqual(self.runner.writes, [])
        self.folder = installer.folder_for("e3f1-app-20261109-refreshed")
        self.prepare()
        self.assertEqual(app.load_recovery(self.folder).snapshot[0x9000], self.runner.data[0x9000])

    def test_real_cli_parser_to_transport_round_trip(self):
        with patch.object(self.cli, "ROOT", self.test_root), \
             patch.object(installer, "_factory", return_value=self.factory):
            prepared = self.cli.main(["prepare-live", "--run", self.folder.name,
                                      "--resource-run", self.source.name])
            self.assertEqual(self.cli.main(["check", "--run", self.folder.name])["mode"], "live")
            argv = ["--run", self.folder.name, "--confirm-plan", prepared["plan_id"]]
            self.assertEqual(self.cli.main(["install", *argv])["result"], "candidate_installed")
            self.assertEqual(self.cli.main(["recover", *argv])["result"], "original_restored")
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(len(self.runner.writes), 2)


if __name__ == "__main__":
    unittest.main()
