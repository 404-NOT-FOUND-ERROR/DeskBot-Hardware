"""Candidate-independent rollback tests. Device Flash is a bytearray."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx

ROOT = Path(__file__).resolve().parents[1]


class MemoryDevice:
    def __init__(self, plan, fail_writes=0, identity=None):
        self.plan, self.raw = plan, bytearray(plan.snapshot)
        self.raw[tx.START:tx.START + 100] = b"\0" * 100
        self.fail_writes, self.writes = fail_writes, []
        self.device_id = identity or plan.metadata["identity_sha256"]

    def identity(self):
        return self.device_id

    def write_app(self, raw):
        self.writes.append(raw)
        if len(self.writes) <= self.fail_writes:
            self.raw[tx.START:tx.START + 13] = raw[:13]
            raise OSError("interrupted restore")
        self.raw[tx.START:tx.START + len(raw)] = raw

    def verify_image(self, payload, *, allow_runtime_nvs):
        return tx.compare(self.plan, bytes(self.raw), payload, allow_runtime_nvs=allow_runtime_nvs)

    def boot(self, candidate):
        if candidate:
            raise AssertionError("recovery must boot the original App")
        return {"normal_boot": True}


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = bytearray(b"\xff" * tx.FLASH)
        raw[tx.START:tx.START + 9000] = b"\xe9" + b"o" * 8999
        cls.original = tx.prepare(bytes(raw), b"\xe9" + b"n" * 10000, "a" * 64, 9000)
        spec = importlib.util.spec_from_file_location("recovery_adapter_test", ROOT / "scripts/deploy-deskbot-studio-e3b.py")
        cls.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.entry)

    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="e3c-recovery-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        tx.persist(self.folder, self.original)
        (self.folder / "candidate-app.bin").unlink()
        self.plan = tx.load_recovery(self.folder)
        self.journal = tx.Journal(self.folder, self.plan)

    def test_original_plan_id_and_bytes_are_retained_without_candidate(self):
        self.assertEqual(self.plan.id, self.original.id)
        self.assertEqual(self.plan.rollback, self.original.rollback)
        self.assertFalse(hasattr(self.plan, "image"))

    def test_full_install_load_still_requires_candidate(self):
        with self.assertRaises(FileNotFoundError):
            tx.load(self.folder)

    def test_recovery_plan_cannot_install(self):
        device = MemoryDevice(self.plan)
        with self.assertRaisesRegex(ValueError, "complete candidate"):
            tx.install(self.plan, device, self.journal)
        self.assertEqual(device.writes, [])

    def test_recovery_rebuilds_exact_flash_after_partial_write(self):
        self.journal.save("restore_required")
        device = MemoryDevice(self.plan)
        result = tx.recover(self.plan, device, self.journal)
        self.assertEqual(result["result"], "original_restored")
        self.assertEqual(bytes(device.raw), self.original.snapshot)
        self.assertEqual(device.writes, [self.original.rollback])

    def test_interrupted_recovery_is_resumable(self):
        self.journal.save("restoring")
        device = MemoryDevice(self.plan, fail_writes=1)
        with self.assertRaises(OSError):
            tx.recover(self.plan, device, self.journal)
        self.assertEqual(self.journal.load()["phase"], "needs_recovery")
        plan = tx.load_recovery(self.folder)
        tx.recover(plan, device, tx.Journal(self.folder, plan))
        self.assertEqual(bytes(device.raw), self.original.snapshot)
        self.assertEqual(self.journal.load()["phase"], "restored")

    def test_entrypoint_repeated_recovery_keeps_separate_result_files(self):
        self.journal.save("installed")
        legacy = self.folder / "recover-result.json"
        legacy.write_bytes(b"previous result")
        device = MemoryDevice(self.plan)
        with patch.object(self.entry, "Device", return_value=device):
            for _ in range(2):
                self.entry.recover_saved(self.folder, self.plan)
        self.assertEqual(legacy.read_bytes(), b"previous result")
        for index in (1, 2):
            result = json.loads((self.folder / f"recover-result-{index:03d}.json").read_bytes())
            self.assertEqual(result["result"], "original_restored")
        self.assertEqual(device.writes, [self.plan.rollback] * 2)

    def test_entrypoint_interruption_retains_attempt_and_can_retry(self):
        self.journal.save("installed")
        device = MemoryDevice(self.plan, fail_writes=1)
        with patch.object(self.entry, "Device", return_value=device):
            with self.assertRaises(OSError):
                self.entry.recover_saved(self.folder, self.plan)
            self.assertEqual(self.journal.load()["phase"], "needs_recovery")
            self.entry.recover_saved(self.folder, self.plan)
        self.assertTrue((self.folder / "recover-result-001.json").exists())
        result = json.loads((self.folder / "recover-result-002.json").read_bytes())
        self.assertEqual(result["result"], "original_restored")
        self.assertEqual(bytes(device.raw), self.plan.snapshot)

    def test_entrypoint_result_reservation_failure_does_not_access_device(self):
        self.journal.save("installed")
        with patch.object(self.entry, "Device") as factory:
            with patch.object(Path, "open", side_effect=PermissionError("result unavailable")):
                with self.assertRaises(PermissionError):
                    self.entry.recover_saved(self.folder, self.plan)
        factory.assert_not_called()
        self.assertEqual(self.journal.load()["phase"], "installed")

    def test_missing_prepared_or_unknown_state_performs_no_write(self):
        device = MemoryDevice(self.plan)
        for phase in (None, "prepared", "unknown"):
            if phase is not None:
                self.journal.save(phase)
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                tx.recover(self.plan, device, self.journal)
        self.assertEqual(device.writes, [])

    def test_other_body_is_rejected_before_write(self):
        self.journal.save("installed")
        device = MemoryDevice(self.plan, identity="b" * 64)
        with self.assertRaisesRegex(ValueError, "device differs"):
            tx.recover(self.plan, device, self.journal)
        self.assertEqual(device.writes, [])

    def test_changed_backup_and_saved_union_are_rejected(self):
        for name in ("snapshot-32m.bin", "rollback-app-union.bin"):
            path = self.folder / name
            original = path.read_bytes()
            path.write_bytes(b"\0" + original[1:])
            with self.subTest(name=name), self.assertRaises(ValueError):
                tx.load_recovery(self.folder)
            path.write_bytes(original)

    def test_geometry_types_bounds_and_unknown_metadata_are_rejected(self):
        for changes in ({"erase_bytes": 1}, {"original_bytes": True}, {"candidate_bytes": -1},
                        {"erase_bytes": tx.PARTITION + 4096}, {"offset": 0}, {"extra": 1},
                        {"identity_sha256": "z" * 64}, {"snapshot_sha256": None}):
            metadata = {**self.plan.metadata, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tx.RecoveryPlan(metadata, self.plan.snapshot).validate()

    def test_changed_plan_id_and_duplicate_saved_keys_are_rejected(self):
        path = self.folder / "plan.json"
        raw = path.read_bytes()
        saved = json.loads(raw)
        saved["plan_id"] = "0" * 64
        path.write_bytes(tx.canonical(saved))
        with self.assertRaisesRegex(ValueError, "recovery bytes"):
            tx.load_recovery(self.folder)
        path.write_bytes(raw[:-2] + b',"plan_id":"duplicate"}\n')
        with self.assertRaises(ValueError):
            tx.load_recovery(self.folder)

    def test_real_adapter_restoration_never_loads_candidate(self):
        device = self.entry.Device.__new__(self.entry.Device)
        device.run_dir, device.deployment_plan = self.folder, self.plan
        device.identity = lambda: self.plan.metadata["identity_sha256"]
        device._new_path = lambda folder, name: self.folder / name
        calls = []
        device._invoke = lambda label, args: calls.append((label, args))
        self.journal.save("restoring")
        with patch.object(tx, "load", side_effect=AssertionError("candidate loader used")):
            device.write_app(self.plan.rollback)
        args = calls[0][1]
        self.assertEqual(args[-2], "0x10000")
        self.assertEqual(Path(args[-1]).read_bytes(), self.plan.rollback)
        self.assertNotIn("--erase-all", args)

    def test_real_adapter_readback_never_loads_candidate(self):
        device = self.entry.Device.__new__(self.entry.Device)
        device.run_dir, device.deployment_plan = self.folder, self.plan
        device.identity = lambda: self.plan.metadata["identity_sha256"]
        device._new_path = lambda folder, name: self.folder / name
        device._capture = lambda label, offset, size: self.plan.snapshot[offset:offset + size]
        device._invoke = lambda label, args: "-- verify OK (digest matched)\n" * 3
        with patch.object(tx, "load", side_effect=AssertionError("candidate loader used")):
            result = device.verify_image(self.plan.rollback, allow_runtime_nvs=True)
        self.assertTrue(result["app_byte_equal"])
        self.assertTrue(result["static_outside_app_equal"])

    def test_runtime_nvs_is_reported_and_protected_resources_are_checked(self):
        self.journal.save("installed")
        device = MemoryDevice(self.plan)
        device.raw[0x9000] ^= 1
        result = tx.recover(self.plan, device, self.journal)
        self.assertFalse(result["readback"]["nvs_equal"])
        device.raw[0x1000000] ^= 1
        with self.assertRaises(ValueError):
            tx.recover(self.plan, device, self.journal)
        self.assertEqual(self.journal.load()["phase"], "needs_recovery")

    def test_fresh_process_loads_recovery_without_candidate_or_build_modules(self):
        self.journal.save("restore_required")
        code = """
import builtins, json, sys
from pathlib import Path
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'serial', 'yaml', 'esptool'} or 'build_deskbot' in name:
        raise AssertionError('recovery imported build or transport dependencies')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from protocol import studio_app_deployment as tx
plan = tx.load_recovery(Path(sys.argv[1]))
class Device:
    raw = bytearray(plan.snapshot)
    def identity(self): return plan.metadata['identity_sha256']
    def write_app(self, value): self.raw[tx.START:tx.START+len(value)] = value
    def verify_image(self, value, *, allow_runtime_nvs):
        return tx.compare(plan, bytes(self.raw), value, allow_runtime_nvs=allow_runtime_nvs)
    def boot(self, candidate):
        assert candidate is False
        return {'normal_boot': True}
device = Device()
device.raw[tx.START:tx.START+64] = b'X'*64
result = tx.recover(plan, device, tx.Journal(Path(sys.argv[1]), plan))
assert bytes(device.raw) == plan.snapshot
print(json.dumps({'result': result['result']}))
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.folder)], cwd=ROOT,
                                capture_output=True, text=True, timeout=20, check=True)
        self.assertEqual(json.loads(result.stdout)["result"], "original_restored")
        self.assertEqual(self.journal.load()["phase"], "restored")
