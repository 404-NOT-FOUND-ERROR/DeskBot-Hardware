from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protocol.eaf_encode import encode_rgba_frames
from protocol.eaf_preprocess import indexed_frames, preprocess_eaf
from protocol.eaf_preview import decode_eaf


def decode(raw):
    with tempfile.TemporaryDirectory() as name:
        p = Path(name) / "animation.eaf"
        p.write_bytes(raw)
        return decode_eaf(p, fps=25)


def rebuild(frames):
    table, storage = bytearray(), bytearray()
    for frame in frames:
        table.extend(struct.pack("<II", len(frame), len(storage)))
        storage.extend(frame)
    payload = table + storage
    return b"\x89EAF" + struct.pack("<III", len(frames), sum(payload) & 0xffffffff, len(payload)) + payload


class EafPreprocessTests(unittest.TestCase):
    def source(self):
        red, blue = b"\xff\0\0\xff", b"\0\0\xff\xff"
        first = red * 4 + blue * 4 + red * 4
        second = blue * 4 + red * 4 + blue * 4
        return encode_rgba_frames((first, second, first), 4, 3, block_height=2), (first, second, first)

    def test_all_mixed_encoding_sources_reblock_without_changing_pixels(self):
        raw, expected = self.source()
        for source_encoding in (0, 1, 3, "best"):
            source = preprocess_eaf(raw, encoding=source_encoding)
            for target_encoding in (0, 1, 3, "best"):
                with self.subTest(source=source_encoding, target=target_encoding):
                    result = decode(preprocess_eaf(source, encoding=target_encoding, block_height=1))
                    self.assertEqual(result.frames, expected)
                    self.assertEqual((result.width, result.height, result.bit_depth), (4, 3, 8))

    def test_crop_keeps_exact_requested_rows_and_palette_without_resampling(self):
        raw, frames = self.source()
        crop = (1, 1, 2, 2)
        result = decode(preprocess_eaf(raw, crop=crop, block_height=3))
        expected = tuple(b"".join(frame[(row * 4 + 1) * 4:(row * 4 + 3) * 4]
                                  for row in (1, 2)) for frame in frames)
        self.assertEqual(result.frames, expected)
        self.assertEqual((result.width, result.height), (2, 2))

    def test_payload_dedup_keeps_duplicate_timeline_entries(self):
        raw, expected = self.source()
        shared = preprocess_eaf(raw, deduplicate=True)
        separate = preprocess_eaf(raw, deduplicate=False)
        self.assertLess(len(shared), len(separate))
        self.assertEqual(decode(shared).frames, expected)
        self.assertEqual(decode(separate).frames, expected)
        self.assertEqual(struct.unpack_from("<II", shared, 16), struct.unpack_from("<II", shared, 32))
        self.assertEqual(raw, self.source()[0])

    def test_invalid_inputs_and_partial_overlap_are_rejected(self):
        raw, _ = self.source()
        for candidate in (raw[:15], raw[:-1], raw[:20] + bytes([raw[20] ^ 1]) + raw[21:]):
            with self.subTest(size=len(candidate)), self.assertRaises(ValueError):
                preprocess_eaf(candidate)
        changed = bytearray(raw)
        struct.pack_into("<I", changed, 28, 1)  # second payload partially overlaps the first
        struct.pack_into("<I", changed, 8, sum(changed[16:]) & 0xffffffff)
        with self.assertRaisesRegex(ValueError, "overlap"):
            preprocess_eaf(bytes(changed))

    def test_metadata_is_rejected_instead_of_silently_dropping_timing(self):
        raw, _ = self.source()
        first_len = struct.unpack_from("<I", raw, 16)[0]
        first = raw[40:40 + first_len]
        with self.assertRaisesRegex(ValueError, "only 8-bit image"):
            indexed_frames(rebuild([first, b"ZZ_C" + bytes(16)]))

    def test_bounds_and_strict_options(self):
        raw, _ = self.source()
        for option in (0, True, 1.5, 1025):
            with self.subTest(block_height=option), self.assertRaises(ValueError):
                preprocess_eaf(raw, block_height=option)
        for crop in ((-1, 0, 1, 1), (0, 0, 5, 2), (0, 0, 1, 0), [0, 0, 1, 1], (False, 0, 1, 1)):
            with self.subTest(crop=crop), self.assertRaises(ValueError):
                preprocess_eaf(raw, crop=crop)
        with self.assertRaises(ValueError):
            preprocess_eaf(raw, deduplicate=1)
        for encoding in (True, 2, "gzip"):
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                preprocess_eaf(raw, encoding=encoding)
        with patch("protocol.eaf_preprocess.MAX_PIXELS", 35):
            with self.assertRaisesRegex(ValueError, "pixel limit"):
                preprocess_eaf(raw)


if __name__ == "__main__":
    unittest.main()
