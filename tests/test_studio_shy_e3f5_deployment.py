"""Shy release evidence and production App transactions at an in-memory device."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_shy_e3f5_deployment as release
from protocol import studio_resource_installation as resources
from tests.test_e3d2b_deployment import MemoryDevice

ROOT = release.ROOT
spec = importlib.util.spec_from_file_location("shy5_deploy_tests", ROOT / "scripts/deploy-studio-shy-e3f5.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class ShyDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = release.load_reference()
        cls.evidence = release.check_build_evidence()
        cls.image = cls.evidence["image"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shy5-deploy-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.memory = SimpleNamespace(raw=bytearray(self.reference["snapshot"]),
                                      events=[], writes=[], boots=0, fail=None)

    def factory(self, folder, identity, plan=None):
        return MemoryDevice(self.memory, folder, identity, plan)

    def live(self):
        source, target = self.folder / "offline", self.folder / "live"
        with patch.object(release, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            deploy.base.prepare_offline(source)
            deploy.base.prepare_live(target, source)
        self.assertEqual(self.memory.writes, [])
        return target

    def execute(self, phase, folder):
        with patch.object(release, "check_build_evidence", return_value=self.evidence), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            return deploy.base.run(phase, folder)

    def test_exact_e4e_reference_includes_e145_and_owner_acceptance(self):
        previous = tx.load(ROOT / release.REFERENCE)
        raw = self.reference["snapshot"]
        self.assertNotEqual(raw, previous.snapshot)
        self.assertEqual(raw[tx.START:tx.START + len(previous.candidate)], previous.candidate)
        self.assertEqual(tx.sha(raw), release.REFERENCE_SNAPSHOT_SHA)
        self.assertEqual(tx.sha(raw[resources.START:resources.START + resources.SIZE]), resources.CANDIDATE_SHA)
        self.assertEqual(tx.sha(self.reference["app_union"]), release.ORIGINAL_UNION_SHA)

    def test_install_recover_preserve_tail_resources_and_all_other_bytes(self):
        target = self.live()
        plan = tx.load(target)
        self.assertEqual(plan.metadata["erase_bytes"], release.ERASE_BYTES)
        self.assertEqual(len(plan.candidate) - len(plan.image), 224)
        self.assertEqual(plan.candidate[len(plan.image):], plan.rollback[len(plan.image):])
        self.assertEqual(self.execute("install", target)["result"], "candidate_installed")
        tx.compare(plan, bytes(self.memory.raw), plan.candidate)
        self.assertEqual(self.execute("recover", target)["result"], "original_restored")
        self.assertEqual(bytes(self.memory.raw), plan.snapshot)
        self.assertEqual(self.memory.writes, [plan.candidate, plan.rollback])
        self.assertTrue(all(row[1:] == (tx.START, release.ERASE_BYTES)
                            for row in self.memory.events if row[0] == "write"))

    def test_partial_write_readback_or_boot_failure_restores(self):
        for failure in ("write", "readback", "boot"):
            with self.subTest(failure=failure):
                self.folder = Path(self.temp.name) / failure
                self.folder.mkdir()
                self.memory = SimpleNamespace(raw=bytearray(self.reference["snapshot"]),
                                              events=[], writes=[], boots=0, fail=None)
                target = self.live()
                self.memory.fail = failure
                with self.assertRaises((OSError, ValueError)):
                    self.execute("install", target)
                self.assertEqual(bytes(self.memory.raw), self.reference["snapshot"])
                self.assertEqual(tx.Journal(target, tx.load_recovery(target)).load()["phase"], "restored")

    def test_recover_requires_only_same_run_originals(self):
        target = self.live()
        self.execute("install", target)
        (target / "candidate-app.bin").unlink()
        with patch.object(release, "reviewed_candidate", side_effect=AssertionError("candidate reopened")), \
             patch.object(release, "check_build_evidence", side_effect=AssertionError("build reopened")), \
             patch.object(release, "load_reference", side_effect=AssertionError("reference reopened")), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            self.assertEqual(deploy.base.run("recover", target)["result"], "original_restored")
        self.assertEqual(bytes(self.memory.raw), self.reference["snapshot"])

    def test_refresh_nvs_before_install_and_keep_new_nvs_on_recovery(self):
        self.memory.raw[0x9000] ^= 1
        target = self.live()
        self.assertEqual(tx.load(target).snapshot[0x9000:0xD000], bytes(self.memory.raw[0x9000:0xD000]))
        self.execute("install", target)
        self.memory.raw[0x9001] ^= 1
        nvs = bytes(self.memory.raw[0x9000:0xD000])
        self.execute("recover", target)
        self.assertEqual(bytes(self.memory.raw[0x9000:0xD000]), nvs)

    def test_changed_app_assets_or_nvs_after_preflight_stop_before_write(self):
        target = self.live()
        for offset in (tx.START + 128, resources.START + 128, 0x9000):
            with self.subTest(offset=offset):
                self.memory.raw[offset] ^= 1
                with self.assertRaises(ValueError):
                    self.execute("install", target)
                self.memory.raw[offset] ^= 1
        self.assertEqual(self.memory.writes, [])

    def test_wrong_identity_or_preflight_target_stops_before_write(self):
        target = self.live()
        self.memory.fail = "identity"
        with self.assertRaises(ValueError):
            self.execute("install", target)
        self.memory.fail = None
        preflight = target / "preflight.json"
        value = json.loads(preflight.read_bytes())
        value["target"] = "base_COM8"
        preflight.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.execute("install", target)
        self.assertEqual(self.memory.writes, [])

    def test_recomputed_plan_cannot_change_static_original_or_tail(self):
        for offset in (0, 0x8000, tx.START + 128, tx.START + release.ORIGINAL_SIZE, resources.START, tx.FLASH - 1):
            with self.subTest(offset=offset):
                raw = bytearray(self.reference["snapshot"])
                raw[offset] ^= 1
                forged = tx.prepare(bytes(raw), self.image, release.IDENTITY_SHA, release.ORIGINAL_SIZE, edition="E3c")
                with self.assertRaises(ValueError):
                    release.check_plan(tx.RecoveryPlan(forged.metadata, forged.snapshot), candidate=False)

    def test_live_reference_reads_fresh_prefix_and_checks_full_flash_digest(self):
        device = deploy.ShyDevice(self.folder, release.IDENTITY_SHA)
        prefix = bytearray(self.reference["snapshot"][:tx.START])
        prefix[0x9000] ^= 1
        with patch.object(device, "identity", return_value=release.IDENTITY_SHA), \
             patch.object(device, "_capture", return_value=bytes(prefix)) as capture, \
             patch.object(device, "_invoke", return_value="-- verify OK (digest matched)") as invoke:
            actual = device.read_reference(self.reference)
        self.assertEqual(actual, bytes(prefix) + self.reference["snapshot"][tx.START:])
        capture.assert_called_once_with("current-prefix", 0, tx.START)
        self.assertEqual(Path(invoke.call_args.args[1][-1]).read_bytes(), actual)
        with patch.object(device, "identity", return_value=release.IDENTITY_SHA), \
             patch.object(device, "_capture", return_value=bytes(prefix)), \
             patch.object(device, "_invoke", return_value="digest differs"), self.assertRaises(ValueError):
            device.read_reference(self.reference)

    def test_changed_release_reports_or_candidate_rejected(self):
        for selected in (release.BUILD_REPORT, release.OFFLINE_REPORT, release.PEER_REPORT, release.SOURCE):
            with self.subTest(path=selected):
                original = release._read
                def read(root, name):
                    raw = original(root, name)
                    return raw + b"\n" if Path(name) == selected else raw
                with patch.object(release, "_read", side_effect=read), self.assertRaises(ValueError):
                    release.check_build_evidence()

    def test_changed_install_receipt_acceptance_or_journal_rejected(self):
        for selected in (release.REFERENCE / "plan.json", release.REFERENCE / "install-result.json", release.ACCEPTANCE):
            with self.subTest(path=selected):
                original = release._read
                def read(root, name):
                    raw = original(root, name)
                    return raw + b"\n" if Path(name) == selected else raw
                with patch.object(release, "_read", side_effect=read), self.assertRaises(ValueError):
                    release.load_reference()
        with patch.object(tx.Journal, "load", return_value={"phase":"restored"}), self.assertRaises(ValueError):
            release.load_reference()

    def test_invalid_candidate_and_prepare_identity_rejected(self):
        with self.assertRaises(ValueError):
            release.prepare_plan(self.reference["snapshot"], "f" * 64, self.image)
        with self.assertRaises(ValueError):
            release.reviewed_candidate(self.image[:-1])

    def test_fresh_cli_process_uses_shy_release_and_current_rollback(self):
        code = (
            "import importlib.util; from pathlib import Path; "
            "s=importlib.util.spec_from_file_location('cli',Path('scripts/deploy-studio-shy-e3f5.py')); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "assert m.entry.reviewed is m.reviewed; assert m.base.reviewed is m.reviewed; "
            "assert m.base.entry.reviewed is m.reviewed; "
            "assert m.base.entry.ROLLBACK_TARGET=='E3f-4e'; "
            "assert m.ShyDevice is m.base.MenuTestDevice; print('bindings_verified')"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bindings_verified", result.stdout)
