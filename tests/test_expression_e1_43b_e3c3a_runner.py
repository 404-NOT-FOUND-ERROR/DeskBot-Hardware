"""Offline checks for the E3c-3a-bound E1.43b execute/recover entrypoint."""
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
    "expression_e143b_e3c3a_runner_tests",
    ROOT / "scripts/run-expression-continuous-mmap-e3c3a.py",
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from protocol.storage_continuous_mmap_experiment import (
    APP_START, FLASH_BYTES, NVS_START, SIZE, START, Journal, make_plan, persist_plan, sha,
)
from test_storage_continuous_mmap import encode, fixture


class FakePlan:
    def __init__(self, run_label="e143b-e3c3a-20261107-test"):
        self.plan_id = "a" * 64
        self.metadata = {"identity_sha256": "b" * 64, "run_label": run_label,
                         "candidate_bytes": 1234}
        self.rollback = b"original-app"


class FakeDevice:
    def __init__(self, current_app=b"original-app"):
        self.current_app = current_app
        self.calls = []

    def identity(self):
        self.calls.append("identity")
        return "b" * 64

    def read_app(self, size):
        self.calls.append(("read_app", size))
        return self.current_app

    def read_full(self):
        self.calls.append("read_full")
        return b"fresh device snapshot"

    def boot_original(self):
        self.calls.append("boot_original")
        return True


class RunnerEntryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "tmp").mkdir()
        self.label = "e143b-e3c3a-20261107-test"
        self.folder = self.root / "tmp" / self.label
        self.folder.mkdir()
        self.plan = FakePlan(self.label)
        self.audit = {"result": "passed", "plan_id": self.plan.plan_id}
        self.audit_path = self.root / "audit.json"
        self.audit_path.write_text(json.dumps(self.audit), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_execute_requires_exact_plan_id_before_creating_device(self):
        factory = mock.Mock()
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "ARCHIVE", self.root),
              mock.patch.object(runner, "_load_audited_plan",
                                return_value=(self.plan, self.audit, "c" * 64))):
            with self.assertRaisesRegex(PermissionError, "exact plan ID"):
                runner.execute_prepared(self.label, self.audit_path, "wrong", device_factory=factory)
        factory.assert_not_called()

    def test_check_recomputes_audit_and_never_opens_device(self):
        archive = self.root / "archive"
        archive.mkdir()
        audit_path = archive / "audit.json"
        audit_path.write_text(json.dumps(self.audit), encoding="utf-8")
        (self.folder / "preflight.json").write_text(json.dumps({
            "build": {"report": "artifacts/build-report.json"},
        }), encoding="utf-8")
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "ARCHIVE", archive),
              mock.patch.object(runner.PREPARATION_AUDIT, "audit_preparation",
                                return_value=self.audit),
              mock.patch.object(runner, "load_plan", return_value=self.plan)):
            result = runner.check_prepared(self.label, audit_path)
        self.assertEqual(result["result"], "ready")
        self.assertFalse(result["device_opened"])
        self.assertFalse(result["device_written"])

    def test_check_rejects_a_saved_audit_that_no_longer_matches(self):
        archive = self.root / "archive"
        archive.mkdir()
        audit_path = archive / "audit.json"
        audit_path.write_text(json.dumps({"result": "passed", "plan_id": "x"}), encoding="utf-8")
        (self.folder / "preflight.json").write_text(json.dumps({
            "build": {"report": "artifacts/build-report.json"},
        }), encoding="utf-8")
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "ARCHIVE", archive),
              mock.patch.object(runner.PREPARATION_AUDIT, "audit_preparation",
                                return_value=self.audit)):
            with self.assertRaisesRegex(ValueError, "saved v3 audit differs"):
                runner.check_prepared(self.label, audit_path)

    def test_fresh_snapshot_read_failure_does_not_start_transaction(self):
        device = FakeDevice()
        device.read_full = mock.Mock(side_effect=OSError("fresh Flash read failed"))
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "ARCHIVE", self.root),
              mock.patch.object(runner, "_load_audited_plan",
                                return_value=(self.plan, self.audit, "c" * 64)),
              mock.patch.object(runner, "execute") as execute):
            with self.assertRaisesRegex(OSError, "fresh Flash read failed"):
                runner.execute_prepared(self.label, self.audit_path, self.plan.plan_id,
                                        device_factory=lambda *_: device)
        execute.assert_not_called()
        self.assertEqual(device.calls.count("boot_original"), 2)
        saved = json.loads((self.folder / "execute-failure.json").read_text(encoding="utf-8"))
        self.assertIsNone(saved["recovery_phase"])

    def test_execute_records_audit_and_plan_after_automatic_recovery(self):
        device = FakeDevice()
        (self.folder / "snapshot-32m.bin").write_bytes(b"same-run snapshot")
        completed = {"plan_id": self.plan.plan_id, "probe_passed": True, "restored": True}
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "ARCHIVE", self.root),
              mock.patch.object(runner, "_load_audited_plan",
                                return_value=(self.plan, self.audit, "c" * 64)),
              mock.patch.object(runner, "Journal"),
              mock.patch.object(runner, "execute", return_value=completed) as execute):
            result = runner.execute_prepared(self.label, self.audit_path, self.plan.plan_id,
                                             device_factory=lambda *_: device)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.kwargs["initial_snapshot"], b"fresh device snapshot")
        self.assertTrue(result["probe_passed"])
        self.assertEqual(result["audit_sha256"], "c" * 64)
        self.assertEqual(result["execution_plan_id"], self.plan.plan_id)
        self.assertFalse(result["physical_interaction_accepted"])
        self.assertTrue((self.folder / "execute-result.json").is_file())

    def test_execution_failure_keeps_recovery_state_for_the_recover_command(self):
        device = FakeDevice()
        (self.folder / "snapshot-32m.bin").write_bytes(b"same-run snapshot")
        journal = mock.Mock()
        journal.load.return_value = {"phase": "needs_recovery"}
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "ARCHIVE", self.root),
              mock.patch.object(runner, "_load_audited_plan",
                                return_value=(self.plan, self.audit, "c" * 64)),
              mock.patch.object(runner, "Journal", return_value=journal),
              mock.patch.object(runner, "execute", side_effect=OSError("COM4 disconnected"))):
            with self.assertRaisesRegex(OSError, "disconnected"):
                runner.execute_prepared(self.label, self.audit_path, self.plan.plan_id,
                                        device_factory=lambda *_: device)
        self.assertEqual(device.calls.count("boot_original"), 1)
        saved = json.loads((self.folder / "execute-failure.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["recovery_phase"], "needs_recovery")

    def test_device_type_disables_page_and_unbounded_flash_methods(self):
        device = object.__new__(runner.E3c3aAppDevice)
        for method, args in (("read_flash", (0, 1)), ("write_flash", (0, b"x")),
                             ("snapshot", (object(),))):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "App|snapshot"):
                getattr(device, method)(*args)
        device._write_armed = False
        with self.assertRaisesRegex(PermissionError, "operator confirms"):
            device.write_app(b"unreviewed")

    def test_execute_refuses_an_existing_journal(self):
        (self.folder / "recovery.json").write_text("{}", encoding="utf-8")
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "ARCHIVE", self.root),
              mock.patch.object(runner, "_load_audited_plan",
                                return_value=(self.plan, self.audit, "c" * 64))):
            with self.assertRaisesRegex(FileExistsError, "execution already started"):
                runner.execute_prepared(self.label, self.audit_path, self.plan.plan_id,
                                        device_factory=mock.Mock())

    def test_recovery_refuses_when_no_transaction_is_pending(self):
        journal = mock.Mock()
        journal.load.return_value = {"phase": "restored"}
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "load_plan", return_value=self.plan),
              mock.patch.object(runner, "Journal", return_value=journal),
              mock.patch.object(runner, "recover") as recover):
            with self.assertRaisesRegex(ValueError, "no pending App recovery"):
                runner.recover_prepared(self.label, device_factory=mock.Mock())
        recover.assert_not_called()

    def test_recovery_writes_a_separate_result_for_each_attempt(self):
        journal = mock.Mock()
        journal.load.return_value = {"phase": "needs_recovery"}
        result = {"phase": "restored", "plan_id": self.plan.plan_id}
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "load_plan", return_value=self.plan),
              mock.patch.object(runner, "Journal", return_value=journal),
              mock.patch.object(runner, "recover", return_value=result),
              mock.patch.object(runner, "E3c3aAppDevice")):
            first = runner.recover_prepared(self.label)
            second = runner.recover_prepared(self.label)
        self.assertEqual(first["recovery_result"], "restored")
        self.assertTrue((self.folder / "recovery-result-001.json").is_file())
        self.assertTrue((self.folder / "recovery-result-002.json").is_file())
        self.assertEqual(second["recovery_result"], "restored")

    def test_recovery_failure_is_saved_before_retry(self):
        journal = mock.Mock()
        journal.load.return_value = {"phase": "needs_recovery"}
        failure = OSError("COM4 disconnected during restore")
        with (mock.patch.object(runner, "ROOT", self.root),
              mock.patch.object(runner, "load_plan", return_value=self.plan),
              mock.patch.object(runner, "Journal", return_value=journal),
              mock.patch.object(runner, "recover", side_effect=failure),
              mock.patch.object(runner, "E3c3aAppDevice")):
            with self.assertRaisesRegex(OSError, "disconnected"):
                runner.recover_prepared(self.label)
        saved = json.loads((self.folder / "recovery-result-001.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["phase"], "needs_recovery")
        self.assertIn("disconnected", saved["error"])

    def test_adapter_restore_write_does_not_load_candidate(self):
        snapshot = b"\xff" * FLASH_BYTES
        with tempfile.TemporaryDirectory(dir=runner.ROOT / "tmp", prefix="e143b-adapter-") as name:
            folder = Path(name)
            plan = make_plan(snapshot, b"\xe9" * 4097, original_app_bytes=8193,
                             identity_sha256="1" * 64,
                             run_label="e143b-adapter-candidate-optional")
            persist_plan(folder, plan)
            (folder / "candidate-app-padded.bin").unlink()
            restore_plan = runner.load_plan(folder)
            journal = Journal(folder, restore_plan)
            journal.save("restoring")
            device = object.__new__(runner.E3c3aAppDevice)
            device.run_dir = folder
            device.plan = restore_plan
            device._write_armed = True
            device._write_authorized = lambda: None
            payload = folder / "rollback-payload.bin"
            log = folder / "write.log"
            calls = []
            device._new_path = lambda _directory, _name: payload
            device._reserve_log = lambda _label: log
            device._require_plan_identity = lambda: restore_plan.metadata["identity_sha256"]
            device._invoke = lambda label, args, **kwargs: calls.append((label, tuple(args), kwargs))

            device.write_app(restore_plan.rollback)
            self.assertEqual(payload.read_bytes(), plan.rollback)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "write-app")


class RunnerTransactionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows, _ = fixture()
        cls.probe_log = "\n".join(encode(row["kind"], row["payload"]) for row in rows) + "\n"

    def make_plan(self, folder, run_label):
        raw = bytearray(b"\xff" * FLASH_BYTES)
        region = bytes(range(256)) * (SIZE // 256)
        raw[START:START + SIZE] = region
        raw[APP_START:APP_START + 8192] = b"original" * 1024
        plan = make_plan(bytes(raw), b"\xe9" * 4097, original_app_bytes=8192,
                         identity_sha256="b" * 64, run_label=run_label)
        persist_plan(folder, plan)
        return plan, raw

    def test_runner_executes_real_transaction_and_restores_using_fresh_nvs(self):
        label = "e143b-e3c3a-20261107-integration"
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="runner-integration-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "tmp").mkdir()
        (root / "archive").mkdir()
        folder = root / "tmp" / label
        folder.mkdir()
        plan, raw = self.make_plan(folder, label)
        raw[NVS_START] ^= 1
        fresh = bytes(raw)
        journal = Journal(folder, plan)

        class Device:
            def __init__(self):
                self.flash = bytearray(fresh)
                self.writes = []

            def identity(self):
                return plan.metadata["identity_sha256"]

            def read_full(self):
                return bytes(self.flash)

            def write_app(self, image):
                self.assert_restore_phase()
                self.writes.append(image)
                self.flash[APP_START:APP_START + len(image)] = image

            def assert_restore_phase(self):
                if journal.load()["phase"] not in {"restore_required", "restoring"}:
                    raise AssertionError("durable recovery intent is required")

            def read_app(self, size):
                return bytes(self.flash[APP_START:APP_START + size])

            def capture(self):
                return RunnerTransactionIntegrationTests.probe_log

            def boot_original(self):
                return True

        device = Device()
        audit = {"result": "passed", "plan_id": plan.plan_id}
        audit_path = root / "archive" / "audit.json"
        audit_path.write_text("{}", encoding="utf-8")
        with (mock.patch.object(runner, "ROOT", root),
              mock.patch.object(runner, "ARCHIVE", root / "archive"),
              mock.patch.object(runner, "_load_audited_plan",
                                return_value=(plan, audit, "c" * 64))):
            result = runner.execute_prepared(label, audit_path, plan.plan_id,
                                             device_factory=lambda *_: device)

        self.assertTrue(result["probe_passed"])
        self.assertTrue(result["restored"])
        self.assertEqual(result["execution_snapshot_sha256"], sha(fresh))
        self.assertEqual(result["nvs_before_sha256"], sha(fresh[NVS_START:0xD000]))
        self.assertFalse(result["recovery"]["details"]["nvs"]["changed"])
        self.assertEqual(bytes(device.flash), fresh)
        self.assertEqual(device.writes, [plan.candidate, plan.rollback])
        self.assertTrue((folder / "execute-result.json").is_file())

    def test_recovery_entrypoint_restores_when_candidate_file_is_missing(self):
        label = "e143b-e3c3a-20261107-recover"
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="runner-recovery-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "tmp").mkdir()
        folder = root / "tmp" / label
        folder.mkdir()
        plan, raw = self.make_plan(folder, label)
        candidate = plan.candidate
        (folder / "candidate-app-padded.bin").unlink()
        raw[APP_START:APP_START + len(candidate)] = candidate
        journal = Journal(folder, plan)
        journal.save("restore_required")

        class Device:
            def __init__(self):
                self.flash = bytearray(raw)
                self.writes = []

            def identity(self):
                return plan.metadata["identity_sha256"]

            def write_app(self, image):
                if journal.load()["phase"] not in {"restore_required", "restoring"}:
                    raise AssertionError("durable recovery intent is required")
                self.writes.append(image)
                self.flash[APP_START:APP_START + len(image)] = image

            def read_app(self, size):
                return bytes(self.flash[APP_START:APP_START + size])

            def read_full(self):
                return bytes(self.flash)

            def capture(self):
                raise AssertionError("recovery does not run a probe capture")

            def boot_original(self):
                return True

        device = Device()
        with mock.patch.object(runner, "ROOT", root):
            result = runner.recover_prepared(label, device_factory=lambda *_: device)

        self.assertEqual(result["phase"], "restored")
        self.assertEqual(device.writes, [plan.rollback])
        self.assertEqual(bytes(device.flash), bytes(raw[:APP_START]) + plan.rollback
                         + bytes(raw[APP_START + len(plan.rollback):]))
        self.assertTrue((folder / "recovery-result-001.json").is_file())


if __name__ == "__main__":
    unittest.main()
