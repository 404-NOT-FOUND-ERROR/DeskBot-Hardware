"""USB diagnostic contract. No serial access or expression execution."""
from __future__ import annotations

from copy import deepcopy
import json
import re

SCHEMA = "deskbot.studio_session/v1"
LEASE_MS = 30000
REPLY_INTERVAL_MS = 250
MAX_FRAME_BYTES = 2048
PARTIAL_TIMEOUT_MS = 2000
ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,95}\Z")
DEVICE = re.compile(r"device-[a-f0-9]{32}\Z")
BOOT = re.compile(r"boot-[a-f0-9]{32}\Z")
SESSION = re.compile(r"session-[a-f0-9]{32}-[1-9][0-9]{0,9}\Z")


def _id(value):
    return isinstance(value, str) and ID.fullmatch(value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def validate_request(value):
    keys = {"schema", "operation", "request_id", "target_id", "boot_id", "client_nonce", "session_id"}
    if (not isinstance(value, dict) or set(value) != keys or value["schema"] != SCHEMA
            or any(not _id(value[k]) for k in keys - {"schema", "session_id"})
            or (value["session_id"] is not None and not _id(value["session_id"]))):
        raise ValueError("invalid_session_request")
    return deepcopy(value)


def decode_request(raw):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_FRAME_BYTES:
        raise ValueError("session_frame_size")
    try:
        return validate_request(json.loads(raw.decode("ascii"), object_pairs_hook=_unique))
    except (UnicodeError, RecursionError) as error:
        raise ValueError("session_json") from error


def encode_request(*, operation, request_id, target_id, boot_id, client_nonce, session_id=None):
    value = validate_request(dict(schema=SCHEMA, operation=operation, request_id=request_id,
                                  target_id=target_id, boot_id=boot_id, client_nonce=client_nonce,
                                  session_id=session_id))
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"


def validate_reply(value):
    keys = {"schema", "message_type", "request_id", "target_id", "boot_id", "sequence",
            "session_id", "state", "reason", "at_ms", "lease_ms", "controls_available"}
    if (not isinstance(value, dict) or set(value) != keys or value["schema"] != SCHEMA
            or value["message_type"] != "session" or not _id(value["request_id"])
            or not isinstance(value["target_id"], str) or not DEVICE.fullmatch(value["target_id"])
            or not isinstance(value["boot_id"], str) or not BOOT.fullmatch(value["boot_id"])
            or type(value["sequence"]) is not int or not 1 <= value["sequence"] <= 0xffffffff
            or type(value["at_ms"]) is not int or not 0 <= value["at_ms"] <= 9007199254740991
            or type(value["lease_ms"]) is not int or value["lease_ms"] not in (0, LEASE_MS)
            or value["controls_available"] is not False or not _id(value["reason"])
            or value["state"] not in {"ready", "closed", "rejected"}):
        raise ValueError("invalid_session_reply")
    session = value["session_id"]
    if value["state"] == "rejected":
        if session is not None or value["lease_ms"] != 0:
            raise ValueError("rejected_session_disclosure")
    elif (not isinstance(session, str) or not SESSION.fullmatch(session)
          or not session.startswith("session-" + value["boot_id"][5:] + "-")
          or not 1 <= int(session.rsplit("-", 1)[1]) <= 0xffffffff
          or value["lease_ms"] != (LEASE_MS if value["state"] == "ready" else 0)):
        raise ValueError("invalid_session_identity")
    return deepcopy(value)
