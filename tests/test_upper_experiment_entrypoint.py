"""A7 entrypoint and legacy recovery boundaries; all device I/O is synthetic."""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
spec = importlib.util.spec_from_file_location(
    "upper_experiment_entrypoint", ROOT / "scripts/run-expression-upper-experiment-e1-43a6.py"
)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

from protocol import storage_upper_experiment as experiment
from protocol.storage_upper_experiment import sha
from protocol.storage_upper_experiment_esptool import APP_ROLLBACK_BYTES
from upper_probe_test_support import FakeDevice, make_plan, temporary_run


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.run_dir = self.enterContext(temporary_run("e143a7-entry-test-"))
        self.run_dir.rmdir()  # prepare creates the direct run directory exclusively.
        self.plan = replace(make_plan(), rollback_app=b"R" * APP_ROLLBACK_BYTES)
        FakeDevice.instances = []
        FakeDevice.invalid_snapshot = False
        FakeDevice.missing_reference = False
        FakeDevice.identity_ok = True

    def preflight(self):
        with patch.object(entry, "build_plan", return_value=self.plan), patch.object(entry, "EsptoolDevice", FakeDevice):
            return entry.preflight(self.run_dir)

    def test_path_rejects_root_escape_and_unknown_labels(self):
        for label in ("../e143a6-x", "e143a6-../../x", "C:\\other", "e143a6-x/y", "e143a6-", "unscoped"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                entry.run_dir_for(label)
        with self.assertRaises(ValueError):
            entry.run_dir_for("e143a6-live-20261102")
        self.assertEqual(entry.run_dir_for("e143a7-live-test").parent, (ROOT / "tmp").resolve())
        self.assertEqual(entry.run_dir_for("e143a6-live-20261102", recovery=True).parent,
                         (ROOT / "tmp").resolve())

    def test_preflight_reads_with_writes_disabled_and_boots_original(self):
        self.assertEqual(self.preflight(), 0)
        device = FakeDevice.instances[0]
        self.assertFalse(device.write_enabled)
        self.assertEqual(device.boot_count, 1)
        report = json.loads((self.run_dir / "preflight.json").read_text())
        self.assertEqual(report["plan_id"], self.plan.plan_id)
        self.assertFalse(report["safety"]["flash_written"])
        self.assertEqual(entry.load_persisted_plan(self.run_dir).plan_id, self.plan.plan_id)

    def test_preflight_static_drift_stops_and_boots_same_original_device(self):
        FakeDevice.invalid_snapshot = True
        with self.assertRaisesRegex(ValueError, "static protected"):
            self.preflight()
        self.assertFalse(FakeDevice.instances[0].write_enabled)
        self.assertEqual(FakeDevice.instances[0].boot_count, 1)
        self.assertFalse((self.run_dir / "preflight.json").exists())

    def test_unconfirmed_identity_does_not_receive_cleanup_reset(self):
        FakeDevice.invalid_snapshot = True
        FakeDevice.identity_ok = False
        with self.assertRaises(ValueError):
            self.preflight()
        self.assertEqual(FakeDevice.instances[0].boot_count, 0)

    def test_repeat_preflight_stops_before_any_new_device_instance(self):
        self.preflight()
        before = len(FakeDevice.instances)
        with self.assertRaises(FileExistsError):
            self.preflight()
        self.assertEqual(len(FakeDevice.instances), before)

    def test_execute_rejects_missing_preflight_before_device_creation(self):
        with patch.object(entry, "build_plan", return_value=self.plan):
            entry.prepare(self.run_dir)
        with patch.object(entry, "build_plan", return_value=self.plan), patch.object(entry, "EsptoolDevice") as device:
            with self.assertRaises(ValueError):
                entry.execute(self.run_dir)
            device.assert_not_called()

    def test_execute_rejects_changed_reviewed_plan_before_device_creation(self):
        self.preflight()
        changed = replace(self.plan, candidate_app=b"changed-candidate")
        with patch.object(entry, "build_plan", return_value=changed), patch.object(entry, "EsptoolDevice") as device:
            with self.assertRaisesRegex(ValueError, "freshly rechecked"):
                entry.execute(self.run_dir)
            device.assert_not_called()

    def test_existing_result_is_not_replayed(self):
        self.preflight()
        (self.run_dir / "result.json").write_text("{}")
        with patch.object(entry, "build_plan", return_value=self.plan), patch.object(entry, "EsptoolDevice") as device:
            with self.assertRaises(FileExistsError):
                entry.execute(self.run_dir)
            device.assert_not_called()

    def execute(self):
        with patch.object(entry, "build_plan", return_value=self.plan), patch.object(entry, "EsptoolDevice", FakeDevice):
            return entry.execute(self.run_dir)

    def test_execute_binds_one_write_snapshot_and_restores_exact_bytes(self):
        self.preflight()
        original = entry.run_experiment
        seen = []

        def inspect(*args, **kwargs):
            seen.append(kwargs["initial_snapshot"])
            self.assertIs(seen[-1], FakeDevice.instances[-1].last_snapshot)
            return original(*args, **kwargs)

        with patch.object(entry, "run_experiment", side_effect=inspect):
            self.assertEqual(self.execute(), 0)
        device = FakeDevice.instances[-1]
        self.assertEqual(device.snapshot_count, 2)  # One write snapshot, one restored snapshot.
        self.assertEqual(device.app, self.plan.rollback_app)
        self.assertEqual(device.page, self.plan.rollback_page)
        self.assertEqual(device.boot_count, 1)
        result = json.loads((self.run_dir / "result.json").read_text())
        manifest_sha = sha((self.run_dir / "snapshot/manifest.json").read_bytes())
        self.assertEqual(result["result"], "probe_passed_and_restored")
        self.assertEqual(result["snapshot_manifest_sha256"], manifest_sha)
        self.assertEqual(result["journal"]["schema"], experiment.BOUND_JOURNAL_SCHEMA)
        self.assertEqual(result["journal"]["snapshot_manifest_sha256"], manifest_sha)
        self.assertEqual(result["runtime_validation"]["binding"]["snapshot_manifest_sha256"], manifest_sha)

    def test_missing_snapshot_or_failed_reference_load_boots_original_without_writes(self):
        for cause in ("missing", "changed"):
            with self.subTest(cause=cause):
                if cause == "changed":
                    # A fresh run is needed because partial snapshot evidence is retained.
                    self.run_dir = self.enterContext(temporary_run("e143a7-entry-test-"))
                    self.run_dir.rmdir()
                self.preflight()
                FakeDevice.missing_reference = cause == "missing"
                try:
                    if cause == "changed":
                        with patch.object(entry, "load_snapshot", side_effect=ValueError("changed reference")):
                            with self.assertRaisesRegex(ValueError, "changed reference"):
                                self.execute()
                    else:
                        with self.assertRaisesRegex(ValueError, "missing the exact"):
                            self.execute()
                    self.assertEqual(FakeDevice.instances[-1].writes, [])
                    self.assertEqual(FakeDevice.instances[-1].boot_count, 1)
                    self.assertFalse((self.run_dir / "recovery.json").exists())
                finally:
                    FakeDevice.missing_reference = False

    def test_execute_static_drift_is_rejected_before_writes(self):
        self.preflight()
        FakeDevice.invalid_snapshot = True
        with self.assertRaisesRegex(ValueError, "static protected"):
            self.execute()
        self.assertEqual(FakeDevice.instances[-1].writes, [])
        self.assertEqual(FakeDevice.instances[-1].boot_count, 1)

    def test_preexisting_journal_prevents_device_creation(self):
        self.preflight()
        journal = experiment.AtomicJournal(self.run_dir / "recovery.json")
        protected = FakeDevice.instances[-1].last_snapshot.protected_sha256
        experiment._record(journal, self.plan, "prepared", protected)
        with patch.object(entry, "build_plan", return_value=self.plan), patch.object(entry, "EsptoolDevice") as device:
            with self.assertRaisesRegex(ValueError, "journal already exists"):
                entry.execute(self.run_dir)
            device.assert_not_called()

    def test_swapped_run_label_is_rejected_before_device_creation(self):
        self.preflight()
        path = self.run_dir / "preflight.json"
        value = json.loads(path.read_text())
        value["run_label"] = "e143a7-another-run"
        path.write_text(json.dumps(value))
        with patch.object(entry, "build_plan", return_value=self.plan), patch.object(entry, "EsptoolDevice") as device:
            with self.assertRaisesRegex(ValueError, "different plan"):
                entry.execute(self.run_dir)
            device.assert_not_called()

    def test_legacy_recovery_uses_saved_plan_without_a7_build_or_snapshot(self):
        old = self.enterContext(temporary_run("e143a6-entry-test-"))
        entry.persist_plan_inputs(old, self.plan)
        journal = experiment.AtomicJournal(old / "recovery.json")
        device = FakeDevice(plan=self.plan, write_enabled=True)
        experiment._record(journal, self.plan, "prepared", device.snapshot(self.plan).protected_sha256)
        with patch.object(entry, "build_plan", side_effect=AssertionError("new build must not be needed")), \
                patch.object(entry, "EsptoolDevice", FakeDevice):
            self.assertEqual(entry.recover_run(old), 0)
        result = json.loads((old / "recovery-result.json").read_text())
        self.assertEqual(result["schema"], "deskbot.upper_pattern.recovery_result.e1_43a6/v1")
        self.assertEqual(result["journal"]["phase"], "complete")
        self.assertFalse((old / "snapshot").exists())

    def test_missing_or_legacy_build_report_is_rejected_before_device_creation(self):
        self.preflight()
        for error in (ValueError("old A6 candidate"), FileNotFoundError("A7 report missing")):
            with self.subTest(error=error), patch.object(entry, "build_plan", side_effect=error), \
                    patch.object(entry, "EsptoolDevice") as device:
                with self.assertRaises(type(error)):
                    entry.execute(self.run_dir)
                device.assert_not_called()


class CandidateGateTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(temporary_run("e143a7-gate-test-"))
        self.source = self.root / "firmware/body"
        names = ("main/Kconfig.projbuild", "main/boards/esp_vocat/dev_tools.cc",
                 "main/boards/esp_vocat/upper_pattern_probe.h", "main/CMakeLists.txt")
        inventory = {}
        for name in names:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("synthetic " + name).encode())
            inventory[name] = sha(path.read_bytes())
        manifest = self.source / "SOURCE_MANIFEST.sha256"
        manifest.write_text("".join(digest + "  " + name + "\n" for name, digest in inventory.items()), encoding="utf-8")
        self.candidate = self.root / "tmp/e143a7-probe-test/evidence/final/on/xiaozhi.bin"
        self.candidate.parent.mkdir(parents=True)
        self.candidate.write_bytes(b"synthetic-new-a7-binary")
        protected = {name: True for name in ("bootloader/bootloader.bin", "partition_table/partition-table.bin",
                                            "expression_assets.bin", "srmodels/srmodels.bin", "ota_data_initial.bin")}
        rounds = {label: {"protected_match_e1_42": protected, "new_task_symbol_present": label == "on",
                          "old_diagnostic_task_present": False, "app_headroom_bytes": 1024 * 1024}
                  for label in ("off", "on")}
        rounds["off"]["disabled_image_comparison"] = {"only_elf_identity_checksum_digest_changed": True}
        rounds["on"].update(evidence_directory=self.candidate.parent.relative_to(self.root).as_posix(),
                             artifacts={"xiaozhi.bin": {"bytes": self.candidate.stat().st_size,
                                                        "sha256": sha(self.candidate.read_bytes())}})
        self.report = {"edition": "E1.43a-7", "result": "isolated_default_off_and_diagnostic_builds_passed",
                       "source_sha256": inventory, "source_manifest_sha256": sha(manifest.read_bytes()), "rounds": rounds}
        self.enterContext(patch.object(entry, "ROOT", self.root))
        self.enterContext(patch.object(entry, "CANDIDATE_PATH", self.candidate))

    def test_complete_current_a7_report_is_accepted(self):
        self.assertEqual(entry.verify_candidate(self.report), self.candidate.read_bytes())

    def test_missing_or_old_report_is_rejected(self):
        for value in ({}, {**self.report, "edition": "E1.43a-2"}):
            with self.subTest(edition=value.get("edition")), self.assertRaisesRegex(ValueError, "old A6"):
                entry.verify_candidate(value)

    def test_omitted_nonrequired_source_is_rejected(self):
        del self.report["source_sha256"]["main/CMakeLists.txt"]
        with self.assertRaisesRegex(ValueError, "complete manifest inventory"):
            entry.verify_candidate(self.report)

    def test_source_or_manifest_drift_is_rejected(self):
        path = self.source / "main/CMakeLists.txt"
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source differs"):
            entry.verify_candidate(self.report)

    def test_historical_digest_in_firmware_is_rejected(self):
        path = self.source / "main/boards/esp_vocat/upper_pattern_probe.h"
        path.write_bytes(b"kExpectedLowProtectedSha")
        self.report["source_sha256"]["main/boards/esp_vocat/upper_pattern_probe.h"] = sha(path.read_bytes())
        manifest = self.source / "SOURCE_MANIFEST.sha256"
        manifest.write_text("".join(digest + "  " + name + "\n"
                                    for name, digest in self.report["source_sha256"].items()), encoding="utf-8")
        self.report["source_manifest_sha256"] = sha(manifest.read_bytes())
        with self.assertRaisesRegex(ValueError, "historical reference"):
            entry.verify_candidate(self.report)

    def test_isolation_protection_headroom_and_default_off_are_required(self):
        for mutate in (lambda r: r["rounds"]["on"]["protected_match_e1_42"].update({"expression_assets.bin": False}),
                       lambda r: r["rounds"]["off"].update(new_task_symbol_present=True),
                       lambda r: r["rounds"]["on"].update(old_diagnostic_task_present=True),
                       lambda r: r["rounds"]["on"].update(app_headroom_bytes=1024 * 1024 - 1),
                       lambda r: r["rounds"]["off"].pop("disabled_image_comparison")):
            report = deepcopy(self.report)
            mutate(report)
            with self.subTest(report=report), self.assertRaises(ValueError):
                entry.verify_candidate(report)

    def test_wrong_candidate_directory_digest_or_missing_binary_is_rejected(self):
        for mutate in (lambda r: r["rounds"]["on"].update(evidence_directory="tmp/old/on"),
                       lambda r: r["rounds"]["on"]["artifacts"]["xiaozhi.bin"].update(sha256=sha(b"wrong"))):
            report = deepcopy(self.report)
            mutate(report)
            with self.assertRaises(ValueError):
                entry.verify_candidate(report)
        self.candidate.unlink()
        with self.assertRaisesRegex(ValueError, "binary is missing"):
            entry.verify_candidate(self.report)


if __name__ == "__main__":
    unittest.main()
