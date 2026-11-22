"""DeskBot's offline full-face adapter for the reviewed AirBoat animation set."""
from __future__ import annotations

import hashlib
import struct

import numpy as np
from PIL import Image

from protocol.eaf_compression import huffman_encode
from protocol.eaf_legacy_terminal import OFFICIAL_TERMINAL
from protocol.grok_round_assets import _rle

SIDE = 360
FPS = 20
COLORS = 8
ROLE_ID = "airboat-face-v1"
LABELS = dict(neutral="平静", idle="待机", listening="倾听", thinking="思考", happy="开心",
    surprised="惊讶", shocked="震惊", question="疑问", look_around="张望", winking="眨眼",
    beat_swing="摇摆", confused="困惑", relaxed="放松", sleepy="困倦", laughing="大笑",
    funny="逗趣", loving="喜爱", embarrassed="害羞", confident="自信", delicious="美味",
    sad="难过", crying="哭泣", silly="呆萌", angry="生气", insert="探头", eat="进食",
    look_left="看左边", look_right="看右边", tried="疲倦")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def image_record(image):
    """Quantize once per image; use the current decoder's packed 4-bit RLE/Huffman."""
    if image.size != (SIDE, SIDE):
        raise ValueError("full-face frames must be 360 by 360")
    indexed = image.convert("RGB").quantize(colors=COLORS, dither=Image.Dither.NONE)
    colors = indexed.getpalette()
    colors += [0] * (48-len(colors))  # The 4-bit container always carries 16 palette slots.
    palette = b"".join(bytes((colors[i+2], colors[i+1], colors[i], 255))
                       for i in range(0, 48, 3))
    pixels = np.asarray(indexed, dtype=np.uint8)
    blocks = []
    for y in range(0, SIDE, 32):
        part = np.zeros((32, SIDE), dtype=np.uint8)
        part[:min(32, SIDE-y)] = pixels[y:y+32]
        packed = (part[:, ::2] << 4) | part[:, 1::2]
        rle = _rle(packed)
        blocks.append(min((b"\0"+rle, b"\1"+huffman_encode(rle),
                           b"\3"+huffman_encode(packed.tobytes())), key=len))
    return (b"ZZ_S\0\0\0\0\0\0\1\4" + struct.pack("<4H", SIDE, SIDE, len(blocks), 32)
            + b"".join(struct.pack("<I", len(b)) for b in blocks) + palette + b"".join(blocks))


def container(records):
    """Keep every timeline entry and append the player's exclusive-end marker."""
    records = tuple(records)
    if not 1 <= len(records) <= 192 or any(r[:4] != b"ZZ_S" for r in records):
        raise ValueError("one to 192 image records required")
    table, storage, seen = bytearray(), bytearray(), {}
    for record in (*records, OFFICIAL_TERMINAL):
        if record not in seen:
            seen[record] = len(storage)
            storage.extend(record)
        table.extend(struct.pack("<II", len(record), seen[record]))
    body = table + storage
    return b"\x89EAF" + struct.pack("<III", len(records)+1, sum(body)&0xffffffff, len(body)) + body


def normalize_seams(entry, loop, exit_, home):
    """Shared pixel endpoints avoid jumps across phases and between expressions."""
    entry, loop, exit_ = list(entry), list(loop), list(exit_)
    if min(map(len, (entry, loop, exit_))) < 2:
        raise ValueError("each phase needs at least two frames")
    loop[-1] = loop[0]
    entry[0], entry[-1] = home, loop[0]
    exit_[0], exit_[-1] = loop[-1], home
    return dict(entry=entry, loop=loop, exit=exit_)
