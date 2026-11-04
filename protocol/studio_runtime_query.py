"""Read-only state codec and single-connection client; transport is caller-owned."""
from copy import deepcopy
import hashlib
import json

from protocol import studio_usb_session as session

SCHEMA = "deskbot.studio_state/v1"
APPLICATIONS = ("starting", "wifi_configuring", "idle", "connecting", "listening", "speaking",
                "upgrading", "activating", "audio_testing", "fatal_error")
STATE_VALID_MS = 2000
REQUEST_TIMEOUT_MS = 2000
MAX_CLOCK = 9007199254740991


def validate_reply(value):
    keys = {"schema", "message_type", "request_id", "target_id", "boot_id", "sequence",
            "session_id", "at_ms", "lease_ms", "application", "page", "sleep", "controls_available"}
    if (not isinstance(value, dict) or set(value) != keys or value["schema"] != SCHEMA
            or value["message_type"] != "state" or value["sleep"] != "unknown"):
        raise ValueError("invalid_state_reply")
    common = {key: value[key] for key in keys - {"application", "page", "sleep"}}
    common.update(schema=session.SCHEMA, message_type="session", state="ready", reason="state_read")
    session.validate_reply(common)
    for name, allowed in (("application", APPLICATIONS), ("page", ("home", "other"))):
        channel = value[name]
        if (not isinstance(channel, dict) or set(channel) != {"revision", "phase", "value"}
                or type(channel["revision"]) is not int or not 0 <= channel["revision"] <= 0xffffffff
                or channel["phase"] not in ("unknown", "transitioning", "settled")
                or (channel["phase"] != "unknown" and channel["revision"] == 0)
                or (channel["phase"] == "settled" and channel["value"] not in allowed)
                or (channel["phase"] != "settled" and channel["value"] is not None)):
            raise ValueError("invalid_state_channel")
    return deepcopy(value)


def decode_reply(raw):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 1024:
        raise ValueError("state_reply_size")
    try:
        return validate_reply(json.loads(raw.decode("ascii"), object_pairs_hook=session._unique))
    except (UnicodeError, RecursionError) as error:
        raise ValueError("state_reply_json") from error


class RuntimeQueryClient:
    """One pending request and one state; local clocks never use device uptime.

    The USB owner supplies a fresh nonce per logical connection and routes
    decoded replies here. Nothing in this class opens or writes a serial port.
    """
    QUERY_OPERATION = "state"
    REPLY_SCHEMA = SCHEMA
    validate_state_reply = staticmethod(validate_reply)

    def __init__(self, target_id, boot_id, client_nonce):
        self.target_id = target_id
        self.boot_id = boot_id
        self.nonce = client_nonce
        self._validate_identity()
        self._now = None
        self._counter = 0
        self._reset(True)

    def _validate_identity(self):
        if (not isinstance(self.target_id, str) or not session.DEVICE.fullmatch(self.target_id)
                or not isinstance(self.boot_id, str) or not session.BOOT.fullmatch(self.boot_id)
                or not session._id(self.nonce)):
            raise ValueError("invalid_connection_identity")

    def _reset(self, connected):
        self.connected = connected
        self.session_id = None
        self._lease = 0
        self._sequence = 0
        self._device_time = None
        self._pending = None
        self._cached = None
        self._last_request = None

    def disconnect(self):
        self._reset(False)

    def reconnect(self, client_nonce, *, boot_id=None):
        if client_nonce == self.nonce:
            raise ValueError("new_connection_requires_new_nonce")
        old_nonce, old_boot = self.nonce, self.boot_id
        self.nonce, self.boot_id = client_nonce, self.boot_id if boot_id is None else boot_id
        try:
            self._validate_identity()
        except ValueError:
            self.nonce, self.boot_id = old_nonce, old_boot
            raise
        self._reset(True)

    def _tick(self, now):
        if type(now) is not int or not 0 <= now <= MAX_CLOCK:
            raise ValueError("invalid_local_clock")
        if self._now is not None and now < self._now:
            self.disconnect()
            raise ValueError("local_clock_moved_backwards")
        self._now = now
        if self.session_id and now >= self._lease:
            self.session_id = None
            self._cached = None
        if self._pending and now - self._pending["sent"] >= REQUEST_TIMEOUT_MS:
            self._pending = None
            self._cached = None
        if self._cached and now >= self._cached["until"]:
            self._cached = None

    def request(self, operation, now):
        self._tick(now)
        if (not self.connected or operation not in ("open", self.QUERY_OPERATION) or self._pending
                or (operation == self.QUERY_OPERATION and not self.session_id)
                or (self._last_request is not None and now - self._last_request < session.REPLY_INTERVAL_MS)
                or self._counter == 0xffffffff):
            raise ValueError("request_not_ready")
        self._counter += 1
        token = hashlib.sha256(self.nonce.encode("ascii")).hexdigest()[:24]
        request_id = f"rq-{token}-{self._counter}"
        wire = session.encode_request(operation=operation, request_id=request_id, target_id=self.target_id,
                                      boot_id=self.boot_id, client_nonce=self.nonce,
                                      session_id=self.session_id if operation == self.QUERY_OPERATION else None)
        self._pending = dict(operation=operation, request_id=request_id, sent=now, session_id=self.session_id)
        self._last_request = now
        if operation == "open":
            self.session_id = None
            self._cached = None
        return wire

    def accept(self, value, now):
        self._tick(now)
        if not self.connected or not self._pending:
            raise ValueError("unsolicited_reply")
        if not isinstance(value, dict):
            raise ValueError("invalid_reply")
        row = self.validate_state_reply(value) if value.get("schema") == self.REPLY_SCHEMA else session.validate_reply(value)
        if row["target_id"] != self.target_id:
            raise ValueError("reply_target_mismatch")
        if row["boot_id"] != self.boot_id:
            self.disconnect()
            raise ValueError("reply_boot_changed")
        if row["request_id"] != self._pending["request_id"]:
            raise ValueError("reply_request_mismatch")
        if row["sequence"] <= self._sequence:
            raise ValueError("reply_replayed")
        if self._device_time is not None and row["at_ms"] < self._device_time:
            self.disconnect()
            raise ValueError("device_clock_moved_backwards")
        pending = self._pending
        if row["schema"] == self.REPLY_SCHEMA:
            if pending["operation"] != self.QUERY_OPERATION or row["session_id"] != pending["session_id"]:
                raise ValueError("reply_session_mismatch")
            self.session_id = row["session_id"]
            self._cached = dict(value=row, sent=pending["sent"], until=pending["sent"] + STATE_VALID_MS)
            self._lease = pending["sent"] + session.LEASE_MS
        elif row["state"] == "ready":
            if pending["operation"] != "open" or row["reason"] not in ("session_opened", "session_resumed"):
                raise ValueError("unexpected_session_reply")
            self.session_id = row["session_id"]
            self._lease = pending["sent"] + session.LEASE_MS
        else:
            self._cached = None
            if row["reason"] in ("session_changed", "session_closed", "lease_expired", "boot_changed"):
                self.session_id = None
        self._sequence, self._device_time = row["sequence"], row["at_ms"]
        self._pending = None
        return deepcopy(row)

    def snapshot(self, now):
        self._tick(now)
        if self._cached and self.session_id:
            return dict(state=deepcopy(self._cached["value"]), age_ms=now-self._cached["sent"], reason="current")
        return dict(state=None, age_ms=None, reason="disconnected" if not self.connected else
                    "session_required" if not self.session_id else "state_unavailable")
