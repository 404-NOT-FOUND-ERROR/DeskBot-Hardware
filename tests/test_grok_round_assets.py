from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import numpy as np

from protocol.eaf_legacy_terminal import OFFICIAL_TERMINAL
from protocol.eaf_preview import _rle_decode, decode_eaf
from protocol.grok_round_assets import (
    EXCLUDED_STATES, ROUND_STATES, _rle, circle_mask, encode_gray_frames, expected_rgba, read_gray_stream,
)

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'tmp/grok-round-eaf-20261111/build/grok-round-render.exe'


class GrayConversionTests(unittest.TestCase):
    def test_long_runs_and_exact_multiples_of_255(self):
        for length in [1, 254, 255, 256, 510, 511, 11520]:
            values = np.full(length, 37, dtype=np.uint8)
            encoded = _rle(values)
            self.assertEqual(_rle_decode(encoded, length, 'gray-test'), values.tobytes())
            self.assertNotIn(0, encoded[::2])

    def test_run_boundaries_and_all_256_values(self):
        values = np.r_[np.arange(256, dtype=np.uint8), np.zeros(511, dtype=np.uint8), np.ones(255, dtype=np.uint8)]
        self.assertEqual(_rle_decode(_rle(values), len(values), 'gray-test'), values.tobytes())

    def test_partial_blocks_opaque_black_palette_and_frame_storage_sharing(self):
        first = np.arange(40, dtype=np.uint8).reshape(5, 8)
        frames = np.stack([first, first, first[::-1]])
        alpha = np.full_like(frames, 255)
        alpha[:, 0, -1] = 0
        raw, metadata = encode_gray_frames(frames, alpha, block_height=4)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            path = Path(directory) / 'roundtrip.eaf'
            path.write_bytes(raw)
            animation = decode_eaf(path, fps=20)
        actual = np.stack([np.frombuffer(frame, dtype=np.uint8).reshape(5, 8, 4) for frame in animation.frames])
        np.testing.assert_array_equal(actual, expected_rgba(frames, alpha))
        self.assertEqual(metadata['stored_unique_images'], 2)
        self.assertEqual(animation.stored_frame_count, 4)
        self.assertEqual(animation.metadata_frame_count, 1)
        self.assertTrue(raw.endswith(OFFICIAL_TERMINAL))
        self.assertEqual(animation.bit_depth, 4)
        self.assertEqual(tuple(actual[0, 0, 0]), (0, 0, 0, 255))
        self.assertEqual(tuple(actual[0, 0, -1]), (0, 0, 0, 0))

    def test_circle_uses_pixel_centers_and_keeps_the_center(self):
        mask = circle_mask(360, 360)
        self.assertFalse(mask[0, 0])
        self.assertTrue(mask[180, 180])
        np.testing.assert_array_equal(mask, mask[::-1, ::-1])
        for dimensions in [(0, 0), (True, True), (360, 320), (1025, 1025)]:
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                circle_mask(*dimensions)

    def test_stream_rejects_corrupt_header_size_and_nondivisor_fps(self):
        for raw in [b'', b'GKR2', b'ABCD' + b'\0' * 16,
                    b'GKR2' + struct.pack('<IIII', 360, 360, 17, 1) + b'\0' * 259200,
                    b'GKR2' + struct.pack('<IIII', 360, 360, 20, 1)]:
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                read_gray_stream(raw)

    def test_invalid_encoder_inputs_and_budget(self):
        for frames in [[], np.zeros((1, 2, 3), dtype=np.float32), np.zeros((2, 3), dtype=np.uint8),
                       np.zeros((0, 2, 2), dtype=np.uint8), np.zeros((1, 1025, 1), dtype=np.uint8),
                       np.zeros((193, 360, 360), dtype=np.uint8)]:
            with self.subTest(type=str(type(frames))), self.assertRaises(ValueError):
                encode_gray_frames(frames, np.zeros((1, 2, 3), dtype=np.uint8))
        for height in [0, True, 1025]:
            with self.subTest(block_height=height), self.assertRaises(ValueError):
                encode_gray_frames(np.zeros((1, 5, 8), dtype=np.uint8), np.zeros((1, 5, 8), dtype=np.uint8), block_height=height)

    def test_all_gray_levels_roundtrip_and_alpha_plane_order(self):
        frames = np.arange(256, dtype=np.uint8).reshape(1, 16, 16)
        alpha = np.full_like(frames, 255)
        raw, _ = encode_gray_frames(frames, alpha)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            path = Path(directory) / 'ramp.eaf'
            path.write_bytes(raw)
            animation = decode_eaf(path)
        np.testing.assert_array_equal(np.frombuffer(animation.frames[0], dtype=np.uint8).reshape(1, 16, 16, 4), expected_rgba(frames, alpha))
        plane = np.zeros((2, 2, 360, 360), dtype=np.uint8)
        plane[0, 0] = 35; plane[0, 1] = 255; plane[1, 0] = 210; plane[1, 1] = 42
        gray, opacity, fps = read_gray_stream(b'GKR2' + struct.pack('<IIII', 360, 360, 20, 2) + plane.tobytes())
        self.assertEqual((int(gray[0, 0, 0]), int(opacity[0, 0, 0]), int(gray[1, 0, 0]), int(opacity[1, 0, 0])), (35, 255, 210, 42))

    def test_state_selection_covers_all_35_without_special_morphs(self):
        self.assertEqual(len(ROUND_STATES), 23)
        self.assertEqual(len(EXCLUDED_STATES), 12)
        self.assertFalse(set(ROUND_STATES) & set(EXCLUDED_STATES))
        self.assertIn('idle', ROUND_STATES)
        self.assertIn('thinking', EXCLUDED_STATES)


@unittest.skipUnless(BINARY.exists(), 'build the pinned desktop renderer before native integration tests')
class NativeRendererIntegrationTests(unittest.TestCase):
    def test_original_60hz_timing_and_seeded_determinism(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            path = Path(directory) / 'sample.gray'
            contents = []
            for _ in range(2):
                run = subprocess.run([str(BINARY), 'happy', '6', '20', str(path)],
                                     check=True, capture_output=True, text=True, timeout=30)
                metadata = json.loads(run.stdout)
                self.assertEqual(metadata['motion_ticks'], 18)
                self.assertEqual(metadata['shape'], 'blob')
                self.assertEqual(metadata['renderer_overflows'], 0)
                contents.append(path.read_bytes())
            self.assertEqual(*contents)
            frames, alpha, fps = read_gray_stream(contents[0])
            self.assertEqual(frames.shape, (6, 360, 360))
            self.assertEqual(alpha.shape, frames.shape)
            self.assertEqual(fps, 20)
            self.assertGreater(frames.max(), 0)

    def test_special_states_invalid_timing_and_unknown_names_do_not_write(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            path = Path(directory) / 'invalid.gray'
            for arguments in [('thinking', '6', '20'), ('heart', '6', '20'),
                              ('idle', '6', '17'), ('idle', '193', '20'), ('idle', '-1', '20')]:
                run = subprocess.run([str(BINARY), *arguments, str(path)], capture_output=True, timeout=10)
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
