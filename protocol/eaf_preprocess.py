"""Bounded offline 8-bit EAF preprocessing; no palette quantization or device IO.

Accept the existing player's mixed 0/1/3 block encodings, preserve every
logical image frame, and share identical stored frames without dropping their
timeline entries. Cropping is explicit and never presented as lossless to the
whole original image. Metadata frames and other formats fail closed.
"""
from __future__ import annotations

import math
import struct

from protocol.eaf_compression import encode_block, huffman_decode
from protocol.eaf_preview import _rle_decode

MAX_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 25_000_000


def indexed_frames(raw: bytes) -> tuple[tuple[bytes, bytes, int, int, bytes], ...]:
    """Return (18-byte image header, BGRA palette, width, height, indices).

    Identical table entries share the decoded tuple. Full final blocks and
    shorter valid final rows are accepted.
    """
    if not 16 <= len(raw) <= MAX_BYTES or raw[:4] != b"\x89EAF":
        raise ValueError("preprocessor requires a bounded EAF image stream")
    count, checksum, size = struct.unpack_from("<III", raw, 4)
    if not 0 < count <= 4096 or size != len(raw) - 16 or size < count * 8:
        raise ValueError("EAF table or payload length is invalid")
    if sum(raw[16:]) & 0xffffffff != checksum:
        raise ValueError("EAF checksum mismatch")
    storage = raw[16 + count * 8:]
    decoded = {}
    spans: list[tuple[int, int]] = []
    frames = []
    dimensions = None
    for index in range(count):
        length, offset = struct.unpack_from("<II", raw, 16 + index * 8)
        if length < 20 or offset > len(storage) or length > len(storage) - offset:
            raise ValueError("EAF frame table offset is invalid")
        end = offset + length
        for left, right in spans:
            if offset < right and left < end and (offset, end) != (left, right):
                raise ValueError("EAF frame payloads partially overlap")
        spans.append((offset, end))
        key = (offset, length)
        if key not in decoded:
            frame = storage[offset:end]
            if frame[:4] != b"ZZ_S" or frame[11] != 8:
                raise ValueError("preprocessor accepts only 8-bit image frames; metadata is not discarded")
            header = frame[2:20]
            width, height, blocks, block_height = struct.unpack_from("<HHHH", header, 10)
            if (not 0 < width <= 1024 or not 0 < height <= 1024 or not 0 < blocks <= 256
                    or not 0 < block_height <= 1024
                    or not (blocks - 1) * block_height < height <= blocks * block_height):
                raise ValueError("EAF dimensions or block height is invalid")
            if dimensions is None:
                dimensions = width, height
            if dimensions != (width, height) or count * width * height > MAX_PIXELS:
                raise ValueError("EAF dimensions change or animation exceeds the pixel limit")
            palette_start = 20 + blocks * 4
            cursor = palette_start + 1024
            if cursor > len(frame):
                raise ValueError("EAF block table or palette is truncated")
            palette = frame[palette_start:cursor]
            lengths = struct.unpack_from("<" + "I" * blocks, frame, 20)
            if any(value < 2 for value in lengths) or cursor + sum(lengths) != len(frame):
                raise ValueError("EAF block lengths are invalid")
            pixels = bytearray()
            for block_index, block_length in enumerate(lengths):
                block = frame[cursor:cursor + block_length]
                cursor += block_length
                expected = width * block_height
                valid = width * min(block_height, height - block_index * block_height)
                if block[0] == 0:
                    indices = _rle_decode(block[1:], expected, "preprocessor", valid)
                elif block[0] == 1:
                    rle = huffman_decode(block[1:], expected * 2)
                    indices = _rle_decode(rle, expected, "preprocessor", valid)
                elif block[0] == 3:
                    indices = huffman_decode(block[1:], expected)
                    if len(indices) not in (expected, valid):
                        raise ValueError("EAF Huffman decoded length is invalid")
                else:
                    raise ValueError("EAF block encoding is unsupported")
                pixels.extend(indices[:valid])
            decoded[key] = header, palette, width, height, bytes(pixels)
        frames.append(decoded[key])
    return tuple(frames)


def preprocess_eaf(raw: bytes, *, encoding: int | str = "best", block_height: int = 32,
                   crop: tuple[int, int, int, int] | None = None,
                   deduplicate: bool = True) -> bytes:
    """Reblock/re-encode source palette indices; optional (x, y, width, height).

    No FPS, segment timing, palette colors, alpha, or frame order is changed.
    The caller owns external FPS/loop metadata and full-display crop review.
    """
    if encoding != "best" and (type(encoding) is not int or encoding not in (0, 1, 3)):
        raise ValueError("encoding must be 0, 1, 3 or best")
    if type(block_height) is not int or not 1 <= block_height <= 1024:
        raise ValueError("block height must be an integer from 1 to 1024")
    if type(deduplicate) is not bool:
        raise ValueError("deduplicate must be a boolean")
    frames = indexed_frames(raw)
    _, _, source_w, source_h, _ = frames[0]
    if crop is None:
        crop = 0, 0, source_w, source_h
    if (not isinstance(crop, tuple) or len(crop) != 4
            or any(type(value) is not int for value in crop)):
        raise ValueError("crop must be a tuple of four integers")
    x, y, width, height = crop
    if (x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > source_w or y + height > source_h):
        raise ValueError("crop is outside the source image")
    blocks = math.ceil(height / block_height)
    if blocks > 256:
        raise ValueError("block count exceeds 256")
    storage, table = bytearray(), bytearray()
    locations: dict[bytes, tuple[int, int]] = {}
    frame_cache: dict[tuple[bytes, bytes, bytes], bytes] = {}
    for header, palette, _, _, indices in frames:
        cache_key = header, palette, indices
        encoded = frame_cache.get(cache_key)
        if encoded is None:
            cropped = b"".join(indices[row * source_w + x:row * source_w + x + width]
                               for row in range(y, y + height))
            encoded_blocks = []
            for block in range(blocks):
                start = block * block_height * width
                part = cropped[start:start + block_height * width]
                # Explicit padding avoids any read of an uninitialized tail.
                encoded_blocks.append(encode_block(part.ljust(block_height * width, b"\0"), encoding))
            new_header = bytearray(header)
            struct.pack_into("<HHHH", new_header, 10, width, height, blocks, block_height)
            encoded = (b"ZZ" + new_header
                       + b"".join(struct.pack("<I", len(part)) for part in encoded_blocks)
                       + palette + b"".join(encoded_blocks))
            frame_cache[cache_key] = encoded
        location = locations.get(encoded) if deduplicate else None
        if location is None:
            location = len(encoded), len(storage)
            locations[encoded] = location
            if len(storage) + len(encoded) + len(frames) * 8 > MAX_BYTES - 16:
                raise ValueError("preprocessed EAF exceeds byte limit")
            storage.extend(encoded)
        table.extend(struct.pack("<II", *location))
    payload = table + storage
    if len(payload) > MAX_BYTES - 16:
        raise ValueError("preprocessed EAF exceeds byte limit")
    return raw[:4] + struct.pack("<III", len(frames), sum(payload) & 0xffffffff, len(payload)) + payload
