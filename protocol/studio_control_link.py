"""E3c-6 computer-side state machine; the console worker owns all USB I/O."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import uuid

from protocol import expression_control as expressions
from protocol import studio_usb_session as session
from protocol import studio_installed_catalog as installed_catalog
from protocol.studio_device_roles import RoleQueryClient
from protocol import studio_device_roles as device_roles
from protocol.studio_display_query import EXPRESSION_SEMANTICS, reason as display_reason, supports
from protocol import studio_timing as timing

SCHEMA = "deskbot.studio_control_link/v1"
CATALOG = "42a6d3c6b062ab63daf23a95128382909b63b06aa4d2416cb1da9464411b4fb5"
ROLE = "role-default"
INTERVAL_MS = 400  # Firmware replies are limited to one request per 250 ms.
QUERY_MS = 1000


def expression_request_id(value):
    """Stable host-to-wire correlation, scoped to this connection/session."""
    identity = {k: value[k] for k in ('command_id', 'connection_id', 'target_id', 'boot_id', 'session_id')}
    raw = json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('ascii')
    return 'expression-' + hashlib.sha256(raw).hexdigest()[:32]


def initial_snapshot(connection_id=None):
    return dict(schema=SCHEMA, source="usb_session", connection_id=connection_id,
                enabled=False, status="off", reason="enable_to_query", target_id=None,
                boot_id=None, session_id=None, display=None, age_ms=None, can_submit=False,
                pending=None, receipts=[], local_result=None, last_command=None,
                catalog=initial_catalog(), roles=device_roles.initial(), role_attempt=None, frames_sent=0, rejected_frames=0)


def initial_catalog(status="idle", reason="not_requested", target_id=None, boot_id=None, session_id=None):
    return dict(status=status, reason=reason, target_id=target_id, boot_id=boot_id,
                session_id=session_id, received_entries=0, total=None, next_cursor=None, manifest=None)


COMMAND_OPERATIONS = ("enable", "disable", *EXPRESSION_SEMANTICS, "read_catalog")


def allowed_operation(value):
    return isinstance(value, str) and (value in COMMAND_OPERATIONS or device_roles.operation(value))


def validate_command(value):
    if (not isinstance(value, dict) or set(value) != {"operation", "command_id", "connection_id",
            "target_id", "boot_id", "session_id"}
            or not allowed_operation(value["operation"])
            or any(not session._id(value[k]) for k in ("command_id", "connection_id"))
            or any(value[k] is not None and not session._id(value[k])
                   for k in ("target_id", "boot_id", "session_id"))):
        raise ValueError("invalid_host_command")
    return deepcopy(value)


def aged_control(value, elapsed_ms, *, connected=True):
    row = deepcopy(value)
    if row["age_ms"] is not None:
        row["age_ms"] += max(0, int(elapsed_ms))
    if row["pending"] is not None:
        row["pending"]["age_ms"] += max(0, int(elapsed_ms))
    if not connected or row["age_ms"] is not None and row["age_ms"] >= timing.STATE_VALID_MS:
        row["display"] = None
        row["can_submit"] = False
        if row["enabled"]:
            row["status"] = "waiting" if connected else "disconnected"
            row["reason"] = "state_stale" if connected else "connection_lost"
    if "roles" in row:
        roles = row["roles"]
        if roles["age_ms"] is not None: roles["age_ms"] += max(0, int(elapsed_ms))
        if not connected or roles["age_ms"] is not None and roles["age_ms"] >= device_roles.VALID_MS:
            row["roles"] = device_roles.initial("stale" if connected else "disconnected")
    if not connected:
        row["role_attempt"] = None
        row["session_id"] = None
        row["enabled"] = False
        row["can_submit"] = False
        row["catalog"] = initial_catalog("disconnected", "connection_lost")
    return row


class ControlLink:
    """No automatic expression retry; an explicit click creates one request.

    Session replies and expression receipts have independent sequence domains.
    State age is measured from a correlated reply's arrival, never device uptime.
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
        self.outgoing_at = None
        self.receipts = deque(maxlen=24)
        self.command_ids = deque(maxlen=64)
        self.history = deque(maxlen=64)
        self.device_time = None
        self.hello = None
        self.open_sent_at = None
        self.session_established = False
        self.catalog = initial_catalog()
        self.catalog_collector = None
        self.catalog_pending = None
        self.roles = device_roles.initial()
        self.roles_enabled = False
        self.role_outgoing = self.role_pending = None
        self.role_received = None
        self.next_role = 0
        self.role_attempt = None

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
        self.outgoing_at = None
        self.client = None
        self.enabled = False
        self.reason = reason
        self.open_sent_at = None
        self.catalog = initial_catalog("disconnected", reason)
        self.catalog_collector = None
        self.catalog_pending = None
        self.connection_id = "connection-" + uuid.uuid4().hex
        self.roles = device_roles.initial("disconnected")
        self.roles_enabled = False
        self.role_outgoing = self.role_pending = self.role_received = None
        self.role_attempt = None

    def command(self, value, now, *, queued_at=None):
        value = validate_command(value)
        if value["command_id"] in self.command_ids:
            return
        self.command_ids.append(value["command_id"])
        why = None
        queued_at = now if queued_at is None else queued_at
        if not timing.monotonic_ms(queued_at) or queued_at > now:
            why = "command_clock_invalid"
        elif value["connection_id"] != self.connection_id:
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
                self.client = RoleQueryClient(self.target, self.boot, self.nonce())
                self.enabled, self.reason = True, "opening_session"
                self.sequence = self.receipt_sequence = 0
                self.device_time = None
                self.pending = self.outgoing = self.local_result = None
                self.outgoing_at = None
                self.history.clear()
                self.open_sent_at = None
                self.session_established = False
                self.next_query = now
                self.catalog = initial_catalog()
                self.catalog_collector = None
                self.catalog_pending = None
                self.roles = device_roles.initial()
                self.roles_enabled = False
                self.role_outgoing = self.role_pending = self.role_received = None
                self.next_role = now
                self.role_attempt = None
        elif value["session_id"] != (self.client.session_id if self.client else None):
            why = "session_changed"
        elif value["operation"] == "disable":
            # Lease expiry closes the device session; no second serial owner.
            self.disconnect(now, "query_stopped")
        elif device_roles.operation(value["operation"]):
            snapshot = self.snapshot(now)
            roles = snapshot["roles"]
            if not self.enabled or not self.client or not self.client.session_id:
                why = "session_required"
            elif self.pending or self.outgoing or self.role_outgoing or self.catalog["status"] == "reading":
                why = "pending_request"
            elif value["operation"] == "read_roles":
                self.roles_enabled = True
                self.next_role = now
            elif now - queued_at >= timing.COMMAND_QUEUE_MS:
                why = "request_expired"
            elif not roles["reply"] or not roles["reply"]["loaded"]:
                why = "roles_unavailable"
            elif value["operation"].rsplit(".", 1)[-1] != roles["reply"]["graph_sha256"]:
                why = "graph_changed"
            elif int(value["operation"].split(".")[2]) >= len(roles["reply"]["roles"]):
                why = "role_missing"
            else:
                self.role_outgoing = dict(operation=value["operation"], queued_at=queued_at,
                                          command_id=value["command_id"])
        elif value["operation"] == "read_catalog":
            if not self.enabled or not self.client or not self.client.session_id:
                why = "session_required"
            elif self.pending or self.outgoing or self.role_outgoing:
                # Complete the explicit expression before starting pagination.
                why = "pending_request"
            elif self.catalog["status"] == "reading":
                why = "catalog_already_reading"
            else:
                self.catalog_collector = installed_catalog.CatalogCollector(
                    self.target, self.boot, self.client.session_id)
                self.catalog = initial_catalog("reading", "reading_pages", self.target,
                                               self.boot, self.client.session_id)
                self.catalog["next_cursor"] = 0
                self.catalog_pending = None
        elif now - queued_at >= timing.COMMAND_QUEUE_MS:
            why = "request_expired"
        elif self.role_outgoing or self.role_pending:
            why = "pending_request"
        elif not self.snapshot(now)["can_submit"]:
            why = "request_not_ready"
        elif not supports(self.client.snapshot(now)["state"], value["operation"]):
            why = "expression_interface_pending"
        else:
            self.sequence += 1
            request = expressions.validate_request(dict(schema=expressions.SCHEMA, message_type="request",
                request_id=expression_request_id(value), target_id=self.target, boot_id=self.boot,
                session_id=self.client.session_id, sequence=self.sequence, catalog_id=CATALOG, role_id=ROLE,
                issued_at=self.wall(), expires_after_ms=timing.EXECUTION_TTL_MS,
                command={"kind": "play_expression", "semantic_id": value["operation"]}))
            self.outgoing = request
            self.outgoing_at = queued_at
            self.local_result = None
        self.last_command = dict(command_id=value["command_id"], operation=value["operation"],
                                 status="rejected" if why else "processed", reason=why or "command_processed")

    def tick(self, now):
        if not self.enabled or not self.client:
            return None
        self.client.snapshot(now)
        if (self.catalog_pending and not self.client._pending
                and now - self.catalog_pending["sent"] >= timing.REQUEST_TIMEOUT_MS):
            self._fail_catalog("page_timeout")
        if not self.client.session_id and self.open_sent_at is not None:
            if self.client._pending:
                return None
            reason = "lease_expired" if self.session_established else "session_reply_timeout"
            self.disconnect(now, reason)
            return None
        if self.role_pending and not self.client._pending:
            self.roles = device_roles.initial("failed", "role_reply_timeout")
            self.role_pending = None
            self.role_received = None
        self._expire_expression(now)
        if self.pending:
            return None
        if now - self.last_sent < INTERVAL_MS:
            return None
        if self.client._pending:
            return None
        if not self.client.session_id:
            # A session is opened once per explicit enable; never revive an expired lease.
            wire = self.client.request("open", now)
            self.open_sent_at = now
            self.reason = "opening_session"
        elif self.role_outgoing:
            queued, self.role_outgoing = self.role_outgoing, None
            if now - queued["queued_at"] >= timing.COMMAND_QUEUE_MS:
                self.roles["error"] = "request_expired"
                return None
            wire = self.client.request_roles(queued["operation"], now)
            self.role_pending = dict(self.client._pending)
            # Preserve host-to-wire correlation while later role queries replace the reply.
            self.role_attempt = dict(command_id=queued["command_id"], operation=queued["operation"],
                                     request_id=self.role_pending["request_id"])
            self.roles["pending"] = queued["operation"]
        elif self.outgoing:
            request, self.outgoing = self.outgoing, None
            queued_at, self.outgoing_at = self.outgoing_at, None
            if now - queued_at >= timing.COMMAND_QUEUE_MS:
                self.local_result = dict(request_id=request["request_id"], outcome="cancelled", reason="request_expired")
                return None
            state = self.client.snapshot(now)["state"]
            if (request["session_id"] != self.client.session_id or not state
                    or display_reason(state) != "sleep_unknown" or self.catalog["status"] == "reading"):
                self.local_result = dict(request_id=request["request_id"], outcome="cancelled", reason="request_not_ready")
                return None
            if not supports(state, request["command"]["semantic_id"]):
                self.local_result = dict(request_id=request["request_id"], outcome="cancelled", reason="expression_interface_pending")
                return None
            wire = expressions.canonical(request) + b"\n"
            self.pending = dict(request=request, sent=now, accepted=False, accepted_at=None)
            self.history.append(request)
        elif self.catalog["status"] == "reading":
            cursor = self.catalog["next_cursor"]
            wire = self.client.request_catalog(0 if cursor is None else cursor, now)
            self.catalog_pending = dict(request_id=self.client._pending["request_id"],
                                        cursor=0 if cursor is None else cursor, sent=now)
        elif self.roles_enabled and now >= self.next_role and self.client.snapshot(now)["state"] is not None and now < self.next_query:
            wire = self.client.request_roles("role.query", now)
            self.role_pending = dict(self.client._pending)
            self.roles["pending"] = "read_roles"
        elif now >= self.next_query:
            wire = self.client.request("display", now)
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
            self._expire_expression(now)
            if value.get("schema") == device_roles.SCHEMA:
                row = self.client.accept_roles(value, now)
                self.roles = dict(status="current", reply=row, age_ms=0, pending=None,
                                  error=row["reason"] if row["result"] == "rejected" else None)
                self.role_received = now
                self.role_pending = None
                self.next_role = now + QUERY_MS
                return
            if value.get("schema") == installed_catalog.SCHEMA:
                pending = self.catalog_pending
                if not pending or not self.catalog_collector:
                    raise ValueError("catalog_request_missing")
                row = self.client.accept_catalog(value, now)
                manifest = self.catalog_collector.accept(row, request_id=pending["request_id"],
                                                         cursor=pending["cursor"])
                self.catalog_pending = None
                if manifest is None:
                    self.catalog.update(reason="reading_pages", received_entries=len(self.catalog_collector.entries),
                                        total=self.catalog_collector.header["total"],
                                        next_cursor=row["next_cursor"])
                else:
                    self.catalog = initial_catalog("complete", "verified", self.target, self.boot,
                                                   self.client.session_id)
                    self.catalog.update(received_entries=len(manifest["entries"]),
                                        total=len(manifest["entries"]), manifest=manifest)
                    self.next_query = now
                return
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
                        if not self.pending["accepted"]:
                            self.pending["accepted"] = True
                            self.pending["accepted_at"] = now
                    else:
                        self.pending = None
                        self.next_query = now
                terminal = row["original_status"] if row["status"] == "duplicate" else row["status"]
                if (terminal != "accepted" and self.local_result
                        and self.local_result["request_id"] == request["request_id"]):
                    self.local_result = None
                return
            was_role = self.client._pending and self.client._pending["operation"].startswith("role.")
            row = self.client.accept(value, now)
            if was_role:
                self.role_pending = None
                self.roles = device_roles.initial("failed", row["reason"])
                self.role_received = None
                if row["reason"] == "unsupported_operation": self.roles_enabled = False
                self.next_role = now + QUERY_MS
                if self.client.session_id: return
            if row["schema"] == "deskbot.studio_display/v1":
                self.reason = display_reason(row)
                self.next_query = now + QUERY_MS
            elif row["state"] != "ready":
                self.reason = row["reason"]
                self.enabled = False
                self.pending = self.outgoing = None
                self.catalog = initial_catalog("disconnected", "session_unavailable")
                self.catalog_collector = None
                self.catalog_pending = None
            else:
                self.session_established = True
                self.reason = "querying_display"
                self.next_query = now
        except (ValueError, TypeError, KeyError, AttributeError):
            self.rejected += 1
            # A late old page cannot cancel the current request or collector.
            if (self.catalog_pending and isinstance(value, dict)
                    and value.get("request_id") == self.catalog_pending["request_id"]):
                if self.client:
                    self.client._pending = None
                self._fail_catalog("invalid_page")

    def _expire_expression(self, now):
        if not self.pending:
            return
        anchor = self.pending["accepted_at"] if self.pending["accepted"] else self.pending["sent"]
        limit = timing.ACCEPTED_TIMEOUT_MS if self.pending["accepted"] else timing.REQUEST_TIMEOUT_MS
        if now - anchor >= limit:
            self.local_result = dict(request_id=self.pending["request"]["request_id"],
                                     outcome="timeout", reason="device_receipt_timeout")
            self.pending = None
            # Keep history for late facts; refresh state without replaying intent.
            self.next_query = now

    def _fail_catalog(self, reason):
        if self.catalog_collector:
            self.catalog_collector.disconnect()
        self.catalog_collector = None
        self.catalog_pending = None
        self.catalog = initial_catalog("failed", reason, self.target, self.boot,
                                       self.client.session_id if self.client else None)
        self.next_query = 0

    def snapshot(self, now):
        result = initial_snapshot(self.connection_id)
        cached = self.client.snapshot(now) if self.client else {"state": None, "age_ms": None}
        if (self.catalog["status"] in ("reading", "complete") and
                (not self.client or not self.client.connected or
                 self.catalog["session_id"] != self.client.session_id)):
            if self.catalog_collector:
                self.catalog_collector.disconnect()
            self.catalog_collector = self.catalog_pending = None
            self.catalog = initial_catalog("disconnected", "session_unavailable")
        display = cached["state"] if self.enabled else None
        ready = bool(not self.role_outgoing and display and display_reason(display) == "sleep_unknown" and self.client.session_id
                     and not self.client._pending and not self.pending and not self.outgoing
                     and self.catalog["status"] != "reading")
        roles = deepcopy(self.roles)
        if self.role_received is not None and roles["reply"] is not None:
            roles["age_ms"] = now - self.role_received
            if roles["age_ms"] >= device_roles.VALID_MS or not self.client or roles["reply"]["session_id"] != self.client.session_id:
                roles = device_roles.initial("stale")
        result["roles"] = roles
        result["role_attempt"] = deepcopy(self.role_attempt)
        result.update(enabled=self.enabled, status="pending" if self.pending or self.outgoing else
                      "ready" if ready else "querying" if self.enabled and not display else
                      "busy" if display else "off", reason=self.reason,
            target_id=self.target, boot_id=self.boot, session_id=self.client.session_id if self.client else None,
            display=display, age_ms=cached["age_ms"], can_submit=ready, receipts=list(self.receipts),
            pending=None if not self.pending else dict(request_id=self.pending["request"]["request_id"],
                phase="accepted" if self.pending["accepted"] else "waiting", age_ms=now-self.pending["sent"]),
            local_result=self.local_result, last_command=self.last_command, catalog=self.catalog,
            frames_sent=self.frames_sent,
            rejected_frames=self.rejected)
        return deepcopy(result)
