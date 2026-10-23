"""Host-side consumer state machine for ``deskbot.body.v1``.

This module is deliberately transport-agnostic.  It consumes already decoded
Python dictionaries (or JSON objects supplied by a future client), and owns no
serial port, network socket, firmware, NVS, UI, role, or personality state.

The protocol is snapshot-first: every ``hello`` starts or resumes a session,
and a complete ``snapshot`` is required before incremental ``event`` messages
are accepted.  Event delivery is at-least-once; message IDs and event
sequences are used for bounded duplicate suppression.  A sequence gap never
gets silently filled: the caller must provide another snapshot.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Deque, Iterable


SCHEMA = "deskbot.body.v1"
MESSAGE_TYPES = frozenset(("hello", "snapshot", "event", "ack"))
CAPABILITIES = frozenset(("base_events", "magnetic_sample", "shell_observation", "touch", "motion", "expression", "audio_doa"))
MAX_SAFE_INTEGER = 9007199254740991


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _integer(value: Any, name: str, *, minimum: int = 0, maximum: int = MAX_SAFE_INTEGER) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def _finite(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f"{name}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f"{name}[{index}]")


def _parse_time(value: Any, name: str) -> datetime:
    text = _text(value, name).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} requires an explicit timezone")
    return parsed


def _copy(value: Any) -> Any:
    """Keep caller mutation from changing the consumer's state."""

    return deepcopy(value)


@dataclass(frozen=True)
class ConsumeOutcome:
    """Result of accepting or rejecting one protocol message."""

    status: str  # accepted | duplicate | rejected | needs_snapshot
    message_type: str | None = None
    reason: str = ""
    boot_changed: bool = False
    event_sequence: int | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True)
class BodyFactsState:
    """Immutable public view of the current consumer state."""

    device_id: str | None = None
    boot_id: str | None = None
    session_id: str | None = None
    firmware_id: str | None = None
    capabilities: tuple[str, ...] = ()
    hello_seen: bool = False
    snapshot_seen: bool = False
    needs_snapshot: bool = True
    generation: int = 0
    last_transport_sequence: int | None = None
    high_watermark_sequence: int = 0
    oldest_available_sequence: int | None = None
    last_event_sequence: int = 0
    gap_detected: bool = False
    latest_snapshot: dict[str, Any] | None = None
    events: tuple[dict[str, Any], ...] = ()


class BodyFactsConsumer:
    """Consume a bounded, snapshot-first ``deskbot.body.v1`` stream.

    ``max_events`` and ``max_message_ids`` are bounded to prevent a malformed
    or long-lived client session from growing without limit.  This class does
    not infer roles or personalities from ``profile_id``.
    """

    def __init__(self, *, max_events: int = 24, max_message_ids: int = 128) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or not 1 <= max_events <= 1024:
            raise ValueError("max_events must be an integer from 1 through 1024")
        if isinstance(max_message_ids, bool) or not isinstance(max_message_ids, int) or not 1 <= max_message_ids <= 4096:
            raise ValueError("max_message_ids must be an integer from 1 through 4096")
        self.max_events = max_events
        self.max_message_ids = max_message_ids
        self._state = BodyFactsState()
        self._message_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._message_order: Deque[tuple[str, str, str, str]] = deque()
        self._event_cache: dict[int, dict[str, Any]] = {}
        self._event_order: Deque[int] = deque()
        self._retired_boots: set[tuple[str, str]] = set()
        self._retired_boot_order: Deque[tuple[str, str]] = deque()
        self._retired_sessions: set[tuple[str, str, str]] = set()
        self._retired_session_order: Deque[tuple[str, str, str]] = deque()

    @property
    def state(self) -> BodyFactsState:
        # The dataclass is frozen, but its protocol snapshots/events contain
        # dictionaries. Return a deep copy so a UI cannot corrupt dedup state.
        return BodyFactsState(**{
            **self._state.__dict__,
            "latest_snapshot": _copy(self._state.latest_snapshot),
            "events": tuple(_copy(item) for item in self._state.events),
        })

    def reset(self) -> None:
        """Forget the current physical session and all deduplication state."""

        self._state = BodyFactsState()
        self._message_cache.clear()
        self._message_order.clear()
        self._event_cache.clear()
        self._event_order.clear()
        self._retired_boots.clear()
        self._retired_boot_order.clear()
        self._retired_sessions.clear()
        self._retired_session_order.clear()

    def consume_many(self, messages: Iterable[dict[str, Any]]) -> list[ConsumeOutcome]:
        return [self.consume(message) for message in messages]

    def consume(self, message: dict[str, Any]) -> ConsumeOutcome:
        try:
            normalized = self._validate_envelope(message)
        except (TypeError, ValueError) as exc:
            return ConsumeOutcome("rejected", reason=str(exc))
        message_type = normalized["message_type"]
        message_id = normalized["message_id"]
        # A message ID is scoped to the current boot.  It is checked before
        # dispatch so retrying a complete message is always idempotent.
        cache_key = (normalized["device_id"], normalized["boot_id"], normalized["session_id"], message_id)
        if message_type == "hello" and (normalized["device_id"], normalized["boot_id"]) in self._retired_boots:
            return ConsumeOutcome("rejected", message_type="hello", reason="retired_boot_id")
        if message_type == "hello" and (normalized["device_id"], normalized["boot_id"], normalized["session_id"]) in self._retired_sessions:
            return ConsumeOutcome("rejected", message_type="hello", reason="retired_session_id")
        previous = self._message_cache.get(cache_key)
        if previous is not None:
            if previous == normalized:
                return ConsumeOutcome("duplicate", message_type=message_type, reason="message_id_seen")
            return ConsumeOutcome("rejected", message_type=message_type, reason="message_id_conflict")
        transport_error = self._transport_guard(normalized)
        if transport_error is not None:
            return transport_error
        if message_type == "hello":
            outcome = self._hello(normalized)
        elif message_type == "snapshot":
            outcome = self._snapshot(normalized)
        elif message_type == "event":
            outcome = self._event(normalized)
        elif message_type == "ack":
            outcome = self._ack(normalized)
        else:
            # ACKs are transport-level acknowledgements.  They do not advance
            # the body state, but are still deduplicated and accepted.
            outcome = ConsumeOutcome("rejected", message_type=message_type, reason="unsupported_message_type")
        if outcome.status in {"accepted", "duplicate"}:
            self._remember_message(cache_key, normalized)
        return outcome

    def _validate_envelope(self, message: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(message, dict):
            raise TypeError("message must be an object")
        _finite(message, "message")
        if message.get("schema") != SCHEMA:
            raise ValueError("unsupported schema")
        allowed_envelope = {"schema", "message_type", "message_id", "device_id", "boot_id", "session_id", "sequence", "occurred_at", "payload"}
        if set(message) - allowed_envelope:
            raise ValueError("message contains unsupported envelope fields")
        message_type = message.get("message_type")
        if message_type not in MESSAGE_TYPES:
            raise ValueError("unsupported message_type")
        normalized = _copy(message)
        _text(message.get("message_id"), "message_id")
        _text(message.get("device_id"), "device_id")
        _text(message.get("boot_id"), "boot_id")
        _text(message.get("session_id"), "session_id")
        _integer(message.get("sequence"), "sequence")
        _parse_time(message.get("occurred_at"), "occurred_at")
        payload = message.get("payload")
        if not isinstance(payload, dict) or payload.get("kind") != message_type:
            raise ValueError("payload.kind must match message_type")
        if message_type == "hello":
            _text(payload.get("firmware_id"), "payload.firmware_id")
            if set(payload) - {"kind", "firmware_id", "hardware_id", "capabilities", "max_event_backlog"}:
                raise ValueError("hello contains unsupported fields")
            if "hardware_id" in payload:
                _text(payload["hardware_id"], "payload.hardware_id")
            if "max_event_backlog" in payload:
                _integer(payload["max_event_backlog"], "payload.max_event_backlog")
            capabilities = payload.get("capabilities")
            if not isinstance(capabilities, list) or any(not isinstance(item, str) or not item for item in capabilities):
                raise ValueError("payload.capabilities must be a list of non-empty strings")
            if len(set(capabilities)) != len(capabilities):
                raise ValueError("payload.capabilities must contain unique values")
            unknown = sorted(set(capabilities) - CAPABILITIES)
            if unknown:
                raise ValueError(f"unsupported capability(s): {', '.join(unknown)}")
        elif message_type == "snapshot":
            self._validate_snapshot_payload(payload)
        elif message_type == "event":
            if set(payload) - {"kind", "event_type", "event_sequence", "facts"}:
                raise ValueError("event contains unsupported fields")
            _text(payload.get("event_type"), "payload.event_type")
            _integer(payload.get("event_sequence"), "payload.event_sequence")
            if not isinstance(payload.get("facts"), dict):
                raise ValueError("payload.facts must be an object")
        elif message_type == "ack":
            if set(payload) - {"kind", "ack_for", "status", "reason"}:
                raise ValueError("ack contains unsupported fields")
            _text(payload.get("ack_for"), "payload.ack_for")
            if payload.get("status") not in {"accepted", "duplicate", "rejected", "unsupported"}:
                raise ValueError("payload.status is invalid")
            if "reason" in payload and payload["reason"] is not None and not isinstance(payload["reason"], str):
                raise ValueError("payload.reason must be string or null")
        return normalized

    @staticmethod
    def _validate_snapshot_payload(payload: dict[str, Any]) -> None:
        allowed = {"kind", "base", "magnetic", "shell_observation", "event_cursor", "touch", "motion", "expression"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"snapshot contains unsupported field(s): {', '.join(sorted(unknown))}")
        base = payload.get("base")
        cursor = payload.get("event_cursor")
        if not isinstance(base, dict) or set(base) - {"link", "mechanical_status", "last_event_sequence"}:
            raise ValueError("snapshot.base has unsupported fields")
        if not isinstance(base, dict) or base.get("link") not in {"unknown", "online", "offline"}:
            raise ValueError("snapshot.base.link is invalid")
        if base.get("mechanical_status") not in {"unknown", "homing", "ready", "failed"}:
            raise ValueError("snapshot.base.mechanical_status is invalid")
        _integer(base.get("last_event_sequence"), "snapshot.base.last_event_sequence")
        if not isinstance(cursor, dict) or set(cursor) - {"oldest_available_sequence", "high_watermark_sequence", "gap_detected"}:
            raise ValueError("snapshot.event_cursor is required")
        oldest = cursor.get("oldest_available_sequence")
        if oldest is not None:
            _integer(oldest, "snapshot.oldest_available_sequence")
        high = _integer(cursor.get("high_watermark_sequence"), "snapshot.high_watermark_sequence")
        if oldest is not None and oldest > high:
            raise ValueError("snapshot oldest sequence exceeds high watermark")
        if not isinstance(cursor.get("gap_detected"), bool):
            raise ValueError("snapshot.gap_detected must be boolean")
        magnetic = payload.get("magnetic")
        if not isinstance(magnetic, dict):
            raise ValueError("snapshot.magnetic is required")
        if set(magnetic) - {"state", "sensor_kind", "x", "y", "z", "sample_at", "age_ms"}:
            raise ValueError("snapshot.magnetic has unsupported fields")
        if magnetic.get("state") not in {"no_data", "fresh", "stale"}:
            raise ValueError("snapshot.magnetic.state is invalid")
        if "sensor_kind" in magnetic and magnetic["sensor_kind"] is not None and not isinstance(magnetic["sensor_kind"], str):
            raise ValueError("snapshot.magnetic.sensor_kind must be string or null")
        if "sample_at" in magnetic and magnetic["sample_at"] is not None:
            _parse_time(magnetic["sample_at"], "snapshot.magnetic.sample_at")
        for axis in ("x", "y", "z"):
            value = magnetic.get(axis)
            if value is not None:
                _integer(value, f"snapshot.magnetic.{axis}", minimum=-32768, maximum=32767)
        age = magnetic.get("age_ms")
        if age is not None:
            _integer(age, "snapshot.magnetic.age_ms")
        shell = payload.get("shell_observation")
        if not isinstance(shell, dict):
            raise ValueError("snapshot.shell_observation is required")
        if set(shell) - {"status", "profile_id", "profile_revision", "sensor_kind", "similarity", "margin", "sample_count", "observed_at"}:
            raise ValueError("snapshot.shell_observation has unsupported fields")
        status = shell.get("status")
        if not {"status", "profile_id", "profile_revision"} <= set(shell):
            raise ValueError("snapshot.shell_observation is missing required fields")
        if status not in {"no_data", "warming_up", "known", "unknown", "ambiguous", "unstable", "stale"}:
            raise ValueError("snapshot.shell_observation.status is invalid")
        profile_id = shell.get("profile_id")
        if status == "known":
            _text(profile_id, "snapshot.shell_observation.profile_id")
        elif profile_id is not None:
            raise ValueError("non-known shell observation must have null profile_id")
        _integer(shell.get("profile_revision"), "snapshot.shell_observation.profile_revision")
        if "sample_count" in shell:
            _integer(shell.get("sample_count"), "snapshot.shell_observation.sample_count")
        if "sensor_kind" in shell and shell["sensor_kind"] is not None and not isinstance(shell["sensor_kind"], str):
            raise ValueError("snapshot.shell_observation.sensor_kind must be string or null")
        if "observed_at" in shell and shell["observed_at"] is not None:
            _parse_time(shell["observed_at"], "snapshot.shell_observation.observed_at")
        for metric in ("similarity", "margin"):
            value = shell.get(metric)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ValueError(f"snapshot.shell_observation.{metric} must be finite numeric or null")
        for name in ("touch", "motion", "expression"):
            optional = payload.get(name)
            if optional is not None and not isinstance(optional, dict):
                raise ValueError(f"snapshot.{name} must be an object")
            if optional is not None:
                BodyFactsConsumer._validate_optional_component(name, optional)

    @staticmethod
    def _validate_optional_component(name: str, value: dict[str, Any]) -> None:
        fields = {
            "touch": {"state", "count"},
            "motion": {"state", "action", "angle"},
            "expression": {"state", "asset_id", "asset_version"},
        }
        if set(value) - fields[name]:
            raise ValueError(f"snapshot.{name} has unsupported fields")
        if name == "touch":
            if "state" in value and value["state"] not in {"unknown", "idle", "contact", "petting"}:
                raise ValueError("snapshot.touch.state is invalid")
            if "count" in value:
                _integer(value["count"], "snapshot.touch.count")
        elif name == "motion":
            if "state" in value and value["state"] not in {"unknown", "idle", "moving", "complete"}:
                raise ValueError("snapshot.motion.state is invalid")
            if "action" in value and value["action"] is not None and not isinstance(value["action"], str):
                raise ValueError("snapshot.motion.action must be string or null")
            if "angle" in value and value["angle"] is not None:
                angle = value["angle"]
                if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(float(angle)):
                    raise ValueError("snapshot.motion.angle must be finite numeric or null")
        else:
            if "state" in value and value["state"] not in {"unknown", "idle", "playing"}:
                raise ValueError("snapshot.expression.state is invalid")
            for field in ("asset_id", "asset_version"):
                if field in value and value[field] is not None and not isinstance(value[field], str):
                    raise ValueError(f"snapshot.expression.{field} must be string or null")

    def _hello(self, message: dict[str, Any]) -> ConsumeOutcome:
        device_id, boot_id = message["device_id"], message["boot_id"]
        changed = self._state.boot_id is not None and (device_id != self._state.device_id or boot_id != self._state.boot_id)
        session_changed = self._state.hello_seen and message["session_id"] != self._state.session_id
        if changed:
            retired = (self._state.device_id, self._state.boot_id)
            if retired[0] is not None and retired[1] is not None:
                self._retired_boots.add(retired)
                self._retired_boot_order.append(retired)
                while len(self._retired_boot_order) > 64:
                    self._retired_boots.discard(self._retired_boot_order.popleft())
            self._event_cache.clear()
            self._event_order.clear()
            self._message_cache.clear()
            self._message_order.clear()
        elif session_changed:
            retired_session = (self._state.device_id, self._state.boot_id, self._state.session_id)
            if all(value is not None for value in retired_session):
                self._retired_sessions.add(retired_session)  # type: ignore[arg-type]
                self._retired_session_order.append(retired_session)  # type: ignore[arg-type]
                while len(self._retired_session_order) > 128:
                    self._retired_sessions.discard(self._retired_session_order.popleft())
        payload = message["payload"]
        generation = self._state.generation + (1 if changed or not self._state.hello_seen else 0)
        prior = self._state
        self._state = BodyFactsState(
            device_id=device_id,
            boot_id=boot_id,
            session_id=message["session_id"],
            firmware_id=payload["firmware_id"],
            capabilities=tuple(payload.get("capabilities", ())),
            hello_seen=True,
            snapshot_seen=False,
            needs_snapshot=True,
            generation=generation,
            last_transport_sequence=message["sequence"],
            high_watermark_sequence=0 if changed else prior.high_watermark_sequence,
            oldest_available_sequence=None if changed else prior.oldest_available_sequence,
            last_event_sequence=0 if changed else prior.last_event_sequence,
            gap_detected=False if changed else prior.gap_detected,
            latest_snapshot=None if changed else _copy(prior.latest_snapshot),
        )
        return ConsumeOutcome("accepted", message_type="hello", reason="boot_changed" if changed else "session_changed" if session_changed else "hello_seen", boot_changed=changed)

    def _identity_matches(self, message: dict[str, Any]) -> bool:
        return (
            message["device_id"] == self._state.device_id
            and message["boot_id"] == self._state.boot_id
            and message["session_id"] == self._state.session_id
        )

    def _transport_guard(self, message: dict[str, Any]) -> ConsumeOutcome | None:
        """Reject only regressions within the same device/boot/session."""

        if not self._state.hello_seen:
            return None
        same_stream = (
            message["device_id"] == self._state.device_id
            and message["boot_id"] == self._state.boot_id
            and message["session_id"] == self._state.session_id
        )
        if same_stream and self._state.last_transport_sequence is not None and message["sequence"] <= self._state.last_transport_sequence:
            return ConsumeOutcome("rejected", message_type=message["message_type"], reason="transport_sequence_not_increasing")
        return None

    def _snapshot(self, message: dict[str, Any]) -> ConsumeOutcome:
        if not self._state.hello_seen:
            return ConsumeOutcome("needs_snapshot", message_type="snapshot", reason="hello_required")
        if not self._identity_matches(message):
            return ConsumeOutcome("rejected", message_type="snapshot", reason="identity_or_session_mismatch")
        payload = message["payload"]
        cursor = payload["event_cursor"]
        high = int(cursor["high_watermark_sequence"])
        if high < self._state.high_watermark_sequence:
            return ConsumeOutcome("rejected", message_type="snapshot", reason="snapshot_watermark_regressed")
        self._event_cache.clear()
        self._event_order.clear()
        self._state = BodyFactsState(
            device_id=self._state.device_id,
            boot_id=self._state.boot_id,
            session_id=message["session_id"],
            firmware_id=self._state.firmware_id,
            capabilities=self._state.capabilities,
            hello_seen=True,
            snapshot_seen=True,
            needs_snapshot=False,
            generation=self._state.generation,
            last_transport_sequence=message["sequence"],
            high_watermark_sequence=high,
            oldest_available_sequence=cursor.get("oldest_available_sequence"),
            last_event_sequence=high,
            gap_detected=bool(cursor["gap_detected"]),
            latest_snapshot=_copy(payload),
            events=(),
        )
        return ConsumeOutcome("accepted", message_type="snapshot", reason="snapshot_applied")

    def _event(self, message: dict[str, Any]) -> ConsumeOutcome:
        if not self._state.hello_seen:
            return ConsumeOutcome("needs_snapshot", message_type="event", reason="hello_required")
        if not self._identity_matches(message):
            return ConsumeOutcome("rejected", message_type="event", reason="identity_or_session_mismatch")
        if self._state.needs_snapshot or not self._state.snapshot_seen:
            return ConsumeOutcome("needs_snapshot", message_type="event", reason="snapshot_required")
        payload = message["payload"]
        event_sequence = int(payload["event_sequence"])
        prior = self._event_cache.get(event_sequence)
        if prior is not None:
            if prior == payload:
                self._state = BodyFactsState(**{**self._state.__dict__, "last_transport_sequence": message["sequence"]})
                return ConsumeOutcome("duplicate", message_type="event", reason="event_sequence_seen", event_sequence=event_sequence)
            return ConsumeOutcome("rejected", message_type="event", reason="event_sequence_conflict", event_sequence=event_sequence)
        expected = self._state.last_event_sequence + 1
        if event_sequence < expected:
            self._state = BodyFactsState(**{**self._state.__dict__, "last_transport_sequence": message["sequence"]})
            return ConsumeOutcome("duplicate", message_type="event", reason="stale_event_sequence", event_sequence=event_sequence)
        if event_sequence > expected:
            self._state = BodyFactsState(**{**self._state.__dict__, "needs_snapshot": True, "gap_detected": True, "last_transport_sequence": message["sequence"]})
            return ConsumeOutcome("needs_snapshot", message_type="event", reason="event_sequence_gap", event_sequence=event_sequence)
        event_copy = _copy(payload)
        self._event_cache[event_sequence] = event_copy
        self._event_order.append(event_sequence)
        while len(self._event_order) > self.max_events:
            self._event_cache.pop(self._event_order.popleft(), None)
        events = tuple(self._event_cache[seq] for seq in self._event_order)
        self._state = BodyFactsState(**{**self._state.__dict__, "last_event_sequence": event_sequence, "high_watermark_sequence": max(self._state.high_watermark_sequence, event_sequence), "last_transport_sequence": message["sequence"], "events": events})
        return ConsumeOutcome("accepted", message_type="event", reason="event_applied", event_sequence=event_sequence)

    def _ack(self, message: dict[str, Any]) -> ConsumeOutcome:
        if not self._state.hello_seen:
            return ConsumeOutcome("needs_snapshot", message_type="ack", reason="hello_required")
        if not self._identity_matches(message):
            return ConsumeOutcome("rejected", message_type="ack", reason="identity_or_session_mismatch")
        self._state = BodyFactsState(**{**self._state.__dict__, "last_transport_sequence": message["sequence"]})
        return ConsumeOutcome("accepted", message_type="ack", reason="ack_ignored")

    def _remember_message(self, cache_key: tuple[str, str, str, str], message: dict[str, Any]) -> None:
        self._message_cache[cache_key] = _copy(message)
        self._message_order.append(cache_key)
        while len(self._message_order) > self.max_message_ids:
            self._message_cache.pop(self._message_order.popleft(), None)


__all__ = ["BodyFactsConsumer", "BodyFactsState", "ConsumeOutcome", "SCHEMA"]
