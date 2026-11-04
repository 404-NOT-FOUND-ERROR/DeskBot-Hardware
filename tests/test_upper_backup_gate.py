"""Portable identity, exact-sector and retained-artifact rejection tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("upper_backup_audit", ROOT / "scripts/audit-expression-e1-43a3-backup.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class UpperBackupGateTests(unittest.TestCase):
    mac = "02:00:00:aa:bb:01"
    common = ("esptool.py v4.12.0\nSerial port COM4\n"
              "Chip is ESP32-S3 (QFN56) (revision v0.2)\nMAC: " + mac + "\n")
    flash = (common + "Embedded PSRAM 16MB\nManufacturer: c2\nDevice: 8039\nDetected flash size: 32MB\n"
             "Flash type set in eFuse: octal (8 data lines)\nFlash voltage set by eFuse to 3.3V\n")
    security = common + "Secure Boot: Disabled\nFlash Encryption: Disabled\n"

    def test_identity_and_disabled_security_are_required(self):
        audit.validate_identity(self.flash, self.security, self.mac)
        for text in (self.flash.replace("32MB", "16MB"), self.flash.replace("COM4", "COM8"),
                     self.flash.replace("ESP32-S3", "ESP32-C61"), self.flash.replace("octal", "quad"),
                     self.flash + "MAC: " + self.mac + "\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                audit.validate_identity(text, self.security, self.mac)

    def test_identity_disagreement_and_invalid_mac_are_rejected(self):
        for mac in ("invalid", self.mac.upper(), "02:00:00:00:00:02", None):
            with self.subTest(mac=mac), self.assertRaises(ValueError):
                audit.validate_identity(self.flash, self.security, mac)
        with self.assertRaises(ValueError):
            audit.validate_identity(self.flash, self.security.replace(self.mac, "02:00:00:00:00:02"), self.mac)

    def test_enabled_missing_and_conflicting_security_are_rejected(self):
        for text in (self.security.replace("Disabled", "Enabled"), self.common,
                     self.security + "Flash Encryption: Enabled\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                audit.validate_identity(self.flash, text, self.mac)

    def test_rollback_uses_larger_payload_and_complete_sectors(self):
        self.assertEqual(audit.rollback_span(4481776, 4486768), 4489216)
        self.assertEqual(audit.rollback_span(8193, 4096), 12288)
        self.assertEqual(audit.rollback_span(4096, 8192), 8192)

    def test_boolean_zero_negative_and_oversize_app_lengths_are_rejected(self):
        for value in (True, 0, -1, audit.APP_BYTES + 1, 4096.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit.rollback_span(4096, value)

    def test_exact_rollback_rejects_prefix_only_padding_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollback.bin"
            raw = bytes(4096) + bytes([0xA5]) * 4096 + bytes(4096)
            path.write_bytes(raw[4096:8192])
            self.assertEqual(audit.verify_slice(path, raw, 4096, 4096)["bytes"], 4096)
            for data in (path.read_bytes()[:-1], path.read_bytes() + b"\xff", bytes(4096)):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    audit.verify_slice(path, raw, 4096, 4096)

    def test_rollback_geometry_rejects_partial_sector_and_out_of_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollback.bin"
            path.write_bytes(bytes(4096))
            for start, size in ((1, 4096), (0, 4095), (4096, 4096), (True, 4096), (0, 0)):
                with self.subTest(start=start, size=size), self.assertRaises(ValueError):
                    audit.verify_slice(path, bytes(4096), start, size)

    def test_artifact_hash_and_size_are_both_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.bin"
            path.write_bytes(b"audited artifact")
            metadata = {"bytes": path.stat().st_size, "sha256": audit.sha(path.read_bytes())}
            self.assertEqual(audit.verified_file(path, metadata), b"audited artifact")
            path.write_bytes(b"changed artifact")
            with self.assertRaises(ValueError):
                audit.verified_file(path, metadata)


class HistoricalSourceGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.source = self.root / "retained/p"
        self.source.mkdir(parents=True)
        self.enterContext(patch.object(audit, "RETAINED_SOURCE", self.source))
        self.enterContext(patch.object(audit, "ROOT", self.root))
        original = {
            "probe.h": b"historical probe",
            "dependencies.lock": b"dependencies:\r\n      path: components/espressif__touch_sensor_fsm\r\nversion: 1\r\n",
        }
        for name, raw in original.items():
            if name == "dependencies.lock":
                raw = raw.replace(b"components/espressif__touch_sensor_fsm",
                                  str(self.source / "components/espressif__touch_sensor_fsm").encode())
            (self.source / name).write_bytes(raw)
        inputs = {name: audit.sha(raw) for name, raw in original.items()}
        manifest = "".join(digest + "  " + name + "\n" for name, digest in inputs.items()).encode()
        self.build = {"source_sha256": inputs, "source_manifest_sha256": audit.sha(manifest)}

    def test_archive_accepts_historical_source_when_current_candidate_has_changed(self):
        current = self.root / "firmware/body"
        current.mkdir(parents=True)
        (current / "probe.h").write_bytes(b"new A7 probe")
        (current / "SOURCE_MANIFEST.sha256").write_bytes(b"new candidate inventory")
        audit.verify_retained_sources(self.build)

    def test_historical_source_changes_and_missing_files_are_rejected(self):
        path = self.source / "probe.h"
        path.write_bytes(b"changed old probe")
        with self.assertRaisesRegex(ValueError, "historical build source changed"):
            audit.verify_retained_sources(self.build)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing or unsafe"):
            audit.verify_retained_sources(self.build)

    def test_historical_inventory_omission_is_rejected(self):
        del self.build["source_sha256"]["probe.h"]
        with self.assertRaisesRegex(ValueError, "historical source manifest changed"):
            audit.verify_retained_sources(self.build)

    def test_only_known_lock_path_rebase_is_accepted(self):
        path = self.source / "dependencies.lock"
        raw = path.read_bytes()
        for data in (raw.replace(b"version: 1", b"version: 2"), raw.replace(b"components", b"other"),
                     raw + b"      path: other\r\n"):
            path.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                audit.verify_retained_sources(self.build)

    def test_lock_newline_changes_are_rejected(self):
        path = self.source / "dependencies.lock"
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
        with self.assertRaisesRegex(ValueError, "historical build source changed"):
            audit.verify_retained_sources(self.build)

    def test_inventory_path_escape_is_rejected_even_with_matching_manifest(self):
        inputs = {"../outside.h": audit.sha(b"outside")}
        (self.source.parent / "outside.h").write_bytes(b"outside")
        self.build["source_sha256"] = inputs
        self.build["source_manifest_sha256"] = audit.sha(
            "".join(digest + "  " + name + "\n" for name, digest in inputs.items()).encode())
        with self.assertRaisesRegex(ValueError, "missing or unsafe"):
            audit.verify_retained_sources(self.build)


if __name__ == "__main__":
    unittest.main()
