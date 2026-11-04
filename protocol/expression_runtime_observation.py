"""Host-side reference model for the E1.25 expression observation contract.

This module is deliberately independent of ESP-IDF and does not claim to
model firmware timing, locking, memory layout, display rendering, or actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCHEMA_ID = "deskbot.expression.runtime_observation.e1_25/v1"
OBSERVATION_POINT = "EmoteDisplay::SetEmotion"
CLAIM_SCOPE = "set_emotion_call_only"
OBSERVATION_KIND = "set_emotion_call_entered"
CAPACITY = 24
COALESCE_WINDOW_MS = 250
MAX_INPUT_BYTES = 24
UINT32_MAX = 0xFFFFFFFF
MAX_REPEAT_COUNT = 0xFFFF
MAX_SEQUENCE = 9_007_199_254_740_991

SEMANTICS = (
    "angry", "beat_swing", "confident", "confused", "crying", "delicious",
    "embarrassed", "eat", "funny", "happy", "idle", "insert", "laughing",
    "listen", "look_around", "look_left", "look_right", "loving", "neutral",
    "question", "relaxed", "sad", "shocked", "silly", "sleepy", "surprised",
    "thinking", "tried", "winking",
)
OTHER_SEMANTIC = "other"


@dataclass(frozen=True)
class ObservationRecord:
    sequence: int
    semantic: str
    first_seen_ms: int
    last_seen_ms: int
    repeat_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "observation": OBSERVATION_KIND,
            "semantic": self.semantic,
            "first_seen_ms": self.first_seen_ms,
            "last_seen_ms": self.last_seen_ms,
            "repeat_count": self.repeat_count,
        }


def _semantic_id(value: str | None) -> str | None:
    """Map only bounded ASCII identifiers; never retain arbitrary input text."""

    if value is None or value == "":
        return None
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return OTHER_SEMANTIC
    if len(encoded) > MAX_INPUT_BYTES:
        return OTHER_SEMANTIC
    if value in SEMANTICS:
        return value
    return OTHER_SEMANTIC


def _sat_add(value: int, increment: int = 1) -> int:
    return min(UINT32_MAX, value + increment)


class ExpressionObservationRing:
    """Logical 24-record ring used for deterministic, offline replay tests."""

    def __init__(self, *, enabled: bool = False, initial_sequence: int = 1) -> None:
        if not isinstance(initial_sequence, int) or not 1 <= initial_sequence <= MAX_SEQUENCE:
            raise ValueError("initial_sequence must be a positive safe JSON integer")
        self._enabled = bool(enabled)
        self._next_sequence = initial_sequence
        self._records: list[ObservationRecord] = []
        self._overwritten = 0
        self._contention_drops = 0
        self._sequence_exhausted = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def observe(self, semantic_name: str | None, timestamp_ms: int) -> bool:
        """Record a semantic call; return false when disabled or empty."""

        if not self._enabled:
            return False
        semantic = _semantic_id(semantic_name)
        if semantic is None:
            return False
        if not isinstance(timestamp_ms, int) or not 0 <= timestamp_ms <= UINT32_MAX:
            raise ValueError("timestamp_ms must be uint32")

        if self._next_sequence > MAX_SEQUENCE:
            self._sequence_exhausted = _sat_add(self._sequence_exhausted)
            return False

        if self._records:
            last = self._records[-1]
            elapsed = (timestamp_ms - last.last_seen_ms) & UINT32_MAX
            if last.semantic == semantic and elapsed <= COALESCE_WINDOW_MS:
                self._records[-1] = ObservationRecord(
                    sequence=last.sequence,
                    semantic=last.semantic,
                    first_seen_ms=last.first_seen_ms,
                    last_seen_ms=timestamp_ms,
                    repeat_count=min(MAX_REPEAT_COUNT, last.repeat_count + 1),
                )
                return True

        record = ObservationRecord(
            sequence=self._next_sequence,
            semantic=semantic,
            first_seen_ms=timestamp_ms,
            last_seen_ms=timestamp_ms,
            repeat_count=1,
        )
        self._next_sequence += 1
        if len(self._records) == CAPACITY:
            del self._records[0]
            self._overwritten = _sat_add(self._overwritten)
        self._records.append(record)
        return True

    def note_contention_drop(self, count: int = 1) -> None:
        """Model a firmware try-lock failure; the reference model has no lock."""

        if not isinstance(count, int) or count < 0:
            raise ValueError("count must be a non-negative integer")
        self._contention_drops = _sat_add(self._contention_drops, count)

    def snapshot(self, captured_at_ms: int) -> dict[str, Any]:
        if not isinstance(captured_at_ms, int) or not 0 <= captured_at_ms <= UINT32_MAX:
            raise ValueError("captured_at_ms must be uint32")
        return {
            "schema": SCHEMA_ID,
            "edition": "E1.25",
            "observation_point": OBSERVATION_POINT,
            "claim_scope": CLAIM_SCOPE,
            "captured_at_ms": captured_at_ms,
            "records_overwritten": self._overwritten,
            "records_dropped_contention": self._contention_drops,
            "records_dropped_sequence_exhausted": self._sequence_exhausted,
            "records": [record.as_dict() for record in self._records],
        }

    def clear(self) -> None:
        """Clear visible evidence and loss counters without rewinding sequence."""

        self._records.clear()
        self._overwritten = 0
        self._contention_drops = 0
        self._sequence_exhausted = 0
