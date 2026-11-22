"""Resolve an authoring draft against installed resources and current USB capability."""
from copy import deepcopy
import hashlib

from protocol import studio_resource_package as packages
from protocol import studio_installed_catalog as installed
from protocol import studio_usb_session as session
from protocol.studio_control_link import validate_command
from protocol.studio_timing import STATE_VALID_MS
from protocol.studio_display_query import supports
from protocol import studio_role_layout as asset_layout

SCHEMA = "deskbot.role_expression_plan/v1"
CONTEXT_KEYS = {"connection_id", "target_id", "boot_id", "session_id"}


def resolve(draft, semantic, catalog, reference, *, control=None, source="saved_snapshot",
            context=None, request_id, issued_ms, now_ms, ttl_ms=2000, emit_command=True,
            layout_requirements=None):
    if type(emit_command) is not bool:
        raise ValueError("invalid_role_expression_analysis_mode")
    role = packages.validate_draft(draft, catalog, complete=False)
    if source not in ("saved_snapshot", "device") or semantic not in catalog["semantics"]:
        raise ValueError("invalid_role_expression_input")
    if not session._id(request_id) or any(type(v) is not int or v < 0 for v in (issued_ms, now_ms, ttl_ms)) or not 100 <= ttl_ms <= 10000:
        raise ValueError("invalid_role_expression_clock_or_id")
    result = dict(schema=SCHEMA, source=source, role_id=role["id"], role_name=role["name"],
        draft_revision=draft["revision"], semantic_id=semantic, resource_id=role["bindings"][semantic],
        installed_resource_id=None, catalog_id=None, status="unavailable", reason="binding_missing", command=None,
        playback=None)

    def finish(status, reason):
        return {**result, "status": status, "reason": reason}

    if now_ms < issued_ms or now_ms - issued_ms >= ttl_ms:
        return finish("stale", "intent_expired")
    if role["bindings"][semantic] is None:
        return finish("unavailable", "binding_missing")
    wanted_playback = asset_layout.playback(catalog, semantic, role["bindings"][semantic], layout_requirements,
        mirror_override=role.get("mirror_overrides", {}).get(role["bindings"][semantic]))
    result["playback"] = wanted_playback
    reference = installed.validate_manifest(reference)
    if source == "device":
        if not control or not control.get("enabled") or not control.get("session_id"):
            return finish("stale", "session_required")
        if (type(context) is not dict or set(context) != CONTEXT_KEYS
                or any(context[k] != control.get(k) for k in CONTEXT_KEYS)):
            return finish("stale", "connection_changed")
        directory = control.get("catalog", {})
        if directory.get("status") != "complete" or not directory.get("manifest"):
            return finish("unavailable", "catalog_required")
        if any(directory.get(k) != control.get(k) for k in ("target_id", "boot_id", "session_id")):
            return finish("stale", "catalog_session_changed")
        current = installed.validate_manifest(directory["manifest"])
    else:
        current = reference
    result["catalog_id"] = current["catalog_id"]
    row = next((row for row in current["entries"] if row["id"] == semantic and row["kind"] in ("expression", "status")), None)
    if row:
        result["installed_resource_id"] = row["resource_id"]
    if not row:
        return finish("needs_publish", "semantic_not_installed")
    resource = role["bindings"][semantic]
    asset, binding = catalog["assets"][resource], catalog["semantics"][semantic]
    expected = dict(resource_id=resource, resource_sha256=asset["deployed_sha256"], bytes=asset["bytes"],
                    **wanted_playback, implicit_action_id=binding["action_id"])
    if any(row[k] != v for k, v in expected.items()):
        return finish("needs_publish", "resource_binding_changed")
    layout = next(row for row in current["entries"] if row["kind"] == "layout" and row["id"] == wanted_playback["layout_id"])
    expected_layout = asset_layout.expected_layout(wanted_playback["layout_id"], reference, layout_requirements)
    if layout != expected_layout:
        return finish("needs_publish", "layout_changed")
    if binding["action_id"]:
        action = next(row for row in current["entries"] if row["kind"] == "action" and row["id"] == binding["action_id"])
        expected_action = next(row for row in reference["entries"] if row["kind"] == "action" and row["id"] == binding["action_id"])
        if action != expected_action:
            return finish("needs_publish", "action_changed")
    if source == "saved_snapshot":
        return finish("planned", "snapshot_review")
    if (semantic != "neutral" and not supports(control.get("display"), semantic)):
        return finish("unavailable", "expression_interface_pending")
    if type(control.get("age_ms")) is not int or not 0 <= control["age_ms"] < STATE_VALID_MS:
        return finish("stale", "display_state_expired")
    if not control.get("can_submit"):
        return finish("busy", "device_not_ready")
    if control.get("display") is not None and not supports(control["display"], semantic):
        return finish("unavailable", "expression_interface_pending")
    # The overview shares matching and readiness checks but ends before any
    # command construction; only the explicit single-expression path emits one.
    if not emit_command:
        return finish("ready", "request_ready")
    # Command IDs are deterministic for this analysis and connection. Repeated
    # submissions reuse the existing worker's bounded command deduplication.
    key = dict(request_id=request_id, draft=draft, semantic=semantic, context=context,
               installed_catalog=current["catalog_id"])
    command_id = "role-" + hashlib.sha256(packages.canonical(key)).hexdigest()[:48]
    result["command"] = validate_command(dict(operation=semantic, command_id=command_id, **deepcopy(context)))
    return finish("ready", "request_ready")
