"""Transport-neutral E1.7 mapping helpers; no socket, auth, or device I/O."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


RECORD_SCHEMA = "deskbot.client_transport_record/v1"
REVIEW_REQUEST_SCHEMA = "deskbot.client_review_request/v1"
REVIEW_COMMAND_SCHEMA = "deskbot.shell_review_command/v1"
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
_OPERATIONS = {
    "confirm": {"case_id", "choice_id"},
    "reject": {"case_id"},
    "request_registration": {"case_id"},
    "begin_rebind": set(),
    "rebind": {"case_id", "choice_id", "previous_binding_id"},
    "unbind": {"binding_id"},
}


def _identifier(value: Any, label: str, *, maximum: int = 64) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or value[0] not in _ID_CHARS[:52]
            or any(char not in _ID_CHARS for char in value)):
        raise ValueError(f"{label} must be an identifier")
    return value


def make_record(*, record_id: str, stream_id: str, sequence: int, created_at: str,
                record_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap one payload without rewriting the source protocol fields.

    This is a builder, not a schema validator or stream consumer. Validate
    the complete record against the E1.7 schema before sending/consuming it.
    """
    _identifier(record_id, "record_id", maximum=96)
    _identifier(stream_id, "stream_id", maximum=96)
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= MAX_SAFE_INTEGER:
        raise ValueError("sequence must be a positive JSON-safe integer")
    if not isinstance(record_type, str) or record_type not in {
        "body_fact", "role_state", "review_state", "review_request", "review_ack", "resync_request"
    }:
        raise ValueError("unsupported record_type")
    if not isinstance(created_at, str) or not isinstance(payload, dict):
        raise ValueError("created_at and object payload are required")
    return {
        "schema": RECORD_SCHEMA,
        "record_id": record_id,
        "stream_id": stream_id,
        "sequence": sequence,
        "created_at": created_at,
        "record_type": record_type,
        "payload": deepcopy(payload),
    }


def review_request_to_command(request: dict[str, Any], *, adapter_instance_id: str,
                              authenticated_actor_id: str) -> dict[str, Any]:
    """Map an actor-less UI request after the caller authenticates its user.

    ``authenticated_actor_id`` must come from trusted server-side session
    context. This helper does not authenticate anyone and must not be exposed
    as an unauthenticated endpoint.
    """
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    operation = request.get("operation")
    fields = _OPERATIONS.get(operation) if isinstance(operation, str) else None
    common = {"schema", "request_id", "target_adapter_instance_id", "operation", "expected_registry_revision"}
    if fields is None or set(request) != common | fields:
        raise ValueError("review request has unsupported operation or fields")
    if request["schema"] != REVIEW_REQUEST_SCHEMA:
        raise ValueError("unsupported review request schema")
    _identifier(request["request_id"], "request_id")
    if request["target_adapter_instance_id"] != _identifier(adapter_instance_id, "adapter_instance_id"):
        raise ValueError("adapter_instance_mismatch")
    actor_id = _identifier(authenticated_actor_id, "authenticated_actor_id")
    revision = request["expected_registry_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or not 0 <= revision <= MAX_SAFE_INTEGER:
        raise ValueError("expected_registry_revision must be a JSON-safe integer")
    args = {key: _identifier(request[key], key) for key in fields}
    return {
        "schema": REVIEW_COMMAND_SCHEMA,
        "command_id": request["request_id"],
        "operation": operation,
        "actor": {"kind": "human", "id": actor_id},
        "expected_registry_revision": revision,
        **args,
    }


def review_result_to_ack(request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Preserve the original decision on duplicate E1.6 command results."""
    _identifier(request_id, "request_id")
    if not isinstance(result, dict) or result.get("status") not in ("accepted", "rejected", "duplicate"):
        raise ValueError("unsupported review result")
    revision = result.get("registry_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or not 0 <= revision <= MAX_SAFE_INTEGER:
        raise ValueError("result registry_revision must be a JSON-safe integer")
    if not isinstance(result.get("reason"), str) or not result["reason"]:
        raise ValueError("result reason is required")
    ack = {"schema": "deskbot.client_review_ack/v1", "request_id": request_id,
           "result": result["status"], "reason": result["reason"], "registry_revision": revision}
    if result["status"] == "duplicate":
        if result.get("original_status") not in ("accepted", "rejected"):
            raise ValueError("duplicate result must preserve original_status")
        ack["original_result"] = result["original_status"]
    return ack
