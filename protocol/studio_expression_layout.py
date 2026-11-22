"""Inspect an explicitly reviewed asset layout against an installed binding.

This host contract describes desired presentation separately from device facts.
It does not add layout fields to the legacy resource index or authorize playback.
"""
from copy import deepcopy
import re

from protocol.studio_installed_catalog import validate_entry

SCHEMA = "deskbot.expression_layout_plan/v1"
PROFILES = {
    "full_frame": dict(align="GFX_ALIGN_CENTER", x=0, y=0, mirror="false"),
    "mirrored_eye": dict(align="GFX_ALIGN_LEFT_MID", x=10, y=10, mirror="auto"),
}


def validate_requirement(value):
    if (type(value) is not dict
            or set(value) != {"resource_id", "resource_sha256", "width", "height", "mode"}
            or not isinstance(value["resource_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,27}\.eaf", value["resource_id"])
            or not isinstance(value["resource_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["resource_sha256"])
            or type(value["mode"]) is not str or value["mode"] not in PROFILES
            or any(type(value[k]) is not int or not 1 <= value[k] <= 360 for k in ("width", "height"))):
        raise ValueError("invalid_asset_layout_requirement")
    # A mirrored eye needs room for both copies and the existing side margins.
    if value["mode"] == "mirrored_eye" and (value["width"] > 170 or value["height"] > 340):
        raise ValueError("mirrored_eye_exceeds_display")
    return deepcopy(value)


def inspect_layout(asset, expression, layout, requirement):
    """Return a review plan; all sources must refer to the same installed bytes."""
    wanted = validate_requirement(requirement)
    row, actual = validate_entry(expression), validate_entry(layout)
    if row["kind"] != "expression" or actual["kind"] != "layout" or row["layout_id"] != actual["id"]:
        raise ValueError("expression_layout_reference_changed")
    if (row["resource_id"] != wanted["resource_id"]
            or row["resource_sha256"] != wanted["resource_sha256"]
            or asset.get("deployed_sha256") != wanted["resource_sha256"]
            or asset.get("bytes") != row["bytes"]):
        raise ValueError("asset_layout_content_changed")
    if any(type(asset.get(k)) is not int or asset[k] != wanted[d]
           for k, d in (("frame_width", "width"), ("frame_height", "height"))):
        raise ValueError("asset_layout_dimensions_changed")
    target = PROFILES[wanted["mode"]]
    current = {k: actual[k] for k in target}
    changes = {k: dict(current=current[k], target=v) for k, v in target.items() if current[k] != v}
    return dict(schema=SCHEMA, semantic_id=row["id"], requirement=wanted,
                resource_bytes=row["bytes"], playback=dict(fps=row["fps"], loop=row["loop"]),
                current=dict(layout_id=actual["id"], **current), target=deepcopy(target),
                changes=changes, status="requires_player_route" if changes else "matched",
                request_authorized=False, preview_only=True)
