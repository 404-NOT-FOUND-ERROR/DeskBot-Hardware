"""Resource-only adapter for the anchored legacy EAF player's exclusive end.

The player recognizes ``_C`` as a non-image flag and uses count-1 as its
exclusive endpoint. Reuse the exact terminal record from the verified official
Happy asset. Its opaque payload is preserved, not interpreted as loop settings.
This adapter targets that player revision; it is not a general EAF exporter.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from protocol.eaf_preprocess import MAX_BYTES, indexed_frames

OFFICIAL_TERMINAL = bytes.fromhex(
    "5a5a5f43000000000000010000001f000000"
    "6f6e6c696e6520636f6e7665727420302e312e345f323032352d30392d3031"
)
MAX_ENTRIES = 4096


@dataclass(frozen=True)
class Stream:
    entries: tuple[tuple[int, int], ...]
    storage: bytes
    has_terminal: bool

    @property
    def images(self) -> tuple[bytes, ...]:
        entries = self.entries[:-1] if self.has_terminal else self.entries
        return tuple(self.storage[offset:offset + length] for length, offset in entries)


def _pack(entries: tuple[tuple[int, int], ...], storage: bytes) -> bytes:
    payload = b"".join(struct.pack("<II", *row) for row in entries) + storage
    if len(payload) + 16 > MAX_BYTES:
        raise ValueError("adapted EAF exceeds byte limit")
    return b"\x89EAF" + struct.pack("<III", len(entries), sum(payload) & 0xffffffff, len(payload)) + payload


def inspect_stream(raw: bytes) -> Stream:
    """Validate bounded image entries and an optional exact final marker.

    Shared payloads are accepted. Gaps, unused suffixes, partial overlaps,
    interior/multiple flags and unrecognized metadata are rejected.
    """
    if type(raw) is not bytes or not 16 <= len(raw) <= MAX_BYTES or raw[:4] != b"\x89EAF":
        raise ValueError("bounded EAF bytes required")
    count, checksum, size = struct.unpack_from("<III", raw, 4)
    if not 0 < count <= MAX_ENTRIES or size != len(raw) - 16 or size < count * 8:
        raise ValueError("invalid EAF table/payload length")
    if sum(raw[16:]) & 0xffffffff != checksum:
        raise ValueError("EAF checksum mismatch")
    entries = tuple(struct.unpack_from("<II", raw, 16 + i * 8) for i in range(count))
    storage = raw[16 + count * 8:]
    has_terminal = False
    spans = set()
    for index, (length, offset) in enumerate(entries):
        if length < 4 or offset > len(storage) or length > len(storage) - offset:
            raise ValueError("frame outside EAF storage")
        frame = storage[offset:offset + length]
        spans.add((offset, offset + length))
        if frame[:4] == b"ZZ_C":
            if index != count - 1 or index == 0 or frame != OFFICIAL_TERMINAL or offset + length != len(storage):
                raise ValueError("only the exact official terminal at the end is supported")
            has_terminal = True
        elif frame[:4] != b"ZZ_S":
            raise ValueError("unsupported frame kind")
    cursor = 0
    for left, right in sorted(spans):
        if left != cursor:
            raise ValueError("EAF storage has a gap or partial overlap")
        cursor = right
    if cursor != len(storage):
        raise ValueError("EAF storage has unused bytes")
    stream = Stream(entries, storage, has_terminal)
    if has_terminal:
        image_raw = _pack(entries[:-1], storage[:-len(OFFICIAL_TERMINAL)])
    else:
        image_raw = raw
    # Validate dimensions, palette, compression blocks and decoded byte limits
    # before any candidate is passed to the native legacy parser.
    indexed_frames(image_raw)
    return stream


def append_official_terminal(raw: bytes) -> bytes:
    """Keep every image payload/table entry; append one 49-byte official flag.

    Already adapted input is byte-identical on repeat. No synthetic image,
    timing endpoint, palette, compressed block or source frame is changed.
    """
    stream = inspect_stream(raw)
    if stream.has_terminal:
        return raw
    if len(stream.entries) == MAX_ENTRIES:
        raise ValueError("no table slot remains for terminal")
    return _pack(stream.entries + ((len(OFFICIAL_TERMINAL), len(stream.storage)),),
                 stream.storage + OFFICIAL_TERMINAL)


def terminal_evidence() -> dict:
    return {"source_member": "Happy.eaf", "source_frame": 42,
            "source_eaf_sha256": "2ed3a64ecdb7a78e2c9959bfc3758c2742d5447d4a2bcdc6d2bf072763deba5c",
            "bytes": len(OFFICIAL_TERMINAL),
            "sha256": hashlib.sha256(OFFICIAL_TERMINAL).hexdigest(),
            "method": "copy_exact_official_opaque_terminal", "payload_interpreted": False,
            "image_bytes_added": 0, "file_growth_bytes": len(OFFICIAL_TERMINAL) + 8}
