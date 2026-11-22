"""E3c-3b backup/restore preparation: sealed fixtures and simulated COM4 only."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol import storage_continuous_mmap_e3c3b_baseline as baseline
from protocol.storage_continuous_mmap_experiment import APP_START, FLASH_BYTES, Journal, load_plan, recover, sha
from protocol.storage_upper_experiment_esptool import FlashReadTransferError, _NORMAL_BOOT_MARKERS


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


prepare = module("e3c3b_preparation_tests", "scripts/prepare-expression-e1-43b-e3c3b-deployment.py")
audit = module("e3c3b_preparation_audit_tests", "scripts/audit-expression-e1-43b-e3c3b-deployment.py")
BUILD = None
MIGRATION = None
SNAPSHOT = None
FLASH_LOG = None
SECURITY_LOG = None


def setUpModule():
    global BUILD, MIGRATION, SNAPSHOT, FLASH_LOG, SECURITY_LOG
    BUILD = baseline.load_e3c3b_probe_inputs()
    MIGRATION = baseline.migrated.load_migrated_baseline()
    historical = ROOT / "tmp/e143b-e3c3a-20261107-live1"
    # This reconstructed fixture is only used with simulated IO, never flashing.
    raw = bytearray((historical / "snapshot-32m.bin").read_bytes())
    accepted = baseline.repo_bytes(ROOT, baseline.BUILD_DIRECTORY / "xiaozhi.bin")
    raw[APP_START:APP_START + len(accepted)] = accepted
    SNAPSHOT = bytes(raw)
    FLASH_LOG = next((historical / "logs").glob("*-flash-id.log")).read_text(encoding="utf-8")
    SECURITY_LOG = next((historical / "logs").glob("*-security-info.log")).read_text(encoding="utf-8")


def boot_log():
    return "\n".join(_NORMAL_BOOT_MARKERS) + "\napp_init: ELF file SHA256: e0941ae3c...\n"


class FakeReadOnly:
    def __init__(self, folder, snapshot, *, identity=None, read_error=None, boot_error=None, wrong_boot=False):
        self.folder, self.snapshot = folder, snapshot
        self.observed_identity = identity or MIGRATION["identity_sha256"]
        self.read_error, self.boot_error, self.wrong_boot = read_error, boot_error, wrong_boot
        self.calls, self.writes = [], []
        for name in ("captures", "logs", "payloads"):
            (folder / name).mkdir()
        (folder / "logs/001-flash-id.log").write_text(FLASH_LOG, encoding="utf-8")
        (folder / "logs/002-security-info.log").write_text(SECURITY_LOG, encoding="utf-8")

    def identity(self):
        self.calls.append("identity")
        return self.observed_identity

    def read_full(self):
        self.calls.append("read_full")
        if self.read_error:
            raise self.read_error
        mac = audit.previous._MAC.search(FLASH_LOG).group(1)
        for index, offset in enumerate(range(0, FLASH_BYTES, 0x100000)):
            sequence = 3 + index * 2
            path = self.folder / "captures" / f"{sequence:03d}-full-readback-{offset:08x}-00100000.bin"
            path.write_bytes(self.snapshot[offset:offset + 0x100000])
            text = ("esptool.py v4.12.0\nSerial port COM4\nMAC: " + mac + "\n"
                    + f"Read 1048576 bytes at 0x{offset:08x} in 25.4 seconds\n")
            (self.folder / "logs" / f"{sequence + 1:03d}-full-readback.log").write_text(text, encoding="utf-8")
        return self.snapshot

    def boot_original(self):
        self.calls.append("boot_original")
        if self.boot_error:
            raise self.boot_error
        path = self.folder / "captures/067-normal-boot.log"
        path.write_text(boot_log().replace("e0941ae3c", "000000000") if self.wrong_boot else boot_log(), encoding="utf-8")
        (self.folder / "logs/068-normal-boot-capture.log").write_text(
            f"Capture complete: {path} (lines=10)\n", encoding="utf-8")
        prepare.validate_boot_log(path.read_text(encoding="utf-8"))
        return True


class BaselineTests(unittest.TestCase):
    def validate(self, snapshot= None, identity=None):
        return baseline._validate_snapshot(SNAPSHOT if snapshot is None else snapshot,
            identity or MIGRATION["identity_sha256"], MIGRATION, BUILD["accepted"])

    def test_current_app_matches_reviewed_report_and_hardware_acceptance(self):
        self.assertEqual(BUILD["accepted"]["app"]["sha256"], baseline.APP_SHA256_EXPECTED)
        self.assertEqual(len(BUILD["candidate"]), 4_494_096)
        result = self.validate()
        self.assertTrue(result["historical_app_range_skipped"])
        self.assertEqual(result["original_app"]["bytes"], 4_489_232)

    def test_old_or_corrupt_app_is_rejected(self):
        raw = bytearray(SNAPSHOT)
        raw[APP_START] ^= 1
        with self.assertRaisesRegex(ValueError, "current App BIN"):
            self.validate(bytes(raw))

    def test_nvs_update_is_recorded_independently(self):
        raw = bytearray(SNAPSHOT)
        raw[0x9000] ^= 1
        result = self.validate(bytes(raw))
        self.assertEqual(result["nvs"]["sha256"], sha(bytes(raw[0x9000:0xD000])))

    def test_wrong_device_and_truncated_snapshot_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "device identity"):
            self.validate(identity="0" * 64)
        with self.assertRaisesRegex(ValueError, "complete 32 MiB"):
            self.validate(SNAPSHOT[:-1])

    def test_each_migrated_static_range_is_checked(self):
        for row in MIGRATION["protected_ranges"]:
            if row["name"] == "app":
                continue
            with self.subTest(range=row["name"]):
                raw = bytearray(SNAPSHOT)
                raw[row["offset"]] ^= 1
                with self.assertRaisesRegex(ValueError, "migrated static baseline"):
                    self.validate(bytes(raw))

    def test_public_reference_inputs_cannot_be_replaced(self):
        with self.assertRaises(TypeError):
            baseline.validate_e3c3b_snapshot(SNAPSHOT, MIGRATION["identity_sha256"], build_reference={})

    def test_changed_build_report_or_acceptance_is_rejected(self):
        for name in ("BUILD_REPORT_SHA256", "ACCEPTANCE_SHA256"):
            with self.subTest(field=name), patch.object(baseline, name, "0" * 64):
                with self.assertRaisesRegex(ValueError, "pinned report digest"):
                    baseline.load_e3c3b_build_reference()

    def test_changed_probe_report_or_audit_is_rejected(self):
        for name in ("PROBE_REPORT_SHA256", "PROBE_AUDIT_SHA256"):
            with self.subTest(field=name), patch.object(baseline, name, "0" * 64):
                with self.assertRaisesRegex(ValueError, "pinned report digest"):
                    baseline.load_e3c3b_probe_inputs()

    def test_changed_candidate_bytes_fail_artifact_check(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            root = Path(directory)
            (root / "candidate.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "artifact differs"):
                baseline._record(root, Path("candidate.bin"), {"bytes": 8, "sha256": "0" * 64})

    def test_path_escape_and_redirected_ancestor_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "relative child"):
            baseline.repo_bytes(ROOT, Path("../outside"))
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            root = Path(directory)
            (root / "file.bin").write_bytes(b"sample")
            original = Path.lstat
            class Reparse:
                st_mode = 0o100644
                st_file_attributes = 0x400
            def redirected(path):
                return Reparse() if path == root else original(path)
            with patch.object(Path, "lstat", redirected):
                with self.assertRaisesRegex(ValueError, "reparse point"):
                    baseline.safe_path(root / "file.bin", root)


class PreparationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e3c3b-preparation-test-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tmp").mkdir()
        self.label = "e143b-e3c3b-20261107-test"
        self.folder = self.root / "tmp" / self.label
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(prepare, "ROOT", self.root))
        self.stack.enter_context(patch.object(audit, "ROOT", self.root))
        self.stack.enter_context(patch.object(baseline, "load_e3c3b_probe_inputs", return_value=BUILD))
        self.stack.enter_context(patch.object(baseline, "load_e3c3b_build_reference", return_value=BUILD["accepted"]))
        self.stack.enter_context(patch.object(baseline.migrated, "load_migrated_baseline", return_value=MIGRATION))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.device = None

    def run_preparation(self, snapshot=None, **options):
        def factory(folder, expected):
            self.assertEqual(expected, MIGRATION["identity_sha256"])
            self.device = FakeReadOnly(folder, SNAPSHOT if snapshot is None else snapshot, **options)
            return self.device
        return prepare.prepare_live(self.folder, self.label, device_factory=factory)

    def test_real_plan_and_independent_audit_with_simulated_device(self):
        result = self.run_preparation()
        audited = audit.audit_preparation(self.folder)
        self.assertEqual(audited["result"], "passed")
        self.assertEqual(audited["plan_id"], result["plan_id"])
        self.assertEqual(audited["app_erase_end_exclusive"], 0x45A000)
        self.assertEqual(len(audited["com4_read_transfers"]), 32)
        self.assertEqual(self.device.calls, ["identity", "read_full", "identity", "boot_original"])
        self.assertEqual(self.device.writes, [])
        self.assertEqual((self.folder / "snapshot-32m.bin").read_bytes(), SNAPSHOT)
        self.assertFalse(result["device_written"])

    def test_nvs_update_preserved_in_snapshot_and_receipt(self):
        raw = bytearray(SNAPSHOT)
        raw[0x9001] ^= 1
        result = self.run_preparation(snapshot=bytes(raw))
        self.assertEqual(result["baseline_reference"]["nvs"]["sha256"], sha(bytes(raw[0x9000:0xD000])))
        self.assertEqual(audit.audit_preparation(self.folder)["result"], "passed")

    def test_identity_mismatch_never_reads_flash_and_attempts_boot(self):
        with self.assertRaisesRegex(ValueError, "identity differs"):
            self.run_preparation(identity="0" * 64)
        self.assertEqual(self.device.calls, ["identity", "boot_original"])
        self.assertFalse((self.folder / "plan.json").exists())

    def test_read_failure_attempts_boot_and_preserves_failure(self):
        with self.assertRaisesRegex(OSError, "simulated read failed"):
            self.run_preparation(read_error=OSError("simulated read failed"))
        observation = json.loads((self.folder / "device-observation.json").read_bytes())
        self.assertTrue(observation["normal_boot_observed"])
        self.assertIn("preparation_error", observation)
        self.assertFalse((self.folder / "preflight.json").exists())

    def test_read_and_boot_failures_are_both_recorded(self):
        with self.assertRaisesRegex(OSError, "read failure"):
            self.run_preparation(read_error=OSError("read failure"), boot_error=OSError("boot failure"))
        observation = json.loads((self.folder / "device-observation.json").read_bytes())
        self.assertIn("read failure", observation["preparation_error"])
        self.assertIn("boot failure", observation["boot_error"])
        self.assertFalse(observation["normal_boot_observed"])

    def test_wrong_boot_elf_cannot_create_a_successful_plan(self):
        with self.assertRaisesRegex(ValueError, "ELF identity"):
            self.run_preparation(wrong_boot=True)
        self.assertFalse((self.folder / "plan.json").exists())
        self.assertFalse((self.folder / "preflight.json").exists())

    def test_static_corruption_saves_snapshot_then_boots_without_plan(self):
        raw = bytearray(SNAPSHOT)
        raw[0x8000] ^= 1
        with self.assertRaisesRegex(ValueError, "migrated static baseline"):
            self.run_preparation(snapshot=bytes(raw))
        self.assertTrue((self.folder / "snapshot-32m.bin").exists())
        self.assertEqual(self.device.calls[-1], "boot_original")
        self.assertFalse((self.folder / "rollback-app.bin").exists())

    def test_existing_run_and_wrong_path_fail_before_device_creation(self):
        self.folder.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_preparation()
        self.assertIsNone(self.device)
        with self.assertRaisesRegex(ValueError, "direct child"):
            prepare._safe_run_folder(self.root / "tmp/other", self.label)

    def test_read_only_transport_rejects_all_write_entry_points(self):
        device = prepare.ReadOnlyAppDevice.__new__(prepare.ReadOnlyAppDevice)
        with self.assertRaises(PermissionError):
            device.write_app(b"payload")
        with self.assertRaises(PermissionError):
            device.write_flash(APP_START, b"payload")
        with self.assertRaises(PermissionError):
            device._invoke("bad", ("--chip", "esp32s3", "write_flash", "0x10000", "payload.bin"))

    def test_truncated_read_retains_partial_evidence_without_retry(self):
        calls = []
        self.stack.enter_context(patch.object(prepare.previous.RUNNER, "ROOT", self.root))
        def factory(folder, expected):
            self.device = prepare.ReadOnlyAppDevice(folder, expected)
            self.stack.enter_context(patch.object(self.device, "identity", return_value=expected))
            self.boot_cleanup = self.stack.enter_context(patch.object(self.device, "boot_original", return_value=True))
            return self.device
        def truncated(label, args, **kwargs):
            calls.append((label, args))
            Path(args[-1]).write_bytes(b"partial packet")
            raise FlashReadTransferError("simulated truncated packet")
        with patch.object(prepare.previous.ReadOnlyAppDevice, "_invoke", side_effect=truncated):
            with self.assertRaisesRegex(OSError, "preparation stopped") as caught:
                prepare.prepare_live(self.folder, self.label, device_factory=factory)
        self.assertNotIsInstance(caught.exception, FlashReadTransferError)
        self.assertIsInstance(caught.exception.__cause__, FlashReadTransferError)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1][0], "read_flash")
        self.boot_cleanup.assert_called_once()
        captures = list((self.folder / "captures").glob("*.bin"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].read_bytes(), b"partial packet")
        self.assertFalse((self.folder / "preflight.json").exists())
        observation = json.loads((self.folder / "device-observation.json").read_bytes())
        self.assertTrue(observation["normal_boot_observed"])
        self.assertIn("preparation stopped", observation["preparation_error"])

    def test_independent_audit_rejects_tampered_rollback_and_unexpected_evidence(self):
        self.run_preparation()
        rollback = self.folder / "rollback-app.bin"
        raw = bytearray(rollback.read_bytes())
        raw[-1] ^= 1
        rollback.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "rollback App differs"):
            audit.audit_preparation(self.folder)
        rollback.write_bytes(SNAPSHOT[APP_START:0x45A000])
        (self.folder / "logs/999-write-app.log").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "write or erase"):
            audit.audit_preparation(self.folder)

    def test_read_log_must_match_snapshot_chunk_sequence(self):
        self.run_preparation()
        source = self.folder / "captures/003-full-readback-00000000-00100000.bin"
        source.rename(source.with_name("099-full-readback-00000000-00100000.bin"))
        with self.assertRaisesRegex(ValueError, "does not follow"):
            audit.audit_preparation(self.folder)

    def test_recovery_works_after_candidate_deletion_and_restores_full_snapshot(self):
        self.run_preparation()
        plan = load_plan(self.folder, execution=True)
        damaged = bytearray(SNAPSHOT)
        damaged[APP_START:APP_START + len(plan.candidate)] = plan.candidate
        (self.folder / "candidate-app-padded.bin").unlink()
        recovery_plan = load_plan(self.folder)
        journal = Journal(self.folder, recovery_plan)
        journal.save("restore_required")
        class RecoveryDevice:
            def identity(self):
                return MIGRATION["identity_sha256"]
            def write_app(self, raw):
                damaged[APP_START:APP_START + len(raw)] = raw
            def read_app(self, size):
                return bytes(damaged[APP_START:APP_START + size])
            def read_full(self):
                return bytes(damaged)
            def boot_original(self):
                return True
        result = recover(recovery_plan, RecoveryDevice(), journal)
        self.assertEqual(result["phase"], "restored")
        self.assertEqual(bytes(damaged), SNAPSHOT)


class BootLogTests(unittest.TestCase):
    def test_accepted_elf_and_normal_markers(self):
        self.assertEqual(prepare.validate_boot_log(boot_log())["elf_prefix"], "e0941ae3c")

    def test_wrong_elf_missing_markers_and_reboot_loop_fail(self):
        for text in (boot_log().replace("e0941ae3c", "cfe8c4b47"), "ELF file SHA256: e0941ae3c...",
                     boot_log() + boot_log(), boot_log() + "\nassert failed"):
            with self.subTest(text=text[:25]), self.assertRaisesRegex(ValueError, "boot markers or ELF"):
                prepare.validate_boot_log(text)


if __name__ == "__main__":
    unittest.main()
