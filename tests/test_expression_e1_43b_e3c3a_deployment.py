"""Offline tests for E1.43b E3c-3a live-preparation boundaries."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "expression_e143b_e3c3a_prepare_tests",
    ROOT / "scripts/prepare-expression-e1-43b-e3c3a-deployment.py",
)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class FakeDevice:
    def __init__(self, snapshot, identity, *, boot_ok=True, identity_error=None,
                 identity_values=None):
        self.snapshot = snapshot
        self._identity = identity
        self.boot_ok = boot_ok
        self.identity_error = identity_error
        self.identity_values = list(identity_values or [])
        self.expected_identity = None
        self.calls = []
        self.writes = []

    def identity(self):
        self.calls.append("identity")
        if self.identity_error is not None:
            raise self.identity_error
        value = self.identity_values.pop(0) if self.identity_values else self._identity
        if self.expected_identity is not None and value != self.expected_identity:
            raise ValueError("COM4 identity changed during read-only preparation")
        return value

    def read_full(self):
        self.calls.append("read_full")
        self.identity()
        self.identity()
        return self.snapshot

    def boot_original(self):
        self.calls.append("boot_original")
        return self.boot_ok

    def write_app(self, _payload):
        self.writes.append("write_app")
        raise AssertionError("preparation must not write Flash")


class LivePreparationTests(unittest.TestCase):
    def test_live_adapter_rejects_every_write_entry_point(self):
        device = object.__new__(prepare.ReadOnlyAppDevice)
        with self.assertRaisesRegex(PermissionError, "read-only"):
            device.write_app(b"candidate")
        with self.assertRaisesRegex(PermissionError, "read-only"):
            device.write_flash(prepare.APP_START, b"candidate")
        with self.assertRaisesRegex(PermissionError, "read-only"):
            device._invoke("unsafe", ("write_flash", "0x10000", "candidate.bin"))
        with self.assertRaisesRegex(PermissionError, "read-only"):
            device._invoke("unsafe", ("--chip", "esp32s3", "write_flash", "0x10000", "candidate.bin"))
        with self.assertRaisesRegex(PermissionError, "read-only"):
            device._invoke("unsafe", ("--port", "COM4", "erase_flash"))

    def test_live_preparation_saves_same_run_rollback_without_flash_write(self):
        identity = "a" * 64
        current_app = b"accepted current E3c-3a App"
        snapshot = bytearray(b"\xff" * prepare.FLASH_BYTES)
        snapshot[prepare.APP_START:prepare.APP_START + len(current_app)] = current_app
        snapshot = bytes(snapshot)
        candidate = b"audited probe App"
        audit = {"result": "passed", "report": "artifacts/build.json"}
        build = {"candidate": candidate, "report_sha256": "b" * 64,
                 "candidate_sha256": prepare.sha(candidate), "candidate_path": ROOT / "candidate.bin",
                 "audit": audit}
        baseline = {
            "identity_sha256": identity,
            "layout": "upper-assets-8m",
            "migration_reference": {"path": "migration.json", "sha256": "c" * 64},
            "migration_plan_id": "d" * 64,
            "build_reference": {"path": "baseline.json", "sha256": "e" * 64},
            "original_app": {"bytes": len(current_app), "sha256": prepare.sha(current_app)},
            "nvs": {"sha256": prepare.sha(snapshot[0x9000:0xD000])},
        }
        device = FakeDevice(snapshot, identity)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            run_label = "e143b-e3c3a-20261107-test"
            folder = root / "tmp" / run_label
            build["candidate_path"] = root / "candidate.bin"
            with (mock.patch.object(prepare, "ROOT", root),
                  mock.patch.object(prepare, "_load_build_inputs", return_value=build),
                  mock.patch.object(prepare.baseline.migrated, "load_migrated_baseline",
                                    return_value={"identity_sha256": identity}),
                  mock.patch.object(prepare.baseline, "validate_e3c3a_snapshot", return_value=baseline)):
                result = prepare.prepare_live(folder, run_label, Path("build.json"),
                                              device_factory=lambda _, expected: setattr(
                                                  device, "expected_identity", expected) or device)

            self.assertEqual(device.calls,
                             ["identity", "read_full", "identity", "identity", "identity", "boot_original"])
            self.assertEqual(device.writes, [])
            self.assertFalse(result["device_written"])
            self.assertEqual((folder / "snapshot-32m.bin").read_bytes(), snapshot)
            saved_plan = json.loads((folder / "plan.json").read_bytes())
            self.assertEqual(saved_plan["metadata"]["snapshot_sha256"], prepare.sha(snapshot))
            rollback = (folder / "rollback-app.bin").read_bytes()
            self.assertEqual(rollback, snapshot[prepare.APP_START:
                                                 prepare.APP_START + saved_plan["metadata"]["erase_bytes"]])
            receipt = json.loads((folder / "preflight.json").read_bytes())
            self.assertEqual(receipt["result"], "snapshot_validated_rollback_prepared")
            self.assertFalse(receipt["flash_written"])

    def test_identity_mismatch_boots_device_without_reading_snapshot(self):
        device = FakeDevice(b"unused", "f" * 64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            label = "e143b-e3c3a-20261107-wrong"
            folder = root / "tmp" / label
            with (mock.patch.object(prepare, "ROOT", root),
                  mock.patch.object(prepare, "_load_build_inputs", return_value={}),
                  mock.patch.object(prepare.baseline.migrated, "load_migrated_baseline",
                                    return_value={"identity_sha256": "a" * 64})):
                with self.assertRaisesRegex(ValueError, "identity"):
                    prepare.prepare_live(folder, label, Path("build.json"),
                                         device_factory=lambda _, expected: setattr(
                                             device, "expected_identity", expected) or device)
            self.assertEqual(device.calls, ["identity", "boot_original"])
            self.assertEqual(device.writes, [])
            self.assertFalse((folder / "preflight.json").exists())

    def test_identity_read_error_attempts_to_boot_original(self):
        device = FakeDevice(b"unused", "a" * 64,
                            identity_error=OSError("serial identity read failed"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            label = "e143b-e3c3a-20261107-identity-error"
            folder = root / "tmp" / label
            with (mock.patch.object(prepare, "ROOT", root),
                  mock.patch.object(prepare, "_load_build_inputs", return_value={}),
                  mock.patch.object(prepare.baseline.migrated, "load_migrated_baseline",
                                    return_value={"identity_sha256": "a" * 64})):
                with self.assertRaisesRegex(OSError, "identity read failed"):
                    prepare.prepare_live(folder, label, Path("build.json"),
                                         device_factory=lambda _, expected: setattr(
                                             device, "expected_identity", expected) or device)
            self.assertEqual(device.calls, ["identity", "boot_original"])
            self.assertEqual(device.writes, [])
            self.assertTrue((folder / "device-observation.json").is_file())

    def test_run_path_must_be_a_fresh_direct_child_of_tmp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            with mock.patch.object(prepare, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "direct child"):
                    prepare._safe_run_folder(root / "outside" / "run",
                                             "e143b-e3c3a-20261107-test")

    def test_baseline_mismatch_retains_snapshot_without_creating_a_plan(self):
        identity = "a" * 64
        snapshot = b"\xff" * prepare.FLASH_BYTES
        device = FakeDevice(snapshot, identity)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            label = "e143b-e3c3a-20261107-mismatch"
            folder = root / "tmp" / label
            with (mock.patch.object(prepare, "ROOT", root),
                  mock.patch.object(prepare, "_load_build_inputs", return_value={}),
                  mock.patch.object(prepare.baseline.migrated, "load_migrated_baseline",
                                    return_value={"identity_sha256": identity}),
                  mock.patch.object(prepare.baseline, "validate_e3c3a_snapshot",
                                    side_effect=ValueError("snapshot baseline mismatch"))):
                with self.assertRaisesRegex(ValueError, "snapshot baseline mismatch"):
                    prepare.prepare_live(folder, label, Path("build.json"),
                                         device_factory=lambda _, expected: setattr(
                                             device, "expected_identity", expected) or device)
            self.assertEqual(device.calls,
                             ["identity", "read_full", "identity", "identity", "identity", "boot_original"])
            self.assertEqual(device.writes, [])
            self.assertEqual((folder / "snapshot-32m.bin").read_bytes(), snapshot)
            self.assertTrue((folder / "device-observation.json").is_file())
            self.assertFalse((folder / "plan.json").exists())
            self.assertFalse((folder / "rollback-app.bin").exists())

    def test_normal_boot_failure_does_not_create_a_plan(self):
        identity = "a" * 64
        snapshot = b"\xff" * prepare.FLASH_BYTES
        device = FakeDevice(snapshot, identity, boot_ok=False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            label = "e143b-e3c3a-20261107-boot-failure"
            folder = root / "tmp" / label
            with (mock.patch.object(prepare, "ROOT", root),
                  mock.patch.object(prepare, "_load_build_inputs", return_value={}),
                  mock.patch.object(prepare.baseline.migrated, "load_migrated_baseline",
                                    return_value={"identity_sha256": identity}),
                  mock.patch.object(prepare.baseline, "validate_e3c3a_snapshot",
                                    return_value={"identity_sha256": identity})):
                with self.assertRaisesRegex(ValueError, "normal boot was not observed"):
                    prepare.prepare_live(folder, label, Path("build.json"),
                                         device_factory=lambda _, expected: setattr(
                                             device, "expected_identity", expected) or device)
            self.assertEqual(device.writes, [])
            self.assertTrue((folder / "device-observation.json").is_file())
            self.assertFalse((folder / "plan.json").exists())

    def test_identity_drift_during_full_read_attempts_to_boot_original(self):
        identity = "a" * 64
        device = FakeDevice(b"unused", identity, identity_values=[identity, "f" * 64])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            label = "e143b-e3c3a-20261107-identity-drift"
            folder = root / "tmp" / label
            with (mock.patch.object(prepare, "ROOT", root),
                  mock.patch.object(prepare, "_load_build_inputs", return_value={}),
                  mock.patch.object(prepare.baseline.migrated, "load_migrated_baseline",
                                    return_value={"identity_sha256": identity})):
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    prepare.prepare_live(folder, label, Path("build.json"),
                                         device_factory=lambda _, expected: setattr(
                                             device, "expected_identity", expected) or device)
            self.assertEqual(device.calls, ["identity", "read_full", "identity", "boot_original"])
            self.assertEqual(device.writes, [])
            self.assertTrue((folder / "device-observation.json").is_file())
            self.assertFalse((folder / "plan.json").exists())


if __name__ == "__main__":
    unittest.main()
