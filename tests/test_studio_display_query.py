"""Compile production display snapshot/session code and validate native replies."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

import jsonschema
from protocol import studio_display_query as display
from protocol import studio_usb_session as session

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "firmware/body/main"
BOARD = MAIN / "boards/esp_vocat"
TARGET, BOOT = "device-" + "a"*32, "boot-" + "b"*32
FIRST = "session-" + "b"*32 + "-1"
IDLE = ("snapshot", 1, 2, 3, 1, 2, 1)
PLAYER = ("player", 1, 2)


class DisplayQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("display_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        command, kind = helper.find_compiler() or (None, None)
        if command is None:
            raise RuntimeError("C++17 compiler required")
        (ROOT / "tmp").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="e3c4-display-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        (cls.folder / "freertos").mkdir()
        (cls.folder / "sdkconfig.h").write_text("#pragma once\n#define CONFIG_DESKBOT_STUDIO_RUNTIME_SNAPSHOT_ENABLE 1\n"
            "#define CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE 1\n#define CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE 1\n", encoding="utf-8")
        (cls.folder / "freertos/FreeRTOS.h").write_text("#pragma once\n", encoding="utf-8")
        (cls.folder / "freertos/portmacro.h").write_text("""#pragma once
#include <mutex>
#include <cstdlib>
struct portMUX_TYPE { std::mutex mutex; };
#define portMUX_INITIALIZER_UNLOCKED {}
inline thread_local bool critical_entered = false;
inline void portENTER_CRITICAL(portMUX_TYPE* mux) {
    if (critical_entered) std::abort();
    mux->mutex.lock(); critical_entered = true;
}
inline void portEXIT_CRITICAL(portMUX_TYPE* mux) {
    if (!critical_entered) std::abort();
    critical_entered = false; mux->mutex.unlock();
}
""", encoding="utf-8")
        cls.exe = cls.folder / "display.exe"
        result = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + [
            "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti", "-pthread",
            "-include", str(cls.folder / "sdkconfig.h"), "-I", str(cls.folder), "-I", str(MAIN), "-I", str(BOARD),
            str(BOARD / "studio_runtime_state.cc"), str(ROOT / "tests/cpp/studio_display_query_test.cc"),
            "-o", str(cls.exe)], cwd=ROOT, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        schema = json.loads((ROOT / "protocol/deskbot-studio-display-v1.schema.json").read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        cls.validator = jsonschema.Draft202012Validator(schema)

    def wire(self, operation="display", **changes):
        return session.encode_request(**(dict(operation=operation, request_id="r1", target_id=TARGET,
            boot_id=BOOT, client_nonce="client-a", session_id=FIRST) | changes))

    def open(self, **changes):
        return self.wire("open", **(dict(session_id=None) | changes))

    def trace(self, *operations):
        raw = bytearray()
        for operation in operations:
            if operation is None: raw.extend(b"X")
            elif isinstance(operation, int): raw.extend(b"T" + struct.pack("<Q", operation))
            elif operation[0] == "snapshot": raw.extend(b"S" + struct.pack("<IBBIBB", *operation[1:]))
            elif operation[0] == "player": raw.extend(b"P" + struct.pack("<IB", *operation[1:]))
            elif operation[0] == "provider": raw.extend(b"Q" + bytes([operation[1]]))
            elif operation[0] == "capacity": raw.extend(b"C" + struct.pack("<I", operation[1]))
            else:
                now, data = operation
                raw.extend(b"D" + struct.pack("<QI", now, len(data)) + data)
        path = self.folder / "trace.bin"
        path.write_bytes(raw)
        result = subprocess.run([str(self.exe), str(path)], capture_output=True, text=True, check=True, timeout=15)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        for row in rows:
            if row["schema"] == display.SCHEMA:
                display.validate_reply(row)
                self.validator.validate(row)
            else:
                session.validate_reply(row)
        return rows, int(result.stderr)

    def sample(self, *state):
        rows, _ = self.trace((0, self.open()), IDLE, PLAYER, *state, (500, self.wire()))
        return rows[-1]

    def test_initial_state(self):
        rows, reads = self.trace((0, self.open()), (500, self.wire()))
        self.assertEqual(reads, 1)
        self.assertEqual(rows[-1]["preview"], dict(available=False, reason="player_unknown"))

    def test_idle_home_initialized_still_has_unknown_sleep(self):
        row = self.sample()
        self.assertEqual(row["player"], dict(revision=1, state="initialized"))
        self.assertEqual(row["preview"], dict(available=False, reason="sleep_unknown"))
        self.assertEqual(row["sleep"], "unknown")
        self.assertFalse(row["controls_available"])

    def test_unavailable_player(self): self.assertEqual(self.sample(("player", 2, 1))["preview"]["reason"], "player_unavailable")
    def test_stopping_player(self): self.assertEqual(self.sample(("player", 2, 3))["preview"]["reason"], "player_stopping")

    def test_invalid_player_is_unknown(self):
        for revision, value in ((0, 2), (1, 0), (1, 255)):
            self.assertEqual(self.sample(("player", revision, value))["preview"]["reason"], "player_unknown")

    def test_all_application_states(self):
        for value in range(1, 11):
            self.assertEqual(self.sample(("snapshot", 3, 2, value, 1, 2, 1))["preview"]["reason"],
                "sleep_unknown" if value == 3 else "application_busy")

    def test_application_transition(self):
        self.assertEqual(self.sample(("snapshot", 2, 1, 3, 1, 2, 1))["preview"]["reason"], "application_transitioning")

    def test_application_unknown(self):
        for revision, phase, value in ((0, 2, 3), (1, 0, 3), (1, 2, 255), (1, 255, 3)):
            self.assertEqual(self.sample(("snapshot", revision, phase, value, 1, 2, 1))["preview"]["reason"], "application_unknown")

    def test_other_page(self): self.assertEqual(self.sample(("snapshot", 1, 2, 3, 2, 2, 2))["preview"]["reason"], "not_home")
    def test_page_transition(self): self.assertEqual(self.sample(("snapshot", 1, 2, 3, 2, 1, 1))["preview"]["reason"], "page_transitioning")

    def test_unknown_page(self):
        for revision, phase, value in ((0, 2, 1), (1, 0, 1), (1, 2, 255), (1, 255, 1)):
            self.assertEqual(self.sample(("snapshot", 1, 2, 3, revision, phase, value))["preview"]["reason"], "page_unknown")

    def test_provider_missing(self):
        rows, reads = self.trace((0, self.open()), ("provider", False), (500, self.wire()))
        self.assertEqual((rows[-1]["reason"], reads), ("display_unavailable", 0))

    def test_identity_session_checks_precede_read(self):
        for change in (dict(target_id="device-wrong"), dict(boot_id="boot-wrong"),
                       dict(client_nonce="client-wrong"), dict(session_id=None), dict(session_id="session-wrong")):
            rows, reads = self.trace((0, self.open()), (500, self.wire(**change)))
            self.assertEqual((rows[-1]["state"], reads), ("rejected", 0))

    def test_expired_session(self):
        rows, reads = self.trace((0, self.open()), (30000, self.wire()))
        self.assertEqual((rows[-1]["reason"], reads), ("lease_expired", 0))

    def test_closed_session(self):
        rows, reads = self.trace((0, self.open()), (500, self.wire("close")), (1000, self.wire()))
        self.assertEqual((rows[-1]["reason"], reads), ("session_closed", 0))

    def test_valid_query_renews_lease(self):
        rows, reads = self.trace((0, self.open()), (29999, self.wire()), (59998, self.wire()))
        self.assertEqual(reads, 2)
        self.assertEqual(rows[-1]["schema"], display.SCHEMA)

    def test_reply_capacity_before_snapshot(self):
        rows, reads = self.trace((0, self.open()), ("capacity", 1023), (500, self.wire()))
        self.assertEqual((len(rows), reads), (1, 0))

    def test_rate_limit(self):
        rows, reads = self.trace((0, self.open()), (249, self.wire()), (250, self.wire()))
        self.assertEqual((len(rows), reads), (2, 1))

    def test_fragmented_request(self):
        wire = self.wire()
        rows, reads = self.trace((0, self.open()), *((500+i, bytes([byte])) for i, byte in enumerate(wire)))
        self.assertEqual((len(rows), reads), (2, 1))

    def test_concatenation(self):
        rows, reads = self.trace((0, self.open()), (500, self.wire()+self.wire()), (1000, self.wire()))
        self.assertEqual((len(rows), reads), (3, 2))

    def test_partial_timeout(self):
        wire = self.wire()
        rows, reads = self.trace((0, self.open()), (500, wire[:15]), 2500, (2600, wire[15:]), (3000, wire))
        self.assertEqual((len(rows), reads), (2, 1))

    def test_disconnect_clears_session(self):
        rows, reads = self.trace((0, self.open()), None, (500, self.wire()))
        self.assertEqual((rows[-1]["reason"], reads), ("session_closed", 0))

    def test_largest_request_id_fits_reply(self):
        row = self.trace((0, self.open()), (500, self.wire(request_id="r"*96)))[0][-1]
        self.assertLess(len(json.dumps(row, separators=(",", ":")).encode()), 1024)

    def test_existing_state_operation_is_separate(self):
        rows, reads = self.trace((0, self.open()), (500, self.wire("state")))
        self.assertEqual((rows[-1]["reason"], reads), ("state_unavailable", 0))

    def connected_client(self):
        client = display.DisplayQueryClient(TARGET, BOOT, "client-a")
        wire = client.request("open", 0)
        opening = self.trace((0, wire))[0][-1]
        client.accept(opening, 20)
        query = client.request("display", 500)
        row = self.trace((0, wire), IDLE, PLAYER, (500, query))[0][-1]
        return client, row

    def test_pc_client_native_round_trip(self):
        client, row = self.connected_client()
        client.accept(row, 550)
        self.assertEqual(client.snapshot(600)["state"], row)
        self.assertEqual(client.snapshot(600)["age_ms"], 100)

    def test_pc_client_expiry(self):
        client, row = self.connected_client()
        client.accept(row, 550)
        self.assertIsNone(client.snapshot(2500)["state"])

    def test_pc_client_reconnect(self):
        client, row = self.connected_client()
        client.accept(row, 550)
        client.reconnect("client-b")
        self.assertIsNone(client.snapshot(600)["state"])
        with self.assertRaises(ValueError): client.accept(row, 650)

    def test_pc_client_timeout(self):
        client, row = self.connected_client()
        with self.assertRaises(ValueError): client.accept(row, 2500)
        self.assertIsNone(client.snapshot(2501)["state"])

    def test_pc_client_malformed_reason_rejected(self):
        client, row = self.connected_client()
        for changes in (dict(preview=dict(available=True, reason="sleep_unknown")),
                        dict(preview=dict(available=False, reason="not_home")), dict(sleep="awake")):
            with self.assertRaises(ValueError): client.accept(row | changes, 550)

    def test_pc_wrong_identity_boot_request_session_and_replay(self):
        for field, value in (("target_id", "device-"+"c"*32), ("boot_id", "boot-"+"c"*32),
                             ("request_id", "different"), ("session_id", "session-"+"b"*32+"-2"), ("sequence", 1)):
            client, row = self.connected_client()
            with self.assertRaises(ValueError): client.accept(row | {field: value}, 550)

    def test_pc_client_clock_reversal(self):
        client, row = self.connected_client()
        client.accept(row, 550)
        with self.assertRaises(ValueError): client.snapshot(549)
        self.assertIsNone(client.snapshot(600)["state"])

    def test_production_adapter_and_concurrent_copies(self):
        subprocess.run([str(self.exe), "adapter"], check=True, timeout=20)

    def test_owner_logic_preserved(self):
        old = json.loads((ROOT / "artifacts/deskbot-studio-e3c3-20261105/firmware-build-report.json").read_bytes())["source_sha256"]
        import hashlib
        source = (MAIN / "display/emote_display.cc").read_text(encoding="utf-8")
        stripped = re.sub(r"#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE\n.*?#endif\n", "", source, flags=re.S)
        self.assertIn(old["main/display/emote_display.cc"],
            {hashlib.sha256(v.encode()).hexdigest() for v in (stripped, stripped.replace("\n", "\r\n"))})

    def test_default_off_and_no_control_calls(self):
        text = (MAIN / "Kconfig.projbuild").read_text(encoding="utf-8").split("config DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE", 1)[1].split("\nconfig ", 1)[0]
        self.assertIn("default n", text)
        self.assertIn("depends on DESKBOT_STUDIO_STATE_QUERY_ENABLE", text)
        core = (BOARD / "studio_display_readiness.h").read_text(encoding="utf-8")
        for token in ("SetEmotion(", "Schedule(", "lv_obj", "new ", "malloc(", "xTaskCreate("):
            self.assertNotIn(token, core)


if __name__ == "__main__":
    unittest.main()
