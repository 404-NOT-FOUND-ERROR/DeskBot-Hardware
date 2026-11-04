"""Fault tests for the permanent App-only installer; synthetic Flash only."""
from pathlib import Path
import hashlib
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("studio_deploy_entry", ROOT / "scripts/deploy-deskbot-studio-e3b.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class Device:
    def __init__(self, plan, *, fail=None):
        self.plan, self.raw, self.fail = plan, bytearray(plan.snapshot), fail
        self.writes = []

    def identity(self):
        return "b" * 64 if self.fail == "identity" else self.plan.metadata["identity_sha256"]

    def read_app(self, size):
        return bytes(self.raw[tx.START:tx.START + size])

    def verify_original(self):
        return self.read_app(len(self.plan.rollback)) == self.plan.rollback

    def write_app(self, raw):
        self.writes.append(raw)
        self.raw[tx.START:tx.START + len(raw)] = raw
        if self.fail == "write" and len(self.writes) == 1:
            raise OSError("partial write")

    def read_full(self):
        raw = bytes(self.raw)
        if self.fail == "readback" and len(self.writes) == 1:
            return raw[:tx.START] + b"\0" + raw[tx.START + 1:]
        return raw

    def boot(self, candidate):
        if candidate and self.fail == "boot":
            raise ValueError("missing hello")
        return {"normal_boot": True}

    def verify_image(self, payload, *, allow_runtime_nvs):
        return tx.compare(self.plan, self.read_full(), payload, allow_runtime_nvs=allow_runtime_nvs)


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = bytearray(b"\xff" * tx.FLASH)
        raw[tx.START:tx.START + 9000] = b"\xe9" + b"o" * 8999
        # A real trailing byte must survive a shorter candidate.
        raw[tx.START + 12000] = 0x47
        cls.plan = tx.prepare(bytes(raw), b"\xe9" + b"n" * 9100, "a" * 64, 9000)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.journal = tx.Journal(self.folder, self.plan)

    def test_success_retains_candidate_and_pending_physical_acceptance(self):
        device = Device(self.plan)
        result = tx.install(self.plan, device, self.journal)
        self.assertEqual(result["result"], "candidate_installed")
        self.assertEqual(result["hardware_acceptance"], "pending")
        self.assertEqual(device.writes, [self.plan.candidate])
        self.assertEqual(self.journal.load()["phase"], "installed")

    def test_failure_restores_exact_union(self):
        for failure in ("write", "readback", "boot"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                journal = tx.Journal(Path(folder), self.plan)
                device = Device(self.plan, fail=failure)
                with self.assertRaises((ValueError, OSError)):
                    tx.install(self.plan, device, journal)
                self.assertEqual(device.writes[-1], self.plan.rollback)
                self.assertEqual(device.read_full(), self.plan.snapshot)
                self.assertEqual(journal.load()["phase"], "restored")

    def test_identity_mismatch_performs_no_write(self):
        device = Device(self.plan, fail="identity")
        with self.assertRaises(ValueError):
            tx.install(self.plan, device, self.journal)
        self.assertEqual(device.writes, [])

    def test_changed_source_app_performs_no_write(self):
        device = Device(self.plan)
        device.raw[tx.START] = 0
        with self.assertRaises(ValueError):
            tx.install(self.plan, device, self.journal)
        self.assertEqual(device.writes, [])

    def test_saved_bytes_are_checked(self):
        tx.persist(self.folder, self.plan)
        self.assertEqual(tx.load(self.folder).id, self.plan.id)
        (self.folder / "candidate-app.bin").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            tx.load(self.folder)

    def test_interrupted_write_can_be_recovered(self):
        device = Device(self.plan)
        self.journal.save("restore_required")
        device.raw[tx.START:tx.START + 100] = b"\0" * 100
        result = tx.recover(self.plan, device, self.journal)
        self.assertEqual(result["result"], "original_restored")
        self.assertEqual(device.read_full(), self.plan.snapshot)

    def test_recovery_refuses_a_different_device(self):
        self.journal.save("restore_required")
        device = Device(self.plan, fail="identity")
        with self.assertRaises(ValueError):
            tx.recover(self.plan, device, self.journal)
        self.assertEqual(device.writes, [])
        self.assertEqual(self.journal.load()["phase"], "needs_recovery")

    def test_repeated_install_is_rejected(self):
        self.journal.save("installed")
        device = Device(self.plan)
        with self.assertRaises(ValueError):
            tx.install(self.plan, device, self.journal)
        self.assertEqual(device.writes, [])

    def test_interrupted_journal_temp_does_not_block_recovery(self):
        self.journal.save("restore_required")
        (self.folder / "deployment-state.new-interrupted").write_bytes(b"partial")
        tx.recover(self.plan, Device(self.plan), self.journal)
        self.assertEqual(self.journal.load()["phase"], "restored")

    def test_changed_protected_bytes_are_rejected(self):
        raw = bytearray(self.plan.snapshot)
        raw[0x8000] ^= 1
        with self.assertRaises(ValueError):
            tx.compare(self.plan, bytes(raw), self.plan.rollback)

    def test_runtime_nvs_changes_are_reported_during_recovery(self):
        raw = bytearray(self.plan.snapshot)
        raw[0x9000] ^= 1
        with self.assertRaises(ValueError):
            tx.compare(self.plan, bytes(raw), self.plan.rollback)
        result = tx.compare(self.plan, bytes(raw), self.plan.rollback, allow_runtime_nvs=True)
        self.assertFalse(result["nvs_equal"])

    def test_trailing_sector_bytes_are_preserved(self):
        self.assertEqual(len(self.plan.candidate), 12288)
        self.assertEqual(self.plan.candidate[12000], 0x47)

    def test_oversize_candidate_rejected(self):
        with self.assertRaises(ValueError):
            tx.prepare(self.plan.snapshot, b"\xe9" * (tx.PARTITION + 1), "a" * 64, 9000)

    def test_adapter_accepts_only_durable_exact_app_payload(self):
        tx.persist(self.folder, self.plan)
        device = entry.Device.__new__(entry.Device)
        device.run_dir, device.deployment_plan = self.folder, self.plan
        device.identity = lambda: "a" * 64
        device._new_path = lambda folder, name: self.folder / name
        calls = []
        device._invoke = lambda label, args: calls.append((label, args))
        with self.assertRaises(ValueError):
            device.write_app(self.plan.candidate)
        self.journal.save("restore_required")
        with self.assertRaises(ValueError):
            device.write_app(b"arbitrary")
        device.write_app(self.plan.candidate)
        args = calls[0][1]
        self.assertEqual(args[-2], "0x10000")
        self.assertNotIn("--erase-all", args)
        self.assertEqual(Path(args[-1]).read_bytes(), self.plan.candidate)


class BootTests(unittest.TestCase):
    MAC = "00:00:00:00:00:01"

    def make_log(self, *, count=3, version="2.0.5", step=10000):
        device_id = "device-" + hashlib.sha256(b"deskbot-link-v1\0" + bytes.fromhex(self.MAC.replace(":", ""))).digest()[:16].hex()
        lines = list(entry._NORMAL_BOOT_MARKERS)
        for seq in range(count):
            value = {"schema": "deskbot.device_link/v1", "message_type": "hello", "device_id": device_id,
                     "boot_id": "boot-" + "b" * 32, "sequence": seq, "uptime_ms": 1000 + seq * step,
                     "firmware": {"edition": "E3b", "version": version, "app_elf_sha256": "c" * 64},
                     "hardware": {"chip": "esp32s3", "board": "esp_vocat", "flash_bytes": tx.FLASH, "psram_bytes": 0x1000000},
                     "capabilities": ["device_identity"], "transport": "usb_console", "controls_available": False,
                     "catalog_id": None, "limits": {"max_frame_bytes": 2048, "hello_interval_ms": 10000}}
            lines.append("2026-11-04T10:00:00\t\x1b[0;32mI (1000) DeskBotLink: " + json.dumps(value) + "\x1b[0m")
        return "\n".join(lines)

    def inspect(self, log, **kwargs):
        with patch.object(entry, "image_identity", return_value={"version": "2.0.5", "app_elf_sha256": "c" * 64}):
            return entry.inspect_boot(log, b"", self.MAC, identity_required=True, **kwargs)

    def test_three_matching_periodic_announcements(self):
        self.assertEqual(self.inspect(self.make_log())["hello_count"], 3)

    def test_wrong_version_is_rejected(self):
        with self.assertRaises(ValueError):
            self.inspect(self.make_log(version="wrong"))

    def test_missing_periodic_announcement_is_rejected(self):
        with self.assertRaises(ValueError):
            self.inspect(self.make_log(count=2))

    def test_wrong_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            self.inspect(self.make_log(step=10))

    def test_panic_is_rejected(self):
        with self.assertRaises(ValueError):
            self.inspect(self.make_log() + "\nGuru Meditation")


if __name__ == "__main__":
    unittest.main()
