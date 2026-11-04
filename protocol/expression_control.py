"""Versioned expression directory and transport-independent message validation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re

SCHEMA = "deskbot.expression_control/v1"
LIMITS = {"max_inflight": 1, "max_history": 24, "max_dedup": 64, "default_timeout_ms": 3000}
ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,95}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
REASON = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _keys(value, names) -> bool:
    return isinstance(value, dict) and set(value) == set(names)


def _id(value) -> bool:
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _integer(value, minimum, maximum=9_007_199_254_740_991) -> bool:
    return type(value) is int and minimum <= value <= maximum


def build_catalog(studio: dict) -> dict:
    """Describe the accepted E1.18 mapping, separate from editable role drafts."""
    actions = [{"action_id": row["id"], "label": row["label"], "wire_command": row["wire_command"],
                "wire_value": row["wire_value"], "completion": "fact_only"} for row in studio["actions"]]
    expressions = [{"semantic_id": name, "label": row["label"], "resource_id": row["resource_id"],
                    "layout_id": row["layout_id"], "fps": row["fps"], "loop": row["loop"],
                    "requestable": name != "listen", "implicit_action_id": row["action_id"]}
                   for name, row in studio["semantics"].items()]
    if any(studio["assets"][row["resource_id"]]["origin"] != "official" for row in expressions):
        raise ValueError("saved E1.18 mapping contains a candidate resource")
    catalog = {"schema": "deskbot.expression_catalog/v1", "library_catalog_id": studio["catalog_id"],
               "revision": "E1.18", "role_id": "role-default", "source": "saved_release_record", "live": False,
               "device": deepcopy(studio["device"]), "expressions": expressions, "actions": actions,
               "limits": dict(LIMITS)}
    catalog["catalog_id"] = hashlib.sha256(canonical(catalog)).hexdigest()
    validate_catalog(catalog)
    return catalog


def validate_catalog(value: dict) -> dict:
    expected = {"schema", "catalog_id", "library_catalog_id", "revision", "role_id", "source", "live",
                "device", "expressions", "actions", "limits"}
    if (not _keys(value, expected) or value["schema"] != "deskbot.expression_catalog/v1"
            or not isinstance(value["catalog_id"], str) or not HASH.fullmatch(value["catalog_id"])
            or not isinstance(value["library_catalog_id"], str) or not HASH.fullmatch(value["library_catalog_id"])
            or value["revision"] != "E1.18" or not _id(value["role_id"])
            or value["source"] != "saved_release_record" or value["live"] is not False
            or not _keys(value["device"], {"source", "live", "app", "bootloader", "assets", "layout", "flash_bytes", "psram_bytes"})
            or value["device"].get("source") != "saved_release_record" or value["device"].get("live") is not False
            or not _id(value["device"].get("app")) or not _id(value["device"].get("bootloader"))
            or value["device"].get("assets") != "E1.18"
            or value["device"].get("layout") != "upper-assets-8m"
            or type(value["device"].get("flash_bytes")) is not int or value["device"]["flash_bytes"] != 33554432
            or type(value["device"].get("psram_bytes")) is not int or value["device"]["psram_bytes"] != 16777216
            or canonical(value["limits"]) != canonical(LIMITS)):
        raise ValueError("invalid saved expression catalog")
    if not isinstance(value["expressions"], list) or not 1 <= len(value["expressions"]) <= 128:
        raise ValueError("invalid expression bounds")
    if not isinstance(value["actions"], list) or len(value["actions"]) > 32:
        raise ValueError("invalid action bounds")
    actions, wires, semantics = set(), set(), set()
    for row in value["actions"]:
        if (not _keys(row, {"action_id", "label", "wire_command", "wire_value", "completion"})
                or not _id(row["action_id"]) or row["action_id"] in actions
                or not isinstance(row["label"], str) or not row["label"]
                or type(row["wire_command"]) is not int or row["wire_command"] != 2
                or not _integer(row["wire_value"], 0, 4) or row["completion"] != "fact_only"
                or (row["wire_command"], row["wire_value"]) in wires):
            raise ValueError("invalid action catalog entry")
        actions.add(row["action_id"])
        wires.add((row["wire_command"], row["wire_value"]))
    for row in value["expressions"]:
        if isinstance(row, dict) and isinstance(row.get("semantic_id"), str) and row["semantic_id"] in semantics:
            raise ValueError("duplicate semantic in expression catalog")
        if (not _keys(row, {"semantic_id", "label", "resource_id", "layout_id", "fps", "loop", "requestable", "implicit_action_id"})
                or not _id(row["semantic_id"]) or row["semantic_id"] in semantics
                or not isinstance(row["label"], str) or not row["label"]
                or not isinstance(row["resource_id"], str) or not re.fullmatch(r"[A-Za-z0-9_]+\.eaf", row["resource_id"])
                or not isinstance(row["layout_id"], str) or row["layout_id"] not in {"eye_anim", "listen_anim"}
                or not _integer(row["fps"], 1, 60)
                or type(row["loop"]) is not bool or type(row["requestable"]) is not bool
                or (row["implicit_action_id"] is not None
                    and (not _id(row["implicit_action_id"]) or row["implicit_action_id"] not in actions))
                or (row["semantic_id"] == "listen") != (row["layout_id"] == "listen_anim")
                or row["requestable"] == (row["semantic_id"] == "listen")):
            raise ValueError("invalid expression catalog entry")
        semantics.add(row["semantic_id"])
    unsigned = {k: v for k, v in value.items() if k != "catalog_id"}
    if hashlib.sha256(canonical(unsigned)).hexdigest() != value["catalog_id"]:
        raise ValueError("expression catalog digest differs from its contents")
    return {"semantics": len(semantics), "actions": len(actions), "catalog_id": value["catalog_id"]}


def validate_request(value: dict, catalog: dict | None = None) -> dict:
    expected = {"schema", "message_type", "request_id", "target_id", "boot_id", "session_id", "sequence",
                "catalog_id", "role_id", "issued_at", "expires_after_ms", "command"}
    if (not _keys(value, expected) or value["schema"] != SCHEMA or value["message_type"] != "request"
            or any(not _id(value[k]) for k in ("request_id", "target_id", "boot_id", "session_id", "role_id"))
            or not _integer(value["sequence"], 1) or not isinstance(value["catalog_id"], str)
            or not HASH.fullmatch(value["catalog_id"]) or not _integer(value["expires_after_ms"], 100, 10000)):
        raise ValueError("invalid expression request envelope")
    timestamp = value["issued_at"]
    if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", timestamp):
        raise ValueError("issued_at must be UTC RFC3339 milliseconds")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("issued_at is not a valid date") from error
    if catalog and (value["catalog_id"] != catalog["catalog_id"] or value["role_id"] != catalog["role_id"]):
        raise ValueError("request catalog or role is stale")
    operation = value["command"]
    if _keys(operation, {"kind", "semantic_id"}) and operation["kind"] == "play_expression" and _id(operation["semantic_id"]):
        if catalog and not any(row["semantic_id"] == operation["semantic_id"] and row["requestable"] for row in catalog["expressions"]):
            raise ValueError("expression is unavailable or owned by the voice state")
    elif _keys(operation, {"kind", "action_id"}) and operation["kind"] == "play_action" and _id(operation["action_id"]):
        if catalog and not any(row["action_id"] == operation["action_id"] for row in catalog["actions"]):
            raise ValueError("action is unavailable")
    else:
        raise ValueError("unsupported expression command")
    return deepcopy(value)


def validate_receipt(value: dict, request: dict | None = None, *, receiver: dict | None = None) -> dict:
    """Correlate request IDs and, when provided, the current receiver snapshot.

    A reconnect changes session_id; a reboot rejection uses the new boot_id.
    Those values are compared with the receiver, not with the old request.
    """
    expected = {"schema", "message_type", "target_id", "boot_id", "session_id", "sequence", "request_id",
                "request_sequence", "status", "reason", "source", "correlated", "original_status", "at_ms"}
    statuses = {"accepted", "dispatched", "busy", "rejected", "interrupted", "expired", "duplicate"}
    if (not _keys(value, expected) or value["schema"] != SCHEMA or value["message_type"] != "receipt"
            or any(not _id(value[k]) for k in ("target_id", "boot_id", "session_id", "request_id"))
            or not _integer(value["sequence"], 1) or not _integer(value["request_sequence"], 1)
            or not _integer(value["at_ms"], 0) or not isinstance(value["status"], str) or value["status"] not in statuses
            or not isinstance(value["reason"], str) or not REASON.fullmatch(value["reason"])
            or value["source"] not in {"simulator", "device"} or value["correlated"] is not True
            or (value["status"] == "duplicate" and (not isinstance(value["original_status"], str)
                or value["original_status"] not in statuses - {"duplicate"}))
            or (value["status"] != "duplicate" and value["original_status"] is not None)):
        raise ValueError("invalid expression receipt")
    if request and (value["request_id"] != request["request_id"]
                    or value["request_sequence"] != request["sequence"]
                    or value["target_id"] != request["target_id"]):
        raise ValueError("receipt does not match its request")
    if receiver is not None and any(value[key] != receiver.get(key) for key in ("target_id", "boot_id", "session_id")):
        raise ValueError("receipt does not match the current receiver")
    return deepcopy(value)
