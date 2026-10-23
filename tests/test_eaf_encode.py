from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from protocol.eaf_encode import _encode_block, encode_rgba_frames
from protocol.eaf_preview import _rle_decode, decode_eaf


class EafEncodeTests(unittest.TestCase):
    def test_palette_rgb565_round_trip_and_transparency_threshold(self) -> None:
        frame = bytes((255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 127, 0, 0, 0, 255))
        encoded = encode_rgba_frames((frame, frame), 2, 2, block_height=1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roundtrip.eaf"
            path.write_bytes(encoded)
            decoded = decode_eaf(path, fps=25)
        self.assertEqual(decoded.frames, (bytes((255, 0, 0, 255, 0, 255, 0, 255,
                                               0, 0, 0, 0, 0, 0, 0, 255)),) * 2)
        self.assertEqual(decoded.metadata_frame_count, 0)
        self.assertEqual(decoded.frame_rate, 25)

    def test_run_lengths_over_255_are_split_without_losing_pixels(self) -> None:
        values = bytes((7,)) * 513
        block = _encode_block(values)
        self.assertEqual(block[0], 0)
        self.assertEqual(_rle_decode(block[1:], len(values), "encoder-test"), values)
        self.assertEqual(block[1:], bytes((255, 7, 255, 7, 3, 7)))

    def test_partial_last_block_is_padded_but_not_visible(self) -> None:
        frame = bytes((0, 0, 255, 255)) * 15
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial-block.eaf"
            path.write_bytes(encode_rgba_frames((frame,), 3, 5, block_height=4))
            decoded = decode_eaf(path)
        self.assertEqual(decoded.frames, (frame,))
        self.assertEqual((decoded.width, decoded.height), (3, 5))

    def test_invalid_or_unbounded_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_rgba_frames((), 1, 1)
        with self.assertRaises(ValueError):
            encode_rgba_frames((b"\0" * 4,), 0, 1)
        with self.assertRaises(ValueError):
            encode_rgba_frames((b"\0" * 4,), 2, 1)
        with self.assertRaises(ValueError):
            encode_rgba_frames((b"\0" * 4,), 1, 1)
        with self.assertRaises(ValueError):
            encode_rgba_frames((b"\0" * 4,) * 4097, 1, 1)


if __name__ == "__main__":
    unittest.main()
