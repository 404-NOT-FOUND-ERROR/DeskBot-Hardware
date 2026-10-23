"""Offline decoder and replay helpers for the ESP-VoCat ``0x06`` telemetry.

The production firmware owns the UART parser.  This module intentionally does
not open a serial port or replace that parser; it is a small, deterministic
host-side oracle for recorded frames and log callbacks.  The wire payload is::

    AA 55 00 08 06 KIND X_H X_L Y_H Y_L Z_H Z_L CHECKSUM

``LEN`` includes the command byte.  XYZ are big-endian signed int16 values.
No device timestamp is present, so freshness is derived only from a supplied
host receive time and an explicit as-of time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import re
from typing import Iterable, Iterator


HEADER = b"\xAA\x55"
MAGNETIC_COMMAND = 0x06
MAGNETIC_DATA_LENGTH = 7
MAGNETIC_PAYLOAD_LENGTH = 1 + MAGNETIC_DATA_LENGTH
MAX_PAYLOAD_LENGTH = 250
FRAME_OVERHEAD = 5  # header + uint16 length + checksum
KIND_NAMES = {0: "unknown", 1: "BMM150", 2: "QMC6309", 3: "SS49E"}


class TelemetryError(ValueError):
    """Raised when one supplied frame/payload is not a valid magnetic sample."""


def checksum(payload: bytes) -> int:
    return sum(payload) & 0xFF


def _ensure_bytes(value: bytes | bytearray | memoryview | Iterable[int]) -> bytes:
    try:
        result = bytes(value)
    except Exception as exc:  # pragma: no cover - defensive error wording
        raise TelemetryError("value must be bytes-like") from exc
    return result


def build_frame(kind: int, x: int, y: int, z: int) -> bytes:
    """Build a canonical frame for a decoded sample (used only for replay)."""

    if not 0 <= kind <= 0xFF:
        raise TelemetryError("kind must be an unsigned byte")
    axes = []
    for name, value in (("x", x), ("y", y), ("z", z)):
        if not isinstance(value, int) or not -32768 <= value <= 32767:
            raise TelemetryError(f"{name} must be a signed int16")
        axes.append(value.to_bytes(2, "big", signed=True))
    payload = bytes((MAGNETIC_COMMAND, kind)) + b"".join(axes)
    return HEADER + len(payload).to_bytes(2, "big") + payload + bytes((checksum(payload),))


def _parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TelemetryError(f"invalid ISO-8601 host timestamp: {value!r}") from exc
    else:
        raise TelemetryError("host timestamp must be ISO text, datetime, or None")
    if result.tzinfo is None:
        raise TelemetryError("host timestamp requires an explicit timezone")
    return result.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _freshness(received: datetime | None, as_of: datetime | None, threshold_s: float) -> tuple[str, float | None]:
    if not math.isfinite(threshold_s) or threshold_s < 0:
        raise TelemetryError("freshness threshold must be finite and non-negative")
    if received is None or as_of is None:
        return "unknown", None
    age = (as_of - received).total_seconds()
    if age < 0:
        return "future", age
    return ("fresh" if age <= threshold_s else "stale"), age


@dataclass(frozen=True)
class MagneticSample:
    """One valid ``0x06`` sample and provenance needed to audit it."""

    kind: int
    kind_name: str
    kind_known: bool
    xyz_available: bool
    units: str
    x: int
    y: int
    z: int
    payload_hex: str
    frame_hex: str
    source: str  # checksum_verified | callback_reconstructed
    checksum_valid: bool | None  # callback logs do not carry the wire checksum
    host_received_at: str | None
    host_time_source: str | None
    freshness: str
    age_seconds: float | None
    raw_line: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def decode_payload(
    payload: bytes | bytearray | memoryview,
    *,
    source: str = "callback_reconstructed",
    host_received_at: str | datetime | None = None,
    host_time_source: str | None = None,
    as_of: str | datetime | None = None,
    freshness_threshold_s: float = 1.0,
    raw_line: str | None = None,
    _validated: bool = False,
) -> MagneticSample:
    """Decode the command-plus-data payload from a validated callback.

    Callback logs do not contain AA55 framing or a transmitted checksum.  They
    are therefore explicitly marked ``callback_reconstructed`` and the frame
    hex is a canonical reconstruction, never claimed to be an observed wire
    frame. ``source='checksum_verified'`` is reserved for callers that have
    already run :func:`decode_frame`; this function itself does not validate a
    checksum because callback payloads do not carry one.
    """

    raw = _ensure_bytes(payload)
    if len(raw) != MAGNETIC_PAYLOAD_LENGTH:
        raise TelemetryError(f"magnetic payload must be 8 bytes including cmd, got {len(raw)}")
    if raw[0] != MAGNETIC_COMMAND:
        raise TelemetryError(f"expected command 0x06, got 0x{raw[0]:02X}")
    if source not in {"callback_reconstructed", "checksum_verified"}:
        raise TelemetryError("source must be callback_reconstructed or checksum_verified")
    if source == "checksum_verified" and not _validated:
        raise TelemetryError("checksum_verified provenance is reserved for decode_frame")
    kind = raw[1]
    x = int.from_bytes(raw[2:4], "big", signed=True)
    y = int.from_bytes(raw[4:6], "big", signed=True)
    z = int.from_bytes(raw[6:8], "big", signed=True)
    frame = HEADER + len(raw).to_bytes(2, "big") + raw + bytes((checksum(raw),))
    received = _parse_iso(host_received_at)
    current = _parse_iso(as_of)
    freshness, age = _freshness(received, current, freshness_threshold_s)
    return MagneticSample(
        kind=kind,
        kind_name=KIND_NAMES.get(kind, "unknown"),
        kind_known=kind in (1, 2, 3),
        xyz_available=kind in (1, 2),
        units="transport_counts",
        x=x,
        y=y,
        z=z,
        payload_hex=raw.hex().upper(),
        frame_hex=frame.hex().upper(),
        source=source,
        checksum_valid=True if source == "checksum_verified" else None,
        host_received_at=_iso(received),
        host_time_source=host_time_source,
        freshness=freshness,
        age_seconds=age,
        raw_line=raw_line,
    )


def decode_frame(
    frame: bytes | bytearray | memoryview,
    *,
    host_received_at: str | datetime | None = None,
    host_time_source: str | None = None,
    as_of: str | datetime | None = None,
    freshness_threshold_s: float = 1.0,
    raw_line: str | None = None,
) -> MagneticSample | None:
    """Validate one complete AA55 frame; return a sample only for command 06."""

    raw = _ensure_bytes(frame)
    if len(raw) < FRAME_OVERHEAD or raw[:2] != HEADER:
        raise TelemetryError("invalid AA55 frame header or truncated frame")
    length = int.from_bytes(raw[2:4], "big")
    if not 1 <= length <= MAX_PAYLOAD_LENGTH:
        raise TelemetryError(f"invalid payload length {length}")
    total = 4 + length + 1
    if len(raw) != total:
        raise TelemetryError(f"frame length mismatch: header says {total}, got {len(raw)}")
    payload = raw[4 : 4 + length]
    if raw[-1] != checksum(payload):
        raise TelemetryError("checksum mismatch")
    if payload[0] != MAGNETIC_COMMAND:
        return None
    if len(payload) != MAGNETIC_PAYLOAD_LENGTH:
        raise TelemetryError(f"0x06 payload must be 8 bytes including cmd, got {len(payload)}")
    return decode_payload(
        payload,
        source="checksum_verified",
        host_received_at=host_received_at,
        host_time_source=host_time_source,
        as_of=as_of,
        freshness_threshold_s=freshness_threshold_s,
        raw_line=raw_line,
        _validated=True,
    )


@dataclass
class ParseStats:
    bytes_seen: int = 0
    frames_seen: int = 0
    frames_accepted: int = 0
    non_magnetic_frames: int = 0
    rejected_checksum: int = 0
    rejected_length: int = 0
    rejected_header: int = 0
    rejected_payload: int = 0
    incomplete_tail_bytes: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class FrameStreamParser:
    """Incremental AA55 parser for offline split/粘包 replay only."""

    def __init__(self, *, freshness_threshold_s: float = 1.0) -> None:
        if not math.isfinite(freshness_threshold_s) or freshness_threshold_s < 0:
            raise ValueError("freshness threshold must be finite and non-negative")
        self._buffer = bytearray()
        self.stats = ParseStats()
        self.freshness_threshold_s = freshness_threshold_s

    def feed(
        self,
        chunk: bytes | bytearray | memoryview,
        *,
        host_received_at: str | datetime | None = None,
        host_time_source: str | None = None,
        as_of: str | datetime | None = None,
        raw_line: str | None = None,
    ) -> list[MagneticSample]:
        data = _ensure_bytes(chunk)
        self.stats.bytes_seen += len(data)
        self._buffer.extend(data)
        samples: list[MagneticSample] = []
        while True:
            offset = self._buffer.find(HEADER)
            if offset < 0:
                if self._buffer[-1:] == HEADER[:1]:
                    self._buffer[:] = HEADER[:1]
                else:
                    self._buffer.clear()
                return samples
            if offset:
                self.stats.rejected_header += offset
                del self._buffer[:offset]
            if len(self._buffer) < 4:
                return samples
            length = int.from_bytes(self._buffer[2:4], "big")
            if not 1 <= length <= MAX_PAYLOAD_LENGTH:
                self.stats.rejected_length += 1
                del self._buffer[:2]
                continue
            total = 4 + length + 1
            if len(self._buffer) < total:
                return samples
            packet = bytes(self._buffer[:total])
            del self._buffer[:total]
            self.stats.frames_seen += 1
            try:
                sample = decode_frame(
                    packet,
                    host_received_at=host_received_at,
                    host_time_source=host_time_source,
                    as_of=as_of,
                    freshness_threshold_s=self.freshness_threshold_s,
                    raw_line=raw_line,
                )
            except TelemetryError as exc:
                if "checksum" in str(exc):
                    self.stats.rejected_checksum += 1
                else:
                    self.stats.rejected_payload += 1
                continue
            if sample is None:
                self.stats.non_magnetic_frames += 1
            else:
                self.stats.frames_accepted += 1
                samples.append(sample)

    def finish(self) -> None:
        self.stats.incomplete_tail_bytes = len(self._buffer)


HANDLE_RE = re.compile(r"\bHandle:\s*cmd\s*=\s*(?:0x)?(?P<command>[0-9a-fA-F]{2})\s*,?(?P<rest>.*)$")
BYTE_RE = re.compile(r"(?<![0-9A-Fa-f])(?:0x)?([0-9A-Fa-f]{2})(?![0-9A-Fa-f])")
ISO_PREFIX_RE = re.compile(r"^(?P<timestamp>\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d))\s*")


def line_timestamp(line: str) -> str | None:
    match = ISO_PREFIX_RE.search(line.strip())
    return match.group("timestamp") if match else None


def callback_payload_from_line(line: str) -> bytes | None:
    """Extract ``Handle: cmd=06, <7 bytes>`` without claiming wire framing."""

    match = HANDLE_RE.search(line)
    if not match or int(match.group("command"), 16) != MAGNETIC_COMMAND:
        return None
    text = match.group("rest").strip()
    # A USB/JTAG log can interleave another task's text into one line. Only
    # accept seven complete byte tokens at the beginning; never stitch bytes
    # across an interleaved message.
    tokens = re.match(r"(?P<bytes>(?:[0-9A-Fa-f]{2}\s+){6}[0-9A-Fa-f]{2})(?P<trailing>.*)$", text)
    if not tokens:
        raise TelemetryError("0x06 callback payload is truncated or interleaved")
    if tokens.group("trailing").strip():
        raise TelemetryError("0x06 callback payload has extra/interleaved text")
    values = bytes.fromhex(tokens.group("bytes"))
    if len(values) != MAGNETIC_DATA_LENGTH:
        raise TelemetryError(f"0x06 callback data must be 7 bytes, got {len(values)}")
    return bytes((MAGNETIC_COMMAND,)) + values


def frame_bytes_from_line(line: str) -> bytes:
    """Extract hex bytes from a plain chunk or explicit ``hex=`` log field.

    Plain chunks may split the header/frame across lines. For other log text
    an AA55 marker is required. Missing checksums and truncated frames remain
    in the stream parser and are reported by ``finish``.
    """

    text = line.strip()
    timestamp = ISO_PREFIX_RE.match(text)
    if timestamp:
        text = text[timestamp.end() :].strip()
    if not text or text.startswith("#"):
        return b""
    field = re.search(r"\bhex=(?P<hex>(?:[0-9A-Fa-f]{2}\s*)+)(?P<truncated>\.\.\.)?", text)
    if field:
        if field.group("truncated"):
            raise TelemetryError("explicitly truncated hex field")
        return bytes.fromhex(field.group("hex"))
    if re.fullmatch(r"(?:[0-9A-Fa-f]{2}\s*)+", text):
        return bytes.fromhex(text)
    marker = re.search(r"(?i)(?<![A-Za-z0-9])AA\s*55", text)
    if not marker:
        return b""
    sequence = re.match(r"(?:[0-9A-Fa-f]{2}\s*)+", text[marker.start() :])
    return bytes.fromhex(sequence.group(0)) if sequence else b""


def iter_samples_from_callback_lines(
    lines: Iterable[str],
    *,
    as_of: str | datetime | None = None,
    freshness_threshold_s: float = 1.0,
) -> Iterator[MagneticSample]:
    """Yield reconstructed samples from callback log lines."""

    for line in lines:
        payload = callback_payload_from_line(line)
        if payload is None:
            continue
        received = line_timestamp(line)
        yield decode_payload(
            payload,
            source="callback_reconstructed",
            host_received_at=received,
            host_time_source="log_prefix" if received else None,
            as_of=as_of,
            freshness_threshold_s=freshness_threshold_s,
            raw_line=line.rstrip("\r\n"),
        )
