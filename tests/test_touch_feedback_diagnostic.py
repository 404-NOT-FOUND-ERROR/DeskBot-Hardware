"""Exercise touch observation and the real App-only transport offline."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as app
from protocol import touch_feedback_diagnostic as probe
from protocol.storage_upper_experiment_esptool import Runner, _NORMAL_BOOT_MARKERS, parse_identity
from tests.test_studio_role_release_e3f9d import FakeRunner
from tests.test_studio_resource_adapter import FLASH_LOG, SECURITY_LOG

ROOT = probe.ROOT
sys.path.insert(0, str(ROOT / "scripts"))
from touch_feedback_diagnostic import BOARD, PROBE, REGISTER, START, patch_touch


def cli():
    spec = importlib.util.spec_from_file_location(
        "touch_test_cli", ROOT / "scripts/deploy-touch-feedback-diagnostic.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class ProbeRunner(FakeRunner):
    def boot_log(self):
        self.boots += 1
        image = bytes(self.data[app.START:app.START + probe.APP_SIZE])
        candidate = app.sha(image) == probe.APP_SHA
        if not candidate:
            image = bytes(self.data[app.START:app.START + probe.ORIGINAL_SIZE])
        describe = cli().parent.parent.entry.base.entry.parent.image_identity
        descriptor = describe(image)
        frames = []
        for i in range(4):
            hello = copy.deepcopy(self.hello)
            hello.update(sequence=i, uptime_ms=1105 + i * 10000,
                         boot_id="boot-%032x" % self.boots)
            hello["device_id"] = "device-" + hashlib.sha256(
                b"deskbot-link-v1\0" + bytes.fromhex(self.mac.replace(":", ""))).digest()[:16].hex()
            hello["firmware"].update(descriptor)
            frames.append(f'I ({hello["uptime_ms"]}) DeskBotLink: {json.dumps(hello)}')
        extras = ["Guru Meditation"] if self.boots in self.fail_boots else []
        if candidate:
            extras.extend((f"TouchProbe: ready period_ms=500 threshold_percent={probe.THRESHOLD_PERCENT} gesture=original",
                           "TouchProbe: pad=7 raw=30000 down=0 up=0 held=0 err=0",
                           "TouchProbe: pad=6 raw=30000 down=0 up=0 held=0 err=0",
                           getattr(self, "display_error", "")))
            if getattr(self, "missing_probe", False):
                extras = []
        return "\n".join((*_NORMAL_BOOT_MARKERS, "USB input ready: /dev/secondary", *frames, *extras))


class TouchProbeTests(unittest.TestCase):
    def test_only_adds_observation_to_original_source(self):
        original = (ROOT / "tmp/display-clock-e3f9d3/p" / BOARD).read_bytes()
        updated = patch_touch(original)
        for added in (PROBE, REGISTER, START):
            updated = updated.replace(added.encode().replace(b"\n", b"\r\n"), b"", 1)
        self.assertEqual(updated, original)
        with self.assertRaises(ValueError):
            patch_touch(original + b" ")

class TouchDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = probe.load_reference()
        cls.image = probe.reviewed_candidate()
        cls.identity = parse_identity(FLASH_LOG, SECURITY_LOG).digest

    def setUp(self):
        guard = patch.object(probe, "IDENTITY_SHA", self.identity)
        guard.start()
        self.addCleanup(guard.stop)
        process = patch.object(Runner, "run", side_effect=AssertionError("real process in offline test"))
        process.start()
        self.addCleanup(process.stop)
        temporary = tempfile.TemporaryDirectory(prefix="touch-test-", dir=ROOT / "tmp")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.plan = probe.prepare_plan(self.reference["snapshot"], self.identity, self.image)
        app.persist(self.folder, self.plan)
        self.device = cli().parent.DisplayDevice(self.folder, self.identity, self.plan)
        self.runner = ProbeRunner(self.plan.snapshot)
        self.device.runner = self.runner
        self.journal = app.Journal(self.folder, self.plan)

    def test_install_preserves_settings_and_resources(self):
        result = app.install(self.plan, self.device, self.journal)
        self.assertEqual(result["result"], "candidate_installed")
        self.assertEqual(self.runner.writes, [(app.START, self.plan.candidate)])
        expected = (self.plan.snapshot[:app.START] + self.plan.candidate
                    + self.plan.snapshot[app.START + len(self.plan.candidate):])
        self.assertEqual(bytes(self.runner.data), expected)

    def test_failed_diagnostic_initialization_restores_current_app(self):
        self.runner.missing_probe = True
        with self.assertRaisesRegex(ValueError, "touch_diagnostic_boot_incomplete"):
            app.install(self.plan, self.device, self.journal)
        self.assertEqual(bytes(self.runner.data), self.plan.snapshot)
        self.assertEqual(self.journal.load()["phase"], "restored")

    def test_recovery_uses_same_run_bytes_without_candidate(self):
        self.journal.save("restore_required")
        (self.folder / "candidate-app.bin").unlink()
        with patch.object(probe, "load_reference", side_effect=AssertionError("historical dependency")):
            recovery = probe.check_plan(app.load_recovery(self.folder), candidate=False)
            device = cli().parent.DisplayDevice(self.folder, self.identity, recovery)
            device.runner = self.runner
            self.runner.data[app.START:app.START + 32] = b"\0" * 32
            app.recover(recovery, device, self.journal)
        self.assertEqual(bytes(self.runner.data), self.plan.snapshot)

if __name__ == "__main__":
    unittest.main()
