"""Minimal encoder for the current ESP-VoCat 8-bit EAF RLE format."""
from __future__ import annotations

import math
import struct
from collections import Counter
from typing import Iterable


MAX_FRAMES = 4096
MAX_DIMENSION = 1024
MAX_PIXELS = 25_000_000


def _palette(frames: tuple[bytes, ...], color_count: int = 255) -> tuple[bytes, bytes]:
    """Build one bounded RGB555 histogram palette and a 15-bit lookup table."""
    histogram: Counter[int] = Counter()
    for frame in frames:
        for offset in range(0, len(frame), 4):
            if frame[offset + 3] >= 128:
                key = ((frame[offset] >> 3) << 10) | ((frame[offset + 1] >> 3) << 5) | (frame[offset + 2] >> 3)
                histogram[key] += 1
    if not histogram:
        raise ValueError("animation frames contain no visible pixels")

    boxes: list[list[int]] = [list(histogram)]
    while len(boxes) < color_count:
        scored: list[tuple[int, int, int, int]] = []
        for index, box in enumerate(boxes):
            if len(box) < 2:
                continue
            ranges = []
            for shift in (10, 5, 0):
                values = [(key >> shift) & 31 for key in box]
                ranges.append(max(values) - min(values))
            channel = max(range(3), key=lambda item: ranges[item])
            score = ranges[channel] * sum(histogram[key] for key in box)
            scored.append((score, index, channel, len(box)))
        if not scored:
            break
        _, index, channel, _ = max(scored)
        shift = (10, 5, 0)[channel]
        ordered = sorted(boxes[index], key=lambda key: ((key >> shift) & 31, key))
        halfway = (sum(histogram[key] for key in ordered) + 1) // 2
        total = 0
        split = 1
        for position, key in enumerate(ordered[:-1], start=1):
            total += histogram[key]
            if total >= halfway:
                split = position
                break
        boxes[index:index + 1] = [ordered[:split], ordered[split:]]

    colors: list[tuple[int, int, int]] = []
    palette = bytearray(256 * 4)
    for index, box in enumerate(boxes, start=1):
        weight = sum(histogram[key] for key in box)
        # Weight palette centers by source-pixel frequency, not by occupied bins.
        red_sum = green_sum = blue_sum = 0
        for key in box:
            count = histogram[key]
            red_sum += (((key >> 10) & 31) * 255 / 31) * count
            green_sum += (((key >> 5) & 31) * 255 / 31) * count
            blue_sum += ((key & 31) * 255 / 31) * count
        color = (round(red_sum / weight), round(green_sum / weight), round(blue_sum / weight))
        colors.append(color)
        palette[index * 4:index * 4 + 4] = bytes((color[2], color[1], color[0], 255))

    lookup = bytearray(1 << 15)
    for key in range(1 << 15):
        red, green, blue = ((key >> 10) & 31), ((key >> 5) & 31), (key & 31)
        lookup[key] = min(
            range(1, len(colors) + 1),
            key=lambda index: ((red * 255 / 31 - colors[index - 1][0]) ** 2
                               + (green * 255 / 31 - colors[index - 1][1]) ** 2
                               + (blue * 255 / 31 - colors[index - 1][2]) ** 2),
        )
    return bytes(palette), bytes(lookup)


def _encode_block(indices: bytes) -> bytes:
    encoded = bytearray((0,))
    if not indices:
        raise ValueError("EAF image block is empty")
    value = indices[0]
    run = 1
    for next_value in indices[1:]:
        if next_value == value and run < 255:
            run += 1
            continue
        encoded.extend((run, value))
        value, run = next_value, 1
    encoded.extend((run, value))
    return bytes(encoded)


def _encode_frame(frame: bytes, width: int, height: int, block_height: int,
                  palette: bytes, lookup: bytes) -> bytes:
    blocks = math.ceil(height / block_height)
    block_data = []
    for block in range(blocks):
        y0 = block * block_height
        rows = min(block_height, height - y0)
        start = y0 * width * 4
        end = (y0 + rows) * width * 4
        rgba = frame[start:end]
        indices = bytearray(width * block_height)
        out = 0
        for offset in range(0, len(rgba), 4):
            alpha = rgba[offset + 3]
            if alpha < 128:
                indices[out] = 0
            else:
                key = ((rgba[offset] >> 3) << 10) | ((rgba[offset + 1] >> 3) << 5) | (rgba[offset + 2] >> 3)
                indices[out] = lookup[key]
            out += 1
        block_data.append(_encode_block(indices))

    content = bytearray(b"_S\0\0\0\0\0\0\1\x08")
    content.extend(struct.pack("<HHHH", width, height, blocks, block_height))
    content.extend(b"".join(struct.pack("<I", len(block)) for block in block_data))
    content.extend(palette)
    content.extend(b"".join(block_data))
    return b"ZZ" + content


def encode_rgba_frames(frames: Iterable[bytes], width: int, height: int,
                       *, block_height: int = 32, palette_colors: int = 255) -> bytes:
    """Encode opaque/translucent RGBA frames as current-player encoding-0 EAF."""
    frame_tuple = tuple(frames)
    if isinstance(palette_colors, bool) or not isinstance(palette_colors, int) or not 1 <= palette_colors <= 255:
        raise ValueError("palette color count must be between 1 and 255")
    if not frame_tuple or len(frame_tuple) > MAX_FRAMES:
        raise ValueError("frame count is outside the supported range")
    if not 0 < width <= MAX_DIMENSION or not 0 < height <= MAX_DIMENSION:
        raise ValueError("frame dimensions are outside the supported range")
    if len(frame_tuple) * width * height > MAX_PIXELS:
        raise ValueError("animation exceeds the E1 25-megapixel safety limit")
    if not 0 < block_height <= MAX_DIMENSION or math.ceil(height / block_height) > 256:
        raise ValueError("block height/count is outside the supported range")
    frame_bytes = width * height * 4
    if any(len(frame) != frame_bytes for frame in frame_tuple):
        raise ValueError("RGBA frame byte length does not match its dimensions")

    palette, lookup = _palette(frame_tuple, palette_colors)
    encoded_frames = [_encode_frame(frame, width, height, block_height, palette, lookup)
                      for frame in frame_tuple]
    table_size = len(encoded_frames) * 8
    offsets = []
    cursor = 0
    for frame in encoded_frames:
        offsets.append((len(frame), cursor))
        cursor += len(frame)
    payload = bytearray()
    for size, offset in offsets:
        payload.extend(struct.pack("<II", size, offset))
    payload.extend(b"".join(encoded_frames))
    if len(payload) != table_size + cursor:
        raise AssertionError("EAF payload length accounting failed")
    header = b"\x89EAF" + struct.pack("<III", len(encoded_frames), sum(payload) & 0xFFFFFFFF, len(payload))
    return header + payload
