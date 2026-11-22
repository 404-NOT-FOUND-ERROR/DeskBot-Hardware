"""Exercise the asset-layout release and real App transaction with a memory device."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_asset_layout_deployment as release
from tests.test_e3d2b_deployment import MemoryDevice

ROOT = release.ROOT
spec = importlib.util.spec_from_file_location("layout_deploy_tests", ROOT / "scripts/deploy-studio-layout-e3f4c.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class LayoutDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = release.load_reference()
        cls.image = release.reviewed_candidate()
        cls.evidence = dict(image=cls.image, candidate=dict(variant="asset-layout", bytes=release.APP_SIZE,
            sha256=release.APP_SHA, app_elf_sha256=release.APP_ELF_SHA),
            build_report_sha256=release.BUILD_REPORT_SHA, offline_report_sha256="a"*64,
            path_audit_sha256="b"*64)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="layout-deploy-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.memory = SimpleNamespace(raw=bytearray(self.reference["snapshot"]), events=[],
                                      writes=[], boots=0, fail=None)

    def live(self):
        source, target = self.folder / "offline", self.folder / "live"
        with patch.object(release, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            deploy.base.prepare_offline(source)
            deploy.base.prepare_live(target, source)
        self.assertEqual(self.memory.writes, [])
        preflight = json.loads((target / "preflight.json").read_bytes())
        self.assertEqual(preflight["backup_verification"], "fresh_prefix_and_full_device_md5")
        return target

    def factory(self, folder, identity, plan=None):
        return MemoryDevice(self.memory, folder, identity, plan)

    def run_phase(self, phase, folder):
        with patch.object(release, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            return deploy.base.run(phase, folder)

    def test_install_and_restore_exact_app_union(self):
        target = self.live()
        plan = tx.load(target)
        self.assertEqual(self.run_phase("install", target)["result"], "candidate_installed")
        tx.compare(plan, bytes(self.memory.raw), plan.candidate)
        self.assertEqual(self.run_phase("recover", target)["result"], "original_restored")
        self.assertEqual(bytes(self.memory.raw), plan.snapshot)
        self.assertEqual(self.memory.writes, [plan.candidate, plan.rollback])

    def test_write_readback_and_boot_failures_restore(self):
        for failure in ("write", "readback", "boot"):
            with self.subTest(failure=failure):
                self.folder = Path(self.temp.name) / failure
                self.folder.mkdir()
                self.memory = SimpleNamespace(raw=bytearray(self.reference["snapshot"]),
                    events=[], writes=[], boots=0, fail=None)
                target = self.live()
                self.memory.fail = failure
                with self.assertRaises((OSError, ValueError)):
                    self.run_phase("install", target)
                self.assertEqual(bytes(self.memory.raw), self.reference["snapshot"])
                self.assertEqual(tx.Journal(target, tx.load_recovery(target)).load()["phase"], "restored")

    def test_recovery_independent_of_candidate_and_build(self):
        target = self.live()
        self.run_phase("install", target)
        (target / "candidate-app.bin").unlink()
        with patch.object(release, "reviewed_candidate", side_effect=AssertionError("candidate accessed")), \
             patch.object(release, "check_build_evidence", side_effect=AssertionError("build accessed")), \
             patch.object(release, "load_reference", side_effect=AssertionError("reference accessed")), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            result = deploy.base.run("recover", target)
        self.assertEqual(result["result"], "original_restored")

    def test_recovery_preserves_current_runtime_nvs(self):
        target = self.live()
        self.run_phase("install", target)
        self.memory.raw[0x9000] ^= 1
        nvs = bytes(self.memory.raw[0x9000:0xD000])
        self.run_phase("recover", target)
        self.assertEqual(bytes(self.memory.raw[0x9000:0xD000]), nvs)
        self.assertEqual(bytes(self.memory.raw[tx.START:tx.START+release.ORIGINAL_SIZE]),
                         self.reference["snapshot"][tx.START:tx.START+release.ORIGINAL_SIZE])

    def test_stale_original_stops_before_write(self):
        target = self.live()
        self.memory.raw[tx.START + 128] ^= 1
        with self.assertRaises(ValueError):
            self.run_phase("install", target)
        self.assertEqual(self.memory.writes, [])

    def test_wrong_target_in_saved_plan_is_rejected(self):
        target = self.live()
        path = target / "preflight.json"
        value = json.loads(path.read_bytes())
        value["target"] = "base_COM8"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_phase("install", target)
        self.assertEqual(self.memory.writes, [])

    def test_recovery_rejects_changed_tail_with_recomputed_plan(self):
        snapshot = bytearray(self.reference["snapshot"])
        snapshot[tx.START + release.ORIGINAL_SIZE] ^= 1
        forged = tx.prepare(bytes(snapshot), self.image, release.IDENTITY_SHA,
                            release.ORIGINAL_SIZE, edition="E3c")
        with self.assertRaises(ValueError):
            release.check_plan(tx.RecoveryPlan(forged.metadata, forged.snapshot), candidate=False)

    def test_live_adapter_refreshes_settings_and_verifies_full_digest(self):
        device = deploy.LayoutDevice(self.folder, release.IDENTITY_SHA)
        prefix = bytearray(self.reference["snapshot"][:tx.START])
        prefix[0x9000] ^= 1
        expected = bytes(prefix) + self.reference["snapshot"][tx.START:]
        with patch.object(device, "identity", return_value=release.IDENTITY_SHA), \
             patch.object(device, "_capture", return_value=bytes(prefix)) as capture, \
             patch.object(device, "_invoke", return_value="-- verify OK (digest matched)") as invoke:
            self.assertEqual(device.read_reference(self.reference), expected)
        capture.assert_called_once_with("current-prefix", 0, tx.START)
        args = invoke.call_args.args[1]
        self.assertEqual(args[-2], "0x0")
        self.assertEqual(Path(args[-1]).read_bytes(), expected)

    def test_live_adapter_rejects_full_digest_mismatch(self):
        device = deploy.LayoutDevice(self.folder, release.IDENTITY_SHA)
        with patch.object(device, "identity", return_value=release.IDENTITY_SHA), \
             patch.object(device, "_capture", return_value=self.reference["snapshot"][:tx.START]), \
             patch.object(device, "_invoke", return_value="digest differs"), self.assertRaises(ValueError):
            device.read_reference(self.reference)
