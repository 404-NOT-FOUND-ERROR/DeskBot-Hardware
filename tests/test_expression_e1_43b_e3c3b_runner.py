"""E3c-3b runner: real plans/transactions and simulated device IO."""
from __future__ import annotations

import contextlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("e3c3b_runner_tests", ROOT / "scripts/run-expression-continuous-mmap-e3c3b.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

from protocol.storage_continuous_mmap_experiment import APP_START, FLASH_BYTES, NVS_START, SIZE, START, Journal, make_plan, persist_plan, sha
from test_storage_continuous_mmap import encode, fixture

SNAPSHOT = CANDIDATE = PROBE_LOG = None


def setUpModule():
    global SNAPSHOT, CANDIDATE, PROBE_LOG
    raw = bytearray(b"\xff" * FLASH_BYTES)
    raw[START:START + SIZE] = bytes(range(256)) * (SIZE // 256)
    app = (ROOT / runner.baseline.BUILD_DIRECTORY / "xiaozhi.bin").read_bytes()
    raw[APP_START:APP_START + len(app)] = app
    SNAPSHOT = bytes(raw)
    CANDIDATE = runner.baseline.load_e3c3b_probe_inputs()["candidate"]
    rows, _ = fixture()
    PROBE_LOG = "\n".join(encode(row["kind"], row["payload"]) for row in rows) + "\n"


class Device:
    def __init__(self, snapshot, plan, folder):
        self.flash, self.plan, self.folder = bytearray(snapshot), plan, folder
        self.writes, self.boots = [], 0
        self.fail_candidate_read = self.fail_restore_read = False

    def identity(self):
        return self.plan.metadata["identity_sha256"]

    def boot_original(self):
        self.boots += 1
        return True

    def read_full(self):
        return bytes(self.flash)

    def read_app(self, size):
        if self.fail_candidate_read and len(self.writes) == 1:
            raise OSError("candidate read interrupted")
        if self.fail_restore_read and len(self.writes) > 1:
            self.fail_restore_read = False
            raise OSError("restore read interrupted")
        return bytes(self.flash[APP_START:APP_START + size])

    def write_app(self, raw):
        phase = Journal(self.folder, self.plan).load()["phase"]
        if phase not in {"restore_required", "restoring"}:
            raise AssertionError("missing durable restore intent")
        self.writes.append(raw)
        self.flash[APP_START:APP_START + len(raw)] = raw

    def capture(self):
        return PROBE_LOG


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="e3c3b-runner-test-", dir=ROOT / "tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "tmp").mkdir()
        self.archive = self.root / "archive"
        self.archive.mkdir()
        self.label = "e143b-e3c3b-20261107-test"
        self.folder = self.root / "tmp" / self.label
        self.folder.mkdir()
        self.plan = make_plan(SNAPSHOT, CANDIDATE, original_app_bytes=runner.baseline.APP_BYTES_EXPECTED,
                              identity_sha256="b" * 64, run_label=self.label)
        persist_plan(self.folder, self.plan)
        self.audited = {"result": "passed", "plan_id": self.plan.plan_id}
        self.audit_path = self.archive / "audit.json"
        self.audit_path.write_text(json.dumps(self.audited), encoding="utf-8")
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(runner, "ROOT", self.root))
        stack.enter_context(patch.object(runner, "ARCHIVE", self.archive))
        self.audit_mock = stack.enter_context(patch.object(runner.AUDIT, "audit_preparation", return_value=self.audited))
        self.device = Device(SNAPSHOT, self.plan, self.folder)

    def run_probe(self):
        return runner.execute_prepared(self.label, self.audit_path, self.plan.plan_id,
                                       device_factory=lambda *_: self.device)

    def test_offline_check_recomputes_audit_without_creating_device(self):
        with patch.object(runner, "E3c3bAppDevice") as factory:
            result = runner.check_prepared(self.label, self.audit_path)
        self.assertEqual(result["result"], "ready")
        self.assertFalse(result["device_opened"])
        factory.assert_not_called()
        self.audit_mock.assert_called_once_with(self.folder)

    def test_changed_audit_or_candidate_rejected_before_device(self):
        self.audit_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "saved audit differs"):
            runner.check_prepared(self.label, self.audit_path)
        self.audit_path.write_text(json.dumps(self.audited), encoding="utf-8")
        (self.folder / "candidate-app-padded.bin").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            runner.check_prepared(self.label, self.audit_path)

    def test_wrong_plan_or_existing_transaction_never_creates_device(self):
        factory = Mock()
        with self.assertRaisesRegex(PermissionError, "exact plan"):
            runner.execute_prepared(self.label, self.audit_path, "wrong", device_factory=factory)
        Journal(self.folder, self.plan).save("restore_required")
        with self.assertRaisesRegex(FileExistsError, "already started"):
            runner.execute_prepared(self.label, self.audit_path, self.plan.plan_id, device_factory=factory)
        factory.assert_not_called()

    def test_real_transaction_restores_snapshot_and_keeps_fresh_nvs(self):
        self.device.flash[NVS_START] ^= 1
        fresh = bytes(self.device.flash)
        result = self.run_probe()
        self.assertTrue(result["probe_passed"])
        self.assertTrue(result["restored"])
        self.assertFalse(result["physical_interaction_accepted"])
        self.assertEqual(result["execution_snapshot_sha256"], sha(fresh))
        self.assertEqual(bytes(self.device.flash), fresh)
        self.assertEqual(self.device.writes, [self.plan.candidate, self.plan.rollback])

    def test_candidate_read_interruption_still_restores_original(self):
        self.device.fail_candidate_read = True
        result = self.run_probe()
        self.assertFalse(result["probe_passed"])
        self.assertTrue(result["restored"])
        self.assertEqual(bytes(self.device.flash), SNAPSHOT)

    def test_recovery_continues_after_interruption_and_candidate_deletion(self):
        self.device.fail_restore_read = True
        with self.assertRaisesRegex(OSError, "restore read interrupted"):
            self.run_probe()
        self.assertEqual(Journal(self.folder, self.plan).load()["phase"], "needs_recovery")
        (self.folder / "candidate-app-padded.bin").unlink()
        self.audit_path.unlink()
        self.audit_mock.side_effect = AssertionError("recovery must not load the audit")
        result = runner.recover_prepared(self.label, device_factory=lambda *_: self.device)
        self.assertEqual(result["phase"], "restored")
        self.assertEqual(bytes(self.device.flash), SNAPSHOT)
        self.assertTrue((self.folder / "recovery-result-001.json").exists())

    def test_fresh_read_failure_attempts_boot_without_write_intent(self):
        self.device.read_full = Mock(side_effect=OSError("fresh read failed"))
        with self.assertRaisesRegex(OSError, "fresh read failed"):
            self.run_probe()
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.device.boots, 2)
        saved = json.loads((self.folder / "execute-failure.json").read_bytes())
        self.assertTrue(saved["boot_cleanup"]["normal_boot_observed"])
        self.assertFalse((self.folder / "recovery.json").exists())

    def test_wrong_identity_stops_before_writes(self):
        self.device.identity = lambda: "0" * 64
        with self.assertRaisesRegex(ValueError, "identity differs"):
            self.run_probe()
        self.assertEqual(self.device.writes, [])

    def test_static_or_app_changes_stop_before_first_write(self):
        self.device.flash[APP_START] ^= 1
        with self.assertRaisesRegex(ValueError, "current App differs"):
            self.run_probe()
        self.assertEqual(self.device.writes, [])

    def test_recovery_requires_current_rollback_and_pending_journal(self):
        with self.assertRaisesRegex(ValueError, "no pending"):
            runner.recover_prepared(self.label)
        fake = Mock(rollback=b"old App")
        with self.assertRaisesRegex(ValueError, "accepted E3c-3b"):
            runner._require_current_rollback(fake)

    def test_boot_checks_current_elf_and_adapter_disables_other_flash_methods(self):
        device = runner.E3c3bAppDevice.__new__(runner.E3c3bAppDevice)
        device._capture_serial = Mock(return_value="wrong ELF")
        with self.assertRaisesRegex(ValueError, "boot markers or ELF"):
            device.boot_original()
        for name, args in (("write_flash", (0, b"x")), ("read_flash", (0, 1)), ("snapshot", (object(),))):
            with self.subTest(method=name), self.assertRaises(ValueError):
                getattr(device, name)(*args)
        device._write_armed = False
        with self.assertRaises(PermissionError):
            device.write_app(b"unreviewed")


if __name__ == "__main__":
    unittest.main()
