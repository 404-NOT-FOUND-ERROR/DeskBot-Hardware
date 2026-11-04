"""Exercise App-only write boundaries and recovery without opening a serial port."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_continuous_mmap_experiment import (
    APP_START, FLASH_BYTES, AppPlan, Journal, execute, load_plan, make_plan, persist_plan, recover,
)
from test_storage_continuous_mmap import fixture, encode


class FakeDevice:
    def __init__(self, plan, snapshot, journal, fault=""):
        self.plan, self.data, self.journal, self.fault = plan, bytearray(snapshot), journal, fault
        self.writes, self.boots = [], 0

    def identity(self):
        return "0" * 64 if self.fault == "identity" else self.plan.metadata["identity_sha256"]

    def write_app(self, raw):
        assert self.journal.load()["phase"] in {"restore_required", "restoring"}
        assert raw in (self.plan.candidate, self.plan.rollback)
        self.writes.append(raw)
        self.data[APP_START:APP_START + len(raw)] = raw
        if self.fault == "candidate_write" and len(self.writes) == 1:
            raise OSError("interrupted write")
        if self.fault == "restore_write" and raw == self.plan.rollback:
            raise OSError("interrupted restore")

    def read_app(self, size):
        raw = bytes(self.data[APP_START:APP_START + size])
        return bytes([raw[0] ^ 1]) + raw[1:] if self.fault == "candidate_readback" and len(self.writes) == 1 else raw

    def read_full(self):
        raw = bytearray(self.data)
        if self.fault == "static":
            raw[-1] ^= 1
        if self.fault == "nvs":
            raw[0x9000] ^= 1
        return bytes(raw)

    def capture(self):
        if self.fault == "capture":
            raise TimeoutError("capture failed")
        rows, _ = fixture()
        text = "\n".join(encode(row["kind"], row["payload"]) for row in rows)
        return text + "\nassert failed" if self.fault == "panic" else text

    def boot_original(self):
        self.boots += 1
        return self.fault != "boot"


class AppOnlyExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = bytearray(b"\xff" * FLASH_BYTES)
        raw[0x1000000:0x1800000] = bytes(range(256)) * (0x800000 // 256)
        cls.snapshot = bytes(raw)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143b-transaction-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.plan = make_plan(self.snapshot, b"\xe9" * 8193, original_app_bytes=4097,
                             identity_sha256="1" * 64, run_label="e143b-synthetic")
        persist_plan(self.folder, self.plan)
        self.journal = Journal(self.folder, self.plan)

    def run_case(self, fault=""):
        device = FakeDevice(self.plan, self.snapshot, self.journal, fault)
        return device, execute(self.plan, device, self.journal, initial_snapshot=self.snapshot)

    def test_success_only_switches_and_restores_exact_app_union(self):
        device, result = self.run_case()
        self.assertTrue(result["probe_passed"])
        self.assertTrue(result["restored"])
        self.assertFalse(result["physical_interaction_accepted"])
        self.assertEqual(device.writes, [self.plan.candidate, self.plan.rollback])
        self.assertEqual(bytes(device.data), self.snapshot)
        self.assertEqual(self.plan.metadata["erase_bytes"], 12288)
        self.assertEqual(device.boots, 1)

    def test_candidate_write_failure_restores_before_return(self):
        device, result = self.run_case("candidate_write")
        self.assertFalse(result["probe_passed"])
        self.assertTrue(result["restored"])
        self.assertEqual(bytes(device.data), self.snapshot)
        self.assertIn("interrupted write", result["error"])

    def test_wrong_candidate_readback_restores(self):
        _, result = self.run_case("candidate_readback")
        self.assertFalse(result["probe_passed"])
        self.assertTrue(result["restored"])

    def test_capture_failure_and_crash_both_restore(self):
        for fault in ("capture", "panic"):
            with self.subTest(fault=fault):
                if self.journal.path.exists():
                    self.journal.path.unlink()
                _, result = self.run_case(fault)
                self.assertFalse(result["probe_passed"])
                self.assertTrue(result["restored"])

    def test_restore_failure_preserves_recovery_and_does_not_need_candidate(self):
        device = FakeDevice(self.plan, self.snapshot, self.journal, "restore_write")
        with self.assertRaises(OSError):
            execute(self.plan, device, self.journal, initial_snapshot=self.snapshot)
        self.assertEqual(self.journal.load()["phase"], "needs_recovery")
        (self.folder / "candidate-app-padded.bin").unlink()
        restored_plan = load_plan(self.folder)
        self.assertIsNone(restored_plan.candidate)
        device.fault = ""
        recover(restored_plan, device, Journal(self.folder, restored_plan))
        self.assertEqual(self.journal.load()["phase"], "restored")
        count = len(device.writes)
        recover(restored_plan, device, self.journal)
        self.assertEqual(len(device.writes), count)

    def test_static_change_and_missing_boot_remain_recovery_required(self):
        for fault in ("static", "boot"):
            with self.subTest(fault=fault):
                if self.journal.path.exists():
                    self.journal.path.unlink()
                with self.assertRaises(ValueError):
                    self.run_case(fault)
                self.assertEqual(self.journal.load()["phase"], "needs_recovery")

    def test_nvs_runtime_change_is_observed(self):
        _, result = self.run_case("nvs")
        self.assertTrue(result["restored"])
        self.assertTrue(result["recovery"]["details"]["nvs"]["changed"])

    def test_wrong_identity_and_snapshot_do_not_write(self):
        device = FakeDevice(self.plan, self.snapshot, self.journal, "identity")
        with self.assertRaises(ValueError):
            execute(self.plan, device, self.journal, initial_snapshot=self.snapshot)
        self.assertEqual(device.writes, [])
        self.assertIsNone(self.journal.load())
        with self.assertRaises(ValueError):
            execute(self.plan, device, self.journal, initial_snapshot=self.snapshot[:-1] + b"x")
        self.assertEqual(device.writes, [])

    def test_first_write_intent_must_be_durable(self):
        device = FakeDevice(self.plan, self.snapshot, self.journal)
        original = self.journal.save
        def save(phase, **details):
            if phase == "restore_required":
                raise OSError("journal cannot persist intent")
            original(phase, **details)
        with patch.object(self.journal, "save", side_effect=save):
            result = execute(self.plan, device, self.journal, initial_snapshot=self.snapshot)
        self.assertEqual(device.writes, [])
        self.assertFalse(result["restored"])
        self.assertTrue(result["unchanged_original_boot_observed"])
        self.assertEqual(device.boots, 1)

    def test_rollback_tampering_and_range_drift_rejected(self):
        (self.folder / "rollback-app.bin").write_bytes(b"x" * len(self.plan.rollback))
        with self.assertRaises(ValueError):
            load_plan(self.folder)
        metadata = copy.deepcopy(self.plan.metadata)
        metadata["static_ranges"][-1]["offset"] += 1
        with self.assertRaises(ValueError):
            AppPlan(metadata, self.plan.rollback).validate()

    def test_execute_replay_and_recovery_without_intent_rejected(self):
        device, _ = self.run_case()
        writes = len(device.writes)
        with self.assertRaises(ValueError):
            execute(self.plan, device, self.journal, initial_snapshot=self.snapshot)
        self.assertEqual(len(device.writes), writes)
        self.journal.path.unlink()
        with self.assertRaises(ValueError):
            recover(self.plan, device, self.journal)


class AdapterBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("continuous_app_runner_test", ROOT / "scripts/run-expression-continuous-mmap-e1-43b.py")
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def test_inherited_test_page_write_is_disabled(self):
        device = object.__new__(self.runner.AppDevice)
        with self.assertRaises(ValueError):
            device.write_flash(0x1000000, b"x" * 65536)

    def test_run_paths_are_fixed_to_repository_tmp(self):
        for label in ("e143a7-live", "../elsewhere", "e143b-../elsewhere", "COM8"):
            with self.assertRaises(ValueError):
                self.runner.folder_for(label)
        self.assertEqual(self.runner.folder_for("e143b-trial").parent, ROOT / "tmp")

    def test_accepted_snapshot_rejects_static_drift_but_allows_nvs(self):
        from protocol.storage_continuous_mmap_experiment import sha
        raw = bytearray(b"\xff" * FLASH_BYTES)
        live={"restored_ranges":{"nvs":{"offset":0x9000,"bytes":1,"sha256":"0"*64},
                                  "assets":{"offset":0x5ec000,"bytes":16,"sha256":sha(b"\xff"*16)}}}
        prep={"baseline_acceptance":{"path":"docs/architecture.md"},
              "original_app":{"bytes":1,"sha256":sha(b"\xff")}}
        values=[{"sealed_experiment_report":"docs/architecture.md"},live]
        raw[0x9000]=0
        with patch.object(self.runner.json,"loads",side_effect=values):
            self.runner.accepted_snapshot(bytes(raw),prep)
        raw[0x5ec000]=0
        with patch.object(self.runner.json,"loads",side_effect=values):
            with self.assertRaisesRegex(ValueError,"assets"):
                self.runner.accepted_snapshot(bytes(raw),prep)

    def test_adapter_accepts_only_persisted_app_payload_and_offset(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tmp",prefix="e143b-adapter-") as name:
            folder=Path(name)
            plan=make_plan(b"\xff"*FLASH_BYTES,b"\xe9"*4097,original_app_bytes=8193,
                           identity_sha256="1"*64,run_label="e143b-adapter")
            persist_plan(folder,plan)
            device=object.__new__(self.runner.AppDevice)
            device.run_dir,device.plan=folder,plan
            device._sequence,device._reserved_logs=0,set()
            device.identity=lambda: "1"*64
            calls=[]
            device._invoke=lambda label,args,**kwargs: calls.append((label,args))
            with self.assertRaises(ValueError):
                device.write_app(plan.candidate)
            self.assertEqual(calls,[])
            journal=Journal(folder,plan)
            journal.save("restore_required")
            device.write_app(plan.candidate)
            self.assertEqual(len(calls),1)
            argv=calls[0][1]
            self.assertEqual(argv[0],"write_flash")
            self.assertEqual(argv[-2],"0x10000")
            self.assertNotIn("--erase-all",argv)
            self.assertEqual(Path(argv[-1]).read_bytes(),plan.candidate)
            with self.assertRaises(ValueError):
                device.write_app(b"x"*len(plan.candidate))
            journal.save("restoring")
            with self.assertRaises(ValueError):
                device.write_app(plan.candidate)
            device.write_app(plan.rollback)
            self.assertEqual(len(calls),2)

    def test_recover_cli_loads_no_build_or_candidate(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tmp",prefix="e143b-adapter-") as name:
            folder=Path(name)
            plan=make_plan(b"\xff"*FLASH_BYTES,b"\xe9"*4097,original_app_bytes=8193,
                           identity_sha256="1"*64,run_label="e143b-recover")
            persist_plan(folder,plan)
            (folder/"candidate-app-padded.bin").unlink()
            journal=Journal(folder,plan)
            journal.save("restore_required")
            device=FakeDevice(plan,b"\xff"*FLASH_BYTES,journal)
            with patch.object(sys,"argv",["runner","recover","--run-label","e143b-recover"]), \
                 patch.object(self.runner,"folder_for",return_value=folder), \
                 patch.object(self.runner,"AppDevice",return_value=device), \
                 patch.object(self.runner,"load_preparation",side_effect=AssertionError("recovery imported builder")):
                self.assertEqual(self.runner.main(),0)
            self.assertEqual(journal.load()["phase"],"restored")


if __name__ == "__main__":
    unittest.main()
