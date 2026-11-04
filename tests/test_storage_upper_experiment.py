"""Offline fault-injection tests for the E1.43a single-page transaction."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import tempfile
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol import storage_upper_experiment as experiment
from protocol.storage_upper_pattern import PAGE_BYTES, PAGE_START, build_pattern


def make_plan() -> experiment.ExperimentPlan:
    protected = {name: experiment.sha(("protected:" + name).encode("ascii"))
                 for name in experiment.STATIC_PROTECTED_NAMES}
    return experiment.ExperimentPlan(
        expected_identity_sha256=experiment.sha(b"synthetic-COM4-identity"),
        rollback_app=bytes([0x42]) * 8192,
        rollback_page=bytes([0xA5]) * PAGE_BYTES,
        candidate_app=b"synthetic-diagnostic-app",
        pattern=build_pattern(),
        expected_static_protected_sha256=protected,
    )


class SimulatedDevice:
    def __init__(self, plan: experiment.ExperimentPlan):
        self.plan = plan
        self.identity_ok = True
        self.app = plan.rollback_app
        self.page = plan.rollback_page
        self.static_protected = dict(plan.expected_static_protected_sha256)
        self.nvs_sha256 = experiment.sha(b"nvs-before")
        self.snapshot_count = 0
        self.drift_static_on_snapshot: int | None = None
        self.partial_write_failures: dict[int, int] = {}
        self.partial_write_failures_by_payload: set[tuple[int, bytes]] = set()
        self.bad_readback_offsets: set[int] = set()
        self.probe_error: Exception | None = None
        self.change_nvs_during_probe = False
        self.change_nvs_during_boot = False
        self.boot_ok = True
        self.boot_count = 0
        self.writes: list[tuple[int, bytes]] = []
        self.events: list[str] = []

    def identity_matches(self, expected_sha256: str) -> bool:
        return self.identity_ok and expected_sha256 == self.plan.expected_identity_sha256

    def snapshot(self, plan: experiment.ExperimentPlan) -> experiment.DeviceSnapshot:
        self.events.append("snapshot")
        self.snapshot_count += 1
        if self.drift_static_on_snapshot == self.snapshot_count:
            self.static_protected["assets"] = experiment.sha(b"drifted-assets")
        protected = dict(self.static_protected)
        protected["nvs"] = self.nvs_sha256
        return experiment.DeviceSnapshot(
            identity_sha256=plan.expected_identity_sha256,
            chip="esp32s3",
            physical_flash_bytes=experiment.FLASH_BYTES,
            psram_bytes=16 * 1024 * 1024,
            secure_boot=False,
            flash_encryption=False,
            app_erase_bytes=self.app,
            upper_page=self.page,
            protected_sha256=protected,
        )

    def write_flash(self, offset: int, data: bytes) -> None:
        self.events.append("write")
        self.writes.append((offset, data))
        failures = self.partial_write_failures.get(offset, 0)
        fail_payload = (offset, data) in self.partial_write_failures_by_payload
        if fail_payload:
            self.partial_write_failures_by_payload.remove((offset, data))
        if offset == PAGE_START:
            target = bytearray(self.page)
            if failures or fail_payload:
                split = max(1, len(data) // 3)
                target[:split] = data[:split]
                self.page = bytes(target)
                if failures:
                    self.partial_write_failures[offset] = failures - 1
                raise OSError("simulated partial page write")
            if len(data) != PAGE_BYTES:
                raise ValueError("page write must cover exactly one page")
            self.page = data
            return
        if offset == experiment.APP_OFFSET:
            if len(data) > len(self.app):
                raise ValueError("App write exceeds rollback span")
            target = bytearray(data + bytes([0xFF]) * (len(self.app) - len(data)))
            if failures or fail_payload:
                split = max(1, len(target) // 3)
                self.app = bytes(target[:split] + bytearray(self.app[split:]))
                if failures:
                    self.partial_write_failures[offset] = failures - 1
                raise OSError("simulated partial App write")
            self.app = bytes(target)
            return
        raise ValueError("write outside the test page and App rollback span")

    def read_flash(self, offset: int, size: int) -> bytes:
        self.events.append("read")
        if offset == PAGE_START:
            value = self.page[:size]
        elif offset == experiment.APP_OFFSET:
            value = self.app[:size]
        else:
            raise ValueError("read outside the test page and App rollback span")
        if offset in self.bad_readback_offsets:
            self.bad_readback_offsets.remove(offset)
            if value:
                return bytes([value[0] ^ 0x01]) + value[1:]
        return value

    def capture_probe(self, timeout_seconds: int) -> str:
        if self.change_nvs_during_probe:
            self.nvs_sha256 = experiment.sha(b"nvs-after-probe")
        if self.probe_error is not None:
            raise self.probe_error
        return "probe-complete"

    def boot_original(self) -> bool:
        self.events.append("boot")
        self.boot_count += 1
        if self.change_nvs_during_boot:
            self.nvs_sha256 = experiment.sha(b"nvs-after-original-boot")
        return self.boot_ok


class SaveFailJournal(experiment.MemoryJournal):
    def __init__(self, phase: str, failures: int = 1):
        super().__init__()
        self.phase = phase
        self.failures = failures

    def save(self, state):
        if state.get("phase") == self.phase and self.failures:
            self.failures -= 1
            raise OSError("simulated journal write failure")
        super().save(state)


class UpperExperimentTests(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan()
        self.plan.validate()
        self.device = SimulatedDevice(self.plan)

    def assert_original_bytes(self):
        self.assertEqual(self.device.page, self.plan.rollback_page)
        self.assertEqual(self.device.app, self.plan.rollback_app)

    def run_plan(self, device=None, journal=None):
        return experiment.run_experiment(
            self.plan, device or self.device, journal or experiment.MemoryJournal(),
            lambda result: result == "probe-complete",
        )

    def test_success_restores_and_finishes_with_normal_app_boot(self):
        journal = experiment.MemoryJournal()
        self.device.change_nvs_during_probe = True
        self.device.change_nvs_during_boot = True
        result = self.run_plan(journal=journal)
        self.assertTrue(result.probe_passed)
        self.assertTrue(result.restored)
        self.assertTrue(result.journal_finalized)
        self.assertEqual(journal.load()["phase"], "complete")
        observation = journal.load()["nvs_observation"]
        self.assertEqual(observation["before_sha256"], experiment.sha(b"nvs-before"))
        self.assertEqual(observation["latest_sha256"], experiment.sha(b"nvs-after-probe"))
        self.assertTrue(observation["changed"])
        self.assert_original_bytes()
        self.assertEqual(self.device.events[-1], "boot")

    def test_completed_recovery_rechecks_bytes_then_leaves_original_app_running(self):
        journal = experiment.MemoryJournal()
        self.run_plan(journal=journal)
        self.device.events.clear()

        experiment.recover(self.plan, self.device, journal)

        self.assertEqual(self.device.events[-1], "boot")
        self.assertEqual(self.device.events.count("snapshot"), 1)

    def test_partial_app_write_failure_restores_both_ranges(self):
        self.device.partial_write_failures[experiment.APP_OFFSET] = 1
        journal = experiment.MemoryJournal()
        result = self.run_plan(journal=journal)
        self.assertFalse(result.probe_passed)
        self.assertTrue(result.restored)
        self.assertEqual(result.phase, "operation_failed_but_restored")
        self.assertEqual(journal.load()["phase"], "complete")
        self.assert_original_bytes()

    def test_pattern_readback_mismatch_restores(self):
        self.device.bad_readback_offsets.add(PAGE_START)
        result = self.run_plan()
        self.assertTrue(result.restored)
        self.assertIn("failed immediate full-page readback", result.error)
        self.assert_original_bytes()

    def test_probe_timeout_restores(self):
        self.device.probe_error = TimeoutError("probe timed out")
        result = self.run_plan()
        self.assertTrue(result.restored)
        self.assertIn("TimeoutError", result.error)
        self.assert_original_bytes()

    def test_nvs_change_does_not_block_static_protection_check(self):
        self.device.change_nvs_during_probe = True
        result = self.run_plan()
        self.assertTrue(result.restored)
        self.assertTrue(result.journal_finalized)

    def test_restore_started_journal_failure_does_not_stop_flash_restore(self):
        journal = SaveFailJournal("restore_started")
        result = self.run_plan(journal=journal)
        self.assertTrue(result.restored)
        self.assertTrue(result.journal_finalized)
        self.assertEqual(journal.load()["phase"], "complete")
        self.assert_original_bytes()

    def test_recovery_intent_journal_failure_prevents_first_flash_write(self):
        journal = SaveFailJournal("restore_required")
        with self.assertRaisesRegex(OSError, "simulated journal write failure"):
            self.run_plan(journal=journal)
        self.assertEqual(journal.load()["phase"], "prepared")
        self.assertEqual(self.device.writes, [])
        experiment.recover(self.plan, self.device, journal)
        self.assertEqual(journal.load()["phase"], "complete")
        self.assertEqual(self.device.writes, [])

    def test_complete_journal_failure_reports_verified_restore_and_can_retry(self):
        journal = SaveFailJournal("complete")
        result = self.run_plan(journal=journal)
        self.assertTrue(result.restored)
        self.assertFalse(result.journal_finalized)
        self.assertEqual(result.phase, "restored_but_journal_pending")
        self.assertEqual(journal.load()["phase"], "restore_verified")
        self.assert_original_bytes()
        experiment.recover(self.plan, self.device, journal)
        self.assertEqual(journal.load()["phase"], "complete")

    def test_preflight_protected_drift_refuses_before_any_flash_write(self):
        self.device.static_protected["assets"] = experiment.sha(b"unreviewed-assets")
        with self.assertRaisesRegex(ValueError, "static protected range"):
            self.run_plan()
        self.assertEqual(self.device.writes, [])

    def test_journal_from_different_static_plan_cannot_recover(self):
        journal = experiment.MemoryJournal()
        protected = experiment.validate_snapshot(self.plan, self.device.snapshot(self.plan))
        experiment._record(journal, self.plan, "prepared", protected)
        changed = dict(self.plan.expected_static_protected_sha256)
        changed["assets"] = experiment.sha(b"different-assets")
        other_plan = replace(self.plan, expected_static_protected_sha256=changed)
        with self.assertRaises(experiment.RecoveryRequired):
            experiment.recover(other_plan, self.device, journal)
        self.assertEqual(self.device.writes, [])

    def test_identity_drift_blocks_restore_then_recovery_succeeds(self):
        journal = experiment.MemoryJournal()
        original_write = self.device.write_flash

        def drift_after_test_page(offset, data):
            original_write(offset, data)
            if offset == PAGE_START and data == self.plan.pattern:
                self.device.identity_ok = False

        self.device.write_flash = drift_after_test_page
        with self.assertRaises(experiment.RecoveryRequired):
            self.run_plan(journal=journal)
        self.assertEqual(journal.load()["phase"], "restore_required")
        self.assertNotEqual(self.device.page, self.plan.rollback_page)
        self.device.identity_ok = True
        experiment.recover(self.plan, self.device, journal)
        self.assertEqual(journal.load()["phase"], "complete")
        self.assert_original_bytes()

    def test_static_protection_drift_blocks_restore_until_baseline_is_back(self):
        journal = experiment.MemoryJournal()
        self.device.drift_static_on_snapshot = 2
        with self.assertRaises(experiment.RecoveryRequired):
            self.run_plan(journal=journal)
        self.assertEqual(journal.load()["phase"], "restore_required")
        self.assert_original_bytes()
        self.device.static_protected = dict(self.plan.expected_static_protected_sha256)
        experiment.recover(self.plan, self.device, journal)
        self.assertEqual(journal.load()["phase"], "complete")
        self.assert_original_bytes()

    def test_failed_restore_retains_journal_for_retry(self):
        journal = experiment.MemoryJournal()
        self.device.probe_error = TimeoutError("probe timed out")
        self.device.partial_write_failures_by_payload.add((PAGE_START, self.plan.rollback_page))
        with self.assertRaises(experiment.RecoveryRequired):
            self.run_plan(journal=journal)
        self.assertEqual(journal.load()["phase"], "restore_required")
        self.assertNotEqual(self.device.page, self.plan.rollback_page)
        experiment.recover(self.plan, self.device, journal)
        self.assertEqual(journal.load()["phase"], "complete")
        self.assert_original_bytes()

    def test_prepared_crash_point_recovers_without_flash_writes(self):
        journal = experiment.MemoryJournal()
        protected = experiment.validate_snapshot(self.plan, self.device.snapshot(self.plan))
        experiment._record(journal, self.plan, "prepared", protected)
        write_count = len(self.device.writes)
        experiment.recover(self.plan, self.device, journal)
        self.assertEqual(journal.load()["phase"], "complete")
        self.assertEqual(len(self.device.writes), write_count)

    def test_every_durable_recovery_phase_can_restore_after_process_restart(self):
        phases = sorted(experiment.RECOVERY_PHASES - {"prepared"})
        for phase in phases:
            with self.subTest(phase=phase):
                plan = make_plan()
                device = SimulatedDevice(plan)
                journal = experiment.MemoryJournal()
                protected = experiment.validate_snapshot(plan, device.snapshot(plan))
                experiment._record(journal, plan, "prepared", protected)
                experiment._record(journal, plan, phase, protected)
                device.page = bytes([0x00]) * PAGE_BYTES
                device.app = bytes([0x99]) * len(plan.rollback_app)

                experiment.recover(plan, device, journal)

                self.assertEqual(journal.load()["phase"], "complete")
                self.assertEqual(device.page, plan.rollback_page)
                self.assertEqual(device.app, plan.rollback_app)
                self.assertEqual(device.boot_count, 1)

    def test_completed_journal_rechecks_identity_and_exact_rollback_bytes(self):
        journal = experiment.MemoryJournal()
        result = self.run_plan(journal=journal)
        self.assertTrue(result.restored)
        write_count = len(self.device.writes)

        self.device.page = bytes([0x00]) * PAGE_BYTES
        with self.assertRaisesRegex(experiment.RecoveryRequired, "completed journal"):
            experiment.recover(self.plan, self.device, journal)
        self.assertEqual(len(self.device.writes), write_count)

        self.device.page = self.plan.rollback_page
        self.device.app = bytes([0x00]) * len(self.plan.rollback_app)
        with self.assertRaisesRegex(experiment.RecoveryRequired, "completed journal"):
            experiment.recover(self.plan, self.device, journal)
        self.assertEqual(len(self.device.writes), write_count)

        self.device.app = self.plan.rollback_app
        self.device.identity_ok = False
        with self.assertRaisesRegex(experiment.RecoveryRequired, "completed journal"):
            experiment.recover(self.plan, self.device, journal)
        self.assertEqual(len(self.device.writes), write_count)

    def test_static_protection_is_locked_into_plan_id(self):
        changed = dict(self.plan.expected_static_protected_sha256)
        changed["assets"] = experiment.sha(b"other reviewed assets")
        other = replace(self.plan, expected_static_protected_sha256=changed)
        self.assertNotEqual(self.plan.plan_id, other.plan_id)

    def test_atomic_journal_roundtrip_and_shape_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.json"
            journal = experiment.AtomicJournal(path)
            protected = experiment.validate_snapshot(self.plan, self.device.snapshot(self.plan))
            experiment._record(journal, self.plan, "prepared", protected)
            loaded = journal.load()
            self.assertEqual(loaded["phase"], "prepared")
            self.assertEqual(loaded["sequence"], 1)
            for mutate in (
                lambda value: value.update(sequence=True),
                lambda value: value.update(sequence=0),
                lambda value: value["static_protected_sha256"].pop("assets"),
                lambda value: value["nvs_observation"].update(changed=True),
                lambda value: value.update(unexpected="field"),
            ):
                invalid = deepcopy(loaded)
                mutate(invalid)
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    journal.save(invalid)

    def test_plan_id_retains_legacy_v3_material(self):
        material = {"schema": "deskbot.upper_pattern.experiment_journal.v3",
                    "identity_sha256": self.plan.expected_identity_sha256,
                    "rollback_app_sha256": experiment.sha(self.plan.rollback_app),
                    "rollback_page_sha256": experiment.sha(self.plan.rollback_page),
                    "candidate_app_sha256": experiment.sha(self.plan.candidate_app),
                    "pattern_sha256": experiment.sha(self.plan.pattern),
                    "static_protected_sha256": self.plan.expected_static_protected_sha256}
        expected = experiment.sha(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii"))
        self.assertEqual(self.plan.plan_id, expected)

    def test_bound_run_shares_snapshot_and_retains_digest_at_every_phase(self):
        class RecordingJournal(experiment.MemoryJournal):
            def __init__(self):
                super().__init__()
                self.history = []

            def save(self, state):
                super().save(state)
                self.history.append(deepcopy(state))

        journal = RecordingJournal()
        snapshot = self.device.snapshot(self.plan)
        digest = experiment.sha(b"saved-manifest")
        seen = []

        def factory(actual):
            seen.append(actual)
            return lambda log: log == "probe-complete"

        result = experiment.run_experiment(self.plan, self.device, journal, probe_validator_factory=factory,
                                          initial_snapshot=snapshot, snapshot_manifest_sha256=digest)
        self.assertIs(seen[0], snapshot)
        self.assertEqual(self.device.snapshot_count, 2)
        self.assertTrue(result.probe_passed and result.restored and result.journal_finalized)
        self.assertGreaterEqual(len(journal.history), 10)
        for state in journal.history:
            self.assertEqual(state["schema"], experiment.BOUND_JOURNAL_SCHEMA)
            self.assertEqual(state["snapshot_manifest_sha256"], digest)

    def test_bound_run_requires_initial_snapshot_and_factory(self):
        for kwargs in ({"probe_validator": lambda log: True},
                       {"probe_validator_factory": lambda snapshot: lambda log: True}):
            with self.subTest(kwargs=tuple(kwargs)), self.assertRaisesRegex(ValueError, "bound journal requires"):
                experiment.run_experiment(self.plan, self.device, experiment.MemoryJournal(),
                                          snapshot_manifest_sha256=experiment.sha(b"manifest"), **kwargs)
        self.assertEqual(self.device.writes, [])

    def test_invalid_factory_result_prevents_journal_and_writes(self):
        journal = experiment.MemoryJournal()
        with self.assertRaisesRegex(ValueError, "return a callable"):
            experiment.run_experiment(self.plan, self.device, journal, probe_validator_factory=lambda snapshot: False,
                                      initial_snapshot=self.device.snapshot(self.plan),
                                      snapshot_manifest_sha256=experiment.sha(b"manifest"))
        self.assertIsNone(journal.load())
        self.assertEqual(self.device.writes, [])

    def test_binding_cannot_change_or_upgrade_a_legacy_journal(self):
        protected = experiment.validate_snapshot(self.plan, self.device.snapshot(self.plan))
        for binding in (None, experiment.sha(b"manifest")):
            journal = experiment.MemoryJournal()
            experiment._record(journal, self.plan, "prepared", protected, snapshot_manifest_sha256=binding)
            original = journal.load()
            with self.assertRaisesRegex(experiment.RecoveryRequired, "binding cannot change"):
                experiment._record(journal, self.plan, "restore_required", protected,
                                   snapshot_manifest_sha256=experiment.sha(b"other"))
            self.assertEqual(journal.load(), original)

    def test_bound_recovery_needs_no_snapshot_files_and_keeps_binding(self):
        digest = experiment.sha(b"manifest")
        for phase in experiment.RECOVERY_PHASES:
            with self.subTest(phase=phase):
                device = SimulatedDevice(self.plan)
                journal = experiment.MemoryJournal()
                protected = experiment.validate_snapshot(self.plan, device.snapshot(self.plan))
                experiment._record(journal, self.plan, phase, protected, snapshot_manifest_sha256=digest)
                if phase != "prepared":
                    device.page = b"x" * PAGE_BYTES
                    device.app = b"x" * len(self.plan.rollback_app)
                experiment.recover(self.plan, device, journal)
                self.assertEqual(journal.load()["phase"], "complete")
                self.assertEqual(journal.load()["snapshot_manifest_sha256"], digest)
                self.assertEqual(device.page, self.plan.rollback_page)
                self.assertEqual(device.app, self.plan.rollback_app)

    def test_bound_journal_shape_and_digest_validation(self):
        journal = experiment.MemoryJournal()
        protected = experiment.validate_snapshot(self.plan, self.device.snapshot(self.plan))
        experiment._record(journal, self.plan, "prepared", protected,
                           snapshot_manifest_sha256=experiment.sha(b"manifest"))
        valid = journal.load()
        for mutate in (lambda v: v.pop("snapshot_manifest_sha256"),
                       lambda v: v.update(snapshot_manifest_sha256="A" * 64),
                       lambda v: v.update(snapshot_manifest_sha256=True),
                       lambda v: v.update(schema=experiment.JOURNAL_SCHEMA),
                       lambda v: v.update(schema="unknown")):
            bad = deepcopy(valid)
            mutate(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                journal.save(bad)

    def test_invalid_binding_rejected_before_snapshot_or_writes(self):
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            experiment.run_experiment(self.plan, self.device, experiment.MemoryJournal(),
                                      probe_validator_factory=lambda snapshot: lambda log: True,
                                      snapshot_manifest_sha256="bad")
        self.assertEqual(self.device.snapshot_count, 0)
        self.assertEqual(self.device.writes, [])


if __name__ == "__main__":
    unittest.main()
