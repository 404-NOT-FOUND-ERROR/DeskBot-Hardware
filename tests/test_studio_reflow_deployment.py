"""Exercise the production reflow release and App transaction at its device boundary."""
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
from protocol import studio_reflow_deployment as release
from protocol import studio_resource_installation as resources
from tests.test_e3d2b_deployment import MemoryDevice

ROOT = release.ROOT
spec = importlib.util.spec_from_file_location("reflow_deploy_tests", ROOT / "scripts/deploy-studio-reflow-e3f4e.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class ReflowDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = release.load_reference()
        cls.image = release.reviewed_candidate()
        cls.evidence = dict(image=cls.image, candidate=dict(variant="asset-reflow", bytes=release.APP_SIZE,
            sha256=release.APP_SHA, app_elf_sha256=release.APP_ELF_SHA),
            build_report_sha256=release.BUILD_REPORT_SHA, offline_report_sha256=release.OFFLINE_REPORT_SHA,
            path_audit_sha256=release.PEER_REPORT_SHA)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reflow-deploy-", dir=ROOT / "tmp")
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

    def test_pinned_build_and_candidate_evidence(self):
        self.assertEqual(release.check_build_evidence(), self.evidence)

    def test_reference_overlays_all_e145_resources(self):
        old = resources.load(ROOT / release.REFERENCE)
        actual = self.reference["snapshot"]
        self.assertNotEqual(actual, old.snapshot)
        self.assertEqual(actual[resources.START:resources.START + resources.SIZE], old.image)
        self.assertEqual(tx.sha(actual), release.REFERENCE_SNAPSHOT_SHA)
        self.assertEqual(tx.sha(self.reference["app_union"]), release.ORIGINAL_UNION_SHA)
        with self.assertRaisesRegex(ValueError, "E145_snapshot"):
            release.validate_baseline(old.snapshot)

    def test_install_and_recover_exact_union_keep_e145_and_tail(self):
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
        for event in self.memory.events:
            if event[0] == "write":
                self.assertEqual(event[1:], (tx.START, release.ERASE_BYTES))

    def test_partial_write_readback_boot_failures_restore(self):
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

    def test_recover_uses_saved_original_without_candidate_or_reference(self):
        target = self.live()
        self.execute("install", target)
        (target / "candidate-app.bin").unlink()
        with patch.object(release, "reviewed_candidate", side_effect=AssertionError("candidate read")), \
             patch.object(release, "check_build_evidence", side_effect=AssertionError("build read")), \
             patch.object(release, "load_reference", side_effect=AssertionError("reference read")), \
             patch.object(deploy.base, "MenuTestDevice", side_effect=self.factory):
            self.assertEqual(deploy.base.run("recover", target)["result"], "original_restored")
        self.assertEqual(bytes(self.memory.raw), self.reference["snapshot"])

    def test_live_nvs_refresh_and_recovery_keep_new_runtime_nvs(self):
        self.memory.raw[0x9000] ^= 1
        target = self.live()
        plan = tx.load(target)
        self.assertEqual(plan.snapshot[0x9000:0xD000], bytes(self.memory.raw[0x9000:0xD000]))
        self.execute("install", target)
        self.memory.raw[0x9001] ^= 1
        current = bytes(self.memory.raw[0x9000:0xD000])
        self.execute("recover", target)
        self.assertEqual(bytes(self.memory.raw[0x9000:0xD000]), current)
        self.assertEqual(bytes(self.memory.raw[resources.START:resources.START + resources.SIZE]),
                         self.reference["snapshot"][resources.START:resources.START + resources.SIZE])

    def test_changed_original_or_assets_stop_before_write(self):
        target = self.live()
        for offset in (tx.START + 128, resources.START + 128, 0x9000):
            with self.subTest(offset=offset):
                self.memory.raw[offset] ^= 1
                with self.assertRaises(ValueError):
                    self.execute("install", target)
                self.memory.raw[offset] ^= 1
        self.assertEqual(self.memory.writes, [])

    def test_wrong_identity_and_target_stop_before_write(self):
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

    def test_recomputed_plan_with_changed_tail_or_resource_rejected(self):
        for offset in (tx.START + release.ORIGINAL_SIZE, resources.START):
            with self.subTest(offset=offset):
                snapshot = bytearray(self.reference["snapshot"])
                snapshot[offset] ^= 1
                forged = tx.prepare(bytes(snapshot), self.image, release.IDENTITY_SHA,
                                    release.ORIGINAL_SIZE, edition="E3c")
                with self.assertRaises(ValueError):
                    release.check_plan(tx.RecoveryPlan(forged.metadata, forged.snapshot), candidate=False)

    def test_live_adapter_refreshes_prefix_and_checks_complete_digest(self):
        device = deploy.ReflowDevice(self.folder, release.IDENTITY_SHA)
        prefix = bytearray(self.reference["snapshot"][:tx.START])
        prefix[0x9000] ^= 1
        with patch.object(device, "identity", return_value=release.IDENTITY_SHA), \
             patch.object(device, "_capture", return_value=bytes(prefix)) as capture, \
             patch.object(device, "_invoke", return_value="-- verify OK (digest matched)") as invoke:
            actual = device.read_reference(self.reference)
        self.assertEqual(actual, bytes(prefix) + self.reference["snapshot"][tx.START:])
        capture.assert_called_once_with("current-prefix", 0, tx.START)
        self.assertEqual(Path(invoke.call_args.args[1][-1]).read_bytes(), actual)

    def test_live_adapter_rejects_complete_digest_mismatch(self):
        device = deploy.ReflowDevice(self.folder, release.IDENTITY_SHA)
        with patch.object(device, "identity", return_value=release.IDENTITY_SHA), \
             patch.object(device, "_capture", return_value=self.reference["snapshot"][:tx.START]), \
             patch.object(device, "_invoke", return_value="digest differs"), self.assertRaises(ValueError):
            device.read_reference(self.reference)

    def test_changed_build_peer_tests_and_image_are_rejected(self):
        for selected in (release.BUILD_REPORT, release.OFFLINE_REPORT, release.PEER_REPORT, release.SOURCE):
            with self.subTest(path=selected):
                original = release._read
                def read(root, name):
                    raw = original(root, name)
                    return raw + b"\n" if Path(name) == selected else raw
                with patch.object(release, "_read", side_effect=read), self.assertRaises(ValueError):
                    release.check_build_evidence()

    def test_reference_requires_installed_receipt_and_journal(self):
        original = release._fixed_json
        def wrong_receipt(root, name, digest):
            value = original(root, name, digest)
            value["readback_sha256"] = "0" * 64
            return value
        with patch.object(release, "_fixed_json", side_effect=wrong_receipt), self.assertRaises(ValueError):
            release.load_reference()
        with patch.object(resources.Journal, "load", return_value={"phase":"restored"}), self.assertRaises(ValueError):
            release.load_reference()

    def test_prepare_rejects_wrong_identity_and_image(self):
        with self.assertRaises(ValueError):
            release.prepare_plan(self.reference["snapshot"], "f" * 64, self.image)
        with self.assertRaises(ValueError):
            release.reviewed_candidate(self.image[:-1])

    def test_cli_binding_in_fresh_process_has_no_serial_access(self):
        code = (
            "import importlib.util; from pathlib import Path; "
            "s=importlib.util.spec_from_file_location('cli', Path('scripts/deploy-studio-reflow-e3f4e.py')); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "assert m.entry.reviewed is m.reviewed; assert m.base.reviewed is m.reviewed; "
            "assert m.base.entry.reviewed is m.reviewed; "
            "assert m.base.entry.ROLLBACK_TARGET == 'E3f-4c'; "
            "assert m.ReflowDevice is m.base.MenuTestDevice; print('bindings_verified')"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bindings_verified", result.stdout)
