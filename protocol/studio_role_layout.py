"""Reviewed asset layouts for role matching; no device I/O or size inference."""
from copy import deepcopy
import json
from pathlib import Path

from protocol.studio_expression_layout import PROFILES, validate_requirement

PATH = Path(__file__).with_name("expression-layout-requirements-v1.json")


def load_requirements(catalog, path=PATH):
    value = json.loads(path.read_bytes())
    if (type(value) is not dict or set(value) != {"schema", "assets"}
            or value["schema"] != "deskbot.asset_layout_requirements/v1"
            or type(value["assets"]) is not list):
        raise ValueError("invalid_asset_layout_registry")
    rows = {}
    for raw in value["assets"]:
        row = validate_requirement(raw)
        if row["resource_id"] in rows:
            raise ValueError("duplicate_asset_layout_requirement")
        asset = catalog["assets"].get(row["resource_id"])
        if (not asset or asset["deployed_sha256"] != row["resource_sha256"]
                or asset["frame_width"] != row["width"] or asset["frame_height"] != row["height"]):
            raise ValueError("asset_layout_content_changed")
        rows[row["resource_id"]] = row
    return rows


def playback(catalog, semantic, resource, requirements=None, *, mirror_override=None):
    asset, binding = catalog["assets"][resource], catalog["semantics"][semantic]
    layout_id = binding["layout_id"]
    if requirements is not None and resource in requirements:
        row = validate_requirement(requirements[resource])
        if (row["resource_id"] != resource or row["resource_sha256"] != asset["deployed_sha256"]
                or row["width"] != asset["frame_width"] or row["height"] != asset["frame_height"]):
            raise ValueError("asset_layout_content_changed")
        layout_id = "full_frame" if row["mode"] == "full_frame" else "eye_anim"
    if mirror_override is not None:
        if type(mirror_override) is not bool or asset["object_kind"] == "listen":
            raise ValueError("invalid_mirror_override")
        layout_id = "eye_anim" if mirror_override else "full_frame"
    candidate = asset["origin"] == "candidate" and resource != binding["resource_id"]
    return dict(layout_id=layout_id, fps=asset["fps"] if candidate else binding["fps"],
                loop=asset["loop"] if candidate else binding["loop"])


def expected_layout(layout_id, reference, requirements):
    if requirements is not None and layout_id in ("eye_anim", "full_frame"):
        mode = "full_frame" if layout_id == "full_frame" else "mirrored_eye"
        return dict(kind="layout", id=layout_id, **deepcopy(PROFILES[mode]))
    return next(row for row in reference["entries"] if row["kind"] == "layout" and row["id"] == layout_id)
