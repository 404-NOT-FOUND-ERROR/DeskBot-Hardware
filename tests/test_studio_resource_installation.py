"""Resource transaction tests with real bytes and a fault-injecting Flash model."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_resource_installation as r

ROOT = Path(__file__).resolve().parents[1]


class FlashModel:
    simulation_only = True

    def __init__(self, plan):
        self.raw = bytearray(plan.snapshot)
        self.ident = plan.metadata["identity_sha256"]
        self.writes, self.boots, self.fail_writes = [], 0, set()
        self.fail_first_boot = self.corrupt_first_readback = False

    def identity(self):
        return self.ident

    def read_full(self):
        raw = bytes(self.raw)
        if self.corrupt_first_readback and len(self.writes) == 1:
            self.corrupt_first_readback = False
            value = bytearray(raw)
            value[r.START + 100] ^= 1
            return bytes(value)
        return raw

    def write_assets(self, raw):
        self.writes.append(raw)
        if len(self.writes) in self.fail_writes:
            self.raw[r.START:r.START + r.SIZE // 2] = raw[:r.SIZE // 2]
            raise OSError("interrupted_write")
        self.raw[r.START:r.START + r.SIZE] = raw

    def boot(self):
        self.boots += 1
        return not (self.fail_first_boot and self.boots == 1)


class ResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = (ROOT / "tmp/e143b-e3c3b-20261107-live1/snapshot-32m.bin").read_bytes()
        cls.image = (ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin").read_bytes()
        cls.plan = r.prepare(cls.snapshot, cls.image, "a" * 64)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resource-transaction-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.journal = r.Journal(self.folder, self.plan)
        self.device = FlashModel(self.plan)

    def test_real_candidate_plan_geometry_and_hashes(self):
        self.assertEqual(r.check(self.plan)["candidate_sha256"], r.CANDIDATE_SHA)
        self.assertEqual((self.plan.metadata["offset"], self.plan.metadata["bytes"]), (0x1000000, 0x800000))

    def test_complete_install_and_manual_recovery(self):
        result = r.install(self.plan, self.device, self.journal)
        self.assertEqual(result["hardware_acceptance"], "pending")
        self.assertEqual(self.device.writes, [self.image])
        self.assertEqual(bytes(self.device.raw[:r.START]), self.snapshot[:r.START])
        r.recover(self.plan, self.device, self.journal)
        self.assertEqual(bytes(self.device.raw), self.snapshot)
        self.assertEqual(self.journal.load()["phase"], "restored")

    def test_partial_candidate_write_recovers(self):
        self.device.fail_writes = {1}
        with self.assertRaises(OSError):
            r.install(self.plan, self.device, self.journal)
        self.assertEqual(bytes(self.device.raw), self.snapshot)

    def test_readback_failure_recovers(self):
        self.device.corrupt_first_readback = True
        with self.assertRaisesRegex(ValueError, "candidate_readback"):
            r.install(self.plan, self.device, self.journal)
        self.assertEqual(bytes(self.device.raw), self.snapshot)

    def test_candidate_boot_failure_recovers(self):
        self.device.fail_first_boot = True
        with self.assertRaisesRegex(ValueError, "candidate_boot"):
            r.install(self.plan, self.device, self.journal)
        self.assertEqual(bytes(self.device.raw), self.snapshot)

    def test_repeated_recovery_after_process_restart_and_candidate_deletion(self):
        r.persist(self.folder, self.plan)
        self.device.fail_writes = {1, 2}
        with self.assertRaises(OSError):
            r.install(self.plan, self.device, self.journal)
        self.assertEqual(self.journal.load()["phase"], "needs_recovery")
        (self.folder / "candidate-assets-8m.bin").unlink()
        plan = r.load(self.folder, recovery=True)
        r.recover(plan, self.device, r.Journal(self.folder, plan))
        self.assertEqual(bytes(self.device.raw), self.snapshot)

    def test_preserves_current_runtime_nvs_during_later_recovery(self):
        r.install(self.plan, self.device, self.journal)
        self.device.raw[r.NVS_START + 3] ^= 1
        current_nvs = self.device.raw[r.NVS_START:r.NVS_END]
        r.recover(self.plan, self.device, self.journal)
        self.assertEqual(self.device.raw[r.NVS_START:r.NVS_END], current_nvs)
        self.assertEqual(bytes(self.device.raw[r.START:r.START + r.SIZE]), self.plan.rollback)

    def test_changed_device_or_snapshot_stops_before_write(self):
        self.device.ident = "b" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            r.install(self.plan, self.device, self.journal)
        self.device.ident = "a" * 64
        self.device.raw[r.NVS_START] ^= 1
        with self.assertRaisesRegex(ValueError, "snapshot_changed"):
            r.install(self.plan, self.device, self.journal)
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.device.boots, 2)

    def test_outside_asset_corruption_is_reported(self):
        self.journal.save("write_intent")
        self.device.raw[0x900000] ^= 1
        with self.assertRaisesRegex(ValueError, "outside_resource"):
            r.recover(self.plan, self.device, self.journal)
        self.assertEqual(self.device.writes, [])

    def test_plan_rejects_wrong_geometry_mode_digest_and_extra_keys(self):
        for key, value in (("offset", 0x10000), ("bytes", True), ("mode", "unknown"),
                           ("candidate_sha256", "0"*64), ("extra", 1)):
            metadata = {**self.plan.metadata, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                r.RecoveryPlan(metadata, self.snapshot).validate()

    def test_truncated_or_unexpected_images_rejected(self):
        for raw in (self.image[:-1], bytes(r.SIZE)):
            with self.assertRaises(ValueError):
                r.prepare(self.snapshot, raw, "a"*64)
        with self.assertRaises(ValueError):
            r.prepare(self.snapshot[:-1], self.image, "a"*64)

    def test_backup_tamper_and_duplicate_saved_key_rejected(self):
        r.persist(self.folder, self.plan)
        self.assertEqual(r.load(self.folder).id, self.plan.id)
        path = self.folder / "plan.json"
        original = path.read_bytes()
        path.write_bytes(original[:-2] + b',"plan_id":"bad"}\n')
        with self.assertRaises(ValueError):
            r.load(self.folder)
        path.write_bytes(original)
        (self.folder / "rollback-assets-8m.bin").write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            r.load(self.folder, recovery=True)

    def test_existing_journal_requires_recovery(self):
        self.journal.save("write_intent")
        with self.assertRaisesRegex(ValueError, "existing_transaction"):
            r.install(self.plan, self.device, self.journal)


if __name__ == "__main__":
    unittest.main()
