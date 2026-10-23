"""Bounded, offline-only event journal for O2b design validation.

This module has no ESP-IDF, FreeRTOS, LVGL, NVS, UART or MCP dependency.  It
is the reference behavior for a later fixed-capacity firmware journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from protocol.base_event_semantics import SemanticEvent, map_command_data


JOURNAL_CAPACITY = 24


@dataclass(frozen=True)
class EventRecord:
    sequence: int
    type: str
    command: int
    value: int | None
    timestamp_ms: int
    data: bytes = b""
    frame: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    synthetic: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "type": self.type,
            "command": self.command,
            "value": self.value,
            "timestamp_ms": self.timestamp_ms,
            "data_hex": self.data.hex().upper(),
            "frame": self.frame,
            "payload": dict(self.payload),
            "synthetic": self.synthetic,
        }


def _frame(command: int, data: bytes) -> str:
    payload = bytes((command,)) + data
    length = len(payload).to_bytes(2, "big")
    checksum = sum(payload) & 0xFF
    return (b"\xAA\x55" + length + payload + bytes((checksum,))).hex().upper()


class BaseEventJournal:
    """A 24-entry chronological ring with protocol-aware de-duplication."""

    def __init__(self, capacity: int = JOURNAL_CAPACITY, timeout_ms: int = 1000) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._capacity = capacity
        self._timeout_ms = timeout_ms
        self._events: list[EventRecord] = []
        self._next_sequence = 1
        self._online = False
        self._last_heartbeat_ms: int | None = None
        self._mechanical_status: int | None = None

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def online(self) -> bool:
        return self._online

    @property
    def last_heartbeat_ms(self) -> int | None:
        return self._last_heartbeat_ms

    @property
    def mechanical_status(self) -> int | None:
        return self._mechanical_status

    @property
    def total_recorded(self) -> int:
        return self._next_sequence - 1

    def records(self) -> tuple[EventRecord, ...]:
        """Return a stable oldest-to-newest snapshot."""

        return tuple(self._events)

    def ingest(
        self,
        command: int,
        data: bytes | bytearray | memoryview | None,
        timestamp_ms: int,
    ) -> EventRecord | None:
        """Map and record one already transport-validated command payload."""

        if not isinstance(timestamp_ms, int) or not 0 <= timestamp_ms <= 0xFFFFFFFF:
            raise ValueError("timestamp_ms must be a uint32 integer")
        if not isinstance(command, int) or not 0 <= command <= 0xFF:
            raise ValueError("command must be a uint8 integer")
        if data is not None and not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like or None")
        event = map_command_data(command, data)
        raw = b"" if data is None else bytes(data)
        if len(raw) > 249:
            raise ValueError("data exceeds the official AA55 payload limit")

        if event.type == "base.link.connected":
            self._last_heartbeat_ms = timestamp_ms
            if self._online:
                return None
            self._online = True
            return self._append(event, raw, timestamp_ms)

        if event.ignored:
            return None

        if event.type == "base.mechanical.status":
            if self._mechanical_status == event.value:
                return None
            self._mechanical_status = event.value

        return self._append(event, raw, timestamp_ms)

    def check_timeout(self, timestamp_ms: int) -> EventRecord | None:
        """Create one synthetic disconnect after the existing timeout window."""

        if not isinstance(timestamp_ms, int) or not 0 <= timestamp_ms <= 0xFFFFFFFF:
            raise ValueError("timestamp_ms must be a uint32 integer")
        if not self._online or self._last_heartbeat_ms is None:
            return None
        elapsed_ms = (timestamp_ms - self._last_heartbeat_ms) & 0xFFFFFFFF
        if elapsed_ms <= self._timeout_ms:
            return None

        self._online = False
        self._mechanical_status = None
        event = SemanticEvent(
            type="base.link.disconnected",
            command=0x00,
            value=None,
            data_length=0,
            known=True,
            payload={"reason": "heartbeat_timeout"},
        )
        return self._append(event, b"", timestamp_ms, synthetic=True)

    def clear(self) -> None:
        self._events.clear()
        self._next_sequence = 1
        self._online = False
        self._last_heartbeat_ms = None
        self._mechanical_status = None

    def _append(
        self,
        event: SemanticEvent,
        data: bytes,
        timestamp_ms: int,
        *,
        synthetic: bool = False,
    ) -> EventRecord:
        record = EventRecord(
            sequence=self._next_sequence,
            type=event.type,
            command=event.command,
            value=event.value,
            timestamp_ms=timestamp_ms,
            data=bytes(data),
            frame="" if synthetic else _frame(event.command, data),
            payload=dict(event.payload),
            synthetic=synthetic,
        )
        self._next_sequence += 1
        self._events.append(record)
        if len(self._events) > self._capacity:
            del self._events[0]
        return record
