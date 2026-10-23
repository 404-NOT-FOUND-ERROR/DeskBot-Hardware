"""E1.8 offline receiver for the adapter-to-client half of E1.7.

Inputs must be decoded JSON objects from an explicitly selected adapter. This
module opens no connection and performs no review, role activation or device
operation. Its injected clock MUST share the producer's monotonic clock domain;
it is not a cross-process lease implementation or an authenticated endpoint.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from protocol.body_facts_consumer import BodyFactsConsumer, MAX_SAFE_INTEGER


CLOCK_DOMAIN = "shared_process_monotonic"
INBOUND_TYPES = frozenset(("body_fact", "role_state", "review_state", "review_ack"))
_ADAPTER_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_DATE_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt](?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z"
)


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_SAFE_INTEGER:
        raise ValueError(f"{name} must be a JSON-safe integer >= {minimum}")
    return value


def _json_only(value: Any) -> None:
    """Reject Python-only values before schema checks, hashing or state changes."""
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("integer exceeds JSON-safe range")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
    elif isinstance(value, list):
        for item in value:
            _json_only(item)
    elif isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_only(item)
    else:
        raise ValueError("input must contain JSON values only")


def _validator() -> Draft202012Validator:
    # An empty Registry has no network retriever. Every external reference used
    # by this contract is registered from the adjacent, versioned local files.
    directory = Path(__file__).resolve().parent
    resources, schema = [], None
    for name in ("deskbot-body-facts-v1.schema.json", "deskbot-role-resolution-v1.schema.json",
                 "deskbot-client-transport-e1-7.schema.json"):
        document = json.loads((directory / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(document)
        resources.append((document["$id"], Resource.from_contents(document)))
        schema = document
    checker = FormatChecker()

    @checker.checks("date-time", raises=ValueError)
    def date_time(value: Any) -> bool:
        # jsonschema's optional RFC3339 dependency may be absent. Do not let
        # that silently turn all timestamp format checks into no-ops.
        if not isinstance(value, str):
            return True  # The schema, not a format checker, enforces types.
        if not _DATE_TIME.fullmatch(value):
            return False
        datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        return True

    return Draft202012Validator(schema, registry=Registry().with_resources(resources),
                                format_checker=checker)


@dataclass(frozen=True)
class RecordOutcome:
    status: str  # accepted | duplicate | rejected | needs_resync
    reason: str
    applied: bool = False
    record_type: str | None = None
    body_outcome: dict[str, Any] | None = None


class ClientRecordReceiver:
    """Bounded, single-writer offline client projection, not a transport.

    ``connect`` is a trusted caller lifecycle notification, not a received
    record. Inbound packets cannot select their own adapter instance. Both full
    role/review states are needed after a sync boundary; a resolved role also
    needs a matching hello/snapshot. No reorder queue or hidden retries exist.
    """

    def __init__(self, *, clock_domain: str, max_record_ids: int = 128,
                 max_acks: int = 24, max_retired_streams: int = 64) -> None:
        if clock_domain != CLOCK_DOMAIN:
            raise ValueError("E1.8 requires a shared process monotonic clock")
        for value, name, maximum in ((max_record_ids, "max_record_ids", 4096),
                                     (max_acks, "max_acks", 128),
                                     (max_retired_streams, "max_retired_streams", 256)):
            if _integer(value, name, minimum=1) > maximum:
                raise ValueError(f"{name} exceeds {maximum}")
        self._validate = _validator()
        self._body = BodyFactsConsumer()
        self._max_record_ids, self._max_acks = max_record_ids, max_acks
        self._max_retired_streams = max_retired_streams
        self._records: OrderedDict[str, str] = OrderedDict()
        self._acks: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._retired: set[str] = set()
        self._adapter: str | None = None
        self._connected = False
        self._now = 0
        self._last_sequence = 0
        self._adapter_clock: int | None = None
        self._revision: int | None = None
        self._revision_floor = 0
        self._role: dict[str, Any] | None = None
        self._review: dict[str, Any] | None = None
        self._snapshot: dict[str, Any] | None = None
        self._lease_anchor: tuple[str, str, datetime, datetime, int, int] | None = None
        self._need_hello = self._need_snapshot = True
        self._need_role = self._need_review = True
        self._reason, self._resync_reason = "not_connected", "client_reconnect"

    def _invalidate(self, reason: str, *, body: bool = False, hello: bool = False,
                    resync_reason: str = "manual") -> None:
        self._role = self._review = None
        self._need_role = self._need_review = True
        self._need_snapshot |= body or hello
        self._need_hello |= hello
        if body or hello:
            self._snapshot = None
        self._reason, self._resync_reason = reason, resync_reason

    def connect(self, adapter_instance_id: str, *, now_ms: int) -> dict[str, Any]:
        """Select a trusted offline producer and require fresh complete state.

        Reconnecting the same instance keeps sequence/dedup history. A changed
        instance resets its own counters and body projection; retired streams
        cannot be selected again during this receiver's bounded lifetime.
        """
        if not isinstance(adapter_instance_id, str) or not _ADAPTER_ID.fullmatch(adapter_instance_id):
            raise ValueError("adapter_instance_id must be an identifier of at most 64 characters")
        if adapter_instance_id in self._retired:
            raise ValueError("retired_adapter_instance")
        changed = self._adapter is not None and adapter_instance_id != self._adapter
        if changed and len(self._retired) >= self._max_retired_streams:
            raise ValueError("retired_stream_capacity_reached")
        self.tick(now_ms)
        if changed:
            self._retired.add(self._adapter)
            self._body.reset()
            self._records.clear()
            self._acks.clear()
            self._last_sequence = 0
            self._revision = self._adapter_clock = None
            self._revision_floor = 0
            self._lease_anchor = None
        self._adapter, self._connected = adapter_instance_id, True
        self._invalidate("adapter_changed" if changed else "connection_requires_full_state",
                         hello=True, resync_reason="adapter_restart" if changed else "client_reconnect")
        return self.state

    def disconnect(self, *, now_ms: int) -> dict[str, Any]:
        self.tick(now_ms)
        self._connected = False
        self._invalidate("transport_disconnected", hello=True, resync_reason="client_reconnect")
        return self.state

    def tick(self, now_ms: int) -> dict[str, Any]:
        now = _integer(now_ms, "now_ms")
        if now < self._now:
            raise ValueError("host/replay clock must be monotonic")
        self._now = now
        if self._role and self._role["valid_until_ms"] is not None and now >= self._role["valid_until_ms"]:
            self._invalidate("role_expired")
        if self._review and self._review["pending_case"] is not None:
            deadline = self._review["pending_case"]["valid_until_ms"]
            if deadline is not None and now >= deadline:
                self._invalidate("review_case_expired")
        return self.state

    @property
    def state(self) -> dict[str, Any]:
        synchronized = self._connected and not (self._need_role or self._need_review)
        body_ready = not (self._need_hello or self._need_snapshot)
        role = self._role if synchronized else None
        body = asdict(self._body.state)
        if not body_ready:
            body.update(needs_snapshot=True, latest_snapshot=None, events=())
        return deepcopy({
            "schema": "deskbot.client_receiver_state/v1", "clock_domain": CLOCK_DOMAIN,
            "clock_ms": self._now, "connected": self._connected,
            "adapter_instance_id": self._adapter, "last_seen_sequence": self._last_sequence,
            "needs_resync": self._need_role or self._need_review,
            "needs_role_state": self._need_role, "needs_review_state": self._need_review,
            "needs_body_hello": self._need_hello, "needs_body_snapshot": self._need_snapshot,
            "reason": self._reason, "registry_revision": self._revision,
            "body": body, "role_state": role, "review_state": self._review,
            "selection": role["selection"] if role and body_ready else None,
            "review_acks": list(self._acks.values()),
            "cache_counts": {"record_ids": len(self._records), "acks": len(self._acks),
                             "retired_streams": len(self._retired)},
            "safety": {"mode": "offline_same_process_receiver", "network_io": False,
                       "hardware_io": False, "device_activated": False,
                       "expression_executed": False, "action_executed": False,
                       "review_executed": False, "nvs_written": False},
        })

    def resync_request(self) -> dict[str, Any] | None:
        """Build a request payload only; the caller may wrap it with make_record."""
        if not self._connected or not (self._need_role or self._need_review):
            return None
        return {"schema": "deskbot.client_resync_request/v1",
                "target_adapter_instance_id": self._adapter, "known_stream_id": self._adapter,
                "last_seen_sequence": self._last_sequence, "reason": self._resync_reason}

    def consume(self, record: dict[str, Any], *, now_ms: int) -> RecordOutcome:
        self.tick(now_ms)
        if not self._connected:
            return RecordOutcome("rejected", "connection_required")
        if isinstance(record, dict) and record.get("stream_id") != self._adapter:
            return RecordOutcome("rejected", "stream_id_mismatch")
        try:
            _json_only(record)
            self._validate.validate(record)
            _integer(record["sequence"], "sequence", minimum=1)
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        except (ValidationError, TypeError, ValueError, RecursionError):
            self._invalidate("invalid_record", body=True)
            return RecordOutcome("rejected", "invalid_record")
        record = deepcopy(record)
        kind, ident, sequence = record["record_type"], record["record_id"], record["sequence"]
        if kind not in INBOUND_TYPES:
            return RecordOutcome("rejected", "wrong_direction", record_type=kind)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if ident in self._records:
            if self._records[ident] == digest:
                return RecordOutcome("duplicate", "record_id_seen", record_type=kind)
            self._invalidate("record_id_conflict", body=True, resync_reason="stream_gap")
            return RecordOutcome("rejected", "record_id_conflict", record_type=kind)
        if sequence <= self._last_sequence:
            return RecordOutcome("rejected", "sequence_not_increasing", record_type=kind)
        gap = self._last_sequence != 0 and sequence != self._last_sequence + 1
        if gap:
            self._invalidate("stream_gap", body=True, resync_reason="stream_gap")
        # Consume the outer position even if the inner record is unusable.
        # Fixes need a new record; late records are never buffered or replayed.
        self._last_sequence = sequence
        self._records[ident] = digest
        if len(self._records) > self._max_record_ids:
            self._records.popitem(last=False)
        try:
            body_outcome = self._apply(kind, record["payload"])
        except ValueError as exc:
            self._invalidate(str(exc), body=True)
            return RecordOutcome("rejected", str(exc), record_type=kind)
        if not (self._need_role or self._need_review):
            self._reason = "full_state_received"
        return RecordOutcome("needs_resync" if gap else "accepted",
                             "stream_gap" if gap else "record_applied", True, kind, body_outcome)

    def _apply(self, kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if kind == "body_fact":
            if self._need_hello and payload["message_type"] != "hello":
                raise ValueError("body_hello_required")
            outcome = self._body.consume(payload)
            if outcome.status in ("rejected", "needs_snapshot"):
                raise ValueError("body_" + outcome.reason)
            if outcome.accepted and outcome.message_type == "hello":
                self._invalidate("body_hello_requires_snapshot", body=True)
                self._need_hello = False
            elif outcome.accepted and outcome.message_type == "snapshot":
                self._invalidate("body_snapshot_requires_full_state")
                self._snapshot = deepcopy(payload)
                self._need_snapshot = False
            elif outcome.accepted and outcome.message_type == "event":
                self._invalidate("body_event_requires_snapshot", body=True)
            return asdict(outcome)
        if kind == "review_ack":
            self._ack(payload)
            return None
        clock = _integer(payload["clock_ms"], "producer_clock_ms")
        if clock > self._now or (self._adapter_clock is not None and clock < self._adapter_clock):
            raise ValueError("producer_clock_outside_shared_domain")
        if kind == "review_state":
            if payload["adapter_instance_id"] != self._adapter:
                raise ValueError("review_adapter_instance_mismatch")
            revision = _integer(payload["registry_revision"], "registry_revision")
            if revision < self._revision_floor:
                raise ValueError("registry_revision_regressed")
            if self._revision is not None and revision > self._revision:
                self._invalidate("registry_changed_requires_snapshot", body=True)
            pending = payload["pending_case"]
            if pending is not None and pending["valid_until_ms"] is not None:
                if self._now >= _integer(pending["valid_until_ms"], "case_valid_until_ms"):
                    raise ValueError("review_case_expired")
            if len({choice["choice_id"] for choice in payload["choices"]}) != len(payload["choices"]):
                raise ValueError("review_choice_id_conflict")
            self._revision = revision
            self._revision_floor = revision
            self._review, self._need_review = deepcopy(payload), False
        else:
            if payload["status"] == "resolved_offline":
                self._check_selection(payload)
            self._role, self._need_role = deepcopy(payload), False
        self._adapter_clock = clock
        return None

    def _check_selection(self, role: dict[str, Any]) -> None:
        if self._need_hello or self._need_snapshot or self._snapshot is None:
            raise ValueError("body_snapshot_required_for_role")
        message, body, evidence = self._snapshot, self._body.state, role["evidence"]
        expected = {"message_id": message["message_id"], "occurred_at": message["occurred_at"],
                    "transport_sequence": message["sequence"]}
        if (any(evidence[key] != message[key] for key in ("device_id", "boot_id", "session_id"))
                or evidence["snapshot"] != expected
                or evidence["last_event_sequence"] != body.last_event_sequence):
            raise ValueError("role_snapshot_evidence_mismatch")
        payload = message["payload"]
        shell, magnetic, selection = payload["shell_observation"], payload["magnetic"], role["selection"]
        if (payload["base"]["link"] != "online" or payload["base"]["mechanical_status"] != "ready"
                or not {"magnetic_sample", "shell_observation"} <= set(body.capabilities)
                or magnetic["state"] != "fresh" or shell["status"] != "known"
                or role["shell_observation"] != shell
                or selection["magnet_profile_id"] != shell["profile_id"]
                or selection["magnet_profile_revision"] != shell["profile_revision"]):
            raise ValueError("role_body_facts_mismatch")
        deadline = _integer(role["valid_until_ms"], "role_valid_until_ms")
        limit = _integer(role["freshness_limit_ms"], "freshness_limit_ms", minimum=1)
        if deadline <= self._now or deadline > role["clock_ms"] + limit:
            raise ValueError("role_expired_or_invalid_lease")
        if not magnetic.get("sample_at") or not shell.get("observed_at") or magnetic.get("age_ms") is None:
            raise ValueError("role_freshness_evidence_missing")
        occurred = datetime.fromisoformat(message["occurred_at"].replace("Z", "+00:00"))
        stamps = [datetime.fromisoformat(value.replace("Z", "+00:00"))
                  for value in (magnetic["sample_at"], shell["observed_at"])]
        ages = [math.ceil((occurred - stamp).total_seconds() * 1000) for stamp in stamps]
        if min(ages) < 0:
            raise ValueError("future_observation_timestamp")
        ages[0] = max(ages[0], _integer(magnetic["age_ms"], "magnetic_age_ms"))
        deadlines = [min(MAX_SAFE_INTEGER, self._now + limit - age) for age in ages]
        anchor = self._lease_anchor
        identity = (message["device_id"], message["boot_id"])
        if anchor is not None and anchor[:2] == identity:
            for index in range(2):
                if stamps[index] < anchor[index + 2]:
                    raise ValueError("observation_timestamp_regressed")
                if stamps[index] == anchor[index + 2]:
                    deadlines[index] = min(deadlines[index], anchor[index + 4])
        # A repeated observation may shorten its lease, never renew it.
        deadlines = [min(value, deadline) for value in deadlines]
        if min(deadlines) < deadline:
            raise ValueError("observation_lease_extended")
        self._lease_anchor = (*identity, *stamps, *deadlines)

    def _ack(self, payload: dict[str, Any]) -> None:
        previous = self._acks.get(payload["request_id"])
        decision = payload.get("original_result", payload["result"])
        if previous is not None:
            prior_decision = previous.get("original_result", previous["result"])
            if decision != prior_decision or payload["registry_revision"] != previous["registry_revision"]:
                raise ValueError("review_ack_conflict")
        self._acks[payload["request_id"]] = deepcopy(payload)
        self._acks.move_to_end(payload["request_id"])
        if len(self._acks) > self._max_acks:
            self._acks.popitem(last=False)
        # Acknowledgements never install a registry or select a role. A newer
        # result only revokes the old view pending the actual full state.
        if payload["registry_revision"] > self._revision_floor:
            self._revision_floor = payload["registry_revision"]
            self._invalidate("review_ack_requires_current_state", body=True)


__all__ = ["ClientRecordReceiver", "RecordOutcome", "CLOCK_DOMAIN"]
