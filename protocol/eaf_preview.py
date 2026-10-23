"""Bounded host EAF decoder: indexed 4/8-bit RLE and Huffman 0/1/3."""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from protocol.eaf_compression import huffman_decode


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_FRAMES = 4096
MAX_DIMENSION = 1024
MAX_ATLAS_PIXELS = 25_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class DecodedAnimation:
    width: int
    height: int
    bit_depth: int
    frame_rate: int
    frames: tuple[bytes, ...]
    stored_frame_count: int
    metadata_frame_count: int


def compose_display_frame(frame: bytes, width: int, height: int,
                          display_width: int = 360, display_height: int = 360,
                          *, object_kind: str = "eye") -> bytes:
    """Compose a frame using the official 360x360 layout geometry and clipping."""
    if len(frame) != width * height * 4:
        raise ValueError("RGBA frame size does not match its dimensions")
    if width <= 0 or height <= 0 or display_width <= 0 or display_height <= 0:
        raise ValueError("frame and display dimensions must be positive")
    canvas = bytearray(display_width * display_height * 4)
    if object_kind == "listen":
        x, y, mirror = (display_width - width) // 2, 25, False
    elif object_kind in ("dialog", "full_screen"):
        x, y, mirror = (display_width - width) // 2, (display_height - height) // 2, False
    elif object_kind == "eye":
        # Official eye_anim: LEFT_MID + (10, 10) + auto mirror. The gfx
        # component expands the object's geometry to keep 10 px side margins.
        x, y, mirror = 10, (display_height - height) // 2 + 10, True
    else:
        raise ValueError("object_kind must be eye, dialog, listen, or full_screen")

    object_width = display_width - 2 * x if mirror else width
    mirror_offset = object_width - 2 * width if mirror else 0
    visible_y0 = max(0, y)
    visible_y1 = min(display_height, y + height)
    visible_x0 = max(0, x)
    visible_x1 = min(display_width, x + object_width)
    if visible_x0 >= visible_x1 or visible_y0 >= visible_y1:
        return bytes(canvas)

    for screen_y in range(visible_y0, visible_y1):
        source_y = screen_y - y
        source_row = source_y * width * 4
        target_row = screen_y * display_width * 4
        # The C renderer writes each source pixel followed by its mirror.
        for source_x in range(min(width, object_width)):
            source = source_row + source_x * 4
            if not frame[source + 3]:
                continue
            screen_x = x + source_x
            if visible_x0 <= screen_x < visible_x1:
                target = target_row + screen_x * 4
                canvas[target:target + 4] = frame[source:source + 4]
            if mirror:
                mirrored_x = width + mirror_offset + width - 1 - source_x
                screen_x = x + mirrored_x
                if visible_x0 <= screen_x < visible_x1 and 0 <= mirrored_x < object_width:
                    target = target_row + screen_x * 4
                    canvas[target:target + 4] = frame[source:source + 4]
    return bytes(canvas)


def _rle_decode(payload: bytes, expected_size: int, label: str, valid_size: int | None = None) -> bytes:
    if len(payload) % 2:
        raise ValueError(f"{label}: RLE payload has an incomplete pair")
    out = bytearray()
    for pos in range(0, len(payload), 2):
        count, value = payload[pos], payload[pos + 1]
        if count == 0 or count > expected_size - len(out):
            raise ValueError(f"{label}: RLE run exceeds the decoded block size")
        out.extend(bytes((value,)) * count)
    if len(out) not in {expected_size, valid_size if valid_size is not None else expected_size}:
        raise ValueError(f"{label}: RLE block decoded to {len(out)} bytes, expected {expected_size}")
    return bytes(out)


def decode_eaf(path: Path, *, fps: int = 20) -> DecodedAnimation:
    """Decode indexed frames for offline preview; this is not device validation."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("EAF file exceeds the 32 MiB preview limit")
    raw = path.read_bytes()
    if len(raw) < 16 or raw[0] != 0x89 or raw[1:4] not in (b"EAF", b"AAF"):
        raise ValueError("invalid EAF magic/header")
    frame_count, checksum, payload_len = struct.unpack_from("<III", raw, 4)
    if not 0 < frame_count <= MAX_FRAMES:
        raise ValueError("EAF frame count is outside the supported range")
    table_end = 16 + frame_count * 8
    payload_end = 16 + payload_len
    if payload_len < frame_count * 8 or table_end > len(raw) or payload_end != len(raw):
        raise ValueError("EAF table or payload length is out of bounds")
    payload = raw[16:payload_end]
    if sum(payload) & 0xFFFFFFFF != checksum:
        raise ValueError("EAF checksum mismatch")

    frame_data_start = table_end
    ranges: list[tuple[int, int]] = []
    decoded: list[bytes] = []
    first_header: tuple[int, int, int] | None = None
    metadata_count = 0
    for index in range(frame_count):
        size, offset = struct.unpack_from("<II", raw, 16 + index * 8)
        frame_region_size = payload_end - frame_data_start
        if size < 4 or offset > frame_region_size or size > frame_region_size - offset:
            raise ValueError(f"EAF frame {index} is out of bounds")
        end = offset + size
        for previous_start, previous_end in ranges:
            overlaps = offset < previous_end and previous_start < end
            if overlaps and (offset, end) != (previous_start, previous_end):
                raise ValueError(f"EAF frame {index} partially overlaps another frame")
        ranges.append((offset, end))
        frame = raw[frame_data_start + offset:frame_data_start + end]
        if frame[:2] != b"ZZ":
            raise ValueError(f"EAF frame {index} does not start with ZZ")
        content = frame[2:]
        if content[:2] == b"_C":
            metadata_count += 1
            continue
        if content[:2] != b"_S":
            raise ValueError(f"EAF frame {index} has an unsupported frame kind")
        if len(content) < 18:
            raise ValueError(f"EAF frame {index} has a truncated image header")
        bit_depth = content[9]
        width, height, blocks, block_height = struct.unpack_from("<HHHH", content, 10)
        if bit_depth not in (4, 8):
            raise ValueError(f"EAF frame {index} uses unsupported {bit_depth}-bit pixels")
        if bit_depth == 4 and width % 2:
            raise ValueError(f"EAF frame {index}: packed 4-bit width must be even")
        if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION
                and 0 < blocks <= 256 and 0 < block_height <= MAX_DIMENSION):
            raise ValueError(f"EAF frame {index} dimensions/block table are invalid")
        if blocks * block_height < height or (blocks - 1) * block_height >= height:
            raise ValueError(f"EAF frame {index} block heights do not cover the image")
        if first_header is None:
            first_header = (width, height, bit_depth)
        elif first_header != (width, height, bit_depth):
            raise ValueError("EAF animation changes frame dimensions or bit depth")
        if (len(decoded) + 1) * width * height > MAX_ATLAS_PIXELS:
            raise ValueError("decoded animation exceeds the 25-megapixel preview limit")

        palette_start = 18 + blocks * 4
        palette_end = palette_start + (1 << bit_depth) * 4
        if palette_end > len(content):
            raise ValueError(f"EAF frame {index} block table or palette is truncated")
        lengths = struct.unpack_from("<" + "I" * blocks, content, 18)
        data_start = palette_end
        data_end = data_start + sum(lengths)
        if palette_end > len(content) or data_end != len(content):
            raise ValueError(f"EAF frame {index} block data or palette is truncated")
        palette = content[palette_start:palette_end]
        # Match the device's RGB888 -> RGB565 conversion and then expand to
        # browser RGBA. Palette entries are BGRA, with all-zero transparent.
        colors = []
        for pos in range(0, len(palette), 4):
            b, g, r, alpha = palette[pos:pos + 4]
            if (b, g, r, alpha) == (0, 0, 0, 0):
                colors.append(b"\0\0\0\0")
            else:
                r5, g6, b5 = r >> 3, g >> 2, b >> 3
                colors.append(bytes(((r5 << 3) | (r5 >> 2),
                                     (g6 << 2) | (g6 >> 4),
                                     (b5 << 3) | (b5 >> 2), 255)))
        pixels = bytearray(width * height * 4)
        cursor = data_start
        for block_index, block_length in enumerate(lengths):
            if block_length < 2:
                raise ValueError(f"EAF frame {index} has an empty/short block")
            block = content[cursor:cursor + block_length]
            cursor += block_length
            y0 = block_index * block_height
            rows = min(block_height, height - y0)
            packed_divisor = 2 if bit_depth == 4 else 1
            expected, valid = width * block_height // packed_divisor, width * rows // packed_divisor
            label = f"EAF frame {index}, block {block_index}"
            if block[0] == 0:
                indices = _rle_decode(block[1:], expected, label, valid)
            elif block[0] == 1:
                rle = huffman_decode(block[1:], expected * 2)
                indices = _rle_decode(rle, expected, label, valid)
            elif block[0] == 3:
                indices = huffman_decode(block[1:], expected)
                if len(indices) not in (expected, valid):
                    raise ValueError(f"{label}: Huffman block has an invalid decoded length")
            else:
                raise ValueError(f"EAF frame {index} uses unsupported block encoding {block[0]}")
            if bit_depth == 4:
                indices = bytes(value for packed in indices for value in (packed >> 4, packed & 15))
            for row in range(rows):
                source_row = indices[row * width:(row + 1) * width]
                out = (y0 + row) * width * 4
                pixels[out:out + width * 4] = b"".join(colors[index] for index in source_row)
        decoded.append(bytes(pixels))

    if not decoded or first_header is None:
        raise ValueError("EAF contains no drawable image frames")
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 120:
        raise ValueError("frame rate must be between 1 and 120")
    return DecodedAnimation(*first_header, fps, tuple(decoded), frame_count, metadata_count)


def _png_chunk(stream: BinaryIO, kind: bytes, data: bytes) -> None:
    stream.write(struct.pack(">I", len(data)))
    stream.write(kind)
    stream.write(data)
    stream.write(struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def write_rgba_atlas(path: Path, frames: tuple[bytes, ...], width: int, height: int,
                     columns: int = 8) -> dict[str, int]:
    """Write a lossless RGBA PNG sprite atlas without image-library dependencies."""
    frame_count = len(frames)
    if not frame_count or width <= 0 or height <= 0:
        raise ValueError("sprite atlas needs positive dimensions and at least one frame")
    if any(len(frame) != width * height * 4 for frame in frames):
        raise ValueError("sprite atlas frame does not match RGBA dimensions")
    columns = max(1, min(columns, frame_count))
    rows = (frame_count + columns - 1) // columns
    atlas_width = width * columns
    atlas_height = height * rows
    if atlas_width * atlas_height > MAX_ATLAS_PIXELS:
        raise ValueError("sprite atlas exceeds the 25-megapixel safety limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    compressor = zlib.compressobj(level=7)
    compressed = bytearray()
    zero_frame = bytes(width * 4)
    for atlas_y in range(atlas_height):
        frame_row, pixel_y = divmod(atlas_y, height)
        scanline = bytearray(b"\0")
        for col in range(columns):
            frame_index = frame_row * columns + col
            if frame_index >= frame_count:
                scanline.extend(zero_frame)
                continue
            frame = frames[frame_index]
            start = pixel_y * width * 4
            scanline.extend(frame[start:start + width * 4])
        compressed.extend(compressor.compress(scanline))
    compressed.extend(compressor.flush())
    with path.open("wb") as stream:
        stream.write(PNG_SIGNATURE)
        _png_chunk(stream, b"IHDR", struct.pack(">IIBBBBB", atlas_width, atlas_height,
                                                     8, 6, 0, 0, 0))
        _png_chunk(stream, b"IDAT", bytes(compressed))
        _png_chunk(stream, b"IEND", b"")
    return {"width": atlas_width, "height": atlas_height, "columns": columns, "rows": rows,
            "frame_width": width, "frame_height": height, "frame_count": frame_count}


def write_sprite_atlas(path: Path, animation: DecodedAnimation, columns: int = 8) -> dict[str, int]:
    """Backward-compatible atlas writer for raw animation frames."""
    return write_rgba_atlas(path, animation.frames, animation.width, animation.height, columns)
