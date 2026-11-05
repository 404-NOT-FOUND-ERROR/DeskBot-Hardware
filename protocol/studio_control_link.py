"""E3c-6 computer-side state machine; the console worker owns all USB I/O."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import uuid

from protocol import expression_control as expressions
from protocol import studio_usb_session as session
from protocol.studio_display_query import DisplayQueryClient, reason as display_reason

SCHEMA = "deskbot.studio_control_link/v1"
CATALOG = "42a6d3c6b062ab63daf23a95128382909b63b06aa4d2416cb1da9464411b4fb5"
ROLE = "role-default"
INTERVAL_MS = 400  # Firmware replies are limited to one request per 250 ms.
QUERY_MS = 1000


def initial_snapshot(connection_id=None):
    return dict(schema=SCHEMA, source="usb_session", connection_id=connection_id,
                enabled=False, status="off", reason="enable_to_query", target_id=None,
                boot_id=None, session_id=None, display=None, age_ms=None, can_submit=False,
                pending=None, receipts=[], local_result=None, last_command=None,
                frames_sent=0, rejected_frames=0)


def validate_command(value):
    if (not isinstance(value, dict) or set(value) != {"operation", "command_id", "connection_id",
            "target_id", "boot_id", "session_id"}
            or value["operation"] not in ("enable", "disable", "neutral")
            or any(not session._id(value[k]) for k in ("command_id", "connection_id"))
            or any(value[k] is not None and not session._id(value[k])
                   for k in ("target_id", "boot_id", "session_id"))):
        raise ValueError("invalid_host_command")
    return deepcopy(value)


def aged_control(value, elapsed_ms, *, connected=True):
    row = deepcopy(value)
    if row["age_ms"] is not None:
        row["age_ms"] += max(0, int(elapsed_ms))
    if not connected or row["age_ms"] is not None and row["age_ms"] >= 2000:
        row["display"] = None
        row["can_submit"] = False
        if row["enabled"]:
            row["status"] = "waiting" if connected else "disconnected"
            row["reason"] = "state_stale" if connected else "connection_lost"
    if not connected:
        row["session_id"] = None
        row["enabled"] = False
        row["can_submit"] = False
    return row


class ControlLink:
    """No automatic expression retry; an explicit click creates one request.

    Session replies and expression receipts have independent sequence domains.
    State age is measured from the local request time, never device uptime.
    """
    def __init__(self, *, nonce=lambda: "client-" + uuid.uuid4().hex,
                 wall=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")):
        self.nonce, self.wall = nonce, wall
        self.connection_id = "connection-" + uuid.uuid4().hex
        self.client = None
        self.enabled = False
        self.target = self.boot = None
        self.reason = "enable_to_query"
        self.frames_sent = self.rejected = self.sequence = self.receipt_sequence = 0
        self.last_sent = -INTERVAL_MS
        self.next_query = 0
        self.outgoing = self.pending = self.local_result = self.last_command = None
        self.receipts = deque(maxlen=24)
        self.command_ids = deque(maxlen=64)
        self.history = deque(maxlen=64)
        self.device_time = None
        self.hello = None
        self.open_sent_at = None
        self.session_established = False

    def observe(self, hello, now):
        self.hello = hello
        identity = (hello["device_id"], hello["boot_id"]) if hello else (None, None)
        if self.enabled and identity != (self.target, self.boot):
            self.disconnect(now, "identity_changed" if hello else "identity_unavailable")

    def disconnect(self, now, reason="connection_lost"):
        if self.pending:
            self.local_result = dict(request_id=self.pending["request"]["request_id"],
                                     outcome="cancelled" if reason == "query_stopped" else "connection_lost", reason=reason)
        if self.client:
            self.client.disconnect()
        self.pending = self.outgoing = None
        self.client = None
        self.enabled = False
        self.reason = reason
        self.open_sent_at = None
        self.connection_id = "connection-" + uuid.uuid4().hex

    def command(self, value, now):
        value = validate_command(value)
        if value["command_id"] in self.command_ids:
            return
        self.command_ids.append(value["command_id"])
        why = None
        if value["connection_id"] != self.connection_id:
            why = "connection_changed"
        elif not self.hello or (value["target_id"], value["boot_id"]) != (self.hello["device_id"], self.hello["boot_id"]):
            why = "identity_unavailable"
        elif value["operation"] == "enable":
            if self.enabled:
                why = "already_enabled"
            elif value["session_id"] is not None:
                why = "session_changed"
            else:
                self.target, self.boot = self.hello["device_id"], self.hello["boot_id"]
                self.client = DisplayQueryClient(self.target, self.boot, self.nonce())
                self.enabled, self.reason = True, "opening_session"
                self.sequence = self.receipt_sequence = 0
                self.device_time = None
                self.pending = self.outgoing = self.local_result = None
                self.history.clear()
                self.open_sent_at = None
                self.session_established = False
                self.next_query = now
        elif value["session_id"] != (self.client.session_id if self.client else None):
            why = "session_changed"
        elif value["operation"] == "disable":
            # Lease expiry closes the device session; no second serial owner.
            self.disconnect(now, "query_stopped")
        elif not self.snapshot(now)["can_submit"]:
            why = "request_not_ready"
        else:
            self.sequence += 1
            request = expressions.validate_request(dict(schema=expressions.SCHEMA, message_type="request",
                request_id="expression-" + uuid.uuid4().hex, target_id=self.target, boot_id=self.boot,
                session_id=self.client.session_id, sequence=self.sequence, catalog_id=CATALOG, role_id=ROLE,
                issued_at=self.wall(), expires_after_ms=2000, command={"kind": "play_expression", "semantic_id": "neutral"}))
            self.outgoing = request
            self.local_result = None
        self.last_command = dict(command_id=value["command_id"], operation=value["operation"],
                                 status="rejected" if why else "processed", reason=why or "command_processed")

    def tick(self, now):
        if not self.enabled or not self.client:
            return None
        self.client.snapshot(now)
        if not self.client.session_id and self.open_sent_at is not None:
            if self.client._pending:
                return None
            reason = "lease_expired" if self.session_established else "session_reply_timeout"
            self.disconnect(now, reason)
            return None
        if self.pending:
            limit = 3000 if self.pending["accepted"] else 2000
            if now - self.pending["sent"] < limit:
                return None
            self.local_result = dict(request_id=self.pending["request"]["request_id"],
                                     outcome="timeout", reason="device_receipt_timeout")
            self.pending = None
            # A lost terminal receipt leaves execution unknown: query again,
            # never resend the expression.
            self.next_query = now
        if now - self.last_sent < INTERVAL_MS:
            return None
        if self.client._pending:
            return None
        if not self.client.session_id:
            # A session is opened once per explicit enable; never revive an expired lease.
            wire = self.client.request("open", now)
            self.open_sent_at = now
            self.reason = "opening_session"
        elif self.outgoing:
            request, self.outgoing = self.outgoing, None
            state = self.client.snapshot(now)["state"]
            if request["session_id"] != self.client.session_id or not state or display_reason(state) != "sleep_unknown":
                self.local_result = dict(request_id=request["request_id"], outcome="cancelled", reason="request_not_ready")
                return None
            wire = expressions.canonical(request) + b"\n"
            self.pending = dict(request=request, sent=now, accepted=False)
            self.history.append(request)
        elif now >= self.next_query:
            wire = self.client.request("display", now)
            self.next_query = now + QUERY_MS
            self.reason = "querying_display"
        else:
            return None
        self.last_sent = now
        self.frames_sent += 1
        return wire

    def accept(self, value, now):
        try:
            if not self.enabled or not self.client:
                raise ValueError("session_not_active")
            self.client.snapshot(now)
            if value.get("schema") == expressions.SCHEMA:
                request = next((r for r in self.history if r["request_id"] == value.get("request_id")), None)
                if request is None:
                    raise ValueError("unknown_request")
                row = expressions.validate_receipt(value, request, receiver=dict(target_id=self.target,
                    boot_id=self.boot, session_id=self.client.session_id))
                if row["source"] != "device" or row["sequence"] <= self.receipt_sequence:
                    raise ValueError("receipt_replayed")
                if self.device_time is not None and row["at_ms"] < self.device_time:
                    raise ValueError("receipt_clock_reset")
                self.receipt_sequence, self.device_time = row["sequence"], row["at_ms"]
                self.receipts.append(row)
                if self.pending and request["request_id"] == self.pending["request"]["request_id"]:
                    status = row["original_status"] if row["status"] == "duplicate" else row["status"]
                    if status == "accepted":
                        self.pending["accepted"] = True
                    else:
                        self.pending = None
                        self.next_query = now
                terminal = row["original_status"] if row["status"] == "duplicate" else row["status"]
                if (terminal != "accepted" and self.local_result
                        and self.local_result["request_id"] == request["request_id"]):
                    self.local_result = None
                return
            row = self.client.accept(value, now)
            if row["schema"] == "deskbot.studio_display/v1":
                self.reason = display_reason(row)
            elif row["state"] != "ready":
                self.reason = row["reason"]
                self.enabled = False
                self.pending = self.outgoing = None
            else:
                self.session_established = True
                self.reason = "querying_display"
                self.next_query = now
        except (ValueError, TypeError, KeyError, AttributeError):
            self.rejected += 1

    def snapshot(self, now):
        result = initial_snapshot(self.connection_id)
        cached = self.client.snapshot(now) if self.client else {"state": None, "age_ms": None}
        display = cached["state"] if self.enabled else None
        ready = bool(display and display_reason(display) == "sleep_unknown" and self.client.session_id
                     and not self.pending and not self.outgoing)
        result.update(enabled=self.enabled, status="pending" if self.pending or self.outgoing else
                      "ready" if ready else "querying" if self.enabled and not display else
                      "busy" if display else "off", reason=self.reason,
            target_id=self.target, boot_id=self.boot, session_id=self.client.session_id if self.client else None,
            display=display, age_ms=cached["age_ms"], can_submit=ready, receipts=list(self.receipts),
            pending=None if not self.pending else dict(request_id=self.pending["request"]["request_id"],
                phase="accepted" if self.pending["accepted"] else "waiting", age_ms=now-self.pending["sent"]),
            local_result=self.local_result, last_command=self.last_command, frames_sent=self.frames_sent,
            rejected_frames=self.rejected)
        return deepcopy(result)
