from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from split_magnetic_state_capture import split_capture  # noqa: E402


class SplitStateCaptureTests(unittest.TestCase):
    def make_capture(self, root: Path, markers: list[tuple[str, str, str]]) -> Path:
        capture = root / "capture"
        capture.mkdir()
        (capture / "summary.json").write_text(json.dumps({
            "schema": "deskbot.magnetic.state_capture.v1",
            "read_only": True,
            "capture_status": "complete",
            "minimum_state_seconds": 10,
        }), encoding="utf-8")
        for name, lines in (
            ("body.log", [
                *[f"2026-11-07T00:00:{second:02d}.000Z\tHandle: cmd=06, 01 00" for second in range(1, 9)],
                "2026-11-07T00:00:11.000Z\tHandle: cmd=06, 01 00",
                *[f"2026-11-07T00:00:{second:02d}.000Z\tHandle: cmd=06, 01 01" for second in range(13, 21)],
                "2026-11-07T00:00:23.000Z\tHandle: cmd=06, 01 01",
            ]),
            ("base.log", [
                "2026-11-07T00:00:03.000Z\tbase before",
                "2026-11-07T00:00:16.000Z\tbase after",
            ]),
        ):
            (capture / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        with (capture / "markers.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp_utc", "state", "boundary"])
            writer.writeheader()
            writer.writerows({"timestamp_utc": t, "state": state, "boundary": boundary}
                             for t, state, boundary in markers)
        return capture

    def test_splits_body_and_base_lines_to_manual_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            markers = [
                ("2026-11-07T00:00:00Z", "baseline", "start"),
                ("2026-11-07T00:00:10Z", "baseline", "end"),
                ("2026-11-07T00:00:12Z", "slider_up", "start"),
                ("2026-11-07T00:00:22Z", "slider_up", "end"),
            ]
            capture = self.make_capture(Path(temporary), markers)
            manifest = split_capture(capture)

            self.assertTrue(manifest["all_windows_have_enough_samples"])
            self.assertEqual([row["magnetic_sample_lines"] for row in manifest["windows"]], [8, 8])
            self.assertEqual(manifest["windows"][0]["body_log"].replace("\\", "/"), "01-baseline-01/body.log")
            self.assertEqual(manifest["windows"][0]["base_log"].replace("\\", "/"), "01-baseline-01/base.log")
            baseline = capture / "labeled-states" / "01-baseline-01"
            slider_up = capture / "labeled-states" / "02-slider_up-01"
            self.assertEqual(len((baseline / "body.log").read_text().splitlines()), 8)
            self.assertEqual(len((baseline / "base.log").read_text().splitlines()), 1)
            self.assertEqual(len((slider_up / "body.log").read_text().splitlines()), 8)
            self.assertEqual(len((slider_up / "base.log").read_text().splitlines()), 1)

    def test_rejects_unfinished_or_overlapping_markers(self):
        with tempfile.TemporaryDirectory() as temporary:
            markers = [
                ("2026-11-07T00:00:00Z", "baseline", "start"),
                ("2026-11-07T00:00:01Z", "slider_up", "start"),
            ]
            capture = self.make_capture(Path(temporary), markers)
            with self.assertRaisesRegex(ValueError, "end baseline"):
                split_capture(capture)

    def test_rejects_non_read_only_capture_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            markers = [("2026-11-07T00:00:00Z", "baseline", "start")]
            capture = self.make_capture(Path(temporary), markers)
            (capture / "summary.json").write_text(json.dumps({
                "schema": "deskbot.magnetic.state_capture.v1", "read_only": False,
                "minimum_state_seconds": 10,
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not a read-only"):
                split_capture(capture)

    def test_rejects_non_object_summary_and_nonpositive_sample_threshold(self):
        with tempfile.TemporaryDirectory() as temporary:
            capture = self.make_capture(Path(temporary), [
                ("2026-11-07T00:00:00Z", "baseline", "start"),
                ("2026-11-07T00:00:10Z", "baseline", "end"),
            ])
            (capture / "summary.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                split_capture(capture)

            (capture / "summary.json").write_text(json.dumps({
                "schema": "deskbot.magnetic.state_capture.v1", "read_only": True,
                "capture_status": "complete",
                "minimum_state_seconds": 10,
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "minimum_samples must be positive"):
                split_capture(capture, minimum_samples=0)

    def test_incomplete_failed_and_missing_status_do_not_create_outputs(self):
        for changes in ({}, {"capture_status": "incomplete"}, {"capture_status": "failed"},
                        {"capture_status": "complete", "read_errors": ["COM4 disconnected"]},
                        {"capture_status": "complete", "unfinished_state": "removed"}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
                capture = self.make_capture(Path(temporary), [
                    ("2026-11-07T00:00:00Z", "baseline", "start"),
                    ("2026-11-07T00:00:10Z", "baseline", "end"),
                ])
                summary = json.loads((capture / "summary.json").read_bytes())
                summary.pop("capture_status")
                summary.update(changes)
                (capture / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "capture is not complete"):
                    split_capture(capture)
                self.assertFalse((capture / "labeled-states").exists())

    def test_marks_windows_with_too_few_samples_not_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            capture = self.make_capture(Path(temporary), [
                ("2026-11-07T00:00:00Z", "baseline", "start"),
                ("2026-11-07T00:00:10Z", "baseline", "end"),
            ])
            (capture / "body.log").write_text(
                "2026-11-07T00:00:01Z\tHandle: cmd=06, 01 00\n"
                "2026-11-07T00:00:02Z\tHandle: cmd=06, 01 00\n",
                encoding="utf-8",
            )
            manifest = split_capture(capture)

            self.assertFalse(manifest["all_windows_have_enough_samples"])
            self.assertFalse(manifest["windows"][0]["m2_window_ready"])
            self.assertEqual(manifest["windows"][0]["magnetic_sample_lines"], 2)


if __name__ == "__main__":
    unittest.main()
