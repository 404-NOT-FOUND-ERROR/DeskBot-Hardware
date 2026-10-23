from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from protocol.magnetic_telemetry import (
    FrameStreamParser,
    TelemetryError,
    build_frame,
    callback_payload_from_line,
    decode_frame,
    decode_payload,
    iter_samples_from_callback_lines,
)


class MagneticTelemetryTests(unittest.TestCase):
    def test_frame_checksum_and_int16_boundaries(self) -> None:
        frame = build_frame(1, -32768, 32767, -1)
        sample = decode_frame(frame)
        assert sample is not None
        self.assertEqual((sample.x, sample.y, sample.z), (-32768, 32767, -1))
        self.assertEqual(sample.source, "checksum_verified")
        self.assertTrue(sample.checksum_valid)

    def test_bad_checksum_and_lengths_rejected(self) -> None:
        frame = bytearray(build_frame(1, 1, 2, 3))
        frame[-1] ^= 0xFF
        with self.assertRaises(TelemetryError):
            decode_frame(frame)
        with self.assertRaises(TelemetryError):
            decode_frame(bytes.fromhex("AA550000"))
        with self.assertRaises(TelemetryError):
            decode_frame(bytes.fromhex("AA5500080601000100020003"))

    def test_split_and_concatenated_frames(self) -> None:
        first = build_frame(1, 1, 2, 3)
        second = build_frame(99, -4, -5, -6)
        parser = FrameStreamParser()
        found = []
        stream = first + second
        for index in range(0, len(stream), 3):
            found.extend(parser.feed(stream[index : index + 3]))
        parser.finish()
        self.assertEqual([(item.kind, item.x) for item in found], [(1, 1), (99, -4)])
        self.assertEqual(parser.stats.frames_accepted, 2)
        self.assertEqual(parser.stats.incomplete_tail_bytes, 0)

    def test_unknown_kind_and_non_magnetic_frame(self) -> None:
        sample = decode_frame(build_frame(77, 1, 2, 3))
        assert sample is not None
        self.assertEqual(sample.kind_name, "unknown")
        self.assertIsNone(decode_frame(b"\xAA\x55\x00\x03\x01\x00\x01\x02"))

    def test_callback_reconstruction_is_distinct_from_wire_validation(self) -> None:
        line = "2026-10-16T21:17:32.304+08:00 Handle: cmd=06, 01 FF 49 FE 72 FB BC"
        self.assertEqual(callback_payload_from_line(line), bytes.fromhex("0601FF49FE72FBBC"))
        sample = next(iter_samples_from_callback_lines([line]))
        self.assertEqual(sample.source, "callback_reconstructed")
        self.assertFalse(sample.checksum_valid)
        self.assertEqual((sample.x, sample.y, sample.z), (-183, -398, -1092))
        self.assertEqual(sample.host_time_source, "log_prefix")

    def test_interleaved_callback_line_is_rejected(self) -> None:
        with self.assertRaises(TelemetryError):
            callback_payload_from_line(
                "2026-10-16T21:17:32.304+08:00 Handle: cmd=06, 01 FE F1 FE I (1) wifi FB 86"
            )

    def test_freshness_uses_host_time_only(self) -> None:
        sample = decode_payload(
            bytes.fromhex("0601000100020003"),
            host_received_at="2026-10-16T12:00:00Z",
            as_of="2026-10-16T12:00:02Z",
            freshness_threshold_s=1.0,
        )
        self.assertEqual(sample.freshness, "stale")
        self.assertEqual(sample.age_seconds, 2.0)
        unknown = decode_payload(bytes.fromhex("0601000100020003"))
        self.assertEqual(unknown.freshness, "unknown")

    def test_invalid_time_and_non_finite_thresholds_are_rejected(self) -> None:
        with self.assertRaises(TelemetryError):
            decode_payload(
                bytes.fromhex("0601000100020003"),
                host_received_at="2026-10-16T12:00:00",
            )
        with self.assertRaises(TelemetryError):
            decode_payload(
                bytes.fromhex("0601000100020003"),
                freshness_threshold_s=float("nan"),
            )
        with self.assertRaises(TelemetryError):
            decode_payload(
                bytes.fromhex("0601000100020003"),
                freshness_threshold_s=float("inf"),
            )

    def test_callback_requires_exactly_seven_data_bytes(self) -> None:
        with self.assertRaises(TelemetryError):
            callback_payload_from_line("Handle: cmd=06, 01 02 03 04 05 06")
        with self.assertRaises(TelemetryError):
            callback_payload_from_line("Handle: cmd=06, 01 02 03 04 05 06 07 08")

    def test_cli_refuses_external_output_and_writes_artifacts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts" / "m1-magnetic-replay.py"
        with tempfile.TemporaryDirectory(dir=root / "artifacts") as temporary:
            source = Path(temporary) / "input.log"
            source.write_text(
                "2026-10-16T12:00:00Z " + build_frame(1, 1, 2, 3).hex(" ") + "\n",
                encoding="utf-8",
            )
            output = root / "artifacts" / "m1-magnetic-readonly-20261016" / "test-cli"
            result = subprocess.run(
                [sys.executable, str(script), "--input", str(source), "--output", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue((output / "samples.jsonl").exists())
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["samples_written"], 1)
            self.assertIn("checksum_verified", result.stdout)
            outside = subprocess.run(
                [sys.executable, str(script), "--input", str(source), "--output", str(root / "tmp" / "bad")],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(outside.returncode, 0)


if __name__ == "__main__":
    unittest.main()
