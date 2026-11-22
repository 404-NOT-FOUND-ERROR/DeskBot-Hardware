"""Offline tests for the role release transaction and COM4 command gates."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import Mock, patch

from protocol import studio_app_deployment as app
from protocol import studio_resource_installation as resources
from protocol import studio_role_release_e3f9d as release
from protocol.studio_role_resources_e3f9d_esptool import ResourceDevice
from protocol.storage_upper_experiment_esptool import CAPTURE_SCRIPT, EsptoolDevice, Runner, _NORMAL_BOOT_MARKERS, parse_identity
from tests.test_studio_resource_adapter import FLASH_LOG, SECURITY_LOG, TRANSFER_LOG

ROOT = release.ROOT
APP_IMAGE = ROOT / release.SOURCE
if not APP_IMAGE.is_file():
    # Until the paired firmware build completes, exercise the transaction using
    # a real 32 MiB App fixture and test-local profile constants.
    APP_IMAGE = ROOT / "tmp/studio-role-player-e3f9c/on/xiaozhi.bin"
RESOURCE_IMAGE = ROOT / release.RESOURCE
OLD_RESOURCE_SNAPSHOT = ROOT / "tmp/resource-read-20261108-live1/snapshot-32m.bin"
BOOT_REFERENCE = ROOT / "tmp/resource-read-20261108-live1/captures/080-catalog-restored.log"


def load_cli():
    spec = importlib.util.spec_from_file_location("role_release_test_cli", ROOT / "scripts/deploy-studio-roles-e3f9d.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def set_app_profile():
    image = APP_IMAGE.read_bytes()
    facts = release.image_facts(image)
    start = facts["segments"][0]["file_offset"]
    return patch.multiple(release, APP_SIZE=len(image), APP_SHA=app.sha(image),
                          APP_ELF_SHA=image[start + 144:start + 176].hex())


class FakeRunner:
    """Memory-backed esptool/capture boundary; no real process or serial port."""
    def __init__(self, data=None, *, mac="02:00:00:43:c2:01"):
        self.data = bytearray(data or b"")
        self.mac = mac
        self.calls, self.writes = [], []
        self.fail_writes, self.fail_boots = set(), set()
        self.boots = 0
        self.boot_extra = ""
        self.transfer_log = TRANSFER_LOG
        saved = next(line.split("DeskBotLink: ", 1)[1]
                     for line in BOOT_REFERENCE.read_text(encoding="utf-8").splitlines()
                     if "DeskBotLink: " in line)
        self.hello = json.loads(saved)

    def boot_log(self):
        self.boots += 1
        current = bytes(self.data[app.START:app.START + release.APP_SIZE])
        if app.sha(current) != release.APP_SHA:
            current = bytes(self.data[app.START:app.START + release.ORIGINAL_SIZE])
        start = release.image_facts(current)["segments"][0]["file_offset"]
        firmware = dict(version=current[start + 16:start + 48].split(b"\0")[0].decode("ascii"),
                        app_elf_sha256=current[start + 144:start + 176].hex())
        frames = []
        for index in range(4):
            hello = copy.deepcopy(self.hello)
            hello.update(sequence=index, uptime_ms=1105 + index * 10000,
                         boot_id="boot-%032x" % self.boots)
            hello["device_id"] = "device-" + hashlib.sha256(
                b"deskbot-link-v1\0" + bytes.fromhex(self.mac.replace(":", ""))).digest()[:16].hex()
            hello["firmware"].update(firmware)
            frames.append("I (%d) DeskBotLink: %s" % (hello["uptime_ms"], json.dumps(hello)))
        extra = self.boot_extra + ("\nGuru Meditation\n" if self.boots in self.fail_boots else "")
        return "\n".join((*_NORMAL_BOOT_MARKERS, "USB input ready: /dev/secondary", *frames)) + "\n" + extra

    def run(self, argv, *, timeout):
        self.calls.append(list(argv))
        if argv[argv.index("--port") + 1] != "COM4":
            raise AssertionError("test transport changed port")
        if str(CAPTURE_SCRIPT) in argv:
            Path(argv[argv.index("--output") + 1]).write_text(self.boot_log(), encoding="utf-8")
            return 0, "Capture complete\n"
        if "flash_id" in argv:
            return 0, FLASH_LOG.replace("02:00:00:43:c2:01", self.mac)
        if "get_security_info" in argv:
            return 0, SECURITY_LOG.replace("02:00:00:43:c2:01", self.mac)
        output = self.transfer_log.replace("02:00:00:43:c2:01", self.mac)
        if "read_flash" in argv:
            start, size = int(argv[-3], 0), int(argv[-2], 0)
            Path(argv[-1]).write_bytes(self.data[start:start + size])
            return 0, output
        if "write_flash" in argv:
            start, raw = int(argv[-2], 0), Path(argv[-1]).read_bytes()
            self.writes.append((start, raw))
            if len(self.writes) in self.fail_writes:
                self.data[start:start + len(raw) // 2] = raw[:len(raw) // 2]
                return 1, output + "interrupted write\n"
            self.data[start:start + len(raw)] = raw
            return 0, output + "Hash of data verified\n"
        if "verify_flash" in argv:
            pairs = argv[argv.index("verify_flash") + 7:]
            for offset, name in zip(pairs[::2], pairs[1::2]):
                start, raw = int(offset, 0), Path(name).read_bytes()
                if bytes(self.data[start:start + len(raw)]) != raw:
                    return 1, output + "verification failed\n"
                output += "-- verify OK (digest matched)\n"
            return 0, output
        raise AssertionError("unexpected fake transport command")


class RoleReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = release.load_reference()
        cls.app_raw = APP_IMAGE.read_bytes()
        cls.resource_raw = RESOURCE_IMAGE.read_bytes()
        cls.identity = parse_identity(FLASH_LOG, SECURITY_LOG).digest
        cls.static_sha = app.sha(cls.reference["snapshot"][:0x9000] + cls.reference["snapshot"][0xD000:])
        cls.resource_image = cls.resource_raw
        plan = app.prepare(cls.reference["snapshot"], cls.app_raw, cls.identity,
                           release.ORIGINAL_SIZE, edition="E3c")
        end = app.START + len(plan.candidate)
        cls.installed_snapshot = plan.snapshot[:app.START] + plan.candidate + plan.snapshot[end:]
        cls.resource_static_sha = app.sha(cls.installed_snapshot[:0x9000] + cls.installed_snapshot[0xD000:])

    def setUp(self):
        self.profile = set_app_profile()
        self.profile.start()
        self.addCleanup(self.profile.stop)
        self.base_profile = patch.multiple(release, IDENTITY_SHA=self.identity,
                                          STATIC_BASELINE_SHA=self.static_sha,
                                          RESOURCE_STATIC_SHA=self.resource_static_sha)
        self.base_profile.start()
        self.addCleanup(self.base_profile.stop)
        no_process = patch.object(Runner, "run", side_effect=AssertionError("real process in offline test"))
        no_process.start()
        self.addCleanup(no_process.stop)
        self.work = ROOT / "tmp" / ("role9d-test-" + uuid.uuid4().hex[:16])
        self.work.mkdir()
        self.addCleanup(self.remove_work)
        self.cli = load_cli()
        self.role_app_class = self.cli.RoleAppDevice

    def remove_work(self):
        if (self.work.parent.resolve() != (ROOT / "tmp").resolve() or self.work.is_symlink()
                or self.work.resolve().parent != (ROOT / "tmp").resolve()):
            raise AssertionError("test cleanup path changed")
        if self.work.exists():
            shutil.rmtree(self.work)

    def app_plan(self):
        with set_app_profile():
            return release.prepare_plan(self.reference["snapshot"], self.identity, self.app_raw)

    def installed_app_snapshot(self):
        plan = self.app_plan()
        end = app.START + len(plan.candidate)
        return plan.snapshot[:app.START] + plan.candidate + plan.snapshot[end:]

    def resource_plan(self, *, mode="live"):
        with set_app_profile():
            return release.prepare_resources(self.installed_app_snapshot(), self.resource_image, mode=mode)

    def app_device(self, folder, plan=None, runner=None):
        device = self.role_app_class(folder, self.identity, plan)
        device.runner = runner or self.runner
        return device

    def installed_pair(self):
        label = "e3c-live-20261112-e3f9d-" + uuid.uuid4().hex[:8]
        app_folder = self.cli.folder(label)
        resource_folder = self.cli.resource_folder(label)
        for folder in (app_folder, resource_folder):
            folder.mkdir()
            self.addCleanup(self.remove_extra, folder)
        plan = self.app_plan()
        app.persist(app_folder, plan)
        self.runner = FakeRunner(plan.snapshot)
        result = app.install(plan, self.app_device(app_folder, plan), app.Journal(app_folder, plan))
        app.write_new(app_folder / "install-result.json", app.canonical(result))
        resources_plan = release.prepare_resources(bytes(self.runner.data), self.resource_image)
        resources.persist(resource_folder, resources_plan)
        device = ResourceDevice(resource_folder, plan=resources_plan, runner=self.runner, write_enabled=True)
        resources.install(resources_plan, device, resources.Journal(resource_folder, resources_plan))
        return label, app_folder, resource_folder, plan, resources_plan

    @staticmethod
    def remove_extra(folder):
        if (folder.parent.resolve() != (ROOT / "tmp").resolve() or folder.is_symlink()
                or folder.resolve().parent != (ROOT / "tmp").resolve()
                or not folder.name.startswith("e3c-live-20261112-e3f9d-")):
            raise AssertionError("test cleanup target changed")
        if folder.exists():
            shutil.rmtree(folder)

    def coordinator(self):
        return patch.object(self.cli, "ResourceDevice", side_effect=lambda folder, **kw:
                            ResourceDevice(folder, runner=self.runner, **kw))

    def app_factory(self):
        return patch.object(self.cli.entry.base, "MenuTestDevice", side_effect=lambda folder, identity, plan=None:
                            self.app_device(folder, plan))

    def test_resource_adapter_rejects_valid_legacy_plan(self):
        old_snapshot = OLD_RESOURCE_SNAPSHOT.read_bytes()
        old_image = (ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin").read_bytes()
        legacy = resources.prepare(old_snapshot, old_image, self.identity, mode="live", baseline="E3d-2b")
        with self.assertRaisesRegex(ValueError, "profile"):
            ResourceDevice(self.work, plan=legacy, runner=FakeRunner(old_snapshot), write_enabled=True)

    def test_role_resource_plan_remains_compatible_with_resource_transaction(self):
        plan = self.resource_plan()
        self.assertIsInstance(plan, resources.Plan)
        self.assertEqual(resources.check(plan)["bytes"], resources.SIZE)
        resources.persist(self.work, plan)
        recovery = release.load_resources(self.work, recovery=True)
        self.assertIsInstance(recovery, resources.RecoveryPlan)
        self.assertEqual(recovery.rollback, plan.rollback)

    def test_resource_recovery_load_does_not_require_candidate_image(self):
        plan = self.resource_plan()
        resources.persist(self.work, plan)
        (self.work / "candidate-assets-8m.bin").unlink()
        recovery = release.load_resources(self.work, recovery=True)
        self.assertEqual(recovery.id, plan.id)
        with self.assertRaises(FileNotFoundError):
            release.load_resources(self.work)

    def test_resource_write_requires_saved_plan_journal_and_exact_arguments(self):
        plan = self.resource_plan()
        resources.persist(self.work, plan)
        runner = FakeRunner(plan.snapshot)
        device = ResourceDevice(self.work, plan=plan, runner=runner, write_enabled=True)
        state = resources.Journal(self.work, plan)
        payload = self.work / "payloads"
        payload.mkdir()
        image_path = payload / "role-assets-8m.bin"
        image_path.write_bytes(plan.image)
        good = ("write_flash", "--verify", "--no-progress", "--flash_mode", "keep",
                "--flash_freq", "keep", "--flash_size", "keep", hex(resources.START), str(image_path))
        with self.assertRaisesRegex(ValueError, "intent"):
            device._invoke("blocked-write", good)
        state.save("write_intent")
        altered = good[:-2] + (hex(resources.START + 0x1000), str(image_path))
        with self.assertRaisesRegex(ValueError, "arguments"):
            device._invoke("blocked-range", altered)
        self.assertEqual(runner.calls, [])

    def test_transfer_requires_exact_single_mac(self):
        plan = self.resource_plan()
        resources.persist(self.work, plan)
        runner = FakeRunner(plan.snapshot)
        device = ResourceDevice(self.work, plan=plan, runner=runner)
        device._identity = parse_identity(FLASH_LOG, SECURITY_LOG)
        captures = self.work / "captures"
        captures.mkdir()
        path = captures / "read.bin"
        args = ("read_flash", "--no-progress", "--flash_size", "keep", "0x0", "0x1000", str(path))
        def missing_mac(argv, *, timeout):
            runner.calls.append(list(argv))
            return 0, "Serial port COM4\n"
        runner.run = missing_mac
        with self.assertRaisesRegex(ValueError, "identity"):
            device._invoke("missing-mac", args)
        self.assertEqual(len(runner.calls), 1)

    def test_app_recovery_plan_rejects_changed_static_snapshot_bytes(self):
        plan = self.app_plan()
        bad_snapshot = bytearray(plan.snapshot)
        bad_snapshot[0x1000] ^= 1
        metadata = dict(plan.metadata)
        metadata["snapshot_sha256"] = app.sha(bytes(bad_snapshot))
        forged = app.RecoveryPlan(metadata, bytes(bad_snapshot))
        metadata["rollback_sha256"] = app.sha(forged.rollback)
        forged = app.RecoveryPlan(metadata, bytes(bad_snapshot))
        with self.assertRaises(ValueError):
            with set_app_profile():
                release.check_plan(forged, candidate=False)

    def test_recovery_coordinator_restores_resources_before_app(self):
        label = "e3c-live-20261112-e3f9d-" + uuid.uuid4().hex[:8]
        app_folder = ROOT / "tmp" / label
        resource_folder = ROOT / "tmp" / (label + "-resources")
        app_folder.mkdir()
        resource_folder.mkdir()
        self.addCleanup(self.remove_extra, app_folder)
        self.addCleanup(self.remove_extra, resource_folder)
        (resource_folder / "resource-state.json").write_text("{}", encoding="utf-8")
        events = []
        recovery = release.ResourceRecovery({}, b"")
        journal = Mock()
        journal.load.return_value = {"phase": "installed"}
        # CLI module is imported below to keep its global legacy bindings isolated.
        import importlib.util
        spec = importlib.util.spec_from_file_location("role_release_test_cli", ROOT / "scripts/deploy-studio-roles-e3f9d.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with patch.object(cli.reviewed, "load_resources", return_value=recovery), \
             patch.object(cli.resources, "Journal", return_value=journal), \
             patch.object(cli, "ResourceDevice", side_effect=lambda *a, **k: object()), \
             patch.object(cli.resources, "recover", side_effect=lambda *a: events.append("resources")), \
             patch.object(cli.entry.base, "run", side_effect=lambda *a, **k: events.append("app") or {"result": "restored"}):
            cli.recover(label)
        self.assertEqual(events, ["resources", "app"])

    def test_installs_app_before_resources_and_recovers_resources_before_app_without_candidates(self):
        label, app_folder, resource_folder, app_plan, resource_plan = self.installed_pair()
        self.assertEqual(bytes(self.runner.data[app.START:app.START + len(app_plan.candidate)]),
                         app_plan.candidate)
        self.assertEqual(bytes(self.runner.data[resources.START:resources.START + resources.SIZE]),
                         resource_plan.image)
        self.assertEqual([(offset, len(raw)) for offset, raw in self.runner.writes],
                         [(app.START, len(app_plan.candidate)), (resources.START, resources.SIZE)])

        (app_folder / "candidate-app.bin").unlink()
        (resource_folder / "candidate-assets-8m.bin").unlink()
        with patch.object(self.cli.reviewed, "check_build_evidence",
                          side_effect=AssertionError("recovery reopened build inputs")), \
             patch.object(self.cli.reviewed, "load_reference",
                          side_effect=AssertionError("recovery reopened historical reference")), \
             patch.object(self.cli.reviewed, "reviewed_candidate",
                          side_effect=AssertionError("recovery reopened candidate")), \
             self.coordinator(), self.app_factory():
            result = self.cli.recover(label)
        self.assertEqual(result["result"], "original_restored")
        self.assertEqual(self.cli.resources.Journal(resource_folder,
                         self.cli.reviewed.load_resources(resource_folder, recovery=True)).load()["phase"], "restored")
        self.assertEqual(bytes(self.runner.data), app_plan.snapshot)
        self.assertEqual([(offset, len(raw)) for offset, raw in self.runner.writes],
                         [(app.START, len(app_plan.candidate)), (resources.START, resources.SIZE),
                          (resources.START, resources.SIZE), (app.START, len(app_plan.rollback))])

    def test_app_recovery_stops_before_write_when_role_resources_are_still_installed(self):
        label, app_folder, resource_folder, app_plan, resource_plan = self.installed_pair()
        recovery_plan = app.load_recovery(app_folder)
        journal = app.Journal(app_folder, recovery_plan)
        before = len(self.runner.writes)
        device = self.app_device(app_folder, recovery_plan, self.runner)
        with self.assertRaises((OSError, ValueError)):
            app.recover(recovery_plan, device, journal)
        self.assertEqual(len(self.runner.writes), before)
        self.assertEqual(self.cli.resources.Journal(resource_folder,
                         self.cli.reviewed.load_resources(resource_folder, recovery=True)).load()["phase"], "installed")

    def test_recovery_does_not_attempt_app_if_resource_restore_fails(self):
        label = "e3c-live-20261112-e3f9d-" + uuid.uuid4().hex[:8]
        resource_folder = self.cli.resource_folder(label)
        app_folder = self.cli.folder(label)
        app_folder.mkdir(); resource_folder.mkdir()
        self.addCleanup(self.remove_extra, app_folder)
        self.addCleanup(self.remove_extra, resource_folder)
        (resource_folder / "resource-state.json").write_text("{}", encoding="utf-8")
        events = []
        journal = Mock(); journal.load.return_value = {"phase": "installed"}
        recovery_plan = release.ResourceRecovery({}, b"")
        with patch.object(self.cli.reviewed, "load_resources", return_value=recovery_plan), \
             patch.object(self.cli.resources, "Journal", return_value=journal), \
             patch.object(self.cli, "ResourceDevice", return_value=object()), \
             patch.object(self.cli.resources, "recover", side_effect=OSError("resource restore failed")), \
             patch.object(self.cli.entry.base, "run", side_effect=lambda *a, **k: events.append("app")):
            with self.assertRaisesRegex(OSError, "resource restore failed"):
                self.cli.recover(label)
        self.assertEqual(events, [])

    def test_resource_partial_write_and_boot_failure_restore_the_exact_original(self):
        for failure in ("write", "boot"):
            with self.subTest(failure=failure):
                folder = self.cli.folder("e3c-live-20261112-e3f9d-" + uuid.uuid4().hex[:8])
                folder.mkdir(); self.addCleanup(self.remove_extra, folder)
                plan = self.resource_plan()
                resources.persist(folder, plan)
                runner = FakeRunner(plan.snapshot)
                runner.fail_writes = {1} if failure == "write" else set()
                runner.fail_boots = {1} if failure == "boot" else set()
                device = ResourceDevice(folder, plan=plan, runner=runner, write_enabled=True)
                journal = resources.Journal(folder, plan)
                with self.assertRaises((OSError, ValueError)):
                    resources.install(plan, device, journal)
                self.assertEqual(bytes(runner.data), plan.snapshot)
                self.assertEqual(journal.load()["phase"], "restored")
                self.assertEqual(runner.writes, [(resources.START, plan.image), (resources.START, plan.rollback)])

    def test_app_partial_write_and_boot_failure_restore_the_exact_original(self):
        for failure in ("write", "boot"):
            with self.subTest(failure=failure):
                folder = self.cli.folder("e3c-live-20261112-e3f9d-" + uuid.uuid4().hex[:8])
                folder.mkdir(); self.addCleanup(self.remove_extra, folder)
                plan = self.app_plan()
                app.persist(folder, plan)
                runner = FakeRunner(plan.snapshot)
                runner.fail_writes = {1} if failure == "write" else set()
                runner.fail_boots = {1} if failure == "boot" else set()
                device = self.app_device(folder, plan, runner)
                journal = app.Journal(folder, plan)
                with self.assertRaises((OSError, ValueError)):
                    app.install(plan, device, journal)
                self.assertEqual(bytes(runner.data), plan.snapshot)
                self.assertEqual(journal.load()["phase"], "restored")
                self.assertEqual(runner.writes, [(app.START, plan.candidate), (app.START, plan.rollback)])

    def test_resource_recovery_preserves_current_nvs_and_uses_new_evidence_sequence(self):
        plan = self.resource_plan()
        resources.persist(self.work, plan)
        runner = FakeRunner(plan.snapshot)
        device = ResourceDevice(self.work, plan=plan, runner=runner, write_enabled=True)
        journal = resources.Journal(self.work, plan)
        resources.install(plan, device, journal)
        runner.data[0x9000] ^= 0x55
        expected_nvs = bytes(runner.data[0x9000:0xD000])
        (self.work / "candidate-assets-8m.bin").unlink()
        recovery = release.load_resources(self.work, recovery=True)
        restored_device = ResourceDevice(self.work, plan=recovery, runner=runner, write_enabled=True)
        sequence = restored_device._sequence
        resources.recover(recovery, restored_device, resources.Journal(self.work, recovery))
        self.assertGreater(restored_device._sequence, sequence)
        expected = plan.snapshot[:0x9000] + expected_nvs + plan.snapshot[0xD000:]
        self.assertEqual(bytes(runner.data), expected)

    def test_resource_write_rejects_read_only_offline_and_non_write_journal_phases(self):
        plan = self.resource_plan()
        resources.persist(self.work, plan)
        runner = FakeRunner(plan.snapshot)
        with self.assertRaises(ValueError):
            ResourceDevice(self.work, plan=plan, runner=runner).write_assets(plan.image)
        with self.assertRaises(ValueError):
            ResourceDevice(self.work, plan=self.resource_plan(mode="offline"), runner=runner, write_enabled=True)
        device = ResourceDevice(self.work, plan=plan, runner=runner, write_enabled=True)
        for phase in ("candidate_verified", "installed", "needs_recovery", "restored"):
            resources.Journal(self.work, plan).save(phase)
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                device.write_assets(plan.image)
        self.assertEqual(runner.calls, [])

    def test_app_write_requires_durable_journal_and_exact_payload(self):
        plan = self.app_plan()
        app.persist(self.work, plan)
        runner = FakeRunner(plan.snapshot)
        device = self.app_device(self.work, plan, runner)
        journal = app.Journal(self.work, plan)
        with self.assertRaises(ValueError):
            device.write_app(plan.candidate)
        for phase in ("prepared", "candidate_verified", "installed", "restored"):
            journal.save(phase)
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                device.write_app(plan.candidate)
        journal.save("restore_required")
        with self.assertRaises(ValueError):
            device.write_app(plan.candidate[:-1])
        self.assertEqual(runner.calls, [])

    def test_both_transports_reject_changed_command_geometry_before_runner(self):
        plan = self.resource_plan()
        runner = FakeRunner(plan.snapshot)
        devices = [ResourceDevice(self.work, plan=plan, runner=runner),
                   self.app_device(self.work, self.app_plan(), runner)]
        path = self.work / "captures" / "test.bin"
        cases = [("erase_flash",), ("erase_region", "0x0", "0x1000"),
                 ("read_flash", "--no-progress", "--flash_size", "keep", "0x0", "0x100001", str(path)),
                 ("read_flash", "--no-progress", "--flash_size", "keep", "0x2000000", "0x1000", str(path)),
                 ("read_flash", "--no-progress", "--flash_size", "32MB", "0x0", "0x1000", str(path)),
                 ("read_flash", "--no-progress", "--flash_size", "keep", "0x0", "0x1000", str(path), "--force"),
                 ("write_flash", "0x9000", str(path)),
                 ("verify_flash", "--flash_mode", "keep", "--flash_size", "32MB", "--flash_freq", "keep", "0x0", str(path))]
        for device in devices:
            for args in cases:
                with self.subTest(device=type(device).__name__, args=args), self.assertRaises(ValueError):
                    device._invoke("blocked-command", args)
        self.assertEqual(runner.calls, [])

    def test_both_transports_reject_missing_wrong_or_multiple_mac_for_reads_and_verifies(self):
        plan = self.resource_plan()
        runner = FakeRunner(plan.snapshot)
        identity = parse_identity(FLASH_LOG, SECURITY_LOG)
        captures, payloads = self.work / "captures", self.work / "payloads"
        captures.mkdir(); payloads.mkdir()
        reference = payloads / "full-reference.bin"
        reference.write_bytes(plan.snapshot)
        read = ("read_flash", "--no-progress", "--flash_size", "keep", "0x0", "0x1000", str(captures / "read.bin"))
        verify = ("verify_flash", "--flash_mode", "keep", "--flash_size", "keep", "--flash_freq", "keep", "0x0", str(reference))
        devices = [ResourceDevice(self.work, plan=plan, runner=runner),
                   self.app_device(self.work, self.app_plan(), runner)]
        outputs = ["Serial port COM4\n", TRANSFER_LOG.replace("c2:01", "c2:02"),
                   TRANSFER_LOG + "MAC: 02:00:00:43:c2:02\n"]
        for device in devices:
            # These represent two successive owners of one run, so refresh the
            # second adapter's evidence counter after the first owner's calls.
            device._sequence = device._last_evidence_sequence()
            device._identity = identity
            for output in outputs:
                runner.transfer_log = output
                for args in (read, verify):
                    with self.subTest(device=type(device).__name__, output=output, operation=args[0]), self.assertRaises(ValueError):
                        device._invoke("bad-transfer-identity", args)

    def test_resource_full_snapshot_verification_detects_unread_static_changes(self):
        plan = self.resource_plan()
        runner = FakeRunner(plan.snapshot)
        runner.data[0xB00000] ^= 1
        device = ResourceDevice(self.work, reference=plan.snapshot, runner=runner)
        with self.assertRaises(OSError):
            device.read_full()
        self.assertEqual(runner.writes, [])

    def test_legacy_resource_loader_cannot_open_new_local_schema(self):
        plan = self.resource_plan()
        resources.persist(self.work, plan)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            resources.load(self.work, recovery=True)

    def test_resource_plan_rejects_recomputed_static_snapshot_corruption(self):
        plan = self.resource_plan()
        changed = bytearray(plan.snapshot)
        changed[0x1000] ^= 1
        metadata = dict(plan.metadata, snapshot_sha256=app.sha(bytes(changed)))
        recovery = release.ResourceRecovery(metadata, bytes(changed))
        with self.assertRaises(ValueError):
            recovery.validate()

    def test_restart_failure_uses_coordinated_recovery(self):
        label, app_folder, resource_folder, app_plan, resource_plan = self.installed_pair()
        self.runner.fail_boots = {self.runner.boots + 1}
        with self.coordinator(), self.app_factory(), \
             patch.object(self.cli, "RoleAppDevice", side_effect=lambda folder, identity, plan=None:
                          self.app_device(folder, plan)):
            with self.assertRaises(ValueError):
                self.cli.run("restart", label)
        self.assertEqual(bytes(self.runner.data), app_plan.snapshot)
        self.assertEqual([offset for offset, raw in self.runner.writes],
                         [app.START, resources.START, resources.START, app.START])

    def test_identity_drift_blocks_resource_write_before_flash_changes(self):
        plan = self.resource_plan()
        resources.persist(self.work, plan)
        resources.Journal(self.work, plan).save("write_intent")
        runner = FakeRunner(plan.snapshot, mac="02:00:00:43:c2:02")
        device = ResourceDevice(self.work, plan=plan, runner=runner, write_enabled=True)
        with self.assertRaises(ValueError):
            device.write_assets(plan.image)
        self.assertEqual(runner.writes, [])

    def test_importing_role_cli_leaves_original_entry_bindings_unchanged(self):
        spec = importlib.util.spec_from_file_location("original_eat_binding_test", ROOT / "scripts/deploy-studio-eat.py")
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        self.assertIs(self.cli.entry.base.reviewed, release)
        self.assertEqual(old.reviewed.EDITION, "E3f-4a")
        self.assertIs(old.base.reviewed, old.reviewed)
        self.assertIs(old.base.entry.reviewed, old.reviewed)

    def test_every_valid_run_label_has_a_matching_resource_folder(self):
        for size in (1, 14, 15, 24):
            label = "e3c-live-20261112-e3f9d-" + "a" * size
            app_folder = self.cli.folder(label)
            resource_folder = self.cli.resource_folder(label)
            self.assertEqual(resource_folder, app_folder.with_name(app_folder.name + "-resources"))
            self.assertEqual(resource_folder.resolve().parent, (ROOT / "tmp").resolve())

    def test_legacy_diagnostic_entries_are_rejected_without_transport(self):
        plan = self.resource_plan()
        runner = FakeRunner(plan.snapshot)
        resource_device = ResourceDevice(self.work, plan=plan, runner=runner)
        app_device = self.app_device(self.work, self.app_plan(), runner)
        for device in (resource_device, app_device):
            for method, args in (("read_flash", (0, 4096)), ("write_flash", (0x1000000, b"x")),
                                 ("snapshot", (object(),)), ("capture_probe", (1,))):
                with self.subTest(device=type(device).__name__, method=method), self.assertRaises(ValueError):
                    getattr(device, method)(*args)
        with self.assertRaises(ValueError):
            app_device.capture()
        self.assertEqual(runner.calls, [])

    def test_role_and_app_writes_require_transfer_mac_after_exact_plan_gates(self):
        resource_plan = self.resource_plan()
        resources.persist(self.work, resource_plan)
        resources.Journal(self.work, resource_plan).save("write_intent")
        runner = FakeRunner(resource_plan.snapshot)
        resource_device = ResourceDevice(self.work, plan=resource_plan, runner=runner, write_enabled=True)
        payloads = self.work / "payloads"; payloads.mkdir()
        path = payloads / "candidate.bin"; path.write_bytes(resource_plan.image)
        resource_args = ("write_flash", "--verify", "--no-progress", "--flash_mode", "keep",
                         "--flash_freq", "keep", "--flash_size", "keep", hex(resources.START), str(path))
        app_folder = self.cli.folder("e3c-live-20261112-e3f9d-" + uuid.uuid4().hex[:8])
        app_folder.mkdir(); self.addCleanup(self.remove_extra, app_folder)
        plan = self.app_plan()
        app.persist(app_folder, plan)
        app.Journal(app_folder, plan).save("restore_required")
        app_device = self.app_device(app_folder, plan, runner)
        app_payloads = app_folder / "payloads"; app_payloads.mkdir()
        app_path = app_payloads / "candidate.bin"; app_path.write_bytes(plan.candidate)
        app_args = ("write_flash", "--verify", "--flash_mode", "keep", "--flash_size", "keep",
                    "--flash_freq", "keep", hex(app.START), str(app_path))
        for device, args in ((resource_device, resource_args), (app_device, app_args)):
            device._identity = parse_identity(FLASH_LOG, SECURITY_LOG)
            with patch.object(EsptoolDevice, "_invoke", return_value="Serial port COM4\nHash of data verified\n"), \
                 self.assertRaisesRegex(ValueError, "identity"):
                device._invoke("missing-write-identity", args)
        self.assertEqual(runner.writes, [])


if __name__ == "__main__":
    unittest.main()
