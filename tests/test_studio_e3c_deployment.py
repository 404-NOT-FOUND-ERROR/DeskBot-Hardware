"""E3c deployment uses synthetic Flash; no device or private build is needed."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as tx
from protocol import studio_e3c_deployment as reviewed

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e3c_deployment_test", ROOT / "scripts/deploy-deskbot-studio-e3c.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = bytearray(b"\xff" * tx.FLASH)
        cls.original = b"\xe9" + b"o" * 8999
        cls.image = b"\xe9" + b"n" * 9999
        raw[tx.START:tx.START + len(cls.original)] = cls.original
        raw[tx.START + 12000] = 0x47
        cls.raw = bytes(raw)

    def make_plan(self):
        return tx.prepare(self.raw, self.image, "a" * 64, len(self.original), edition="E3c")

    def test_old_default_preserves_exact_plan_material(self):
        old = tx.prepare(self.raw, self.image, "a" * 64, len(self.original))
        explicit = tx.prepare(self.raw, self.image, "a" * 64, len(self.original), edition="E3b")
        self.assertEqual(old.id, explicit.id)
        self.assertEqual(old.metadata["edition"], "E3b")
        self.assertNotEqual(old.id, self.make_plan().id)

    def test_unknown_edition_rejected(self):
        with self.assertRaises(ValueError):
            tx.prepare(self.raw, self.image, "a" * 64, len(self.original), edition="E4")

    def test_e3c_union_keeps_original_tail(self):
        plan = self.make_plan()
        self.assertEqual(plan.metadata["edition"], "E3c")
        self.assertEqual(len(plan.rollback), 12288)
        self.assertEqual(plan.candidate[12000], 0x47)
        self.assertEqual(plan.snapshot[:tx.START], self.raw[:tx.START])

    def test_real_geometry(self):
        span = (max(reviewed.APP_SIZE, reviewed.ORIGINAL_SIZE) + 4095) // 4096 * 4096
        self.assertEqual(span, 4513792)
        self.assertEqual(tx.START + span, 0x45E000)
        self.assertEqual(tx.PARTITION - reviewed.APP_SIZE, 1633568)

    def test_every_static_region_is_compared(self):
        for offset in (0, 0x8000, 0xD000, tx.START, tx.START + 12000, 0xBC8000, 0x1000000, tx.FLASH-1):
            with self.subTest(offset=offset):
                raw = bytearray(self.raw)
                raw[offset] ^= 1
                with self.assertRaises(ValueError):
                    reviewed.check_current(bytes(raw), {"snapshot": self.raw})

    def test_current_nvs_values_are_backed_up(self):
        raw = bytearray(self.raw)
        raw[0x9000] ^= 1
        reviewed.check_current(bytes(raw), {"snapshot": self.raw})
        plan = tx.prepare(bytes(raw), self.image, "a" * 64, len(self.original), edition="E3c")
        self.assertEqual(plan.snapshot[0x9000], raw[0x9000])

    def reference_device(self, folder, raw, result="-- verify OK (digest matched)"):
        device = entry.Device.__new__(entry.Device)
        device._capture = lambda label, offset, size: raw[:size]
        device.identity = lambda: "a" * 64
        device._new_path = lambda kind, name: folder / name
        device._invoke = lambda *args: result
        return device

    def test_retained_backup_uses_current_nvs_and_full_device_verification(self):
        raw = bytearray(self.raw)
        raw[0x9000] = 0x23
        with tempfile.TemporaryDirectory() as name:
            device = self.reference_device(Path(name), bytes(raw))
            with patch.object(device, "_invoke", return_value="-- verify OK (digest matched)") as verify:
                actual = device.read_reference({"snapshot": self.raw})
            self.assertEqual(actual, bytes(raw))
            self.assertEqual((Path(name) / "current-flash-reference.bin").read_bytes(), actual)
            self.assertEqual(verify.call_args.args[1][0], "verify_flash")
            self.assertEqual(verify.call_args.args[1][-2], "0x0")
            self.assertIn("keep", verify.call_args.args[1])
            self.assertEqual(Path(name, "current-flash-reference.bin").stat().st_size, tx.FLASH)

    def test_retained_backup_digest_failure_prevents_preparation(self):
        for output in ("", "-- verify OK (digest matched)\n-- verify OK (digest matched)"):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as name:
                device = self.reference_device(Path(name), self.raw, output)
                with self.assertRaises(ValueError):
                    device.read_reference({"snapshot": self.raw})

    def test_retained_backup_static_prefix_drift_prevents_device_verification(self):
        raw = bytearray(self.raw)
        raw[0x8000] ^= 1
        with tempfile.TemporaryDirectory() as name:
            device = self.reference_device(Path(name), bytes(raw))
            with patch.object(device, "_invoke") as verify, self.assertRaises(ValueError):
                device.read_reference({"snapshot": self.raw})
            verify.assert_not_called()

    def test_retained_reference_option_cannot_be_used_during_install(self):
        with patch.object(entry, "Device") as device, self.assertRaises(ValueError):
            entry.run("install", Path("unused"), reuse_reference=True)
        device.assert_not_called()

    def test_short_backup_rejected(self):
        with self.assertRaises(ValueError):
            reviewed.check_current(self.raw[:-1], {"snapshot": self.raw})

    def test_changed_candidate_rejected_before_image_parser(self):
        with patch.object(reviewed, "image_facts") as parser:
            with self.assertRaises(ValueError):
                reviewed.reviewed_candidate(self.image)
        parser.assert_not_called()

    def test_candidate_pins_capacity_as_well_as_digest(self):
        with patch.multiple(reviewed, APP_SIZE=len(self.image), APP_SHA=tx.sha(self.image)):
            with patch.object(reviewed, "image_facts", return_value={"configured_flash_bytes": tx.FLASH}):
                self.assertEqual(reviewed.reviewed_candidate(self.image), self.image)
            with patch.object(reviewed, "image_facts", return_value={"configured_flash_bytes": tx.FLASH//2}):
                with self.assertRaises(ValueError):
                    reviewed.reviewed_candidate(self.image)

    def test_recovery_independent_of_candidate_and_reference_report(self):
        plan = self.make_plan()
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            tx.persist(folder, plan)
            (folder / "candidate-app.bin").unlink()
            saved = tx.load_recovery(folder)
            with patch.multiple(reviewed, ORIGINAL_SIZE=len(self.original), ORIGINAL_SHA=tx.sha(self.original)):
                reviewed.check_plan(saved, candidate=False)
            self.assertEqual(saved.rollback, plan.rollback)
            self.assertEqual(saved.id, plan.id)

    def test_saved_plan_requires_pinned_original_app(self):
        with self.assertRaises(ValueError):
            reviewed.check_plan(self.make_plan(), candidate=False)

    def test_valid_run_stays_inside_repository(self):
        for label in ("../e3c-live-20261105-x", "e3b-live-20261105-x", "e3c-live-20261105-x/../bad"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                entry.folder_for(label)

    def test_recovery_reserves_result_before_device_access(self):
        with tempfile.TemporaryDirectory() as name:
            with patch.object(entry, "Device") as factory:
                with patch.object(Path, "open", side_effect=PermissionError("read only")):
                    with self.assertRaises(PermissionError):
                        entry.recover_saved(Path(name), self.make_plan())
                factory.assert_not_called()

    def test_restored_app_identity_is_checked(self):
        device = entry.Device.__new__(entry.Device)
        device.deployment_plan = self.make_plan()
        synthetic_mac = ":".join(("00",) * 5 + ("01",))
        device._identity = type("Identity", (), {"mac": synthetic_mac})()
        device._serial = lambda label, duration: "boot log"
        with patch.object(entry.parent, "inspect_boot", return_value={"normal_boot": True}) as inspect:
            device.boot(False)
        self.assertEqual(inspect.call_args.args[1], self.original)
        self.assertTrue(inspect.call_args.kwargs["identity_required"])

    def test_candidate_requires_secondary_usb_input_initialization(self):
        device = entry.Device.__new__(entry.Device)
        device.deployment_plan = self.make_plan()
        device._identity = type("Identity", (), {"mac": ":".join(("00",) * 5 + ("01",))})()
        for log in ("boot log", "USB input unavailable (2)",
                    "USB input ready: /dev/secondary\nUSB input owner changed"):
            device._serial = lambda label, duration, log=log: log
            with self.subTest(log=log), patch.object(entry.parent, "inspect_boot") as inspect:
                with self.assertRaisesRegex(ValueError, "USB input initialization failed"):
                    device.boot(True)
                inspect.assert_not_called()
        device._serial = lambda label, duration: "USB input ready: /dev/secondary"
        with patch.object(entry.parent, "inspect_boot", return_value={"normal_boot": True}) as inspect:
            self.assertEqual(device.boot(True), {"normal_boot": True})
        self.assertEqual(inspect.call_args.args[1], self.image)

    def test_http_state_includes_host_progress_without_changing_worker_contract(self):
        from protocol.studio_control_link import initial_snapshot
        spec = importlib.util.spec_from_file_location("e3c_live_state_test", ROOT / "tools/verify-studio-control-e3c.py")
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        row = initial_snapshot("connection-" + "a" * 32)
        for host in (None, {"command_id": "live-1", "operation": "neutral", "status": "queued"}):
            value = dict(row, host_command=host)
            self.assertEqual(verify.checked_state(value), value)

    def test_http_state_rejects_extra_fields_and_invalid_host_progress(self):
        from protocol.studio_control_link import initial_snapshot
        spec = importlib.util.spec_from_file_location("e3c_invalid_live_state_test", ROOT / "tools/verify-studio-control-e3c.py")
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        row = initial_snapshot("connection-" + "a" * 32)
        for value in (dict(row, arbitrary=True), dict(row, host_command={"operation": "turn_head"})):
            with self.assertRaises(ValueError):
                verify.checked_state(value)


if __name__ == "__main__":
    unittest.main()
