"""Offline E3c receiver reference; no serial, firmware, UI or hardware access.

Reuse the E2c envelope and decision rules. The owner explicitly calls dispatch()
to model Application::MainEventLoop; receiving another frame never executes it.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Iterator

from protocol import expression_control as contract

MAX_FRAME_BYTES = 2048
MAX_JSON_DEPTH = 8
MAX_COUNTER = 9_007_199_254_740_991
COMMAND = {"kind": "play_expression", "semantic_id": "neutral"}


@dataclass(frozen=True)
class Frame:
    request: dict | None = None
    error: str | None = None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _integer_only(_):
    raise ValueError("integer_numbers_required")


def _depth_check(raw: bytes):
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("json_too_deep")
        elif byte in (93, 125):
            depth -= 1


def decode_request(raw: bytes) -> dict:
    """A complete UTF-8 JSON payload, excluding its LF delimiter."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_FRAME_BYTES:
        raise ValueError("frame_size")
    _depth_check(raw)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_float=_integer_only, parse_constant=_integer_only)
        return contract.validate_request(value)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("request_json") from error


class RequestFrames:
    """Incremental JSON-lines input; discard an oversized line through its LF.

    Retains at most 2 KiB. Iteration must finish before feeding another chunk;
    yielding frames avoids accumulating an unbounded result list for a burst.
    CRLF is accepted, with CR included in the 2 KiB limit. Empty lines are ignored.
    """

    def __init__(self):
        self._buffer = bytearray()
        self._overflow = False

    @property
    def buffered_bytes(self):
        return len(self._buffer)

    def reset(self):
        self._buffer.clear()
        self._overflow = False

    def feed(self, chunk: bytes) -> Iterator[Frame]:
        if not isinstance(chunk, bytes):
            raise TypeError("byte chunks required")
        for byte in chunk:
            if byte == 10:
                if self._overflow:
                    self.reset()
                    yield Frame(error="frame_too_large")
                    continue
                raw = bytes(self._buffer)
                self._buffer.clear()
                if not raw.strip():
                    continue
                try:
                    yield Frame(request=decode_request(raw))
                except (ValueError, TypeError):
                    yield Frame(error="invalid_request")
            elif not self._overflow:
                if len(self._buffer) == MAX_FRAME_BYTES:
                    self._buffer.clear()
                    self._overflow = True
                else:
                    self._buffer.append(byte)


class Receiver:
    """Single-threaded, neutral-only reference with simulated receipts.

    clock() supplies integer monotonic milliseconds. TTL starts when a complete
    valid request reaches submit(). UTC issued_at is for display. dispatch()
    represents one owner-loop turn, and requires a fresh owner_ready snapshot.
    """

    def __init__(self, catalog: dict, *, target_id: str, boot_id: str, clock):
        contract.validate_catalog(catalog)
        neutral = next((row for row in catalog["expressions"]
                        if row["semantic_id"] == "neutral"), None)
        if (neutral is None or neutral["resource_id"] != "winking.eaf"
                or not neutral["requestable"] or neutral["implicit_action_id"] is not None):
            raise ValueError("neutral_mapping_changed")
        self._catalog = deepcopy(catalog)
        self._target = self._identifier(target_id)
        self._boot = self._identifier(boot_id)
        self._clock = clock
        self._last_time = -1
        self._connected = False
        self._session_number = 0
        self._session = "offline-session-0"
        self._pending = None
        self._ledger = OrderedDict()
        self._journal = deque(maxlen=contract.LIMITS["max_history"])
        self._high_watermark = 0
        self._receipt_sequence = 0
        self._dispatch_count = 0

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not contract.ID.fullmatch(value):
            raise ValueError("invalid_receiver_identifier")
        return value

    def _now(self):
        value = self._clock()
        if type(value) is not int or not 0 <= value <= MAX_COUNTER or value < self._last_time:
            raise ValueError("clock_must_be_monotonic_integer_ms")
        self._last_time = value
        return value

    def connect(self):
        if not self._connected:
            self._session_number += 1
            self._session = f"offline-session-{self._session_number}"
            self._connected = True
        return self.snapshot()

    def disconnect(self):
        self._connected = False

    def reboot(self, boot_id: str):
        boot_id = self._identifier(boot_id)
        if boot_id == self._boot:
            raise ValueError("reboot_requires_new_boot_id")
        self.__init__(self._catalog, target_id=self._target, boot_id=boot_id, clock=self._clock)

    def _emit(self, request, status, reason, *, now, remember=True, original=None):
        receipt = {
            "schema": contract.SCHEMA, "message_type": "receipt",
            "target_id": self._target, "boot_id": self._boot, "session_id": self._session,
            "sequence": self._receipt_sequence + 1, "request_id": request["request_id"],
            "request_sequence": request["sequence"], "status": status, "reason": reason,
            "source": "simulator", "correlated": True, "original_status": original, "at_ms": now,
        }
        contract.validate_receipt(receipt)
        self._receipt_sequence += 1
        self._journal.append(deepcopy(receipt))
        if remember and request["request_id"] in self._ledger:
            self._ledger[request["request_id"]]["receipt"] = deepcopy(receipt)
        return receipt

    def expire(self):
        return self._expire_at(self._now())

    def _expire_at(self, now):
        if self._pending and now >= self._pending["deadline"]:
            request = self._pending["request"]
            self._pending = None
            return self._emit(request, "expired", "deadline_before_dispatch", now=now)
        return None

    def submit(self, request: dict, *, owner_ready: bool):
        if type(owner_ready) is not bool:
            raise ValueError("owner_snapshot_required")
        request = contract.validate_request(request)
        now = self._now()
        self._expire_at(now)
        if not self._connected:
            return None

        def reject(reason):
            return self._emit(request, "rejected", reason, now=now, remember=False)

        if request["target_id"] != self._target:
            return reject("target_mismatch")
        if request["boot_id"] != self._boot:
            return reject("boot_changed")
        fingerprint = hashlib.sha256(contract.canonical(request)).hexdigest()
        previous = self._ledger.get(request["request_id"])
        if previous is not None:
            if previous["fingerprint"] != fingerprint:
                return reject("request_id_conflict")
            return self._emit(request, "duplicate", "replayed_receipt", now=now, remember=False,
                              original=previous["receipt"]["status"])
        if request["session_id"] != self._session:
            return reject("session_changed")
        if (request["catalog_id"] != self._catalog["catalog_id"]
                or request["role_id"] != self._catalog["role_id"]):
            return reject("catalog_or_role_changed")
        if request["sequence"] <= self._high_watermark:
            return reject("sequence_not_increasing")
        if len(self._ledger) >= contract.LIMITS["max_dedup"]:
            return reject("idempotency_window_full")
        if now > MAX_COUNTER - request["expires_after_ms"]:
            return reject("deadline_out_of_range")
        self._high_watermark = request["sequence"]
        self._ledger[request["request_id"]] = {"fingerprint": fingerprint, "receipt": None}
        if request["command"] != COMMAND:
            return self._emit(request, "rejected", "unsupported_expression_or_action", now=now)
        if self._pending is not None or not owner_ready:
            return self._emit(request, "busy", "existing_owner_or_request", now=now)
        self._pending = {"request": request, "deadline": now + request["expires_after_ms"]}
        return self._emit(request, "accepted", "queued_for_owner", now=now)

    def dispatch(self, *, owner_ready: bool):
        if type(owner_ready) is not bool:
            raise ValueError("owner_snapshot_required")
        now = self._now()
        expired = self._expire_at(now)
        if expired is not None or self._pending is None:
            return expired
        request = self._pending["request"]
        self._pending = None
        if not owner_ready:
            return self._emit(request, "interrupted", "owner_changed_before_dispatch", now=now)
        self._dispatch_count += 1
        return self._emit(request, "dispatched", "submitted_to_display", now=now)

    def snapshot(self):
        return deepcopy({
            "target_id": self._target, "boot_id": self._boot, "session_id": self._session,
            "connected": self._connected, "catalog_id": self._catalog["catalog_id"],
            "role_id": self._catalog["role_id"], "pending": self._pending,
            "receipts": list(self._journal), "retained_decisions": len(self._ledger),
            "dispatch_count": self._dispatch_count, "limits": dict(contract.LIMITS),
            "source": "simulator", "hardware_accessed": False,
        })
