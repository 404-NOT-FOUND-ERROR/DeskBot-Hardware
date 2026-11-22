from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

from protocol.grok_round_transitions import DEFAULT_TIMELINE, boundary_metrics, validate_timeline

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'tmp/grok-round-transitions-20261111/build/grok-round-transition-render.exe'


class TimelineTests(unittest.TestCase):
    def test_fixed_sequence_has_stable_boundaries(self):
        segments = validate_timeline(DEFAULT_TIMELINE)
        self.assertEqual([(i['state'], i['start_frame'], i['frames']) for i in segments],
                         [('idle', 0, 40), ('happy', 40, 40), ('sad', 80, 40), ('idle', 120, 40)])

    def test_invalid_timeline_rejected_before_export(self):
        for timeline, fps in [([], 20), ([('thinking', 40)], 20), ([('idle', 0)], 20),
                              ([('idle', 193)], 20), ([('idle', 40)] * 5, 20), ([('idle', 20)], 17),
                              ([('idle', True)], 20)]:
            with self.subTest(timeline=timeline, fps=fps), self.assertRaises(ValueError):
                validate_timeline(timeline, fps)

    def test_boundary_metrics_include_nearby_frames(self):
        frames = np.zeros((8, 2, 2, 4), dtype=np.uint8)
        frames[4:, ..., :3] = 200
        frames[4:, ..., 3] = 255
        segments = validate_timeline([('idle', 4), ('happy', 4)])
        metrics = boundary_metrics(frames, segments)
        self.assertEqual((metrics[0]['frame'], metrics[0]['mean_rgb_delta']), (4, 200))
        self.assertEqual(len(metrics[0]['nearby_adjacent_mean_rgb_delta']), 7)

    def test_measurement_ignores_invisible_rgb_and_rejects_timeline_mismatch(self):
        frames = np.zeros((8, 2, 2, 4), dtype=np.uint8)
        frames[4:, ..., :3] = 255
        segments = validate_timeline([('idle', 4), ('happy', 4)])
        self.assertEqual(boundary_metrics(frames, segments)[0]['mean_rgb_delta'], 0)
        segments[1]['start_frame'] = 3
        with self.assertRaises(ValueError):
            boundary_metrics(frames, segments)


@unittest.skipUnless(BINARY.exists(), 'build the pinned transition renderer before native integration tests')
class ContinuousRendererTests(unittest.TestCase):
    def render(self, directory: str, *segments: str):
        output = Path(directory) / 'render.gray'
        result = subprocess.run([str(BINARY), '20', str(output), *segments], check=True,
                                capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout), output.read_bytes()

    def test_same_state_segments_do_not_reset_the_engine(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            first, one = self.render(directory, 'idle:40', 'idle:40')
            second, two = self.render(directory, 'idle:80')
            third, three = self.render(directory, 'idle:40', 'idle:40')
        self.assertEqual((first['reset_count'], first['image_frames'], first['motion_ticks']), (1, 80, 240))
        self.assertEqual(first['segments'][1]['start_frame'], 40)
        self.assertEqual(one, two)
        self.assertEqual(one, three)

    def test_emotion_sequence_keeps_one_clock_and_seed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            first, one = self.render(directory, 'idle:40', 'happy:40', 'sad:40', 'idle:40')
            second, two = self.render(directory, 'idle:40', 'happy:40', 'sad:40', 'idle:40')
        self.assertEqual(first['segments'], validate_timeline(DEFAULT_TIMELINE))
        self.assertEqual(first['motion_ticks'], 480)
        self.assertTrue(first['smooth_presentation'])
        self.assertFalse(first['mirror'])
        self.assertEqual(first['shape'], 'blob')
        self.assertEqual(one, two)

    def test_invalid_native_schedule_leaves_no_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            output = Path(directory) / 'invalid.gray'
            for schedule in [('thinking:20',), ('idle:193',), ('idle:0',), ('idle:100', 'happy:100')]:
                result = subprocess.run([str(BINARY), '20', str(output), *schedule],
                                        capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
