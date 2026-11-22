"""Offline gates for the query-enabled E3c-3a App and query-off rollback."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_e3c3a_deployment as query_off
from protocol import studio_e3c3a_query_deployment as reviewed

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "e3c3a_query_deploy", ROOT / "scripts/deploy-deskbot-studio-e3c3a.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class E3c3aQueryDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = reviewed.load_reference()
        cls.evidence = reviewed.check_build_evidence()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e3c3a-query-gate-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        deploy.select_reviewed(True)
        self.offline = self.root / "offline"
        deploy.prepare_offline(self.offline)

    def tearDown(self):
        deploy.select_reviewed(False)

    def prepare_live_with_mock(self, live, snapshot=None):
        with patch.object(deploy, "MenuTestDevice") as factory:
            device = factory.return_value
            device.identity.return_value = self.reference["identity_sha256"]
            device.read_reference.return_value = snapshot or self.reference["snapshot"]
            result = deploy.prepare_live(live, self.offline)
        return result, factory

    def test_query_on_candidate_is_pinned_to_the_audited_build(self):
        candidate = reviewed.reviewed_candidate(self.evidence["image"])
        self.assertEqual(len(candidate), reviewed.APP_SIZE)
        self.assertEqual(tx.sha(candidate), reviewed.APP_SHA)
        self.assertEqual(self.evidence["candidate"]["app_elf_sha256"], reviewed.APP_ELF_SHA)
        self.assertEqual(self.evidence["candidate"]["variant"], "query_enabled")

    def test_current_reference_is_the_accepted_query_off_install(self):
        self.assertEqual(len(self.reference["snapshot"]), tx.FLASH)
        self.assertEqual(tx.sha(self.reference["app_union"]), reviewed.QUERY_OFF_APP_UNION_SHA)
        self.assertEqual(self.reference["original_bytes"], reviewed.QUERY_OFF_APP_SIZE)
        self.assertEqual(self.reference["report"]["path"], reviewed.LIVE_RESULT.as_posix())

    def test_offline_plan_targets_only_app_and_query_off_rollback(self):
        plan = tx.load(self.offline)
        receipt = json.loads((self.offline / "preflight.json").read_bytes())
        self.assertEqual(plan.metadata["candidate_sha256"], reviewed.APP_SHA)
        self.assertEqual(plan.metadata["original_bytes"], reviewed.QUERY_OFF_APP_SIZE)
        self.assertEqual(plan.metadata["offset"], 0x10000)
        self.assertEqual(plan.metadata["erase_bytes"], (reviewed.APP_SIZE + 4095) // 4096 * 4096)
        self.assertEqual(plan.rollback, self.reference["snapshot"][tx.START:tx.START + len(plan.rollback)])
        self.assertEqual(receipt["candidate"]["variant"], "query_enabled")
        self.assertEqual(receipt["rollback_target"], "E3c-3a-query-off")
        self.assertFalse(receipt["hardware_accessed"])
        self.assertFalse(receipt["device_written"])

    def test_live_preflight_refreshes_nvs_and_does_not_write(self):
        changed = bytearray(self.reference["snapshot"])
        changed[0x9000] ^= 0x01
        live = self.root / "live"
        result, factory = self.prepare_live_with_mock(live, bytes(changed))
        plan = tx.load(live)
        receipt = json.loads((live / "preflight.json").read_bytes())
        self.assertEqual(result["phase"], "prepared_live")
        self.assertEqual(plan.snapshot, bytes(changed))
        self.assertEqual(plan.rollback, self.reference["snapshot"][tx.START:tx.START + len(plan.rollback)])
        self.assertEqual(receipt["rollback_target"], "E3c-3a-query-off")
        self.assertTrue(receipt["hardware_accessed"])
        self.assertFalse(receipt["device_written"])
        factory.return_value.write_app.assert_not_called()
        self.assertEqual(deploy.validate_saved(live, "install").id, plan.id)

    def test_live_preflight_rejects_changed_app_or_protected_bytes(self):
        for offset in (tx.START + 17, 0xBC8000, 0x1000000):
            with self.subTest(offset=offset):
                changed = bytearray(self.reference["snapshot"])
                changed[offset] ^= 1
                live = self.root / f"changed-{offset:x}"
                with patch.object(deploy, "MenuTestDevice") as factory:
                    device = factory.return_value
                    device.identity.return_value = self.reference["identity_sha256"]
                    device.read_reference.return_value = bytes(changed)
                    with self.assertRaisesRegex(ValueError, "differs from the accepted E3c-3a"):
                        deploy.prepare_live(live, self.offline)
                    device.boot.assert_called_once_with(False)
                    device.write_app.assert_not_called()
                self.assertFalse((live / "preflight.json").exists())

    def test_wrong_target_receipt_stops_before_install_dispatch(self):
        live = self.root / "live"
        self.prepare_live_with_mock(live)
        path = live / "preflight.json"
        receipt = json.loads(path.read_bytes())
        receipt["target"] = "base_COM8"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        with patch.object(deploy.entry, "run") as runner:
            with self.assertRaisesRegex(ValueError, "preflight receipt differs"):
                deploy.run("install", live)
            runner.assert_not_called()

    def test_query_off_variant_rejects_query_on_plan(self):
        deploy.select_reviewed(False)
        with self.assertRaises(ValueError):
            deploy.validate_saved(self.offline, "check")

    def test_recovery_uses_saved_query_off_bytes_without_candidate_inputs(self):
        plan = tx.load_recovery(self.offline)
        tx.Journal(self.offline, plan).save("restore_required")
        (self.offline / "candidate-app.bin").unlink()
        (self.offline / "preflight.json").unlink()
        with patch.object(reviewed, "check_build_evidence",
                          side_effect=AssertionError("candidate build is not needed for recovery")), \
                patch.object(reviewed, "load_reference",
                             side_effect=AssertionError("candidate reference is not needed for recovery")), \
                patch.object(deploy, "MenuTestDevice") as factory:
            device = factory.return_value
            device.identity.return_value = plan.metadata["identity_sha256"]
            device.verify_image.return_value = {"app_byte_equal": True}
            device.boot.return_value = {"normal_boot": True}
            result = deploy.run("recover", self.offline)
            device.write_app.assert_called_once_with(plan.rollback)
            device.boot.assert_called_once_with(False)
        self.assertEqual(result["result"], "original_restored")
        self.assertEqual(tx.Journal(self.offline, plan).load()["phase"], "restored")
