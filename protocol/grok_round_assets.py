"""Offline circular Gray8 asset conversion using the existing EAF wire format."""
from __future__ import annotations

import struct
from collections import Counter

import numpy as np

from protocol.eaf_compression import huffman_encode
from protocol.eaf_legacy_terminal import OFFICIAL_TERMINAL

MAX_PIXELS = 25_000_000
ROUND_STATES = {
    'sleeping': '入睡', 'waking': '唤醒', 'idle': '待机', 'listening': '倾听',
    'searching': '搜寻', 'working': '工作', 'excited': '兴奋', 'surprised': '惊讶',
    'suspicious': '怀疑', 'angry': '生气', 'drowsy': '犯困', 'happy': '开心',
    'curious': '好奇', 'confused': '困惑', 'bored': '无聊', 'proud': '得意',
    'shy': '害羞', 'sad': '难过', 'laughing': '大笑', 'scared': '害怕',
    'playful': '玩耍', 'celebrate': '庆祝', 'humming': '哼唱',
}
EXCLUDED_STATES = ['thinking', 'orbit', 'radar', 'progress', 'spawning', 'dictating',
                   'writing', 'sending', 'receiving', 'uploading', 'alerting', 'powering-down']


def read_gray_stream(raw: bytes) -> tuple[np.ndarray, np.ndarray, int]:
    """Validate the desktop adapter's packed little-endian header and exact byte length."""
    if len(raw) < 20 or raw[:4] != b'GKR2':
        raise ValueError('invalid Gray8 export header')
    width, height, fps, count = struct.unpack_from('<IIII', raw, 4)
    if (width, height) != (360, 360) or not 1 <= count <= 192 or not 1 <= fps <= 60 or 60 % fps:
        raise ValueError('unsupported Gray8 dimensions, count or sampling rate')
    if len(raw) != 20 + 2 * width * height * count or width * height * count > MAX_PIXELS:
        raise ValueError('Gray8 export length or pixel budget mismatch')
    planes = np.frombuffer(raw, dtype=np.uint8, offset=20).reshape(count, 2, height, width)
    return planes[:, 0].copy(), planes[:, 1].copy(), fps


def circle_mask(width: int, height: int) -> np.ndarray:
    if type(width) is not int or type(height) is not int or not 1 <= width <= 1024 or width != height:
        raise ValueError('a bounded square canvas is required')
    y, x = np.ogrid[:height, :width]
    return (x + .5 - width / 2) ** 2 + (y + .5 - height / 2) ** 2 <= (width / 2) ** 2


def _rle(data: np.ndarray) -> bytes:
    """Split long equal-pixel runs at 255, including exact multiples and final padding."""
    values = np.asarray(data, dtype=np.uint8).reshape(-1)
    if not values.size:
        raise ValueError('empty image block')
    starts = np.r_[0, np.flatnonzero(values[1:] != values[:-1]) + 1]
    lengths = np.diff(np.r_[starts, len(values)])
    repetitions = (lengths + 254) // 255
    within = np.arange(repetitions.sum()) - np.repeat(np.cumsum(repetitions) - repetitions, repetitions)
    counts = np.minimum(255, np.repeat(lengths, repetitions) - 255 * within)
    runs = np.empty((len(counts), 2), dtype=np.uint8)
    runs[:, 0] = counts
    runs[:, 1] = np.repeat(values[starts], repetitions)
    return runs.tobytes()


def encode_gray_frames(frames: np.ndarray, alpha: np.ndarray, *, block_height: int = 32) -> tuple[bytes, dict]:
    """Encode transparent 4-bit Gray EAF, comparing RLE and Huffman per block.

    Transparent index zero preserves the round screen and opaque index one keeps black eyes.
    The source renderer's Gray8 edge coverage becomes a 15-level gray ramp.
    """
    if not isinstance(frames, np.ndarray) or frames.dtype != np.uint8 or frames.ndim != 3:
        raise ValueError('uint8 frames[count,height,width] required')
    count, height, width = frames.shape
    if (not 1 <= count < 4096 or not 1 <= width <= 1024 or not 1 <= height <= 1024
            or width % 2 or not isinstance(alpha, np.ndarray) or alpha.dtype != np.uint8
            or alpha.shape != frames.shape
            or frames.size > MAX_PIXELS or type(block_height) is not int or not 1 <= block_height <= 1024
            or (height + block_height - 1) // block_height > 256):
        raise ValueError('EAF dimensions, frame count or pixel budget exceeded')
    palette = b'\0\0\0\0' + b''.join(
        bytes((gray, gray, gray, 255)) for gray in [round((index - 1) * 255 / 14) for index in range(1, 16)])
    blocks = (height + block_height - 1) // block_height
    storage, table = bytearray(), bytearray()
    offsets: dict[bytes, tuple[int, int]] = {}
    encodings: Counter[int] = Counter()
    for image, image_alpha in zip(frames, alpha):
        encoded = []
        for row in range(blocks):
            start_y = row * block_height
            rows = min(block_height, height - start_y)
            part = np.zeros((block_height, width), dtype=np.uint8)
            gray = image[start_y:start_y + rows].astype(np.uint16)
            opacity = image_alpha[start_y:start_y + rows] > 0
            levels = ((gray * 14 + 127) // 255 + 1).astype(np.uint8)
            levels[~opacity] = 0
            part[:rows] = levels
            packed = ((part[:, 0::2] << 4) | part[:, 1::2]).astype(np.uint8)
            rle = _rle(packed)
            compressed = huffman_encode(rle)
            block = min((b'\x00' + rle, b'\x01' + compressed), key=len)
            encodings[block[0]] += 1
            encoded.append(block)
        frame = (b'ZZ_S\0\0\0\0\0\0\x01\x04' +
                 struct.pack('<HHHH', width, height, blocks, block_height) +
                 b''.join(struct.pack('<I', len(block)) for block in encoded) + palette + b''.join(encoded))
        location = offsets.get(frame)
        if location is None:
            location = len(frame), len(storage)
            offsets[frame] = location
            storage.extend(frame)
        table.extend(struct.pack('<II', *location))
    # The current player's exclusive image endpoint needs its existing opaque final record.
    table.extend(struct.pack('<II', len(OFFICIAL_TERMINAL), len(storage)))
    storage.extend(OFFICIAL_TERMINAL)
    payload = table + storage
    raw = b'\x89EAF' + struct.pack('<III', count + 1, sum(payload) & 0xffffffff, len(payload)) + payload
    return raw, {'image_frames': count, 'stored_unique_images': len(offsets),
                 'metadata_frames': 1, 'block_encodings': dict(encodings), 'bit_depth': 4,
                 'palette_levels': 15, 'transparent_index': 0, 'opaque_black_index': 1}


def expected_rgba(frames: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Expand each 4-bit gray level as the current RGB565 display path does."""
    levels = ((frames.astype(np.uint16) * 14 + 127) // 255 + 1).astype(np.uint8)
    levels[alpha == 0] = 0
    colors = np.array([0] + [round((index - 1) * 255 / 14) for index in range(1, 16)], dtype=np.uint16)
    values = colors[levels]
    r5, g6 = values >> 3, values >> 2
    result = np.empty((*frames.shape, 4), dtype=np.uint8)
    result[..., 0] = result[..., 2] = (r5 << 3) | (r5 >> 2)
    result[..., 1] = (g6 << 2) | (g6 >> 4)
    result[..., 3] = (levels != 0) * 255
    return result
