"""Offline semantic mapping for validated ESP-VoCat base frames.

This module is deliberately transport- and device-independent.  The official
UART component remains the only production frame parser; this mapping is an
O2a oracle for tests and later bounded event recording.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


UNKNOWN_EVENT = "base.uart.unknown_event"


@dataclass(frozen=True)
class SemanticEvent:
    """The stable facts produced from one already validated command payload."""

    type: str
    command: int
    value: int | None
    data_length: int
    known: bool
    ignored: bool = False
    payload: Mapping[str, Any] = field(default_factory=dict)


def _unknown(command: int, data: bytes) -> SemanticEvent:
    value = int.from_bytes(data[:2], "big") if len(data) >= 2 else None
    return SemanticEvent(
        type=UNKNOWN_EVENT,
        command=command,
        value=value,
        data_length=len(data),
        known=False,
        payload={},
    )


def mechanical_status_name(status: int) -> str:
    return {
        0x0000: "homing",
        0x0001: "ready",
        0x0002: "failed",
    }.get(status, "unknown")


def _event(
    command: int,
    data: bytes,
    event_type: str,
    *,
    payload: Mapping[str, Any] | None = None,
) -> SemanticEvent:
    return SemanticEvent(
        type=event_type,
        command=command,
        value=int.from_bytes(data, "big"),
        data_length=len(data),
        known=True,
        payload={} if payload is None else dict(payload),
    )


_SLIDE_EVENTS = {
    0x0001: "base.magnetic_switch.slide_down",
    0x0002: "base.magnetic_switch.slide_up",
    0x0003: "base.magnetic_switch.removed_from_up",
    0x0004: "base.magnetic_switch.removed_from_down",
    0x0005: "base.magnetic_switch.placed_from_up",
    0x0006: "base.magnetic_switch.placed_from_down",
    0x0007: "base.magnetic_switch.single_click",
}

_ACCESSORY_EVENTS = {
    0x0008: ("attached", "fish"),
    0x0009: ("detached", "fish"),
    0x000C: ("attached", "ice_cream"),
    0x000D: ("detached", "ice_cream"),
    0x000E: ("attached", "donut"),
    0x000F: ("detached", "donut"),
    0x0010: ("attached", "iphone_lean_front"),
    0x0011: ("detached", "iphone_lean_front"),
    0x0012: ("attached", "iphone_under_base"),
    0x0013: ("detached", "iphone_under_base"),
}

_CALIBRATION_EVENTS = {
    0x0081: ("started", "started"),
    0x0082: ("position_recorded", "position_recorded"),
    0x0083: ("completed", "completed"),
}


def map_command_data(command: int, data: bytes | bytearray | memoryview | None) -> SemanticEvent:
    """Map a validated command and payload without side effects.

    The caller must have already validated AA55 framing and checksum.  A
    malformed command payload is returned as ``unknown_event`` so callers can
    preserve the original fact without guessing a meaning.
    """

    if not isinstance(command, int) or not 0 <= command <= 0xFF:
        raw = b"" if data is None else bytes(data)
        return _unknown(command, raw)
    if data is None:
        raw = b""
    elif isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
    else:
        raise TypeError("data must be bytes-like or None")

    if command == 0x06:
        # Sensor kind plus three signed 16-bit axes.  It is telemetry, not an
        # event, and must not be allowed to flood a future event journal.
        if len(raw) == 7:
            return SemanticEvent(
                type=UNKNOWN_EVENT,
                command=command,
                value=int.from_bytes(raw[:2], "big"),
                data_length=len(raw),
                known=True,
                ignored=True,
                payload={"telemetry": "magnetic_sample"},
            )
        return _unknown(command, raw)

    if len(raw) != 2:
        return _unknown(command, raw)

    value = int.from_bytes(raw, "big")
    if command == 0x00 and value == 0x0001:
        return _event(command, raw, "base.link.connected")
    if command == 0x02 and value == 0x0010:
        return _event(command, raw, "base.action.completed")
    if command == 0x05 and value in (0x0000, 0x0001, 0x0002):
        return _event(
            command,
            raw,
            "base.mechanical.status",
            payload={"status": mechanical_status_name(value)},
        )
    if command != 0x03:
        return _unknown(command, raw)

    if value in _SLIDE_EVENTS:
        return _event(command, raw, _SLIDE_EVENTS[value])
    if value in _ACCESSORY_EVENTS:
        action, accessory = _ACCESSORY_EVENTS[value]
        return _event(
            command,
            raw,
            f"base.accessory.{action}",
            payload={"accessory": accessory},
        )
    if value == 0x000A:
        return _event(
            command,
            raw,
            "base.pairing.entered",
            payload={"method": "magnetic_field"},
        )
    if value == 0x000B:
        return _event(
            command,
            raw,
            "base.pairing.cancelled",
            payload={"method": "magnetic_field"},
        )
    if value in _CALIBRATION_EVENTS:
        event_name, stage = _CALIBRATION_EVENTS[value]
        return _event(
            command,
            raw,
            f"base.calibration.{event_name}",
            payload={"stage": stage},
        )
    return _unknown(command, raw)
