"""Bounded host-only EAF Huffman codecs and lossless block transcoding.

Wire format follows esp_emote_gfx 2.1.0 gfx_eaf_dec.c. This module does
not import a serial/flashing API or change any device player code.
"""
from __future__ import annotations

import heapq
import struct
from collections import Counter

MAX_HUFFMAN_OUTPUT_BYTES = 2_097_152
MAX_HUFFMAN_INPUT_BYTES = MAX_HUFFMAN_OUTPUT_BYTES + 2_561 + 2
MAX_ANIMATION_PIXELS = 25_000_000


def huffman_encode(data: bytes) -> bytes:
    if not data:
        raise ValueError("Huffman input must not be empty")
    if len(data) > MAX_HUFFMAN_OUTPUT_BYTES:
        raise ValueError("Huffman input exceeds the supported byte limit")
    heap = [(count, symbol, symbol) for symbol, count in sorted(Counter(data).items())]
    heapq.heapify(heap)
    serial = 256
    while len(heap) > 1:
        left, right = heapq.heappop(heap), heapq.heappop(heap)
        heapq.heappush(heap, (left[0] + right[0], serial, (left[2], right[2])))
        serial += 1
    codes: dict[int, tuple[int, int]] = {}

    def visit(node, value: int, length: int) -> None:
        if isinstance(node, int):
            codes[node] = (value, max(1, length))
        else:
            visit(node[0], value << 1, length + 1)
            visit(node[1], (value << 1) | 1, length + 1)

    visit(heap[0][2], 0, 0)
    if max(length for _, length in codes.values()) > 64:
        raise ValueError("Huffman code exceeds the device's uint64 code field")
    encoded = bytearray()
    accumulator = bits = 0
    for symbol in data:
        code, length = codes[symbol]
        accumulator = (accumulator << length) | code
        bits += length
        while bits >= 8:
            bits -= 8
            encoded.append((accumulator >> bits) & 255)
        accumulator &= (1 << bits) - 1
    padding = (8 - bits) % 8
    if bits:
        encoded.append(accumulator << padding)
    dictionary = bytearray((padding,))
    for symbol, (code, length) in sorted(codes.items()):
        dictionary.extend((symbol, length))
        dictionary.extend(code.to_bytes((length + 7) // 8, "big"))
    # Always write explicit symbol bits, including single-color blocks. The
    # device's zero-stream shortcut fills its advertised capacity; for 4-bit
    # gfx_anim that capacity is larger than its actual packed allocation.
    return struct.pack("<H", len(dictionary)) + dictionary + encoded


def huffman_decode(data: bytes, capacity: int, *, allow_single_fill: bool = True) -> bytes:
    if (len(data) < 3 or len(data) > MAX_HUFFMAN_INPUT_BYTES
            or capacity <= 0 or capacity > MAX_HUFFMAN_OUTPUT_BYTES):
        raise ValueError("Huffman header or output capacity is invalid")
    dictionary_size = struct.unpack_from("<H", data)[0]
    if dictionary_size < 1 or dictionary_size > 2561 or 2 + dictionary_size > len(data):
        raise ValueError("Huffman dictionary length is invalid")
    dictionary = data[2:2 + dictionary_size]
    padding = dictionary[0]
    if padding > 7:
        raise ValueError("Huffman padding exceeds seven bits")
    tree: dict = {}
    symbols = set()
    cursor = 1
    while cursor < len(dictionary):
        if cursor + 2 > len(dictionary):
            raise ValueError("Huffman dictionary entry is truncated")
        symbol, length = dictionary[cursor:cursor + 2]
        cursor += 2
        byte_length = (length + 7) // 8
        if length == 0 or length > 64 or cursor + byte_length > len(dictionary) or symbol in symbols:
            raise ValueError("Huffman code length or duplicate symbol is invalid")
        code = int.from_bytes(dictionary[cursor:cursor + byte_length], "big")
        cursor += byte_length
        if code >= (1 << length):
            raise ValueError("Huffman code does not fit declared bit length")
        symbols.add(symbol)
        node = tree
        for shift in range(length - 1, -1, -1):
            if "symbol" in node:
                raise ValueError("Huffman prefix collision")
            node = node.setdefault((code >> shift) & 1, {})
        if node:
            raise ValueError("Huffman prefix collision")
        node["symbol"] = symbol
    encoded = data[2 + dictionary_size:]
    if not encoded:
        if not allow_single_fill or len(symbols) != 1 or padding:
            raise ValueError("Huffman empty stream is not an allowed single-symbol block")
        return bytes((next(iter(symbols)),)) * capacity
    if not symbols or "symbol" in tree:
        raise ValueError("Huffman stream needs nonzero-length codes")
    if padding and encoded[-1] & ((1 << padding) - 1):
        raise ValueError("Huffman padding bits must be zero")
    result = bytearray()
    node = tree
    for bit in range(len(encoded) * 8 - padding):
        node = node.get((encoded[bit // 8] >> (7 - bit % 8)) & 1)
        if node is None:
            raise ValueError("Huffman stream follows an invalid code path")
        if "symbol" in node:
            if len(result) >= capacity:
                raise ValueError("Huffman output exceeds decoded block size")
            result.append(node["symbol"])
            node = tree
    if node is not tree:
        raise ValueError("Huffman stream ends with a truncated code")
    return bytes(result)


def encode_block(indices: bytes, encoding: int | str = "best") -> bytes:
    from protocol.eaf_encode import _encode_block
    if not indices or len(indices) > MAX_HUFFMAN_OUTPUT_BYTES:
        raise ValueError("EAF block input is outside the supported range")
    if encoding != "best" and (type(encoding) is not int or encoding not in (0, 1, 3)):
        raise ValueError("E1.3b permits only encoding 0, 1, 3, or best")
    rle = _encode_block(indices)
    if encoding == 0:
        return rle
    if encoding == 1:
        return b"\x01" + huffman_encode(rle[1:])
    if encoding == 3:
        return b"\x03" + huffman_encode(indices)
    if encoding != "best":
        raise ValueError("E1.3b permits only encoding 0, 1, 3, or best")
    return min((rle, b"\x01" + huffman_encode(rle[1:]),
                b"\x03" + huffman_encode(indices)), key=lambda value: (len(value), value[0]))


def transcode_legacy_eaf(raw: bytes, *, encoding: int | str = "best",
                         bit_depth: int = 8, deduplicate: bool = True) -> bytes:
    """Re-encode accepted legacy RLE frames; 4-bit needs <=16 used colors.

    Preserve table order, dimensions, per-frame palette color values and block
    heights. In 4-bit output remap only used palette slots, without quantizing.
    Validate the source before decoding; this is not a device playback check.
    """
    from protocol.eaf_preview import _rle_decode
    if (len(raw) < 16 or len(raw) > 32 * 1024 * 1024 or type(bit_depth) is not int or bit_depth not in (4, 8)
            or raw[:4] != b"\x89EAF"):
        raise ValueError("transcoder requires EAF input and 4/8-bit indexed output")
    if encoding != "best" and (type(encoding) is not int or encoding not in (0, 1, 3)):
        raise ValueError("E1.3b permits only encoding 0, 1, 3, or best")
    count = struct.unpack_from("<I", raw, 4)[0]
    payload_length = struct.unpack_from("<I", raw, 12)[0]
    if (not 0 < count <= 4096 or payload_length != len(raw) - 16
            or len(raw) < 16 + count * 8):
        raise ValueError("transcoder frame table is invalid")
    if sum(raw[16:]) & 0xffffffff != struct.unpack_from("<I", raw, 8)[0]:
        raise ValueError("transcoder source checksum or payload length is invalid")
    table, storage = bytearray(), bytearray()
    offsets: dict[bytes, tuple[int, int]] = {}
    frame_ranges: list[tuple[int, int]] = []
    storage_size = payload_length - count * 8
    dimensions = None
    for index in range(count):
        size, offset = struct.unpack_from("<II", raw, 16 + index * 8)
        if size < 4 or offset > storage_size or size > storage_size - offset:
            raise ValueError("transcoder frame table offset is invalid")
        frame_end = offset + size
        for previous_start, previous_end in frame_ranges:
            overlaps = offset < previous_end and previous_start < frame_end
            if overlaps and (offset, frame_end) != (previous_start, previous_end):
                raise ValueError("transcoder source frames partially overlap")
        frame_ranges.append((offset, frame_end))
        start = 16 + count * 8 + offset
        frame = raw[start:start + size]
        if len(frame) < 20 or frame[:4] != b"ZZ_S" or frame[11] != 8:
            raise ValueError("transcoder only accepts 8-bit image frames")
        content = frame[2:]
        width, height, blocks, block_height = struct.unpack_from("<HHHH", content, 10)
        if not (0 < width <= 1024 and 0 < height <= 1024 and 0 < blocks <= 256
                and 0 < block_height <= 1024 and (blocks - 1) * block_height < height <= blocks * block_height):
            raise ValueError("transcoder dimensions/block table are invalid")
        if width * block_height > MAX_HUFFMAN_OUTPUT_BYTES:
            raise ValueError("transcoder block exceeds the supported decoded size")
        if dimensions is None:
            dimensions = (width, height)
        elif dimensions != (width, height):
            raise ValueError("transcoder source changes frame dimensions")
        if count * width * height > MAX_ANIMATION_PIXELS:
            raise ValueError("transcoder animation exceeds the 25-megapixel limit")
        if bit_depth == 4 and width % 2:
            raise ValueError("packed 4-bit frames require even width")
        palette_start = 18 + 4 * blocks
        if palette_start + 1024 > len(content):
            raise ValueError("transcoder source block table or palette is truncated")
        lengths = struct.unpack_from("<" + "I" * blocks, content, 18)
        palette = content[palette_start:palette_start + 1024]
        cursor = palette_start + 1024
        if (len(palette) != 1024 or any(length < 3 for length in lengths)
                or cursor + sum(lengths) != len(content)):
            raise ValueError("transcoder source block lengths are invalid")
        decoded = []
        for block_index, length in enumerate(lengths):
            block = content[cursor:cursor + length]
            cursor += length
            if length < 3 or block[0] != 0:
                raise ValueError("transcoder source must use legacy RLE")
            rows = min(block_height, height - block_index * block_height)
            indices = _rle_decode(block[1:], width * block_height, "transcoder", width * rows)
            # Keep partial-block padding explicit, so all device paths decode
            # a full block and never read an uninitialized bottom-row tail.
            decoded.append(indices + bytes(width * block_height - len(indices)))
        if bit_depth == 4:
            used = sorted(set(b"".join(decoded)))
            if len(used) > 16:
                raise ValueError("4-bit conversion would require color quantization")
            lookup = {value: position for position, value in enumerate(used)}
            palette = b"".join(palette[value * 4:value * 4 + 4] for value in used).ljust(64, b"\0")
            decoded = [bytes((lookup[block[pos]] << 4) | lookup[block[pos + 1]]
                             for pos in range(0, len(block), 2)) for block in decoded]
        encoded_blocks = [encode_block(block, encoding) for block in decoded]
        header = bytearray(content[:18])
        header[9] = bit_depth
        new_frame = (b"ZZ" + header + b"".join(struct.pack("<I", len(block)) for block in encoded_blocks)
                     + palette + b"".join(encoded_blocks))
        location = offsets.get(new_frame) if deduplicate else None
        if location is None:
            location = len(new_frame), len(storage)
            offsets[new_frame] = location
            storage.extend(new_frame)
        table.extend(struct.pack("<II", *location))
    payload = table + storage
    return raw[:4] + struct.pack("<III", count, sum(payload) & 0xffffffff, len(payload)) + payload
