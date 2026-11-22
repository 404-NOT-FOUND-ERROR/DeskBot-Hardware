"""Read-only role overview using the existing expression matching rules."""
from copy import deepcopy

from protocol import studio_role_expression as expression

SCHEMA = "deskbot.role_expression_overview/v1"
STATUSES = ("ready", "matched", "needs_publish", "missing", "interface_pending", "state_managed", "waiting")
MATCHED_REASONS = {"request_ready", "snapshot_review", "expression_interface_pending",
                   "display_state_expired", "device_not_ready"}


def _current_directory(reference, source, control, context):
    if source == "saved_snapshot":
        return reference
    if (not control or not control.get("enabled") or not control.get("session_id")
            or type(context) is not dict or set(context) != expression.CONTEXT_KEYS
            or any(context[key] != control.get(key) for key in expression.CONTEXT_KEYS)):
        return None
    directory = control.get("catalog", {})
    if (directory.get("status") != "complete" or not directory.get("manifest")
            or any(directory.get(key) != control.get(key) for key in ("target_id", "boot_id", "session_id"))):
        return None
    return expression.installed.validate_manifest(directory["manifest"])


def overview(draft, catalog, reference, *, control=None, source="saved_snapshot", context=None,
             request_id, issued_ms, now_ms, ttl_ms=2000, layout_requirements=None):
    role = expression.packages.validate_draft(draft, catalog, complete=False)
    reference = expression.installed.validate_manifest(reference)
    # Take a private copy once: every semantic sees the same connection and
    # directory even when the bridge publishes a new snapshot concurrently.
    control, context = deepcopy(control), deepcopy(context)
    rows, counts = [], dict.fromkeys(STATUSES, 0)
    for semantic, binding in catalog["semantics"].items():
        resolved = expression.resolve(draft, semantic, catalog, reference, control=control,
            source=source, context=context, request_id=request_id, issued_ms=issued_ms,
            now_ms=now_ms, ttl_ms=ttl_ms, emit_command=False, layout_requirements=layout_requirements)
        reason = resolved["reason"]
        matches = reason in MATCHED_REASONS
        if reason == "binding_missing":
            status = "missing"
        elif resolved["status"] == "needs_publish":
            status = "needs_publish"
        elif semantic == "listen" and matches:
            status, reason = "state_managed", "voice_state_managed"
        elif resolved["status"] == "planned":
            status = "matched"
        elif resolved["status"] == "ready":
            status = "ready"
        elif reason == "expression_interface_pending":
            status = "interface_pending"
        else:
            status = "waiting"
        resource = role["bindings"][semantic]
        playback = None if resource is None else expression.asset_layout.playback(
            catalog, semantic, resource, layout_requirements,
            mirror_override=role.get("mirror_overrides", {}).get(resource))
        rows.append(dict(semantic_id=semantic, label=binding["label"],
            kind="status" if semantic == "listen" else "expression", resource_id=resource,
            installed_resource_id=resolved["installed_resource_id"], status=status, reason=reason,
            resource_matches=matches, playback=playback,
            implicit_action_id=binding["action_id"],
            action_completion="fact_only" if binding["action_id"] else None))
        counts[status] += 1
    # A role may have every binding disconnected. Validate the directory
    # independently so those rows still show the checked installed resources.
    current = _current_directory(reference, source, control, context) if 0 <= now_ms-issued_ms < ttl_ms else None
    catalog_id = current["catalog_id"] if current else None
    if current is not None:
        installed = {row["id"]: row for row in current["entries"] if row["kind"] in ("expression", "status")}
        for row in rows:
            row["installed_resource_id"] = installed.get(row["semantic_id"], {}).get("resource_id")
    return dict(schema=SCHEMA, source=source, role_id=role["id"], role_name=role["name"],
        draft_revision=draft["revision"], catalog_id=catalog_id,
        context=deepcopy(context) if source == "device" else None,
        total=len(rows), expression_count=sum(row["kind"] == "expression" for row in rows),
        status_count=sum(row["kind"] == "status" for row in rows),
        resource_matches=sum(row["resource_matches"] for row in rows), counts=counts, entries=rows)
