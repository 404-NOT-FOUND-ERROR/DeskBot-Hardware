"""Compile the portable framing core and compare it with the Python oracle."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from protocol import studio_expression_receiver as receiver

ROOT = Path(__file__).resolve().parents[1]


class CppFramerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("studio_cpp_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        found = helper.find_compiler()
        if found is None:
            raise RuntimeError("C++17 compiler required; run the compiler preflight")
        command, kind = found
        (ROOT / "tmp").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="e3c-framer-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.exe = cls.folder / "framer.exe"
        result = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + [
            "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti",
            "-I", str(ROOT / "protocol/cpp"), str(ROOT / "tests/cpp/studio_request_framer_test.cc"),
            "-o", str(cls.exe)], cwd=ROOT, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def trace(self, *operations):
        data = bytearray()
        oracle = receiver.RequestFrames()
        expected = []
        for operation in operations:
            if operation is None:
                data.extend(b"R")
                oracle.reset()
            else:
                data.extend(b"D" + struct.pack("<I", len(operation)) + operation)
                expected.extend(oracle.feed(operation))
        path = self.folder / "trace.bin"
        path.write_bytes(data)
        result = subprocess.run([str(self.exe), str(path)], cwd=ROOT, capture_output=True,
                                text=True, timeout=15, check=True)
        actual = []
        rows = result.stdout.splitlines()
        for row in rows[:-1]:
            if row == "overflow":
                actual.append(receiver.Frame(error="frame_too_large"))
            else:
                self.assertTrue(row.startswith("line "), row)
                raw = bytes.fromhex(row[5:])
                try:
                    actual.append(receiver.Frame(request=receiver.decode_request(raw)))
                except (ValueError, TypeError):
                    actual.append(receiver.Frame(error="invalid_request"))
        self.assertEqual(actual, expected)
        self.assertEqual(rows[-1], f"buffered {oracle.buffered_bytes}")
        return actual

    @staticmethod
    def wire():
        from protocol import expression_control as contract
        catalog = json.loads((ROOT / "artifacts/deskbot-studio-e2c-20261104/expression-catalog.json").read_bytes())
        return contract.canonical({
            "schema": contract.SCHEMA, "message_type": "request", "request_id": "cpp-r1",
            "target_id": "offline-device", "boot_id": "offline-boot", "session_id": "offline-session",
            "sequence": 1, "catalog_id": catalog["catalog_id"], "role_id": catalog["role_id"],
            "issued_at": "2026-11-04T00:00:00.000Z", "expires_after_ms": 3000,
            "command": dict(receiver.COMMAND),
        })

    def test_valid_line_and_crlf(self):
        self.assertEqual(len(self.trace(self.wire() + b"\n", self.wire() + b"\r\n")), 2)

    def test_each_byte_is_a_separate_chunk(self):
        self.trace(*(bytes([byte]) for byte in self.wire() + b"\n"))

    def test_many_concatenated_frames(self):
        self.assertEqual(len(self.trace((self.wire() + b"\n") * 100)), 100)

    def test_fragmented_json_and_carriage_return(self):
        raw = self.wire()
        self.trace(raw[:31], raw[31:79], raw[79:], b"\r", b"\n")

    def test_blank_ascii_lines(self):
        self.assertEqual(self.trace(b"\n \r\n\t\v\f\n"), [])

    def test_exact_limit_including_cr(self):
        raw = self.wire()
        self.trace(raw + b" " * (2047 - len(raw)) + b"\r\n")

    def test_one_byte_over_limit(self):
        self.trace(b"x" * 2049 + b"\n" + self.wire() + b"\n")

    def test_large_overflow_stays_discarded_to_lf(self):
        self.trace(b"x" * 100000, self.wire(), b"\n", self.wire() + b"\n")

    def test_disconnect_discards_partial(self):
        self.trace(self.wire()[:42], None, self.wire() + b"\n")

    def test_disconnect_discards_overflow(self):
        self.trace(b"x" * 3000, None, self.wire() + b"\n")

    def test_invalid_utf8_nul_and_json_are_delivered_for_validation(self):
        self.trace(b"\xff\n\0\n{bad}\n", self.wire() + b"\n")

    def test_unterminated_line_is_retained(self):
        self.assertEqual(self.trace(self.wire()), [])
