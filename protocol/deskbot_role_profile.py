"""Validation and resolution helpers for the client-owned role profile v1."""
from __future__ import annotations

import re
from typing import Any, Iterable


SCHEMA = "deskbot.role_profile.v1"
ROLE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
SAFE_ACTIONS = {
    "shark_head",
    "shark_head_decay",
    "look_around",
    "beat_swing",
    "cat_nuzzle",
}
PROFILE_FIELDS = {
    "schema", "shell_id", "magnet_profile_id", "role_id", "display_name",
    "voice_id", "catchphrases", "expression_map", "action_map", "asset_version",
    "binding_version", "profile_revision", "binding_state", "notes",
}


def validate_role_profile(value: Any) -> dict[str, Any]:
    """Validate role data without external packages or accepting secrets."""
    if not isinstance(value, dict):
        raise ValueError("role profile must be a JSON object")
    unexpected = set(value) - PROFILE_FIELDS
    if unexpected:
        raise ValueError(f"unsupported role profile fields: {', '.join(sorted(unexpected))}")
    required = PROFILE_FIELDS - {"notes", "binding_version"}
    missing = required - set(value)
    if missing:
        raise ValueError(f"missing role profile fields: {', '.join(sorted(missing))}")
    if value.get("schema") != SCHEMA:
        raise ValueError("unsupported role profile schema/version")
    for key in ("shell_id", "role_id"):
        if not isinstance(value.get(key), str) or not ROLE_ID_RE.fullmatch(value[key]):
            raise ValueError(f"{key} has an invalid identifier")
    magnetic_id = value.get("magnet_profile_id")
    if (not isinstance(magnetic_id, str) or not magnetic_id.strip()
            or len(magnetic_id) > 128):
        raise ValueError("magnet_profile_id must preserve a non-empty identity of at most 128 characters")
    for key, limit in (("display_name", 80), ("voice_id", 96), ("asset_version", 96)):
        field = value.get(key)
        if not isinstance(field, str) or not field.strip() or len(field) > limit:
            raise ValueError(f"{key} must be a non-empty string of at most {limit} characters")
    binding_version = value.get("binding_version")
    if "binding_version" in value and (
        not isinstance(binding_version, str)
        or len(binding_version) > 96
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*/v[1-9][0-9]*", binding_version)
    ):
        raise ValueError("binding_version must be a versioned identifier such as deskbot.expression_bindings/v1")
    phrases = value.get("catchphrases")
    if not isinstance(phrases, list) or len(phrases) > 32 or any(
        not isinstance(item, str) or len(item) > 160 for item in phrases
    ):
        raise ValueError("catchphrases must contain at most 32 strings of at most 160 characters")
    for field_name in ("expression_map", "action_map"):
        mapping = value.get(field_name)
        if not isinstance(mapping, dict) or len(mapping) > 64:
            raise ValueError(f"{field_name} must be an object of at most 64 entries")
        for key, target in mapping.items():
            if not isinstance(key, str) or not ROLE_ID_RE.fullmatch(key):
                raise ValueError(f"{field_name} contains an invalid intent identifier")
            if not isinstance(target, str) or not ROLE_ID_RE.fullmatch(target):
                raise ValueError(f"{field_name}.{key} has an invalid target identifier")
            if field_name == "action_map" and target not in SAFE_ACTIONS:
                raise ValueError(f"action_map.{key} is not an approved body action")
    revision = value.get("profile_revision")
    if (isinstance(revision, bool) or not isinstance(revision, int)
            or not 1 <= revision <= 9_007_199_254_740_991):
        raise ValueError("profile_revision must be a positive JSON-safe integer")
    if value.get("binding_state") not in {"draft", "confirmed", "disabled"}:
        raise ValueError("binding_state must be draft, confirmed, or disabled")
    notes = value.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 1000:
        raise ValueError("notes must be a string of at most 1000 characters")
    if value["shell_id"] == value["role_id"]:
        raise ValueError("shell_id and role_id must use separate identity namespaces")
    return value


def resolve_confirmed_role(
    shell_observation: dict[str, Any], profiles: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Only return a role for a known shell with an explicit confirmed match."""
    if not isinstance(shell_observation, dict) or shell_observation.get("status") != "known":
        return None
    profile_id = shell_observation.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        return None
    candidates = []
    for profile in profiles:
        try:
            validate_role_profile(profile)
        except ValueError:
            continue
        if (profile["binding_state"] == "confirmed"
                and profile["magnet_profile_id"] == profile_id):
            candidates.append(profile)
    return candidates[0] if len(candidates) == 1 else None
