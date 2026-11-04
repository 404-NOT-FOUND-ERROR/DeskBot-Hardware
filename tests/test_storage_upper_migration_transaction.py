"""Fault injection for the actual controller using a synthetic 32 MiB Flash.

Controller tests replace only the versioned resource hash constants. Real
SHA-256, geometry, files, locking and journal/recovery code remain active.
Retained-input integration tests use the original resource constants.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import storage_upper_migration_transaction as tx  # noqa: E402
from protocol.storage_upper_migration_simulator import FileFlash, SimulatedPowerLoss, synthetic_snapshot  # noqa: E402


class MemoryFlash:
    simulation_only = True

    def __init__(self, snapshot, plan, folder):
        self.data = bytearray(snapshot)
        self.plan, self.folder = plan, folder
        self.identity_value = plan.metadata["identity_sha256"]
        self.writes, self.boots, self.events = [], [], []
        self.interrupt, self.fail, self.bad_read, self.bad_boot = set(), set(), set(), set()
        self.change_static = False
        self.change_nvs = False
        self.short_full = False

    def identity(self):
        self.events.append("identity")
        return self.identity_value

    def read_full(self):
        self.events.append("full_read")
        raw = bytes(self.data)
        return raw[:-1] if self.short_full else raw

    def read_flash(self, offset, size):
        phase = tx.Journal(self.folder, self.plan).load()["phase"]
        self.events.append(("read", offset, phase))
        raw = bytes(self.data[offset:offset + size])
        key = (phase, offset)
        if key in self.bad_read:
            self.bad_read.remove(key)
            return bytes([raw[0] ^ 1]) + raw[1:]
        return raw

    def write_flash(self, offset, raw):
        state = tx.Journal(self.folder, self.plan).load()
        phase = state["phase"]
        allowed = {
            "upper_write_intent": (tx.UPPER_START, self.plan.candidate_upper),
            "table_write_intent": (tx.TABLE_START, self.plan.candidate_table),
            "restore_table_intent": (tx.TABLE_START, self.plan.original_table),
            "restore_upper_intent": (tx.UPPER_START, self.plan.original_upper),
        }
        assert state["write_intent"] and allowed.get(phase) == (offset, raw)
        self.events.append(("write", offset, phase))
        self.writes.append((offset, phase))
        self.data[offset:offset + len(raw)] = b"\xff" * len(raw)
        if phase in self.interrupt or phase in self.fail:
            self.data[offset:offset + len(raw) // 2] = raw[:len(raw) // 2]
            if phase in self.interrupt:
                self.interrupt.remove(phase)
                raise SimulatedPowerLoss(phase)
            self.fail.remove(phase)
            raise OSError("partial write: " + phase)
        self.data[offset:offset + len(raw)] = raw
        if phase == "upper_write_intent" and self.change_static:
            self.data[0x10000] ^= 1
        if phase == "upper_write_intent" and self.change_nvs:
            self.data[tx.NVS_START] ^= 1

    def boot(self, target):
        self.boots.append(target)
        return target not in self.bad_boot


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_table = b"O" * tx.TABLE_BYTES
        cls.new_payload = b"T" * 3072
        cls.upper = b"C" * tx.UPPER_BYTES
        raw = bytearray(b"\xff" * tx.FLASH_BYTES)
        raw[tx.TABLE_START:tx.TABLE_START + tx.TABLE_BYTES] = cls.old_table
        raw[tx.UPPER_START:tx.UPPER_START + tx.UPPER_BYTES] = b"R" * tx.UPPER_BYTES
        raw[0xBC8000:0xC90000] = b"M" * 0xC8000
        cls.snapshot = bytes(raw)
        cls.patches = [
            patch.object(tx, "LEGACY_TABLE_SECTOR_SHA256", tx.sha(cls.old_table)),
            patch.object(tx, "TABLE_SHA256", {"upper-assets-8m": tx.sha(cls.new_payload)}),
            patch.object(tx, "TABLE_SECTOR_SHA256", tx.sha(cls.new_payload + b"\xff" * 1024)),
            patch.object(tx, "IMAGE_SHA256", tx.sha(cls.upper)),
        ]
        for item in cls.patches:
            item.start()

    @classmethod
    def tearDownClass(cls):
        for item in reversed(cls.patches):
            item.stop()

    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143c-controller-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "run"
        self.plan = tx.make_plan(self.snapshot, self.upper, self.new_payload,
                                 identity_sha256="1" * 64, run_label="e143c-sim-unit")
        tx.persist_plan(self.folder, self.plan)
        self.device = MemoryFlash(self.snapshot, self.plan, self.folder)

    def execute(self):
        return tx.execute(self.folder, self.device, initial_snapshot=self.snapshot)

    def state(self):
        return tx.Journal(self.folder, self.plan).load()

    def original_ranges(self):
        self.assertEqual(bytes(self.device.data[tx.TABLE_START:tx.TABLE_START + tx.TABLE_BYTES]), self.plan.original_table)
        self.assertEqual(bytes(self.device.data[tx.UPPER_START:tx.UPPER_START + tx.UPPER_BYTES]), self.plan.original_upper)

    def journal_fault(self, phase, *, power=False, after=False):
        original = tx.Journal.save
        used = False
        def save(journal, current, **details):
            nonlocal used
            if current == phase and not used:
                used = True
                if after:
                    original(journal, current, **details)
                if power:
                    raise SimulatedPowerLoss("journal: " + current)
                raise OSError("journal: " + current)
            return original(journal, current, **details)
        return patch.object(tx.Journal, "save", save)

    def test_success_commits_resources_before_table_and_waits_for_interaction(self):
        outcome = self.execute()
        self.assertEqual(outcome["result"], "candidate_committed_simulation")
        self.assertEqual(self.device.writes, [(tx.UPPER_START, "upper_write_intent"), (tx.TABLE_START, "table_write_intent")])
        self.assertEqual(self.state()["phase"], "awaiting_interaction")
        self.assertFalse(outcome["physical_interaction_accepted"])
        self.assertFalse(outcome["device_written"])
        self.assertEqual(self.device.boots, ["candidate"])

    def test_success_can_be_explicitly_restored_table_first(self):
        self.execute()
        outcome = tx.recover(self.folder, self.device)
        self.assertTrue(outcome["restored"])
        self.assertEqual(self.device.writes[-2:], [(tx.TABLE_START, "restore_table_intent"), (tx.UPPER_START, "restore_upper_intent")])
        self.original_ranges()

    def test_partial_upper_write_error_recovers_without_committing_table(self):
        self.device.fail.add("upper_write_intent")
        outcome = self.execute()
        self.assertEqual(outcome["result"], "failed_and_restored_simulation")
        self.assertNotIn((tx.TABLE_START, "table_write_intent"), self.device.writes)
        self.assertNotIn((tx.TABLE_START, "restore_table_intent"), self.device.writes)
        self.original_ranges()

    def test_partial_table_write_error_recovers_both_ranges(self):
        self.device.fail.add("table_write_intent")
        self.assertTrue(self.execute()["restored"])
        self.original_ranges()

    def test_bad_upper_readback_prevents_table_commit(self):
        self.device.bad_read.add(("upper_write_intent", tx.UPPER_START))
        self.assertTrue(self.execute()["restored"])
        self.assertNotIn((tx.TABLE_START, "table_write_intent"), self.device.writes)
        self.original_ranges()

    def test_upper_is_rechecked_immediately_before_commit(self):
        self.device.bad_read.add(("upper_verified", tx.UPPER_START))
        self.assertTrue(self.execute()["restored"])
        self.assertNotIn((tx.TABLE_START, "table_write_intent"), self.device.writes)

    def test_bad_table_readback_triggers_restore(self):
        self.device.bad_read.add(("table_write_intent", tx.TABLE_START))
        self.assertTrue(self.execute()["restored"])
        self.original_ranges()

    def test_candidate_boot_failure_restores(self):
        self.device.bad_boot.add("candidate")
        self.assertTrue(self.execute()["restored"])
        self.original_ranges()
        self.assertEqual(self.device.boots, ["candidate", "original"])

    def test_power_loss_during_upper_write_resumes_from_disk(self):
        self.device.interrupt.add("upper_write_intent")
        with self.assertRaises(SimulatedPowerLoss):
            self.execute()
        self.assertEqual(self.state()["phase"], "upper_write_intent")
        self.assertTrue(tx.recover(self.folder, self.device)["restored"])
        self.original_ranges()

    def test_power_loss_during_table_write_resumes_from_disk(self):
        self.device.interrupt.add("table_write_intent")
        with self.assertRaises(SimulatedPowerLoss):
            self.execute()
        self.assertTrue(tx.recover(self.folder, self.device)["restored"])
        self.original_ranges()

    def test_power_loss_after_table_intent_before_write_is_recoverable(self):
        with self.journal_fault("table_write_intent", power=True, after=True):
            with self.assertRaises(SimulatedPowerLoss):
                self.execute()
        self.assertNotIn((tx.TABLE_START, "table_write_intent"), self.device.writes)
        tx.recover(self.folder, self.device)
        self.original_ranges()

    def test_power_loss_after_verified_candidate_is_recoverable(self):
        with self.journal_fault("candidate_verified", power=True, after=True):
            with self.assertRaises(SimulatedPowerLoss):
                self.execute()
        tx.recover(self.folder, self.device)
        self.original_ranges()

    def test_power_loss_during_table_restore_can_resume(self):
        self.execute()
        self.device.interrupt.add("restore_table_intent")
        with self.assertRaises(SimulatedPowerLoss):
            tx.recover(self.folder, self.device)
        self.assertEqual(self.state()["phase"], "restore_table_intent")
        tx.recover(self.folder, self.device)
        self.original_ranges()

    def test_power_loss_during_upper_restore_can_resume(self):
        self.execute()
        self.device.interrupt.add("restore_upper_intent")
        with self.assertRaises(SimulatedPowerLoss):
            tx.recover(self.folder, self.device)
        self.assertEqual(self.state()["phase"], "restore_upper_intent")
        tx.recover(self.folder, self.device)
        self.original_ranges()

    def test_regular_restore_failure_keeps_recovery_required(self):
        self.execute()
        self.device.fail.add("restore_upper_intent")
        with self.assertRaises(tx.RecoveryRequired):
            tx.recover(self.folder, self.device)
        self.assertEqual(self.state()["phase"], "needs_recovery")
        tx.recover(self.folder, self.device)
        self.original_ranges()

    def test_restored_table_is_verified_before_restoring_upper(self):
        self.execute()
        self.device.bad_read.add(("restore_table_intent", tx.TABLE_START))
        # First corrupt read causes an allowed table rewrite; corrupt the
        # verification read too, so upper recovery must remain pending.
        original = self.device.read_flash
        def wrong_table(offset, size):
            raw = original(offset, size)
            if self.state()["phase"] == "restore_table_intent" and offset == tx.TABLE_START:
                return bytes([raw[0] ^ 2]) + raw[1:]
            return raw
        with patch.object(self.device, "read_flash", wrong_table):
            with self.assertRaises(tx.RecoveryRequired):
                tx.recover(self.folder, self.device)
        self.assertNotIn((tx.UPPER_START, "restore_upper_intent"), self.device.writes)
        tx.recover(self.folder, self.device)
        self.original_ranges()

    def test_recovery_does_not_need_candidates_full_snapshot_or_build(self):
        self.execute()
        for name in tx.CANDIDATE_FILES.values():
            (self.folder / name).unlink()
        self.assertIsNone(tx.load_plan(self.folder).candidate_upper)
        with patch.object(tx, "make_plan", side_effect=AssertionError("cannot reconstruct a plan")):
            self.assertTrue(tx.recover(self.folder, self.device)["restored"])
        self.original_ranges()

    def test_repeated_recovery_rechecks_without_rewriting_equal_bytes(self):
        self.execute()
        tx.recover(self.folder, self.device)
        count = len(self.device.writes)
        tx.recover(self.folder, self.device)
        self.assertEqual(len(self.device.writes), count)
        self.assertEqual(self.state()["phase"], "restored")

    def test_nvs_changes_are_observed_separately(self):
        self.device.change_nvs = True
        self.assertTrue(self.execute()["nvs"]["changed"])
        outcome = tx.recover(self.folder, self.device)
        self.assertTrue(outcome["nvs"]["changed"])
        self.assertNotEqual(bytes(self.device.data), self.snapshot)
        self.original_ranges()

    def test_protected_static_damage_is_not_rewritten_or_booted(self):
        self.device.change_static = True
        with self.assertRaises(tx.RecoveryRequired):
            self.execute()
        self.assertEqual(self.state()["phase"], "needs_recovery")
        self.assertEqual(self.device.boots, [])
        self.original_ranges()
        self.assertTrue(all(offset in {tx.TABLE_START, tx.UPPER_START} for offset, _ in self.device.writes))

    def test_identity_mismatch_before_write_does_nothing(self):
        self.device.identity_value = "2" * 64
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.device.writes, [])
        self.assertIsNone(self.state())

    def test_identity_is_rechecked_after_first_durable_intent(self):
        original = tx.Journal.save
        def swap(journal, phase, **details):
            original(journal, phase, **details)
            if phase == "upper_write_intent":
                self.device.identity_value = "2" * 64
        with patch.object(tx.Journal, "save", swap):
            with self.assertRaises(tx.RecoveryRequired):
                self.execute()
        self.assertEqual(self.device.writes, [])
        self.device.identity_value = "1" * 64
        tx.recover(self.folder, self.device)

    def test_simulation_mode_rejects_hardware_adapter_before_identity(self):
        self.device.simulation_only = False
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.device.events, [])

    def test_snapshot_drift_before_first_write_is_rejected(self):
        self.device.data[-1] ^= 1
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.device.writes, [])
        self.assertIsNone(self.state())

    def test_missing_or_wrong_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            tx.execute(self.folder, self.device, initial_snapshot=self.snapshot[:-1])
        self.assertEqual(self.device.events, [])

    def test_first_intent_disk_error_prevents_all_writes(self):
        with self.journal_fault("upper_write_intent"):
            self.assertEqual(self.execute()["result"], "failed_before_write")
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.state()["phase"], "prepared")

    def test_later_journal_error_restores(self):
        with self.journal_fault("upper_verified"):
            self.assertTrue(self.execute()["restored"])
        self.original_ranges()

    def test_restore_intent_disk_error_keeps_saved_entry(self):
        self.execute()
        count = len(self.device.writes)
        with self.journal_fault("restore_table_intent"):
            with self.assertRaises(tx.RecoveryRequired):
                tx.recover(self.folder, self.device)
        self.assertEqual(len(self.device.writes), count)
        tx.recover(self.folder, self.device)
        self.original_ranges()

    def test_final_journal_error_can_resume_without_rewriting(self):
        self.execute()
        with self.journal_fault("restored"):
            with self.assertRaises(tx.RecoveryRequired):
                tx.recover(self.folder, self.device)
        count = len(self.device.writes)
        tx.recover(self.folder, self.device)
        self.assertEqual(len(self.device.writes), count)

    def test_failed_original_boot_keeps_pending_recovery(self):
        self.execute()
        self.device.bad_boot.add("original")
        with self.assertRaises(tx.RecoveryRequired):
            tx.recover(self.folder, self.device)
        self.assertEqual(self.state()["phase"], "needs_recovery")
        self.original_ranges()
        self.device.bad_boot.clear()
        tx.recover(self.folder, self.device)

    def test_corrupt_recovery_bytes_are_rejected_before_device_access(self):
        (self.folder / tx.RECOVERY_FILES["upper"]).write_bytes(b"x" * tx.UPPER_BYTES)
        with self.assertRaises(ValueError):
            tx.recover(self.folder, self.device)
        self.assertEqual(self.device.events, [])

    def test_corrupt_candidate_prevents_first_write(self):
        (self.folder / tx.CANDIDATE_FILES["table"]).write_bytes(b"x" * tx.TABLE_BYTES)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.device.events, [])

    def test_plan_range_and_mode_changes_are_rejected(self):
        for key, value in (("write_ranges", []), ("mode", "live"), ("candidate_table_sha256", "0" * 64)):
            metadata = deepcopy(self.plan.metadata)
            metadata[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(self.plan, metadata=metadata).validate()

    def test_journal_checksum_and_plan_binding_are_checked(self):
        self.execute()
        path = self.folder / "recovery.json"
        state = json.loads(path.read_bytes())
        state["plan_id"] = "0" * 64
        path.write_bytes(tx.canonical(state))
        count = len(self.device.writes)
        with self.assertRaises(ValueError):
            tx.recover(self.folder, self.device)
        self.assertEqual(len(self.device.writes), count)

    def test_execute_cannot_replay_an_existing_run(self):
        self.execute()
        count = len(self.device.writes)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(len(self.device.writes), count)

    def test_recovery_without_intent_performs_no_device_operation(self):
        outcome = tx.recover(self.folder, self.device)
        self.assertEqual(outcome["result"], "no_persisted_write_intent")
        self.assertEqual(self.device.events, [])

    def test_concurrent_operation_is_rejected_and_os_lock_releases(self):
        with tx.operation_lock(self.folder):
            with self.assertRaises(OSError):
                self.execute()
        self.assertEqual(self.device.events, [])
        self.assertEqual(self.execute()["result"], "candidate_committed_simulation")

    def test_short_full_readback_is_rejected(self):
        self.device.short_full = True
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.device.writes, [])

    def test_existing_plan_directory_is_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            tx.persist_plan(self.folder, self.plan)


class RetainedResourceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upper_path = ROOT / "tmp/e135b-migration-20261029/candidate/assets.bin"
        cls.table_path = ROOT / "tmp/e136-n32r16-layout-20261030/tables/02-upper-assets-8m.bin"
        if not cls.upper_path.is_file() or not cls.table_path.is_file():
            raise unittest.SkipTest("retained E1.18 and E1.36 inputs required")
        old = ROOT / "tmp/e135b-migration-20261029/old"
        cls.snapshot = synthetic_snapshot((old / "partition-table.bin").read_bytes(), (old / "assets.bin").read_bytes())
        cls.upper, cls.table = cls.upper_path.read_bytes(), cls.table_path.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143c-file-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "run"
        self.plan = tx.make_plan(self.snapshot, self.upper, self.table,
                                 identity_sha256=tx.sha(b"synthetic-file-device"), run_label="e143c-sim-file")
        tx.persist_plan(self.folder, self.plan)

    def test_file_backed_roundtrip_uses_real_retained_resources(self):
        device = FileFlash.create(self.folder, self.snapshot)
        self.assertEqual(tx.execute(self.folder, device, initial_snapshot=self.snapshot)["result"], "candidate_committed_simulation")
        for name in tx.CANDIDATE_FILES.values():
            (self.folder / name).unlink()
        restarted = FileFlash(self.folder)
        self.assertTrue(tx.recover(self.folder, restarted)["restored"])
        self.assertEqual(restarted.read_full(), self.snapshot)

    def test_file_backed_power_loss_recovery_from_new_adapter(self):
        device = FileFlash.create(self.folder, self.snapshot, interrupt_at="table_write_intent")
        with self.assertRaises(SimulatedPowerLoss):
            tx.execute(self.folder, device, initial_snapshot=self.snapshot)
        partial_table = device.read_flash(tx.TABLE_START, tx.TABLE_BYTES)
        self.assertNotEqual(partial_table, self.plan.candidate_table)
        self.assertNotEqual(partial_table, self.plan.original_table)
        self.assertEqual(device.writes[-1]["bytes_written"], 128)
        self.assertTrue(device.writes[-1]["interrupted"])
        for name in tx.CANDIDATE_FILES.values():
            (self.folder / name).unlink()
        restarted = FileFlash(self.folder)
        self.assertTrue(tx.recover(self.folder, restarted)["restored"])
        self.assertEqual(restarted.read_full(), self.snapshot)

    def test_simulator_rejects_unplanned_range(self):
        device = FileFlash.create(self.folder, self.snapshot)
        with self.assertRaises(ValueError):
            device.write_flash(0x10000, b"\x00" * 4096)


class EntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("e143c1_exercise_test", ROOT / "scripts/exercise-expression-e1-43c1-migration.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def test_paths_stay_inside_repository_tmp(self):
        for label in ("../COM4", "e143c-sim-../escape", "live", "e143c-sim-x/y"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.module.folder_for(label)
        self.assertEqual(self.module.folder_for("e143c-sim-check").parent, ROOT / "tmp")

    def test_only_simulation_commands_are_exposed(self):
        source = (ROOT / "scripts/exercise-expression-e1-43c1-migration.py").read_text(encoding="utf-8")
        self.assertIn('choices=("simulate", "recover-simulation")', source)
        for forbidden in ("EsptoolDevice", "import serial", "run_officejs", "subprocess"):
            self.assertNotIn(forbidden, source)

    def test_public_two_step_simulation_recovers_after_interrupted_table_write(self):
        if not (ROOT / "tmp/e136-n32r16-layout-20261030/tables/02-upper-assets-8m.bin").is_file():
            self.skipTest("retained local preflight inputs required")
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143c-cli-test-") as parent:
            folder = Path(parent) / "e143c-sim-cli-test"
            # Test the simulation entrypoint with retained binary inputs, while
            # historical deployment preflight keeps rejecting newer source trees.
            paths = [ROOT / name for name in (
                "tmp/e135b-migration-20261029/old/partition-table.bin",
                "tmp/e135b-migration-20261029/old/assets.bin",
                "tmp/e135b-migration-20261029/candidate/assets.bin",
                "tmp/e136-n32r16-layout-20261030/tables/02-upper-assets-8m.bin")]
            data = tuple(path.read_bytes() for path in paths)
            recorded = json.loads((ROOT / "artifacts/expression-upper-migration-e1-43c-20261103/preflight.json").read_bytes())["inputs"]
            self.assertEqual(tx.sha(data[0] + b"\xff" * (4096 - len(data[0]))), tx.LEGACY_TABLE_SECTOR_SHA256)
            for path, raw in zip(paths[1:], data[1:]):
                self.assertEqual(tx.sha(raw), recorded[path.relative_to(ROOT).as_posix()]["sha256"])
            with patch.object(self.module, "verified_inputs", return_value=data):
                started = self.module.run_new(folder, "table_write_intent")
            self.assertEqual(started["result"], "simulated_power_loss")
            self.assertEqual(started["recovery_phase"], "table_write_intent")
            for name in tx.CANDIDATE_FILES.values():
                (folder / name).unlink()
            with patch.object(self.module, "verified_inputs", side_effect=AssertionError("recovery must not load preflight")):
                resumed = self.module.run_recovery(folder)
            self.assertEqual(resumed["result"], "restored_simulation")
            self.assertTrue(resumed["restored"])
            self.assertTrue(resumed["full_image_equal"])
            self.assertEqual(resumed["full_readback_sha256"], resumed["snapshot_sha256"])
            device = FileFlash(folder)
            self.assertEqual(tx.sha(device.read_full()), tx.sha(synthetic_snapshot(
                (ROOT / "tmp/e135b-migration-20261029/old/partition-table.bin").read_bytes(),
                (ROOT / "tmp/e135b-migration-20261029/old/assets.bin").read_bytes())))

    def test_result_sequence_continues_after_highest_direct_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143c-result-test-") as parent:
            folder = Path(parent)
            (folder / "result-001.json").write_text("{}", encoding="utf-8")
            (folder / "result-003.json").write_text("{}", encoding="utf-8")
            path = self.module.save_result(folder, {"result": "synthetic"})
            self.assertEqual(path.name, "result-004.json")


if __name__ == "__main__":
    unittest.main()
