from __future__ import annotations

import json
import sys
import tempfile
import unittest
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from compare_magnetic_state_capture import CAPTURE_ROOT, compare_capture  # noqa: E402
from split_magnetic_state_capture import split_capture  # noqa: E402


def callback_line(second: int, xyz: tuple[int, int, int]) -> str:
    data = b"\x01" + b"".join(value.to_bytes(2, "big", signed=True) for value in xyz)
    return f"2026-11-07T00:00:{second:02d}.000Z\tHandle: cmd=06, {data.hex(' ').upper()}"


class CompareStateCaptureTests(unittest.TestCase):
    def make_capture(self, root: Path) -> Path:
        capture = root / "capture"
        states = capture / "labeled-states"
        states.mkdir(parents=True)
        windows = []
        for index, (label, center) in enumerate((
            ("baseline", (10, 20, 30)),
            ("slider_up", (40, 20, 30)),
            ("reinstalled", (13, 20, 30)),
        ), 1):
            folder = states / f"{index:02d}-{label}-01"
            folder.mkdir()
            lines = [callback_line(second, center) for second in range(index, index + 8)]
            (folder / "body.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
            (folder / "base.log").write_text("base event\n", encoding="utf-8")
            windows.append({
                "state": label,
                "body_log": f"{folder.name}/body.log",
                "base_log": f"{folder.name}/base.log",
            })
        (states / "manifest.json").write_text(json.dumps({
            "schema": "deskbot.magnetic.state_split.v1",
            "source_capture": str(capture),
            "windows": windows,
        }), encoding="utf-8")
        return capture

    def test_reports_window_statistics_and_pairwise_centers(self):
        CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=CAPTURE_ROOT) as temporary:
            capture = self.make_capture(Path(temporary))
            report = compare_capture(capture)

            self.assertTrue(report["all_windows_ready"])
            self.assertFalse(report["automatic_identity_decision"])
            self.assertEqual(report["states"]["baseline"]["sample_count"], 8)
            self.assertEqual(report["states"]["baseline"]["all_samples_axes"]["x"]["mean"], 10)
            self.assertEqual(report["windows"][0]["within_window_scatter_rms"], 0)
            slider_distance = next(
                row for row in report["pairwise_state_center_distances"]
                if {row["left_state"], row["right_state"]} == {"baseline", "slider_up"}
            )
            self.assertEqual(slider_distance["center_distance"], 30)
            repeat = next(
                row for row in report["pairwise_window_center_distances"]
                if row["left_state"] == "baseline" and row["right_state"] == "reinstalled"
            )
            self.assertEqual(repeat["center_distance"], 3)

    def test_manifest_paths_cannot_escape_capture(self):
        CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=CAPTURE_ROOT) as temporary:
            capture = self.make_capture(Path(temporary))
            manifest_path = capture / "labeled-states" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["windows"][0]["body_log"] = "../../outside.log"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "escapes"):
                compare_capture(capture)

    def test_splitter_output_can_be_compared_directly(self):
        CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=CAPTURE_ROOT) as temporary:
            capture = Path(temporary) / "capture"
            capture.mkdir()
            (capture / "summary.json").write_text(json.dumps({
                "schema": "deskbot.magnetic.state_capture.v1",
                "read_only": True,
                "capture_status": "complete",
                "minimum_state_seconds": 10,
            }), encoding="utf-8")
            (capture / "body.log").write_text("\n".join(
                [callback_line(second, (10, 20, 30)) for second in range(1, 9)]
                + [callback_line(second, (40, 20, 30)) for second in range(13, 21)]
            ) + "\n", encoding="utf-8")
            (capture / "base.log").write_text("", encoding="utf-8")
            with (capture / "markers.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["timestamp_utc", "state", "boundary"])
                writer.writeheader()
                writer.writerows([
                    {"timestamp_utc": "2026-11-07T00:00:00Z", "state": "baseline", "boundary": "start"},
                    {"timestamp_utc": "2026-11-07T00:00:10Z", "state": "baseline", "boundary": "end"},
                    {"timestamp_utc": "2026-11-07T00:00:12Z", "state": "slider_up", "boundary": "start"},
                    {"timestamp_utc": "2026-11-07T00:00:22Z", "state": "slider_up", "boundary": "end"},
                ])

            split_capture(capture)
            report = compare_capture(capture)

            self.assertTrue(report["all_windows_ready"])
            self.assertEqual(report["states"]["baseline"]["sample_count"], 8)
            self.assertEqual(report["states"]["slider_up"]["sample_count"], 8)

    def test_capture_outside_project_capture_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "must be under"):
                compare_capture(Path(temporary))


if __name__ == "__main__":
    unittest.main()
