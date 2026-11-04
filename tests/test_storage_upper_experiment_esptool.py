"""Offline safety tests for the E1.43a-5 esptool adapter."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
from protocol import storage_upper_experiment as experiment
from protocol.storage_upper_experiment import (
    APP_OFFSET, AtomicJournal, DeviceSnapshot, ExperimentPlan, PAGE_BYTES, PAGE_START,
    STATIC_PROTECTED_NAMES, sha,
)
from protocol.storage_upper_experiment_esptool import (
    APP_ERASE_END,
    APP_ROLLBACK_BYTES,
    CAPTURE_SCRIPT,
    CAPTURE_SCRIPT_SHA256,
    EsptoolDevice,
    Identity,
    PROTECTED_RANGES,
    READ_TRANSFER_ATTEMPTS,
    WRITE_CONFIRMATION_PREFIX,
    load_persisted_plan,
    parse_identity,
    persist_plan_inputs,
)
from protocol.storage_upper_pattern import build_pattern


FLASH_LOG = """esptool.py v4.12.0
Serial port COM4
Chip is ESP32-S3 (QFN56) (revision v0.2)
Features: WiFi, BLE, Embedded PSRAM 16MB (AP_1v8)
MAC: 02:00:00:43:a5:01
Detected flash size: 32MB
Flash type set in eFuse: octal (8 data lines)
"""
SECURITY_LOG = """Security Information:
esptool.py v4.12.0
Serial port COM4
MAC: 02:00:00:43:a5:01
Secure Boot: Disabled
Flash Encryption: Disabled
"""


class RecordingRunner:
    def __init__(self, outputs: list[str] | None = None):
        self.calls: list[tuple[list[str], int]] = []
        self.outputs = list(outputs or [FLASH_LOG, SECURITY_LOG])

    def run(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if "flash_id" in argv:
            return 0, FLASH_LOG
        if "get_security_info" in argv:
            return 0, SECURITY_LOG
        if "write_flash" in argv:
            return 0, "Serial port COM4\nMAC: 02:00:00:43:a5:01\nHash of data verified\n"
        return 0, "Serial port COM4\nMAC: 02:00:00:43:a5:01\n"


class ReadbackRunner(RecordingRunner):
    def run(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if "flash_id" in argv:
            return 0, FLASH_LOG
        if "get_security_info" in argv:
            return 0, SECURITY_LOG
        if "read_flash" in argv:
            size = int(argv[-2], 0)
            Path(argv[-1]).write_bytes(bytes([0x5A]) * size)
        return 0, "Serial port COM4\nMAC: 02:00:00:43:a5:01\n"


class CaptureRunner(RecordingRunner):
    def __init__(self, capture_text: str):
        super().__init__()
        self.capture_text = capture_text

    def run(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if "flash_id" in argv:
            return 0, FLASH_LOG
        if "get_security_info" in argv:
            return 0, SECURITY_LOG
        if str(CAPTURE_SCRIPT) in argv:
            output = Path(argv[argv.index("--output") + 1])
            output.write_text(self.capture_text, encoding="utf-8")
            return 0, "Capture complete\n"
        raise AssertionError("unexpected command")


class TruncatedReadRunner(RecordingRunner):
    def __init__(self, *, failures: int = 1):
        super().__init__()
        self.failures = failures
        self.read_attempts = 0

    def run(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if "flash_id" in argv:
            return 0, FLASH_LOG
        if "get_security_info" in argv:
            return 0, SECURITY_LOG
        if "read_flash" in argv:
            self.read_attempts += 1
            size = int(argv[-2], 0)
            target = Path(argv[-1])
            if self.read_attempts <= self.failures:
                target.write_bytes(b"partial")
                return 2, ("Serial port COM4\nMAC: 02:00:00:43:a5:01\n"
                           "A fatal error occurred: Corrupt data, expected 0x1000 bytes "
                           "but received 0xed5 bytes\n")
            target.write_bytes(b"\x5a" * size)
            return 0, "Serial port COM4\nMAC: 02:00:00:43:a5:01\n"
        raise AssertionError("unexpected retry-runner command")


class TransactionRunner(RecordingRunner):
    def __init__(self, plan: ExperimentPlan):
        super().__init__()
        self.page = plan.rollback_page
        self.app = plan.rollback_app

    def run(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if "flash_id" in argv:
            return 0, FLASH_LOG
        if "get_security_info" in argv:
            return 0, SECURITY_LOG
        if "write_flash" in argv:
            offset = int(argv[-2], 0)
            data = Path(argv[-1]).read_bytes()
            if offset == PAGE_START:
                self.page = data
            elif offset == APP_OFFSET:
                self.app = data + bytes([0xFF]) * (APP_ROLLBACK_BYTES - len(data))
            else:
                raise AssertionError("transaction attempted an unreviewed Flash address")
            return 0, "Serial port COM4\nMAC: 02:00:00:43:a5:01\nHash of data verified\n"
        if "read_flash" in argv:
            offset, size = int(argv[-3], 0), int(argv[-2], 0)
            if offset == PAGE_START:
                data = self.page
            elif APP_OFFSET <= offset < APP_OFFSET + APP_ROLLBACK_BYTES:
                start = offset - APP_OFFSET
                data = self.app[start:start + size]
            else:
                raise AssertionError(f"transaction read an unreviewed Flash address: {offset:#x}")
            Path(argv[-1]).write_bytes(data[:size])
            return 0, "Serial port COM4\nMAC: 02:00:00:43:a5:01\n"
        raise AssertionError("unexpected transaction command")


def plan_for(identity: Identity) -> ExperimentPlan:
    return ExperimentPlan(
        expected_identity_sha256=identity.digest,
        rollback_app=bytes([0x42]) * APP_ROLLBACK_BYTES,
        rollback_page=bytes([0xA5]) * PAGE_BYTES,
        candidate_app=b"candidate-app",
        pattern=build_pattern(),
        expected_static_protected_sha256={
            name: sha(("reviewed:" + name).encode("ascii"))
            for name in STATIC_PROTECTED_NAMES
        },
    )


class EsptoolAdapterTests(unittest.TestCase):
    def setUp(self):
        self.identity = parse_identity(FLASH_LOG, SECURITY_LOG)
        self.plan = plan_for(self.identity)
        self.python = Path(__import__("sys").executable)

    def adapter(self, runner=None, **kwargs):
        temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(temp.cleanup)
        run_dir = Path(temp.name)
        if kwargs.get("write_enabled"):
            phrase = f"{WRITE_CONFIRMATION_PREFIX} {self.plan.plan_id}"
            with patch("builtins.input", return_value=phrase):
                return EsptoolDevice(
                    port="COM4", run_dir=run_dir, plan=self.plan, python=self.python,
                    runner=runner or RecordingRunner(), **kwargs,
                )
        return EsptoolDevice(
            port="COM4", run_dir=run_dir, plan=self.plan, python=self.python,
            runner=runner or RecordingRunner(), **kwargs,
        )

    def arm_recovery_intent(self, device):
        persist_plan_inputs(device.run_dir, self.plan)
        protected = dict(self.plan.expected_static_protected_sha256)
        protected["nvs"] = sha(b"synthetic-nvs")
        journal = AtomicJournal(device.recovery_journal_path)
        experiment._record(journal, self.plan, "prepared", protected)
        experiment._record(journal, self.plan, "restore_required", protected)
        return journal

    def test_identity_parser_requires_exactly_one_complete_identity(self):
        parsed = self.identity
        self.assertEqual(parsed.mac, "02:00:00:43:a5:01")
        self.assertEqual(parsed.flash_bytes, 32 * 1024 * 1024)
        self.assertEqual(parsed.psram_bytes, 16 * 1024 * 1024)
        self.assertFalse(parsed.secure_boot)
        self.assertFalse(parsed.flash_encryption)
        with self.assertRaisesRegex(ValueError, "same single MAC"):
            parse_identity(FLASH_LOG + "MAC: 00:11:22:33:44:55\n", SECURITY_LOG)
        with self.assertRaisesRegex(ValueError, "capture tool or port"):
            parse_identity(FLASH_LOG, "Secure Boot: Disabled\n")
        with self.assertRaisesRegex(ValueError, "same single MAC"):
            parse_identity(FLASH_LOG, SECURITY_LOG.replace("02:00:00:43:a5:01", "00:11:22:33:44:55"))

    def test_constructor_defaults_to_read_only(self):
        device = self.adapter()
        self.assertFalse(device.write_enabled)
        with self.assertRaisesRegex(PermissionError, "writes are disabled"):
            device.write_flash(PAGE_START, bytes(PAGE_BYTES))

    def test_write_arming_requires_interactive_exact_plan_confirmation(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(temp.cleanup)
        with patch("builtins.input", return_value="wrong phrase") as prompt:
            with self.assertRaisesRegex(PermissionError, "operator confirmation"):
                EsptoolDevice(port="COM4", run_dir=Path(temp.name), plan=self.plan,
                              python=self.python, write_enabled=True)
        self.assertIn(self.plan.plan_id, prompt.call_args.args[0])
        phrase = f"{WRITE_CONFIRMATION_PREFIX} {self.plan.plan_id}"
        with patch("builtins.input", return_value=phrase):
            device = EsptoolDevice(port="COM4", run_dir=Path(temp.name), plan=self.plan,
                                   python=self.python, write_enabled=True)
        self.assertTrue(device.write_enabled)

    def test_write_requires_matching_durable_restore_intent(self):
        runner = RecordingRunner()
        device = self.adapter(runner=runner, write_enabled=True)
        persist_plan_inputs(device.run_dir, self.plan)
        protected = dict(self.plan.expected_static_protected_sha256)
        protected["nvs"] = sha(b"synthetic-nvs")
        experiment._record(AtomicJournal(device.recovery_journal_path), self.plan,
                           "prepared", protected)
        with self.assertRaisesRegex(PermissionError, "restore_required recovery intent"):
            device.write_flash(PAGE_START, self.plan.pattern)
        self.assertFalse(any("write_flash" in call[0] for call in runner.calls))

    def test_port_and_probe_command_must_be_explicit(self):
        with self.assertRaisesRegex(ValueError, "reviewed body port COM4"):
            EsptoolDevice(port="", run_dir=Path(tempfile.mkdtemp()), plan=self.plan, python=self.python)
        with self.assertRaisesRegex(ValueError, "reviewed body port COM4"):
            EsptoolDevice(port="COM8", run_dir=Path(tempfile.mkdtemp(dir=ROOT / "tmp")),
                          plan=self.plan, python=self.python)

    def test_capture_script_is_pinned_and_esptool_reads_round_up_then_trim(self):
        self.assertEqual(__import__("hashlib").sha256(CAPTURE_SCRIPT.read_bytes()).hexdigest(),
                         CAPTURE_SCRIPT_SHA256)
        runner = ReadbackRunner()
        device = self.adapter(runner=runner)
        self.assertEqual(device._fresh_identity().digest, self.plan.expected_identity_sha256)

        result = device._capture("unaligned-app", 0x1000, 0x1001)

        self.assertEqual(result, bytes([0x5A]) * 0x1001)
        read_calls = [call[0] for call in runner.calls if "read_flash" in call[0]]
        self.assertEqual(len(read_calls), 1)
        self.assertEqual(int(read_calls[0][-2], 0), 0x2000)
        self.assertIn("--flash_size", read_calls[0])
        self.assertEqual(read_calls[0][read_calls[0].index("--flash_size") + 1], "keep")

    def test_only_truncated_read_packets_retry_with_fresh_evidence_files(self):
        runner = TruncatedReadRunner(failures=2)
        device = self.adapter(runner=runner)
        self.assertTrue(device.identity_matches(self.plan.expected_identity_sha256))

        observed = device._capture("retry-read", APP_OFFSET, 0x1000)

        self.assertEqual(observed, b"\x5a" * 0x1000)
        self.assertEqual(runner.read_attempts, 3)
        captures = sorted((device.run_dir / "captures").iterdir())
        self.assertEqual(len(captures), 3)
        self.assertEqual([path.stat().st_size for path in captures], [7, 7, 0x1000])
        self.assertEqual(len(list((device.run_dir / "logs").iterdir())), 9)

    def test_truncated_read_retry_limit_is_bounded(self):
        runner = TruncatedReadRunner(failures=READ_TRANSFER_ATTEMPTS)
        device = self.adapter(runner=runner)
        self.assertTrue(device.identity_matches(self.plan.expected_identity_sha256))

        with self.assertRaisesRegex(OSError, "truncated read packet"):
            device._capture("retry-limit", APP_OFFSET, 0x1000)

        self.assertEqual(runner.read_attempts, READ_TRANSFER_ATTEMPTS)
        captures = sorted((device.run_dir / "captures").iterdir())
        self.assertEqual(len(captures), READ_TRANSFER_ATTEMPTS)
        self.assertTrue(all(path.stat().st_size == 7 for path in captures))

    def test_generic_read_error_is_not_retried(self):
        class FailingReadRunner(RecordingRunner):
            def run(inner_self, argv, *, timeout):
                if "read_flash" in argv:
                    inner_self.calls.append((list(argv), timeout))
                    return 2, "Serial port COM4\nA fatal error occurred: unrelated error\n"
                return super().run(argv, timeout=timeout)

        runner = FailingReadRunner()
        device = self.adapter(runner=runner)
        with self.assertRaisesRegex(OSError, "exit code 2"):
            device._capture("unrelated-read-failure", APP_OFFSET, 0x1000)
        self.assertEqual(sum("read_flash" in call[0] for call in runner.calls), 1)

    def test_identity_drift_blocks_read_retry(self):
        class DriftingRunner(TruncatedReadRunner):
            def run(inner_self, argv, *, timeout):
                code, output = super().run(argv, timeout=timeout)
                if inner_self.read_attempts and ("flash_id" in argv or "get_security_info" in argv):
                    output = output.replace("02:00:00:43:a5:01", "02:00:00:43:a5:02")
                return code, output

        runner = DriftingRunner()
        device = self.adapter(runner=runner)
        self.assertTrue(device.identity_matches(self.plan.expected_identity_sha256))
        with self.assertRaisesRegex(ValueError, "freshly confirmed device identity"):
            device._capture("drifting-read", APP_OFFSET, 0x1000)
        self.assertEqual(runner.read_attempts, 1)

    def test_packet_error_on_write_is_not_retried(self):
        class FailingWriteRunner(RecordingRunner):
            def run(inner_self, argv, *, timeout):
                if "write_flash" in argv:
                    inner_self.calls.append((list(argv), timeout))
                    return 2, ("Serial port COM4\nA fatal error occurred: Corrupt data, "
                               "expected 0x1000 bytes but received 0xed5 bytes\n")
                return super().run(argv, timeout=timeout)

        runner = FailingWriteRunner()
        device = self.adapter(runner=runner, write_enabled=True)
        self.arm_recovery_intent(device)
        with self.assertRaisesRegex(OSError, "exit code 2"):
            device.write_flash(PAGE_START, self.plan.pattern)
        self.assertEqual(sum("write_flash" in call[0] for call in runner.calls), 1)

    def test_recovery_inputs_are_persisted_before_a_write_and_hash_checked(self):
        device = self.adapter()
        manifest = persist_plan_inputs(device.run_dir, self.plan)
        self.assertTrue(manifest.is_file())
        restored = load_persisted_plan(device.run_dir)
        self.assertEqual(restored.plan_id, self.plan.plan_id)
        (device.run_dir / "plan" / "rollback-page.bin").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "failed its byte/hash check"):
            load_persisted_plan(device.run_dir)

    def test_incomplete_recovery_bundle_is_not_completed_implicitly(self):
        device = self.adapter()
        directory = device.run_dir / "plan"
        directory.mkdir()
        (directory / "rollback-page.bin").write_bytes(self.plan.rollback_page)
        with self.assertRaisesRegex(FileExistsError, "incomplete persisted-plan"):
            persist_plan_inputs(device.run_dir, self.plan)

    def test_read_range_is_limited_to_reviewed_page_and_app(self):
        device = self.adapter()
        with self.assertRaisesRegex(ValueError, "outside the exact App/test-page"):
            device.read_flash(0, 0x1000)
        with self.assertRaisesRegex(ValueError, "outside the exact App/test-page"):
            device.read_flash(PAGE_START, 0x1000)
        with self.assertRaisesRegex(ValueError, "outside the exact App/test-page"):
            device.read_flash(0x10000, 0x2000)

    def test_write_range_and_payload_geometry_are_strict(self):
        runner = RecordingRunner()
        device = self.adapter(runner=runner, write_enabled=True)
        with self.assertRaisesRegex(ValueError, "exact reviewed pattern or rollback page"):
            device.write_flash(PAGE_START, b"short")
        with self.assertRaisesRegex(ValueError, "limited to the test page and App"):
            device.write_flash(0x8000, b"x")
        with self.assertRaisesRegex(ValueError, "exact reviewed pattern or rollback page"):
            device.write_flash(PAGE_START, bytes(PAGE_BYTES))
        self.arm_recovery_intent(device)
        device.write_flash(PAGE_START, self.plan.pattern)
        write_call = next(call[0] for call in runner.calls if "write_flash" in call[0])
        self.assertEqual(write_call[write_call.index("--after") + 1], "no_reset")
        self.assertEqual(write_call[write_call.index("--port") + 1], "COM4")
        self.assertEqual(write_call[write_call.index("--chip") + 1], "esp32s3")

    def test_app_write_is_limited_to_exact_candidate_or_rollback(self):
        runner = RecordingRunner()
        device = self.adapter(runner=runner, write_enabled=True)
        with self.assertRaisesRegex(ValueError, "exact candidate or rollback"):
            device.write_flash(0x10000, b"partial-app")
        self.arm_recovery_intent(device)
        device.write_flash(0x10000, self.plan.candidate_app)
        self.assertTrue(any("write_flash" in call[0] for call in runner.calls))

    def test_stale_identity_stops_before_any_write_command(self):
        class OtherDeviceRunner(RecordingRunner):
            def run(inner_self, argv, *, timeout):
                inner_self.calls.append((list(argv), timeout))
                if "flash_id" in argv:
                    return 0, FLASH_LOG.replace("02:00:00:43:a5:01", "02:00:00:00:00:99")
                if "get_security_info" in argv:
                    return 0, SECURITY_LOG.replace("02:00:00:43:a5:01", "02:00:00:00:00:99")
                raise AssertionError("write must not be attempted for a different device")

        runner = OtherDeviceRunner()
        device = self.adapter(runner=runner, write_enabled=True)
        self.arm_recovery_intent(device)

        with self.assertRaisesRegex(ValueError, "differs from the reviewed experiment plan"):
            device.write_flash(PAGE_START, self.plan.pattern)
        self.assertFalse(any("write_flash" in call[0] for call in runner.calls))

    def test_adapter_and_transaction_controller_complete_restore_integration(self):
        runner = TransactionRunner(self.plan)
        device = self.adapter(runner=runner, write_enabled=True)
        identity = self.identity

        class IntegratedDevice(EsptoolDevice):
            def _require_plan_identity(self):
                return identity

            def snapshot(self, plan):
                protected = dict(plan.expected_static_protected_sha256)
                protected["nvs"] = sha(b"synthetic-nvs")
                return DeviceSnapshot(
                    identity_sha256=identity.digest,
                    chip="esp32s3",
                    physical_flash_bytes=32 * 1024 * 1024,
                    psram_bytes=16 * 1024 * 1024,
                    secure_boot=False,
                    flash_encryption=False,
                    app_erase_bytes=runner.app,
                    upper_page=runner.page,
                    protected_sha256=protected,
                )

            def identity_matches(self, expected_sha256):
                return expected_sha256 == identity.digest

            def capture_probe(self, timeout_seconds):
                return "synthetic probe complete"

            def boot_original(self):
                self.boot_count = getattr(self, "boot_count", 0) + 1
                return runner.app == self.plan.rollback_app and runner.page == self.plan.rollback_page

        phrase = f"{WRITE_CONFIRMATION_PREFIX} {self.plan.plan_id}"
        with patch("builtins.input", return_value=phrase):
            integrated = IntegratedDevice(
                port="COM4", run_dir=device.run_dir, plan=self.plan, python=self.python,
                runner=runner, write_enabled=True,
            )
        result = experiment.run_experiment(
            self.plan, integrated, AtomicJournal(integrated.recovery_journal_path),
            lambda log: log == "synthetic probe complete",
        )
        self.assertTrue(result.probe_passed)
        self.assertTrue(result.restored)
        self.assertEqual(result.phase, "probe_passed_and_restored")
        self.assertEqual(AtomicJournal(integrated.recovery_journal_path).load()["phase"], "complete")
        writes = [int(call[0][-2], 0) for call in runner.calls if "write_flash" in call[0]]
        self.assertEqual(writes, [PAGE_START, APP_OFFSET, PAGE_START, APP_OFFSET])
        self.assertTrue(integrated.boot_count == 1)

    def test_probe_and_boot_capture_use_only_the_pinned_usb_reset_entry(self):
        boot_log = "\n".join((
            "Calling app_main()", "LCD panel create success",
            "Touch sensor initialized successfully", "Found 16MB PSRAM device",
            "detected chip: mxic (opi)", "flash io: opi_str",
        ))
        runner = CaptureRunner(boot_log)
        device = self.adapter(runner=runner)

        self.assertEqual(device.capture_probe(20), boot_log)
        self.assertTrue(device.boot_original())
        capture_calls = [call[0] for call in runner.calls if str(CAPTURE_SCRIPT) in call[0]]
        self.assertEqual(len(capture_calls), 2)
        for call in capture_calls:
            self.assertEqual(call[call.index("--port") + 1], "COM4")
            self.assertIn("--usb-reset", call)
            self.assertNotIn("--no-reset", call)

    def test_non_normal_or_panicking_boot_is_rejected(self):
        runner = CaptureRunner("Calling app_main()\nGuru Meditation Error")
        device = self.adapter(runner=runner)
        self.assertFalse(device.boot_original())

    def test_protected_ranges_cover_all_flash_outside_write_targets(self):
        from protocol.storage_upper_experiment import PROTECTED_NAMES

        self.assertEqual(set(PROTECTED_RANGES), set(PROTECTED_NAMES))
        protected = [(offset, offset + size, name) for name, (offset, size) in PROTECTED_RANGES.items()]
        writes = [(0x10000, APP_ERASE_END, "app_write"),
                  (PAGE_START, PAGE_START + PAGE_BYTES, "page_write")]
        spans = sorted(protected + writes)
        for offset, size in PROTECTED_RANGES.values():
            self.assertEqual(offset % 0x1000, 0)
            self.assertEqual(size % 0x1000, 0)
            self.assertLessEqual(offset + size, 32 * 1024 * 1024)
        for (_left_start, left_end, left_name), (right_start, _right_end, right_name) in zip(spans, spans[1:]):
            self.assertEqual(left_end, right_start, f"unprotected gap between {left_name} and {right_name}")
        for start, end, _name in writes:
            self.assertEqual(start, 0x10000 if end == APP_ERASE_END else PAGE_START)

    def test_unsafe_esptool_options_and_log_overwrite_are_rejected(self):
        runner = RecordingRunner()
        device = self.adapter(runner=runner)
        with self.assertRaisesRegex(ValueError, "unsafe esptool"):
            device._invoke("unsafe", ("read_flash", "--erase-all"))
        device._invoke("flash-id", ("flash_id",), timeout=30)
        device._sequence = 0
        calls_before_collision = len(runner.calls)
        with self.assertRaises(FileExistsError):
            device._invoke("flash-id", ("flash_id",), timeout=30)
        self.assertEqual(len(runner.calls), calls_before_collision,
                         "evidence collision must be detected before invoking esptool")

    def test_restarted_adapter_continues_sequence_and_recovers_same_run(self):
        class IntegratedDevice(EsptoolDevice):
            def _require_plan_identity(self):
                return self.identity_for_test

            def snapshot(self, plan):
                protected = dict(plan.expected_static_protected_sha256)
                protected["nvs"] = sha(b"synthetic-nvs")
                return DeviceSnapshot(
                    identity_sha256=self.identity_for_test.digest,
                    chip="esp32s3",
                    physical_flash_bytes=32 * 1024 * 1024,
                    psram_bytes=16 * 1024 * 1024,
                    secure_boot=False,
                    flash_encryption=False,
                    app_erase_bytes=self.runner.app,
                    upper_page=self.runner.page,
                    protected_sha256=protected,
                )

            def identity_matches(self, expected_sha256):
                return expected_sha256 == self.identity_for_test.digest

            def boot_original(self):
                return (self.runner.app == self.plan.rollback_app
                        and self.runner.page == self.plan.rollback_page)

        temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(temp.cleanup)
        run_dir = Path(temp.name)
        runner = TransactionRunner(self.plan)
        phrase = f"{WRITE_CONFIRMATION_PREFIX} {self.plan.plan_id}"

        with patch("builtins.input", return_value=phrase):
            first = IntegratedDevice(
                port="COM4", run_dir=run_dir, plan=self.plan, python=self.python,
                runner=runner, write_enabled=True,
            )
        first.identity_for_test = self.identity
        self.arm_recovery_intent(first)
        first.write_flash(PAGE_START, self.plan.pattern)
        first.write_flash(APP_OFFSET, self.plan.candidate_app)
        self.assertEqual(runner.page, self.plan.pattern)
        self.assertNotEqual(runner.app, self.plan.rollback_app)

        def evidence_snapshot():
            return {
                str(path.relative_to(run_dir)): path.read_bytes()
                for folder in ("logs", "captures", "payloads")
                for path in ((run_dir / folder).iterdir()
                             if (run_dir / folder).exists() else ())
                if path.is_file()
            }

        old_evidence = evidence_snapshot()
        highest_old_sequence = max(
            int(Path(name).name.split("-", 1)[0])
            for name in old_evidence
        )

        with patch("builtins.input", return_value=phrase):
            restarted = IntegratedDevice(
                port="COM4", run_dir=run_dir, plan=self.plan, python=self.python,
                runner=runner, write_enabled=True,
            )
        restarted.identity_for_test = self.identity
        self.assertEqual(restarted._sequence, highest_old_sequence)

        experiment.recover(
            self.plan, restarted, AtomicJournal(restarted.recovery_journal_path)
        )

        self.assertEqual(runner.page, self.plan.rollback_page)
        self.assertEqual(runner.app, self.plan.rollback_app)
        self.assertEqual(
            AtomicJournal(restarted.recovery_journal_path).load()["phase"], "complete"
        )
        self.assertTrue(set(old_evidence).issubset(evidence_snapshot()))
        for name, content in old_evidence.items():
            self.assertEqual((run_dir / name).read_bytes(), content,
                             "recovery must preserve all evidence from before restart")
        all_sequences = [
            int(Path(name).name.split("-", 1)[0])
            for name in evidence_snapshot()
        ]
        self.assertEqual(len(all_sequences), len(set(all_sequences)))
        self.assertGreater(max(all_sequences), highest_old_sequence)

    def test_restart_uses_all_direct_evidence_files_without_recursing(self):
        first = self.adapter()
        previous = {
            "logs/004-flash-id.log": b"old identity evidence",
            "captures/1002-upper-probe.log": b"old serial evidence",
            "payloads/011-write-page.bin": b"old payload",
            "captures/nested/9999-not-adapter-evidence.bin": b"nested output",
        }
        for name, content in previous.items():
            path = first.run_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        runner = RecordingRunner()
        restarted = EsptoolDevice(
            port="COM4", run_dir=first.run_dir, plan=self.plan,
            python=self.python, runner=runner,
        )
        self.assertEqual(runner.calls, [], "reopening a run must not access hardware")
        self.assertTrue(restarted.identity_matches(self.plan.expected_identity_sha256))
        self.assertTrue((first.run_dir / "logs/1003-flash-id.log").is_file())
        self.assertTrue((first.run_dir / "logs/1004-security-info.log").is_file())
        for name, content in previous.items():
            self.assertEqual((first.run_dir / name).read_bytes(), content)

    def test_log_collision_stops_flash_write_and_serial_reset_before_the_command(self):
        for operation in ("write-page", "upper-probe-capture"):
            with self.subTest(operation=operation):
                runner = CaptureRunner("synthetic serial log")
                device = self.adapter(runner=runner, write_enabled=operation == "write-page")
                if operation == "write-page":
                    self.arm_recovery_intent(device)
                directory = device.run_dir / "logs"
                directory.mkdir()
                # The payload or serial-output reservation consumes slot 1;
                # reserve the command log slot to force a pre-device collision.
                existing = directory / f"002-{operation}.log"
                existing.write_bytes(b"preserve previous evidence")
                with self.assertRaises(FileExistsError):
                    if operation == "write-page":
                        device.write_flash(PAGE_START, self.plan.pattern)
                    else:
                        device.capture_probe(20)
                self.assertEqual(runner.calls, [], "collision must stop before any USB/reset command")
                self.assertEqual(existing.read_bytes(), b"preserve previous evidence")

    def test_identity_matches_freshly_queries_capacity_and_security(self):
        runner = RecordingRunner()
        device = self.adapter(runner=runner)
        self.assertTrue(device.identity_matches(self.plan.expected_identity_sha256))
        self.assertGreaterEqual(len(runner.calls), 2)
        bad_plan = ExperimentPlan(
            expected_identity_sha256=sha(b"different-device"),
            rollback_app=self.plan.rollback_app, rollback_page=self.plan.rollback_page,
            candidate_app=self.plan.candidate_app, pattern=self.plan.pattern,
            expected_static_protected_sha256=self.plan.expected_static_protected_sha256,
        )
        self.assertFalse(device.identity_matches(bad_plan.expected_identity_sha256))


if __name__ == "__main__":
    unittest.main()
