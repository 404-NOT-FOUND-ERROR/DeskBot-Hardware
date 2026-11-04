from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protocol.eaf_encode import encode_rgba_frames
from protocol.eaf_legacy_terminal import OFFICIAL_TERMINAL, append_official_terminal, inspect_stream
from protocol.eaf_preview import decode_eaf


def pack(entries, storage):
    payload = b"".join(struct.pack("<II", *row) for row in entries) + storage
    return b"\x89EAF" + struct.pack("<III", len(entries), sum(payload) & 0xffffffff, len(payload)) + payload


class LegacyTerminalTests(unittest.TestCase):
    def source(self):
        first = b"\xff\0\0\xff" * 12
        second = b"\0\0\xff\xff" * 12
        return encode_rgba_frames((first, second, first), 4, 3, block_height=2)

    def test_preserves_all_payloads_and_relative_offsets_including_shared_frames(self):
        raw = self.source()
        source = inspect_stream(raw)
        adapted = append_official_terminal(raw)
        result = inspect_stream(adapted)
        self.assertEqual(result.entries[:-1], source.entries)
        self.assertEqual(result.storage[:-49], source.storage)
        self.assertEqual(result.images, source.images)
        self.assertEqual(result.storage[-49:], OFFICIAL_TERMINAL)
        self.assertEqual(len(adapted) - len(raw), 57)
        self.assertTrue(result.has_terminal)

    def test_second_adaptation_is_byte_identical(self):
        adapted = append_official_terminal(self.source())
        self.assertEqual(append_official_terminal(adapted), adapted)

    def test_independent_decoder_keeps_all_pixels_and_counts_metadata_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "source.eaf"
            p.write_bytes(self.source())
            before = decode_eaf(p, fps=25)
            p.write_bytes(append_official_terminal(p.read_bytes()))
            after = decode_eaf(p, fps=25)
            self.assertEqual(before.frames, after.frames)
            self.assertEqual((after.stored_frame_count, after.metadata_frame_count), (4, 1))

    def test_bad_checksum_truncation_magic_and_empty_input_are_rejected(self):
        raw = self.source()
        for bad in (b"", raw[:15], raw[:-1], raw + b"\0", b"\x89AAF" + raw[4:],
                    raw[:20] + bytes((raw[20] ^ 1,)) + raw[21:]):
            with self.subTest(size=len(bad)), self.assertRaises(ValueError):
                append_official_terminal(bad)

    def test_out_of_bounds_lengths_and_offsets_are_rejected(self):
        stream = inspect_stream(self.source())
        for entry in ((3, 0), (100, len(stream.storage)), (100, 0xffffffff), (0xffffffff, 0)):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                append_official_terminal(pack((entry,) + stream.entries[1:], stream.storage))

    def test_partial_overlap_gap_and_unused_storage_are_rejected(self):
        stream = inspect_stream(self.source())
        for entries, storage in (
            ((stream.entries[0], (stream.entries[1][0], 1)), stream.storage),
            (tuple((length, offset + 1) for length, offset in stream.entries), b"\0" + stream.storage),
            (stream.entries, stream.storage + b"unused"),
        ):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                append_official_terminal(pack(entries, storage))

    def test_unknown_short_interior_and_multiple_metadata_are_rejected(self):
        image = inspect_stream(self.source()).images[0]
        for frames in ([image, b"ZZ_C"], [image, OFFICIAL_TERMINAL[:-1]],
                       [image, OFFICIAL_TERMINAL[:-1] + b"x"],
                       [OFFICIAL_TERMINAL, image], [image, OFFICIAL_TERMINAL, OFFICIAL_TERMINAL],
                       [OFFICIAL_TERMINAL], [image, b"ZZ_?"]):
            storage, entries = b"", []
            for frame in frames:
                entries.append((len(frame), len(storage)))
                storage += frame
            with self.subTest(frames=len(frames)), self.assertRaises(ValueError):
                append_official_terminal(pack(entries, storage))

    def test_corrupt_image_block_is_rejected_before_native_player(self):
        stream = inspect_stream(self.source())
        data = bytearray(stream.storage)
        data[11] = 7  # Unsupported indexed depth, with a valid outer checksum.
        with self.assertRaisesRegex(ValueError, "8-bit"):
            append_official_terminal(pack(stream.entries, data))

    def test_byte_pixel_and_frame_limits_include_the_appended_entry(self):
        raw = self.source()
        with patch("protocol.eaf_legacy_terminal.MAX_BYTES", len(raw) + 56):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                append_official_terminal(raw)
        with patch("protocol.eaf_legacy_terminal.MAX_ENTRIES", 3):
            with self.assertRaisesRegex(ValueError, "slot"):
                append_official_terminal(raw)
        with patch("protocol.eaf_preprocess.MAX_PIXELS", 35):
            with self.assertRaisesRegex(ValueError, "pixel limit"):
                append_official_terminal(raw)


if __name__ == "__main__":
    unittest.main()
