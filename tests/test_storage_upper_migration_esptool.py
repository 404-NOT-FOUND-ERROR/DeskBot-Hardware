"""Exercise the actual serial adapter against in-memory command responses."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import storage_upper_migration_transaction as tx
from protocol.storage_upper_experiment_esptool import (
    CAPTURE_SCRIPT, CommandTimedOut, READ_CHUNK_BYTES, READ_TRANSFER_ATTEMPTS,
    _NORMAL_BOOT_MARKERS, parse_identity,
)
from protocol.storage_upper_migration_esptool import UpperMigrationEsptoolDevice
from protocol.storage_upper_migration_simulator import synthetic_snapshot

FLASH_LOG = """esptool.py v4.12.0
Serial port COM4
Chip is ESP32-S3 (QFN56) (revision v0.2)
Features: WiFi, BLE, Embedded PSRAM 16MB (AP_1v8)
MAC: 02:00:00:43:c2:01
Detected flash size: 32MB
Flash type set in eFuse: octal (8 data lines)
"""
SECURITY_LOG = """esptool.py v4.12.0
Serial port COM4
MAC: 02:00:00:43:c2:01
Secure Boot: Disabled
Flash Encryption: Disabled
"""
TRANSFER_LOG = "Serial port COM4\nMAC: 02:00:00:43:c2:01\n"
# Public excerpt of the accepted A7/E1.42 boot, without device/network identity.
EMOTE_BOOT_LOG = """I (100) esp_psram: Found 16MB PSRAM device
I (188) spi_flash: detected chip: mxic (opi)
I (192) spi_flash: flash io: opi_str
I (228) main_task: Calling app_main()
I (358) st77916: LCD panel create success, version: 1.0.1
I (728) ui_bridge: LVGL display bridge initialized for 360x360
I (1188) esp_vocat: Touch sensor initialized successfully
"""


class ReplayRunner:
    """Interpret only the adapter's known argv. Never starts a process."""

    def __init__(self, snapshot, candidate_table):
        self.data = bytearray(snapshot)
        self.candidate_table = candidate_table
        self.calls, self.writes = [], []
        self.truncated_reads = 0
        self.truncation_budget = 0
        self.fail_write_once = False
        self.missing_write_hash_once = False
        self.short_read_once = False
        self.timeout_once = False
        self.transfer_identity = TRANSFER_LOG
        self.flash_identity = FLASH_LOG
        self.boot_extra = ""
        self.boot_log = EMOTE_BOOT_LOG
        self.capture_calls = 0
        self.before_write = None

    def run(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if self.timeout_once:
            self.timeout_once = False
            raise CommandTimedOut("simulated host timeout\n")
        if str(CAPTURE_SCRIPT) in argv:
            self.capture_calls += 1
            text = self.boot_log + self.boot_extra
            Path(argv[argv.index("--output") + 1]).write_text(text, encoding="utf-8")
            return 0, "Capture complete\n"
        if "flash_id" in argv:
            return 0, self.flash_identity
        if "get_security_info" in argv:
            return 0, SECURITY_LOG
        if "read_flash" in argv:
            offset, size = int(argv[-3], 0), int(argv[-2], 0)
            if self.truncated_reads < self.truncation_budget:
                self.truncated_reads += 1
                Path(argv[-1]).write_bytes(b"partial")
                return 2, self.transfer_identity + "A fatal error occurred: Corrupt data, expected 0x1000 bytes but received 0xed5 bytes\n"
            raw = bytes(self.data[offset:offset + size])
            if self.short_read_once:
                self.short_read_once = False
                raw = raw[:-1]
            Path(argv[-1]).write_bytes(raw)
            return 0, self.transfer_identity
        if "write_flash" in argv:
            if self.before_write:
                self.before_write()
            offset, raw = int(argv[-2], 0), Path(argv[-1]).read_bytes()
            self.writes.append({"offset": offset, "bytes": len(raw), "sha256": tx.sha(raw)})
            self.data[offset:offset + len(raw)] = b"\xff" * len(raw)
            if self.fail_write_once:
                self.fail_write_once = False
                prefix = 128 if offset == tx.TABLE_START else len(raw) // 2
                self.data[offset:offset + prefix] = raw[:prefix]
                return 2, self.transfer_identity + "A fatal error occurred: interrupted write\n"
            self.data[offset:offset + len(raw)] = raw
            marker = "Hash of data verified\n"
            if self.missing_write_hash_once:
                self.missing_write_hash_once = False
                marker = ""
            return 0, self.transfer_identity + marker
        raise AssertionError("unexpected command: " + repr(argv))


class RehearsalDevice(UpperMigrationEsptoolDevice):
    """The simulation label belongs only to this fake-runner test device."""
    simulation_only = True

    def __init__(self, folder, runner, *, write_enabled=True):
        if not isinstance(runner, ReplayRunner):
            raise ValueError("offline controller requires a replay runner")
        super().__init__(folder, runner=runner, python=Path(sys.executable), write_enabled=write_enabled)


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.identity = parse_identity(FLASH_LOG, SECURITY_LOG)
        cls.old_table, cls.new_payload = b"O" * 4096, b"T" * 3072
        cls.upper = b"C" * tx.UPPER_BYTES
        raw = bytearray(b"\xff" * tx.FLASH_BYTES)
        raw[tx.TABLE_START:tx.TABLE_START + tx.TABLE_BYTES] = cls.old_table
        raw[tx.UPPER_START:tx.UPPER_START + tx.UPPER_BYTES] = b"R" * tx.UPPER_BYTES
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
        self.folder = ROOT / "tmp" / ("e143c-sim-c2-" + uuid.uuid4().hex)
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.plan = tx.make_plan(self.snapshot, self.upper, self.new_payload,
                                 identity_sha256=self.identity.digest, run_label=self.folder.name)
        tx.persist_plan(self.folder, self.plan)
        self.runner = ReplayRunner(self.snapshot, self.plan.candidate_table)
        self.device = RehearsalDevice(self.folder, self.runner)
        self.journal = tx.Journal(self.folder, self.plan)

    def write_intent(self, phase="upper_write_intent"):
        self.journal.save("prepared")
        self.journal.save(phase)

    def execute(self):
        return tx.execute(self.folder, self.device, initial_snapshot=self.snapshot)

    def hardware_device(self):
        folder = ROOT / "tmp" / ("e143c-live-test-" + uuid.uuid4().hex)
        self.addCleanup(shutil.rmtree, folder, True)
        plan = tx.make_plan(self.snapshot, self.upper, self.new_payload,
                            identity_sha256=self.identity.digest, run_label=folder.name,
                            mode=tx.LIVE_MODE, authorization=tx.DIRECT_AUTHORIZATION)
        tx.persist_plan(folder, plan)
        return folder, plan, UpperMigrationEsptoolDevice(
            folder, runner=self.runner, python=Path(sys.executable), write_enabled=True)

    def test_hardware_plan_requires_direct_migration_authorization(self):
        for authorization in (None, "", "other"):
            with self.subTest(authorization=authorization), self.assertRaises(ValueError):
                tx.make_plan(self.snapshot, self.upper, self.new_payload,
                             identity_sha256=self.identity.digest, run_label="e143c-live-test",
                             mode=tx.LIVE_MODE, authorization=authorization)
        self.assertEqual(self.runner.calls, [])

    def test_hardware_and_simulation_labels_do_not_mix(self):
        with self.assertRaises(ValueError):
            tx.make_plan(self.snapshot, self.upper, self.new_payload,
                         identity_sha256=self.identity.digest, run_label="e143c-sim-test",
                         mode=tx.LIVE_MODE, authorization=tx.DIRECT_AUTHORIZATION)

    def test_simulated_device_cannot_execute_a_hardware_plan(self):
        folder, plan, device = self.hardware_device()
        device.simulation_only = True
        with self.assertRaisesRegex(ValueError, "hardware plans require"):
            tx.execute(folder, device, initial_snapshot=self.snapshot)
        self.assertEqual(self.runner.calls, [])

    def test_hardware_contract_marker_is_required_before_identity(self):
        folder, plan, device = self.hardware_device()
        device.hardware_migration = False
        with self.assertRaisesRegex(ValueError, "hardware plans require"):
            tx.execute(folder, device, initial_snapshot=self.snapshot)
        self.assertEqual(self.runner.calls, [])

    def test_native_hardware_migration_and_candidate_independent_recovery(self):
        folder, plan, device = self.hardware_device()
        result = tx.execute(folder, device, initial_snapshot=self.snapshot)
        self.assertEqual(result["mode"], tx.LIVE_MODE)
        self.assertEqual(result["result"], "candidate_committed")
        self.assertTrue(result["device_written"])
        self.assertFalse(result["physical_interaction_accepted"])
        self.assertEqual(tx.Journal(folder, plan).load()["mode"], tx.LIVE_MODE)
        for name in tx.CANDIDATE_FILES.values():
            (folder / name).unlink()
        self.assertTrue(tx.recover(folder, device)["restored"])
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual([row["offset"] for row in self.runner.writes],
                         [tx.UPPER_START, tx.TABLE_START, tx.TABLE_START, tx.UPPER_START])

    def test_native_hardware_partial_write_restores_original_bytes(self):
        folder, plan, device = self.hardware_device()
        self.runner.fail_write_once = True
        result = tx.execute(folder, device, initial_snapshot=self.snapshot)
        self.assertEqual(result["result"], "failed_and_restored")
        self.assertTrue(result["restored"])
        self.assertEqual(bytes(self.runner.data), self.snapshot)

    def test_native_hardware_bad_boot_restores_table_and_upper_region(self):
        folder, plan, device = self.hardware_device()
        original = device.boot
        def boot(target):
            return False if target == "candidate" else original(target)
        with patch.object(device, "boot", side_effect=boot):
            result = tx.execute(folder, device, initial_snapshot=self.snapshot)
        self.assertTrue(result["restored"])
        self.assertEqual(bytes(self.runner.data), self.snapshot)

    def test_hardware_entry_requires_confirmation_before_device_access(self):
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "scripts/run-expression-upper-migration-e1-43c.py"),
                            "execute", "--run-label", "e143c-live-test"],
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--confirm", r.stderr)

    def read_commands(self):
        return [args for args, _ in self.runner.calls if "read_flash" in args]

    def test_constructor_opens_no_port_or_process(self):
        self.assertEqual(self.runner.calls, [])

    def test_c1_controller_rejects_native_transport_before_identity(self):
        native = UpperMigrationEsptoolDevice(self.folder, runner=self.runner, python=Path(sys.executable), write_enabled=True)
        with self.assertRaisesRegex(ValueError, "simulation adapters only"):
            tx.execute(self.folder, native, initial_snapshot=self.snapshot)
        self.assertEqual(self.runner.calls, [])

    def test_native_writes_default_to_disabled(self):
        native = UpperMigrationEsptoolDevice(self.folder, runner=self.runner, python=Path(sys.executable))
        self.write_intent()
        with self.assertRaises(PermissionError):
            native.write_flash(tx.UPPER_START, self.upper)
        self.assertEqual(self.runner.calls, [])

    def test_run_label_is_bound_to_direct_repository_tmp_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143c-unrelated-") as folder:
            with self.assertRaises(ValueError):
                UpperMigrationEsptoolDevice(Path(folder), runner=self.runner, python=Path(sys.executable))
        self.assertEqual(self.runner.calls, [])

    def test_complete_same_execution_read_is_split_into_32_one_mib_commands(self):
        self.assertEqual(self.device.read_full(), self.snapshot)
        commands = self.read_commands()
        self.assertEqual(len(commands), 32)
        self.assertEqual([int(args[-3], 0) for args in commands], list(range(0, tx.FLASH_BYTES, READ_CHUNK_BYTES)))
        self.assertTrue(all(int(args[-2], 0) == READ_CHUNK_BYTES for args in commands))

    def test_reads_reject_arbitrary_geometry_before_device_access(self):
        for offset, size in ((tx.TABLE_START, 1), (tx.UPPER_START+1, tx.UPPER_BYTES), (tx.NVS_START, 4096)):
            with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                self.device.read_flash(offset, size)
        self.assertEqual(self.runner.calls, [])

    def test_boolean_geometry_is_rejected_before_device_access(self):
        with self.assertRaises(ValueError):
            self.device.read_flash(False, 0x8000)
        self.assertEqual(self.runner.calls, [])

    def test_truncated_read_retries_only_same_block_with_new_evidence(self):
        self.runner.truncation_budget = 1
        self.assertEqual(self.device.read_flash(tx.TABLE_START, tx.TABLE_BYTES), self.old_table)
        commands = self.read_commands()
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][-3:-1], commands[1][-3:-1])
        self.assertNotEqual(commands[0][-1], commands[1][-1])
        self.assertEqual(Path(commands[0][-1]).read_bytes(), b"partial")

    def test_truncated_read_stops_at_three_attempts(self):
        self.runner.truncation_budget = READ_TRANSFER_ATTEMPTS + 1
        with self.assertRaises(OSError):
            self.device.read_flash(tx.TABLE_START, tx.TABLE_BYTES)
        self.assertEqual(len(self.read_commands()), READ_TRANSFER_ATTEMPTS)

    def test_short_successful_read_is_not_retried(self):
        self.runner.short_read_once = True
        with self.assertRaises(ValueError):
            self.device.read_flash(tx.TABLE_START, tx.TABLE_BYTES)
        self.assertEqual(len(self.read_commands()), 1)

    def test_each_transfer_must_include_matching_identity(self):
        for log in ("Serial port COM4\n", TRANSFER_LOG.replace("43:c2:01", "43:c2:02")):
            self.runner.transfer_identity = log
            with self.subTest(log=log), self.assertRaises(ValueError):
                self.device.read_flash(tx.TABLE_START, tx.TABLE_BYTES)

    def test_wrong_capacity_stops_before_read(self):
        self.runner.flash_identity = FLASH_LOG.replace("size: 32MB", "size: 16MB")
        with self.assertRaises(ValueError):
            self.device.read_full()
        self.assertEqual(self.read_commands(), [])

    def test_missing_intent_prevents_write_and_device_access(self):
        with self.assertRaises(ValueError):
            self.device.write_flash(tx.UPPER_START, self.upper)
        self.assertEqual(self.runner.calls, [])

    def test_wrong_phase_payload_or_address_is_rejected_before_identity(self):
        self.write_intent()
        for offset, raw in ((tx.TABLE_START, self.plan.candidate_table), (0x10000, self.upper), (tx.UPPER_START, b"X"*tx.UPPER_BYTES)):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.device.write_flash(offset, raw)
        self.assertEqual(self.runner.calls, [])

    def test_write_options_keep_mode_frequency_size_and_no_after_reset(self):
        self.write_intent()
        self.device.write_flash(tx.UPPER_START, self.upper)
        command = next(args for args, _ in self.runner.calls if "write_flash" in args)
        for option in ("--flash_mode", "--flash_freq", "--flash_size"):
            self.assertEqual(command[command.index(option)+1], "keep")
        self.assertEqual(command[command.index("--port")+1], "COM4")
        self.assertEqual(command[command.index("--after")+1], "no_reset")
        self.assertIn("--verify", command)
        self.assertNotIn("--erase-all", command)
        self.assertNotIn("--force", command)
        self.assertTrue(all(timeout <= 180 for _, timeout in self.runner.calls))

    def test_write_error_is_not_retried_by_transport(self):
        self.write_intent()
        self.runner.fail_write_once = True
        with self.assertRaises(OSError):
            self.device.write_flash(tx.UPPER_START, self.upper)
        self.assertEqual(len(self.runner.writes), 1)

    def test_write_success_requires_tool_hash_marker(self):
        self.write_intent()
        self.runner.missing_write_hash_once = True
        with self.assertRaisesRegex(ValueError, "payload verification"):
            self.device.write_flash(tx.UPPER_START, self.upper)

    def test_changed_journal_after_identity_prevents_write(self):
        self.write_intent()
        original = self.device._require_plan_identity
        def change():
            identity = original()
            self.journal.save("needs_recovery", reason="simulated state change")
            return identity
        with patch.object(self.device, "_require_plan_identity", change):
            with self.assertRaisesRegex(ValueError, "intent changed"):
                self.device.write_flash(tx.UPPER_START, self.upper)
        self.assertEqual(self.runner.writes, [])

    def test_payload_log_reservation_failure_precedes_device_access(self):
        self.write_intent()
        with patch.object(self.device, "_reserve_log", side_effect=FileExistsError("collision")):
            with self.assertRaises(FileExistsError):
                self.device.write_flash(tx.UPPER_START, self.upper)
        self.assertEqual(self.runner.calls, [])

    def test_process_timeout_is_retained_in_command_log(self):
        self.runner.timeout_once = True
        with self.assertRaises(CommandTimedOut):
            self.device.identity()
        self.assertTrue(any("simulated host timeout" in p.read_text(encoding="utf-8") for p in (self.folder/"logs").iterdir()))

    def test_restart_continues_maximum_direct_evidence_sequence(self):
        (self.folder/"logs").mkdir()
        (self.folder/"logs/055-old.log").write_text("preserve", encoding="utf-8")
        restarted = RehearsalDevice(self.folder, self.runner)
        restarted.identity()
        self.assertTrue((self.folder/"logs/056-flash-id.log").is_file())
        self.assertEqual((self.folder/"logs/055-old.log").read_text(encoding="utf-8"), "preserve")

    def test_a7_entry_points_are_unavailable(self):
        with self.assertRaises(ValueError):
            self.device.snapshot(self.plan)
        with self.assertRaises(ValueError):
            self.device.capture_probe(45)
        self.assertEqual(self.runner.calls, [])

    def test_boot_accepts_emote_startup_without_lvgl_assets_logs(self):
        self.assertNotIn("The partition size is", EMOTE_BOOT_LOG)
        self.assertNotIn("The checksum calculation time is", EMOTE_BOOT_LOG)
        for target in ("original", "candidate"):
            with self.subTest(target=target):
                self.assertTrue(self.device.boot(target))

    def test_boot_rejects_missing_initialization_markers(self):
        for marker in _NORMAL_BOOT_MARKERS:
            self.runner.boot_log = EMOTE_BOOT_LOG.replace(marker, "incomplete")
            with self.subTest(marker=marker):
                self.assertFalse(self.device.boot("original"))

    def test_empty_boot_log_does_not_count_as_success(self):
        self.runner.boot_log = ""
        self.assertFalse(self.device.boot("original"))

    def test_repeated_app_start_does_not_count_as_stable_boot(self):
        self.runner.boot_extra = "I (228) main_task: Calling app_main()\n"
        self.assertFalse(self.device.boot("original"))

    def test_boot_capture_failure_does_not_count_as_success(self):
        with patch.object(self.device, "_capture_serial", side_effect=OSError("capture failed")):
            self.assertFalse(self.device.boot("original"))

    def test_unknown_boot_target_fails_before_device_access(self):
        with self.assertRaises(ValueError):
            self.device.boot("other")
        self.assertEqual(self.runner.calls, [])

    def test_boot_rejects_panics_and_asset_model_errors(self):
        for marker in (
            "assert failed", "Guru Meditation", "abort() was called",
            "Failed to load srmodels.bin", "No assets partition found",
            "The index.json file is not found", "The index.json file is not valid",
            "The srmodels file srmodels.bin is not found", "Failed to initialize emote",
            "Emote display is not initialized", "Failed to get asset data by name: index.json",
        ):
            self.runner.boot_extra = marker
            with self.subTest(marker=marker):
                self.assertFalse(self.device.boot("original"))

    def test_boot_capture_explicitly_uses_usb_reset_and_bounded_duration(self):
        self.assertTrue(self.device.boot("original"))
        argv, timeout = next(item for item in self.runner.calls if str(CAPTURE_SCRIPT) in item[0])
        self.assertIn("--usb-reset", argv)
        self.assertEqual(argv[argv.index("--duration")+1], "45")
        self.assertEqual(timeout, 90)

    def test_complete_controller_migration_and_recovery_through_actual_adapter(self):
        result = self.execute()
        self.assertEqual(result["result"], "candidate_committed_simulation")
        self.assertFalse(result["physical_interaction_accepted"])
        self.assertEqual([row["offset"] for row in self.runner.writes], [tx.UPPER_START,tx.TABLE_START])
        for filename in tx.CANDIDATE_FILES.values():
            (self.folder/filename).unlink()
        restarted = RehearsalDevice(self.folder, self.runner)
        restored = tx.recover(self.folder,restarted)
        self.assertTrue(restored["restored"])
        self.assertEqual([row["offset"] for row in self.runner.writes[-2:]], [tx.TABLE_START,tx.UPPER_START])
        self.assertEqual(bytes(self.runner.data),self.snapshot)
        count = len(self.runner.writes)
        tx.recover(self.folder,restarted)
        self.assertEqual(len(self.runner.writes),count)

    def test_partial_serial_write_recovers_and_keeps_original_table(self):
        self.runner.fail_write_once = True
        result = self.execute()
        self.assertTrue(result["restored"])
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertTrue(all(row["offset"] == tx.UPPER_START for row in self.runner.writes))

    def test_missing_tool_verification_causes_controller_restore(self):
        self.runner.missing_write_hash_once = True
        self.assertTrue(self.execute()["restored"])
        self.assertEqual(bytes(self.runner.data), self.snapshot)

    def test_preexisting_boot_or_asset_failures_do_not_claim_acceptance(self):
        self.runner.boot_extra = "Failed to mmap assets partition"
        with self.assertRaises(tx.RecoveryRequired):
            self.execute()
        self.assertEqual(bytes(self.runner.data), self.snapshot)
        self.assertEqual(self.journal.load()["phase"], "needs_recovery")


class RetainedIntegrationTests(unittest.TestCase):
    def test_real_e118_resource_roundtrip_through_serial_command_boundary(self):
        upper_path = ROOT / "tmp/e135b-migration-20261029/candidate/assets.bin"
        table_path = ROOT / "tmp/e136-n32r16-layout-20261030/tables/02-upper-assets-8m.bin"
        if not upper_path.is_file() or not table_path.is_file():
            self.skipTest("retained private E1.18 and E1.36 inputs required")
        old = ROOT / "tmp/e135b-migration-20261029/old"
        snapshot = synthetic_snapshot((old/"partition-table.bin").read_bytes(), (old/"assets.bin").read_bytes())
        folder = ROOT / "tmp" / ("e143c-sim-c2-" + uuid.uuid4().hex)
        self.addCleanup(shutil.rmtree, folder, True)
        plan = tx.make_plan(snapshot, upper_path.read_bytes(), table_path.read_bytes(),
                             identity_sha256=parse_identity(FLASH_LOG,SECURITY_LOG).digest, run_label=folder.name)
        tx.persist_plan(folder,plan)
        runner = ReplayRunner(snapshot,plan.candidate_table)
        device = RehearsalDevice(folder,runner)
        self.assertEqual(tx.execute(folder,device,initial_snapshot=snapshot)["result"], "candidate_committed_simulation")
        for filename in tx.CANDIDATE_FILES.values():
            (folder/filename).unlink()
        self.assertTrue(tx.recover(folder,RehearsalDevice(folder,runner))["restored"])
        self.assertEqual(bytes(runner.data),snapshot)
        self.assertEqual([row["offset"] for row in runner.writes],
                         [tx.UPPER_START,tx.TABLE_START,tx.TABLE_START,tx.UPPER_START])


if __name__ == "__main__":
    unittest.main()
