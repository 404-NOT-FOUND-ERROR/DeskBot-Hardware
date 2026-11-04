import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol.capacity_serial_e1_42_3 import extract_records


def record(kind, value):
    payload = json.dumps(value, separators=(",", ":"))
    return f"timestamp\t[DESKBOT_E1423 {kind} bytes={len(payload.encode())}] {payload}"


class SerialRecordTests(unittest.TestCase):
    def test_two_records_preserve_raw_fields(self):
        log = "boot log\n" + record("capacity", {"a": 1}) + "\n" + record("probe", {"b": False})
        self.assertEqual(extract_records(log), {"capacity": {"a": 1}, "probe": {"b": False}})

    def test_incomplete_and_empty_are_rejected(self):
        for log in ("", record("probe", {"a": 1})):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                extract_records(log)

    def test_duplicate_boot_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            extract_records(record("probe", {}) + "\n" + record("probe", {}))

    def test_truncation_and_oversize_are_rejected(self):
        for log in (record("probe", {"a": 1})[:-1], record("probe", {"a": "x" * 8192})):
            with self.assertRaisesRegex(ValueError, "length"):
                extract_records(log)

    def test_malformed_and_unknown_record_are_rejected(self):
        for log in ("[DESKBOT_E1423 probe bytes=no] {}", record("other", {})):
            with self.assertRaisesRegex(ValueError, "malformed"):
                extract_records(log)

    def test_error_and_nonobject_are_rejected(self):
        for value in ({"error": "unavailable"}, [], None):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                extract_records(record("probe", value))

    def test_garbled_json_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "JSON"):
            extract_records("[DESKBOT_E1423 probe bytes=1] {")

    def test_diagnostic_is_default_off_and_exits(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "firmware/body/main/boards/esp_vocat/dev_tools.cc").read_text(encoding="utf-8")
        body = source.split("void ExportFlashDiagnosticsOnce(void*) {", 1)[1].split("\n#endif", 1)[0]
        self.assertIn("seconds < 120", body)
        self.assertIn("quiet_observations == 3", body)
        self.assertIn("kDeviceStateWifiConfiguring && stable_observations >= 10", body)
        self.assertIn("app.GetAudioService().IsIdle()", body)
        for state in ("kDeviceStateSpeaking", "kDeviceStateListening", "kDeviceStateUpgrading"):
            self.assertNotIn("state == " + state, body)
        self.assertIn("vTaskDelete(nullptr)", body)
        for forbidden in ("Schedule(", "GetDisplay(", "lv_", "esp_flash_write", "nvs_set"):
            self.assertNotIn(forbidden, body)
        config = (root / "firmware/body/main/Kconfig.projbuild").read_text(encoding="utf-8")
        block = config.split("config DESKBOT_FLASH_PROBE_BOOT_EXPORT", 1)[1].split("\nconfig ", 1)[0]
        self.assertIn("default n", block)


if __name__ == "__main__":
    unittest.main()
