"""Actual COM4 adapter/CLI with a fake command runner; no subprocess or USB."""
import importlib.util
from pathlib import Path
import re
import shutil
import unittest
import uuid
from unittest.mock import Mock, patch

from protocol import studio_resource_installation as r
from protocol.studio_resource_esptool import ResourceDevice
from protocol.storage_upper_experiment_esptool import CAPTURE_SCRIPT, _NORMAL_BOOT_MARKERS, parse_identity

ROOT = Path(__file__).resolve().parents[1]
FLASH_LOG = """esptool.py v4.12.0
Serial port COM4
Chip is ESP32-S3 (QFN56) (revision v0.2)
Features: WiFi, BLE, Embedded PSRAM 16MB (AP_1v8)
MAC: 02:00:00:43:c2:01
Detected flash size: 32MB
Flash type set in eFuse: octal (8 data lines)
"""
SECURITY_LOG = "esptool.py v4.12.0\nSerial port COM4\nMAC: 02:00:00:43:c2:01\nSecure Boot: Disabled\nFlash Encryption: Disabled\n"
TRANSFER_LOG = "Serial port COM4\nMAC: 02:00:00:43:c2:01\n"


class CommandRunner:
    def __init__(self, snapshot):
        self.data = bytearray(snapshot)
        self.calls, self.writes = [], []
        self.flash_log = FLASH_LOG

    def run(self, argv, *, timeout):
        self.calls.append(list(argv))
        if str(CAPTURE_SCRIPT) in argv:
            Path(argv[argv.index("--output")+1]).write_text("\n".join(_NORMAL_BOOT_MARKERS)+"\nELF file SHA256: e0941ae3c\n", encoding="utf-8")
            return 0, "Capture complete\n"
        self.assert_com4(argv)
        if "flash_id" in argv:
            return 0, self.flash_log
        if "get_security_info" in argv:
            return 0, SECURITY_LOG
        if "read_flash" in argv:
            start, size = int(argv[-3], 0), int(argv[-2], 0)
            Path(argv[-1]).write_bytes(self.data[start:start+size])
            return 0, TRANSFER_LOG
        if "write_flash" in argv:
            start, raw = int(argv[-2], 0), Path(argv[-1]).read_bytes()
            self.writes.append((start, raw))
            self.data[start:start+len(raw)] = raw
            return 0, TRANSFER_LOG + "Hash of data verified\n"
        raise AssertionError("unexpected command")

    @staticmethod
    def assert_com4(argv):
        if argv[argv.index("--port")+1] != "COM4" or any(arg in argv for arg in ("erase_flash", "erase_region", "--erase-all")):
            raise AssertionError("incorrect resource command")


class ResourceAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = (ROOT / "tmp/e143b-e3c3b-20261107-live1/snapshot-32m.bin").read_bytes()
        cls.image = (ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin").read_bytes()
        cls.identity = parse_identity(FLASH_LOG, SECURITY_LOG).digest
        spec = importlib.util.spec_from_file_location("resource_cli_tests", ROOT / "scripts/deploy-studio-resources.py")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def setUp(self):
        self.folder = self.run_folder()
        self.plan = r.prepare(self.snapshot, self.image, self.identity, mode="live")
        r.persist(self.folder, self.plan)
        self.runner = CommandRunner(self.snapshot)

    def run_folder(self, *, create=True):
        # CLI labels have a fixed alphabet. tempfile's random suffix can contain
        # underscores, so use an explicit hexadecimal label for every run.
        folder = ROOT / "tmp" / ("e145-assets-20261108-test-" + uuid.uuid4().hex[:16])
        self.addCleanup(self.remove_run, folder)
        if create:
            folder.mkdir()
        return folder

    @staticmethod
    def remove_run(folder):
        if (folder.parent.resolve() != (ROOT / "tmp").resolve()
                or re.fullmatch(r"e145-assets-20261108-test-[a-f0-9]{16}", folder.name) is None
                or folder.is_symlink()):
            raise AssertionError("test cleanup target changed")
        if folder.exists():
            if folder.resolve().parent != (ROOT / "tmp").resolve():
                raise AssertionError("test cleanup target is redirected")
            shutil.rmtree(folder)

    def device(self, *, plan=None, write_enabled=False):
        return ResourceDevice(self.folder, plan=plan or self.plan, runner=self.runner, write_enabled=write_enabled)

    def test_actual_adapter_installs_only_8m_and_recovers_without_candidate(self):
        device = self.device(write_enabled=True)
        r.install(self.plan, device, r.Journal(self.folder, self.plan))
        self.assertEqual([(offset, len(raw)) for offset, raw in self.runner.writes], [(0x1000000, 0x800000)])
        (self.folder / "candidate-assets-8m.bin").unlink()
        original = r.load(self.folder, recovery=True)
        recovery_device = self.device(plan=original, write_enabled=True)
        previous_sequence = recovery_device._sequence
        r.recover(original, recovery_device, r.Journal(self.folder, original))
        self.assertGreater(recovery_device._sequence, previous_sequence)
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(len(self.runner.writes), 2)
        self.assertTrue(all(call[call.index("--port")+1] == "COM4" for call in self.runner.calls))

    def test_default_read_only_and_offline_plan_cannot_enable_writes(self):
        device = self.device()
        with self.assertRaises(PermissionError):
            device.write_assets(self.image)
        offline = r.prepare(self.snapshot, self.image, self.identity)
        with self.assertRaisesRegex(ValueError, "live_resource_plan"):
            self.device(plan=offline, write_enabled=True)
        self.assertEqual(self.runner.calls, [])

    def test_write_requires_persisted_intent_exact_payload_and_geometry(self):
        device = self.device(write_enabled=True)
        with self.assertRaisesRegex(ValueError, "intent"):
            device.write_assets(self.image)
        r.Journal(self.folder, self.plan).save("write_intent")
        for raw in (self.image[:-1], self.plan.rollback):
            with self.assertRaises(ValueError):
                device.write_assets(raw)
        with self.assertRaises(ValueError):
            device.write_flash(0x10000, self.image)
        self.assertEqual(self.runner.writes, [])

    def test_identity_drift_prevents_flash_write(self):
        r.Journal(self.folder, self.plan).save("write_intent")
        self.runner.flash_log = FLASH_LOG.replace("02:00:00:43:c2:01", "02:00:00:43:c2:02")
        with self.assertRaises(ValueError):
            self.device(write_enabled=True).write_assets(self.image)
        self.assertEqual(self.runner.writes, [])

    def test_cli_offline_check_and_install_gating_never_create_transport(self):
        offline = r.prepare(self.snapshot, self.image, self.identity)
        folder = self.run_folder()
        r.persist(folder, offline)
        factory = Mock()
        self.assertEqual(self.cli.run("check", folder, device_factory=factory)["mode"], "offline")
        with self.assertRaises(ValueError):
            self.cli.run("install", folder, confirm_plan=offline.id, device_factory=factory)
        factory.assert_not_called()

    def test_cli_live_prepare_check_install_and_candidate_independent_recovery(self):
        folder = self.run_folder(create=False)
        factory = Mock(side_effect=lambda run, **kw: ResourceDevice(run, runner=self.runner, **kw))
        prepared = self.cli.run("prepare-live", folder, identity=self.identity, device_factory=factory)
        plan = r.load(folder)
        self.assertEqual((prepared["mode"], prepared["plan_id"]), ("live", plan.id))
        self.assertEqual((self.runner.writes, bytes(self.runner.data)), ([], self.snapshot))
        self.assertEqual(sum(str(CAPTURE_SCRIPT) in call for call in self.runner.calls), 1)

        calls_before = len(self.runner.calls)
        self.assertEqual(self.cli.run("check", folder, device_factory=factory)["plan_id"], plan.id)
        with self.assertRaisesRegex(ValueError, "matching_live_resource_plan"):
            self.cli.run("install", folder, confirm_plan="0"*64, device_factory=factory)
        self.assertEqual(len(self.runner.calls), calls_before)
        self.assertEqual(factory.call_count, 1)

        installed = self.cli.run("install", folder, confirm_plan=plan.id, device_factory=factory)
        self.assertEqual(installed["result"], "candidate_resources_installed")
        expected = self.snapshot[:r.START] + self.image + self.snapshot[r.START+r.SIZE:]
        self.assertEqual(bytes(self.runner.data), expected)
        self.assertEqual(r.Journal(folder, plan).load()["phase"], "installed")

        (folder / "candidate-assets-8m.bin").unlink()
        restored = self.cli.run("recover", folder, confirm_plan=plan.id, device_factory=factory)
        self.assertEqual(restored["result"], "original_resources_restored")
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(self.runner.writes, [(r.START, self.image), (r.START, plan.rollback)])
        self.assertEqual(r.Journal(folder, plan).load()["phase"], "restored")
        self.assertEqual(sum(str(CAPTURE_SCRIPT) in call for call in self.runner.calls), 3)
        self.assertEqual(factory.call_count, 3)

    def test_windows_reparse_file_or_parent_rejected_in_python311(self):
        real = Path.lstat
        class Reparse:
            st_mode = 0
            st_file_attributes = 0x400
        for target in (self.folder, self.folder / "plan.json", self.folder / "resource-state.json"):
            def observed(path, *a, **kw):
                return Reparse() if path == target else real(path, *a, **kw)
            with patch.object(Path, "lstat", observed), self.assertRaisesRegex(ValueError, "redirected"):
                r.load(self.folder, recovery=True) if target.name != "resource-state.json" else r.Journal(self.folder, self.plan).load()


if __name__ == "__main__":
    unittest.main()
