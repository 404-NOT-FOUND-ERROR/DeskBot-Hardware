"""Compile/run the exact firmware core; all I/O uses local trace files."""
import importlib.util
import json
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest

import jsonschema

from protocol import studio_usb_session as contract

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "firmware/body/main/boards/esp_vocat"
TARGET, BOOT = "device-" + "a" * 32, "boot-" + "b" * 32
FIRST = "session-" + "b" * 32 + "-1"


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("usb_session_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        found = helper.find_compiler()
        if found is None:
            raise RuntimeError("C++17 compiler required")
        command, kind = found
        (ROOT / "tmp").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="e3c-usb-test-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.exe = cls.folder / "session.exe"
        run = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + [
            "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti",
            "-I", str(BOARD), str(ROOT / "tests/cpp/studio_usb_session_test.cc"), "-o", str(cls.exe)],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        if run.returncode:
            raise RuntimeError(run.stdout + run.stderr)

    def wire(self, operation="open", **changes):
        return contract.encode_request(**dict(operation=operation, request_id="r1", target_id=TARGET,
                                             boot_id=BOOT, client_nonce="client-a", session_id=None) | changes)

    def run_trace(self, *operations):
        raw = bytearray()
        for operation in operations:
            if operation is None:
                raw.extend(b"X")
            elif isinstance(operation, int):
                raw.extend(b"T" + struct.pack("<Q", operation))
            else:
                now, data = operation
                raw.extend(b"D" + struct.pack("<QI", now, len(data)) + data)
        path = self.folder / "trace.bin"
        path.write_bytes(raw)
        result = subprocess.run([str(self.exe), str(path)], capture_output=True, text=True, timeout=10, check=True)
        return [contract.validate_reply(json.loads(line)) for line in result.stdout.splitlines()]

    def test_open_ping_close(self):
        rows = self.run_trace((0, self.wire()), (500, self.wire("ping", session_id=FIRST)),
                              (1000, self.wire("close", session_id=FIRST)))
        self.assertEqual([r["state"] for r in rows], ["ready", "ready", "closed"])
        self.assertEqual([r["sequence"] for r in rows], [1, 2, 3])

    def test_repeated_open_returns_same_session(self):
        rows = self.run_trace((0, self.wire()), (500, self.wire(request_id="r2")))
        self.assertEqual(rows[0]["session_id"], rows[1]["session_id"])
        self.assertEqual(rows[1]["reason"], "session_resumed")

    def test_new_connection_nonce_rotates_and_rejects_old(self):
        rows = self.run_trace((0, self.wire()), (500, self.wire(client_nonce="client-b")),
                              (1000, self.wire("ping", session_id=FIRST)))
        self.assertTrue(rows[1]["session_id"].endswith("-2"))
        self.assertEqual(rows[2]["reason"], "session_changed")
        self.assertIsNone(rows[2]["session_id"])

    def test_lease_expires_exactly_at_boundary(self):
        rows = self.run_trace((0, self.wire()), (30000, self.wire("ping", session_id=FIRST)),
                              (30500, self.wire()))
        self.assertEqual(rows[1]["reason"], "lease_expired")
        self.assertTrue(rows[2]["session_id"].endswith("-2"))

    def test_ping_refreshes_lease(self):
        rows = self.run_trace((0, self.wire()), (29999, self.wire("ping", session_id=FIRST)),
                              (30001, self.wire("ping", session_id=FIRST)),
                              (30500, self.wire("ping", session_id=FIRST)))
        self.assertEqual(rows[-1]["reason"], "session_alive")

    def test_disconnect_invalidates_session_and_partial_frame(self):
        rows = self.run_trace((0, self.wire()), (500, b'{"schema":'), None,
                              (1000, self.wire("ping", session_id=FIRST)), (1500, self.wire()))
        self.assertEqual(rows[1]["reason"], "session_closed")
        self.assertTrue(rows[2]["session_id"].endswith("-2"))

    def test_disconnect_allows_immediate_reconnect_reply(self):
        rows = self.run_trace((500, self.wire()), None,
                              (501, self.wire(client_nonce="client-b")))
        self.assertEqual([r["at_ms"] for r in rows], [500, 501])
        self.assertTrue(rows[1]["session_id"].endswith("-2"))

    def test_wrong_target_boot_and_session_never_rotate(self):
        changes = [dict(target_id="device-other"), dict(boot_id="boot-other")]
        for change, reason in zip(changes, ["target_mismatch", "boot_changed"]):
            with self.subTest(change=change):
                rows = self.run_trace((0, self.wire(**change)), (500, self.wire()))
                self.assertEqual(rows[0]["reason"], reason)
                self.assertEqual(rows[1]["session_id"], FIRST)

    def test_unknown_operation_never_executes_expression(self):
        rows = self.run_trace((0, self.wire("play_expression")), (500, self.wire()))
        self.assertEqual(rows[0]["reason"], "unsupported_operation")
        self.assertEqual(rows[1]["session_id"], FIRST)

    def test_no_session_disclosed_for_rejected_nonce(self):
        rows = self.run_trace((0, self.wire()), (500, self.wire("ping", client_nonce="client-b", session_id=FIRST)))
        self.assertIsNone(rows[1]["session_id"])
        self.assertEqual(rows[1]["lease_ms"], 0)

    def test_response_throttle_and_clock_backwards(self):
        rows = self.run_trace((500, self.wire()), (600, self.wire()), (400, self.wire()), (750, self.wire()))
        self.assertEqual([r["at_ms"] for r in rows], [500, 750])

    def test_reply_time_above_uint32(self):
        rows = self.run_trace((2**32 + 1234, self.wire()))
        self.assertEqual(rows[0]["at_ms"], 2**32 + 1234)

    def test_clock_above_json_safe_maximum_is_ignored(self):
        self.assertEqual(self.run_trace((9007199254740992, self.wire())), [])

    def test_fragmented_and_concatenated_frames(self):
        raw = self.wire()
        rows = self.run_trace((0, raw[:40]), (10, raw[40:]), (500, raw + raw))
        self.assertEqual(len(rows), 2)

    def test_crlf_conversion_and_empty_lines(self):
        rows = self.run_trace((0, b"\n\r\n" + self.wire().replace(b"\n", b"\r\n") + b"\n\n"))
        self.assertEqual(len(rows), 1)

    def test_partial_timeout_recovers(self):
        rows = self.run_trace((0, b'{"schema":'), 2000, (2500, self.wire()))
        self.assertEqual(len(rows), 1)

    def test_overflow_discards_until_lf(self):
        rows = self.run_trace((0, b"x" * 6000 + self.wire()), (500, self.wire()))
        self.assertEqual(len(rows), 1)

    def test_exact_frame_boundary(self):
        raw = self.wire()[:-1]
        self.assertEqual(len(self.run_trace((0, raw + b" " * (2048 - len(raw)) + b"\n"))), 1)
        self.assertEqual(self.run_trace((0, raw + b" " * (2049 - len(raw)) + b"\n")), [])

    def test_json_fields_order_and_escaped_ascii(self):
        value = json.loads(self.wire())
        raw = json.dumps(dict(reversed(list(value.items())))).encode()
        raw = raw.replace(b'"schema"', b'"\\u0073chema"').replace(b'client-a', b'client-\\u0061')
        self.assertEqual(len(self.run_trace((0, raw + b"\n"))), 1)

    def test_duplicate_decoded_keys_and_trailing_bytes_rejected(self):
        raw = self.wire()[:-2]
        for suffix in [b',"schema":"x"}\n', b',"\\u0073chema":"x"}\n', b'}{}\n', b'}garbage\n']:
            with self.subTest(suffix=suffix):
                self.assertEqual(self.run_trace((0, raw + suffix)), [])

    def test_non_ascii_nul_nested_scalars_and_large_strings(self):
        raw = self.wire()
        variants = [raw.replace(b'"client-a"', x) for x in [
            b'"\xff"', b'"a\x00b"', b'"a\\u0000b"', b'"a\\ud800b"', b'null', b'true',
            b'1', b'1e1', b'{}', b'[]', b'"' + b'a'*97 + b'"']]
        for value in variants:
            with self.subTest(value=value):
                self.assertEqual(self.run_trace((0, value)), [])

    def test_bad_input_does_not_refresh_active_lease(self):
        rows = self.run_trace((0, self.wire()), (29999, b"invalid\n"),
                              (30000, self.wire("ping", session_id=FIRST)))
        self.assertEqual(rows[-1]["reason"], "lease_expired")

    def test_rejected_requests_do_not_refresh_lease(self):
        rows = self.run_trace((0, self.wire()), (29999, self.wire("ping", session_id="session-wrong")),
                              (30250, self.wire("ping", session_id=FIRST)))
        self.assertEqual(rows[-1]["reason"], "lease_expired")

    def test_random_bytes_recover_to_valid_request(self):
        rng = random.Random(43)
        for _ in range(25):
            noise = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 4096)))
            rows = self.run_trace((0, noise + b"\n"), (1000, self.wire()))
            self.assertEqual(rows[-1]["session_id"], FIRST)

    def test_request_codec_rejects_duplicate_key(self):
        with self.assertRaises(ValueError):
            contract.decode_request(self.wire()[:-2] + b',"operation":"open"}')

    def test_reply_validator_rejects_control_and_stale_identity(self):
        row = self.run_trace((0, self.wire()))[0]
        for changes in [dict(controls_available=True), dict(session_id="session-" + "c"*32 + "-1"),
                        dict(sequence=True), dict(lease_ms=False)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                contract.validate_reply(row | changes)

    def test_request_schema_and_codec_accept_diagnostic_mismatches(self):
        schema = json.loads((ROOT / "protocol/deskbot-studio-session-v1.schema.json").read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
        for changes in [dict(target_id="device-other"), dict(boot_id="boot-other"),
                        dict(operation="ping", session_id="session-wrong"),
                        dict(operation="play_expression")]:
            with self.subTest(changes=changes):
                value = contract.decode_request(self.wire(**changes))
                validator.validate(value)
        for field in ("request_id", "target_id", "boot_id", "client_nonce", "session_id"):
            value = json.loads(self.wire()) | {field: "valid\n"}
            with self.subTest(field=field):
                self.assertFalse(validator.is_valid(value))
                with self.assertRaises(ValueError):
                    contract.validate_request(value)

    def test_actual_replies_match_schema(self):
        schema = json.loads((ROOT / "protocol/deskbot-studio-session-v1.schema.json").read_bytes())
        validator = jsonschema.Draft202012Validator(schema)
        rows = self.run_trace((0, self.wire()), (500, self.wire("ping", session_id="session-wrong")),
                              (1000, self.wire("close", session_id=FIRST)))
        self.assertEqual([r["state"] for r in rows], ["ready", "rejected", "closed"])
        for row in rows:
            validator.validate(row)


if __name__ == "__main__":
    unittest.main()
