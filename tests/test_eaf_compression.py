from __future__ import annotations

import struct
import unittest

from protocol.eaf_compression import (
    encode_block,
    huffman_decode,
    huffman_encode,
    transcode_legacy_eaf,
)
from protocol.eaf_encode import _encode_block, encode_rgba_frames
from protocol.eaf_preview import _rle_decode, decode_eaf


class HuffmanCodecTests(unittest.TestCase):
    def test_round_trip_single_mixed_and_all_byte_data(self) -> None:
        samples = (b"A", b"A" * 1024, bytes(range(256)) * 3,
                   b"abracadabra" * 100)
        for sample in samples:
            with self.subTest(size=len(sample)):
                encoded = huffman_encode(sample)
                self.assertEqual(huffman_decode(encoded, len(sample)), sample)

    def test_encoding_is_deterministic(self) -> None:
        sample = bytes(range(31)) * 17
        self.assertEqual(huffman_encode(sample), huffman_encode(sample))

    def test_malformed_dictionary_and_streams_are_rejected(self) -> None:
        def dictionary(entries: bytes, padding: int = 0, stream: bytes = b"") -> bytes:
            data = bytes((padding,)) + entries
            return struct.pack("<H", len(data)) + data + stream

        # Truncated dictionary entry, zero-length code, duplicate symbol,
        # invalid code value, and a prefix collision.
        cases = (
            struct.pack("<H", 3) + b"\0\x07\x02",
            dictionary(b"\x07\x00"),
            dictionary(b"\x07\x01\0\x07\x01\x01"),
            dictionary(b"\x07\x01\x02"),
            dictionary(b"\x01\x01\0\x02\x02\0"),
            dictionary(b"\x07\x41" + bytes(9)),
            dictionary(b"\x07\x01\0", padding=8, stream=b"\0"),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    huffman_decode(payload, 8)

        encoded = huffman_encode(b"abc")
        dictionary_size = struct.unpack_from("<H", encoded)[0]
        truncated_stream = encoded[:2 + dictionary_size]
        with self.assertRaises(ValueError):
            huffman_decode(truncated_stream, 8)

        with self.assertRaisesRegex(ValueError, "invalid code path"):
            huffman_decode(dictionary(b"\x01\x01\0", padding=7, stream=b"\x80"), 1)
        with self.assertRaisesRegex(ValueError, "truncated code"):
            huffman_decode(dictionary(b"\x01\x02\0", padding=7, stream=b"\0"), 1)

        padded = bytearray(huffman_encode(b"ab"))
        padded[-1] |= 1
        with self.assertRaisesRegex(ValueError, "padding"):
            huffman_decode(bytes(padded), 2)

    def test_output_capacity_is_enforced(self) -> None:
        encoded = huffman_encode(b"0123456789")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            huffman_decode(encoded, 3)
        with self.assertRaises(ValueError):
            huffman_decode(encoded, 0)

    def test_single_symbol_empty_stream_requires_explicit_permission(self) -> None:
        encoded = struct.pack("<H", 4) + b"\0\x07\x01\0"
        self.assertEqual(huffman_decode(encoded, 3), b"\x07" * 3)
        with self.assertRaisesRegex(ValueError, "single-symbol"):
            huffman_decode(encoded, 3, allow_single_fill=False)
        self.assertGreater(len(huffman_encode(b"\x07" * 3)), len(encoded))


class EafCompressionTests(unittest.TestCase):
    def test_block_encodings_round_trip(self) -> None:
        indices = bytes((0,)) * 30 + bytes(range(1, 30))
        expected_rle = _encode_block(indices)[1:]
        for encoding in (0, 1, 3, "best"):
            with self.subTest(encoding=encoding):
                block = encode_block(indices, encoding)
                self.assertIn(block[0], (0, 1, 3))
                if block[0] == 0:
                    self.assertEqual(_rle_decode(block[1:], len(indices), "test"), indices)
                elif block[0] == 1:
                    rle = huffman_decode(block[1:], len(expected_rle) * 2)
                    self.assertEqual(_rle_decode(rle, len(indices), "test"), indices)
                else:
                    self.assertEqual(huffman_decode(block[1:], len(indices)), indices)

    def test_transcode_preserves_frames_and_supports_8bit_encodings(self) -> None:
        red = bytes((255, 0, 0, 255))
        blue = bytes((0, 0, 255, 255))
        source = encode_rgba_frames((red * 8 + blue * 8, blue * 16), 4, 4,
                                    block_height=2, palette_colors=8)
        original = decode_eaf_from_bytes(source)
        for encoding in (0, 1, 3, "best"):
            with self.subTest(encoding=encoding):
                candidate = transcode_legacy_eaf(source, encoding=encoding)
                self.assertEqual(decode_eaf_from_bytes(candidate), original)

    def test_4bit_requires_even_width_and_rejects_more_than_16_used_indices(self) -> None:
        one_pixel = bytes((255, 0, 0, 255))
        odd_source = encode_rgba_frames((one_pixel * 3,), 3, 1, block_height=1)
        with self.assertRaisesRegex(ValueError, "even width"):
            transcode_legacy_eaf(odd_source, bit_depth=4)

        pixels = b"".join(bytes((i * 15, i * 15, i * 15, 255)) for i in range(17))
        many_colors = encode_rgba_frames((pixels + pixels,), 34, 1,
                                         block_height=1, palette_colors=64)
        with self.assertRaisesRegex(ValueError, "quantization"):
            transcode_legacy_eaf(many_colors, bit_depth=4)

    def test_lossless_4bit_remap_preserves_visible_frames(self) -> None:
        red, blue = bytes((255, 0, 0, 255)), bytes((0, 0, 255, 255))
        source = encode_rgba_frames((red * 8 + blue * 8,), 4, 4,
                                    block_height=2, palette_colors=4)
        candidate = transcode_legacy_eaf(source, encoding="best", bit_depth=4)
        self.assertEqual(decode_eaf_from_bytes(candidate), decode_eaf_from_bytes(source))

    def test_partial_last_block_survives_all_encodings_and_depths(self) -> None:
        source = encode_rgba_frames((bytes((10, 90, 30, 255)) * 12,), 4, 3,
                                    block_height=2, palette_colors=4)
        original = decode_eaf_from_bytes(source)
        for encoding in (0, 1, 3, "best"):
            for depth in (4, 8):
                with self.subTest(encoding=encoding, depth=depth):
                    self.assertEqual(decode_eaf_from_bytes(transcode_legacy_eaf(
                        source, encoding=encoding, bit_depth=depth)), original)

    def test_invalid_encoding_and_depth_are_rejected(self) -> None:
        source = encode_rgba_frames((bytes((1, 2, 3, 255)) * 4,), 2, 2)
        for encoding in (True, 2, -1, 1.0, "unknown"):
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                transcode_legacy_eaf(source, encoding=encoding)
        for depth in (True, 8.0, 16):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                transcode_legacy_eaf(source, bit_depth=depth)

    def test_deduplication_keeps_frame_sequence(self) -> None:
        pixel = bytes((20, 40, 80, 255))
        source = encode_rgba_frames((pixel * 16, pixel * 16, pixel * 16), 4, 4,
                                    block_height=2)
        candidate = transcode_legacy_eaf(source, encoding="best", deduplicate=True)
        self.assertLess(len(candidate), len(source))
        self.assertEqual(decode_eaf_from_bytes(candidate), decode_eaf_from_bytes(source))

    def test_truncated_input_fails_with_value_error(self) -> None:
        source = encode_rgba_frames((bytes((1, 2, 3, 255)) * 4,), 2, 2)
        for cut in (0, 1, 8, len(source) - 1):
            with self.subTest(cut=cut):
                with self.assertRaises(ValueError):
                    transcode_legacy_eaf(source[:cut])

    def test_truncated_block_table_is_rejected_before_unpack(self) -> None:
        source = encode_rgba_frames((bytes((1, 2, 3, 255)) * 4,), 2, 2)
        # Keep valid outer sizes/checksum, but omit the image block table.
        frame = source[24:44]
        malformed = assemble_frames((frame,))
        with self.assertRaisesRegex(ValueError, "block table or palette"):
            transcode_legacy_eaf(malformed)

    def test_frame_dimensions_and_total_pixel_budget_are_enforced(self) -> None:
        from unittest.mock import patch
        pixel = bytes((1, 2, 3, 255))
        first = encode_rgba_frames((pixel * 4,), 2, 2)[24:]
        second = encode_rgba_frames((pixel * 6,), 2, 3)[24:]
        with self.assertRaisesRegex(ValueError, "changes frame dimensions"):
            transcode_legacy_eaf(assemble_frames((first, second)))
        with patch("protocol.eaf_compression.MAX_ANIMATION_PIXELS", 7):
            with self.assertRaisesRegex(ValueError, "megapixel"):
                transcode_legacy_eaf(assemble_frames((first, first)))


def assemble_frames(frames: tuple[bytes, ...]) -> bytes:
    table = bytearray()
    offset = 0
    for frame in frames:
        table.extend(struct.pack("<II", len(frame), offset))
        offset += len(frame)
    payload = table + b"".join(frames)
    return b"\x89EAF" + struct.pack("<III", len(frames), sum(payload) & 0xffffffff, len(payload)) + payload


def decode_eaf_from_bytes(raw: bytes) -> tuple[bytes, ...]:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "candidate.eaf"
        path.write_bytes(raw)
        return decode_eaf(path, fps=25).frames


if __name__ == "__main__":
    unittest.main()
