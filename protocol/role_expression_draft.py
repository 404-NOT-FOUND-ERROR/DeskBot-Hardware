"""Offline role expression drafts; companion to the unchanged role_profile.v1."""
from __future__ import annotations

from typing import Any

from protocol.deskbot_role_profile import validate_role_profile

SCHEMA = "deskbot.role_expression_draft/v1"
MAX_ROLES = 16
MAX_BYTES = 512 * 1024
MAX_INTEGER = 9_007_199_254_740_991
FIELDS = {"schema", "edition", "scope", "firmware_deployable", "draft_revision",
          "baseline", "selected_role_id", "roles"}


def validate_draft(value: Any, context: dict) -> dict:
    """Validate structure and exact catalog/baseline compatibility, without I/O."""
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("draft fields are missing or unsupported")
    if (value["schema"] != SCHEMA or value["edition"] != "E1.31"
            or value["scope"] != "offline_draft_only"
            or value["firmware_deployable"] is not False):
        raise ValueError("unsupported draft version or scope")
    revision = value["draft_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= MAX_INTEGER:
        raise ValueError("draft_revision must be a positive JSON-safe integer")
    if value["baseline"] != context["baseline"]:
        raise ValueError("draft baseline/catalog does not match the accepted E1.18 snapshot")
    roles = value["roles"]
    if not isinstance(roles, list) or not 1 <= len(roles) <= MAX_ROLES:
        raise ValueError("draft must contain 1..16 roles")
    identities = set()
    semantics = context["semantics"]
    for role in roles:
        if not isinstance(role, dict) or set(role) != {"role_profile", "semantic_resources"}:
            raise ValueError("role draft contains missing or unsupported fields")
        profile = validate_role_profile(role["role_profile"])
        if (profile["binding_state"] != "draft"
                or profile.get("binding_version") != context["binding_version"]
                or profile["asset_version"] != context["asset_version"]):
            raise ValueError("role must be a draft using this catalog and binding version")
        if profile["role_id"] in identities:
            raise ValueError("duplicate role_id")
        identities.add(profile["role_id"])
        for field in ("shell_id", "role_id"):
            if profile[field] in {"constructor", "prototype"}:
                raise ValueError("reserved identity")
        for field in ("expression_map", "action_map"):
            if set(profile[field]) & {"constructor", "prototype", "__proto__"}:
                raise ValueError("reserved intent identifier")
        if any(target not in semantics for target in profile["expression_map"].values()):
            raise ValueError("role expression_map points to an unknown semantic")
        mapping = role["semantic_resources"]
        if not isinstance(mapping, dict) or set(mapping) != set(semantics):
            raise ValueError("semantic_resources must contain the complete known semantic set")
        for semantic, target in mapping.items():
            if target is not None and (
                not isinstance(target, str)
                or target not in semantics[semantic]["allowed_resources"]
            ):
                raise ValueError(f"semantic_resources.{semantic} is incompatible")
    if not isinstance(value["selected_role_id"], str) or value["selected_role_id"] not in identities:
        raise ValueError("selected_role_id does not exist")
    return value
