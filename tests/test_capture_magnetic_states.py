from __future__ import annotations

import csv
import io
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from capture_magnetic_states import StateMarkerTracker, capture_status  # noqa: E402


class StateMarkerTests(unittest.TestCase):
    def make_tracker(self, minimum_seconds: float = 10.0):
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=["timestamp_utc", "state", "boundary"])
        writer.writeheader()
        return StateMarkerTracker(writer, minimum_seconds=minimum_seconds), output

    def test_records_non_overlapping_state_boundaries_in_utc(self):
        tracker, output = self.make_tracker()
        start = datetime(2026, 11, 7, 1, 0, tzinfo=timezone(timedelta(hours=8)))
        tracker.start("baseline", start)
        duration = tracker.end(start + timedelta(seconds=10))

        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(duration, 10)
        self.assertEqual(rows[0], {"timestamp_utc": "2026-11-06T17:00:00.000Z", "state": "baseline", "boundary": "start"})
        self.assertEqual(rows[1]["boundary"], "end")
        self.assertEqual(tracker.completed, ["baseline"])

    def test_rejects_unknown_state_overlapping_start_and_early_end(self):
        tracker, _ = self.make_tracker()
        now = datetime(2026, 11, 7, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "state must be"):
            tracker.start("shell_guess", now)
        tracker.start("removed", now)
        with self.assertRaisesRegex(ValueError, "end removed"):
            tracker.start("reinstalled", now + timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "at least 10"):
            tracker.end(now + timedelta(seconds=9.9))
        self.assertEqual(tracker.active_state, "removed")

    def test_requires_start_before_end(self):
        tracker, _ = self.make_tracker()
        now = datetime(2026, 11, 7, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "start a state first"):
            tracker.end(now)


class CaptureStatusTests(unittest.TestCase):
    def test_unmarked_or_unfinished_capture_is_incomplete(self):
        self.assertEqual(capture_status([], None, 100, 80, []),
                         ("incomplete", "no complete state window was marked"))
        self.assertEqual(capture_status(["baseline"], "slider_up", 100, 80, []),
                         ("incomplete", "end the unfinished slider_up state before stopping"))

    def test_requires_body_magnetic_samples(self):
        self.assertEqual(capture_status(["baseline"], None, 100, 0, []),
                         ("incomplete", "no body magnetic samples (cmd=06) were captured"))

    def test_complete_capture_and_serial_error_status(self):
        self.assertEqual(capture_status(["baseline"], None, 100, 80, []), ("complete", None))
        self.assertEqual(capture_status(["baseline"], None, 100, 80, ["COM4 disconnected"]),
                         ("failed", "serial read errors were recorded"))


if __name__ == "__main__":
    unittest.main()
