"""Audit saved package contents against expression and action source mappings."""
from copy import deepcopy
import hashlib
import json
import re

from protocol import studio_installed_catalog as catalog

ACTION_CONSTANTS = {
    "SHARK_HEAD": "shark_head", "SHARK_HEAD_DECAY": "shark_head_decay",
    "LOOK_AROUND": "look_around", "BEAT_SWING": "beat_swing", "CAT_NUZZLE": "cat_nuzzle",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def action_bindings(source):
    block = re.search(r"emotion_to_action_map\s*=\s*\{(.*?)\n\s*\};", source, re.S)
    if not block:
        raise ValueError("action_mapping_source_missing")
    pairs = re.findall(r'\{\s*"([A-Za-z_]+)"\s*,\s*VOCAT_BASE_CMD_SET_ACTION_([A-Z_]+)\s*\}', block[1])
    remainder = re.sub(r'\{\s*"[A-Za-z_]+"\s*,\s*VOCAT_BASE_CMD_SET_ACTION_[A-Z_]+\s*\}\s*,?', '', block[1])
    if remainder.strip() or len({name for name, _ in pairs}) != len(pairs):
        raise ValueError("unrecognized_action_mapping_source")
    if any(name not in ACTION_CONSTANTS for _, name in pairs):
        raise ValueError("unrecognized_action_constant")
    return {name: ACTION_CONSTANTS[action] for name, action in pairs}


def audit(members, saved, default_emotes, display_source):
    """Inputs are already checked package members; inspect installed index first."""
    index = json.loads(members["index.json"])
    mapping = action_bindings(display_source)
    expected = {row["semantic_id"]: row for row in saved["expressions"]}
    ordinary = index["emoji_collection"]
    if (len(expected) != len(saved["expressions"]) or len({r["name"] for r in ordinary}) != len(ordinary)
            or {r["name"] for r in ordinary} != set(expected) - {"listen"}
            or set(mapping) - {r["name"] for r in ordinary}):
        raise ValueError("installed_semantic_set_changed")
    layouts = {row["name"]: row for row in index["layout"]}
    entries = []
    for row in ordinary:
        sid, source = row["name"], row["file"]
        old = expected[sid]
        if (row["eaf"] != {"fps": old["fps"], "loop": old["loop"]}
                or old["resource_id"] != source or old["implicit_action_id"] != mapping.get(sid)):
            raise ValueError("saved_mapping_differs_from_installed_index_or_source")
        entries.append(dict(kind="expression", id=sid, resource_id=source, bytes=len(members[source]),
            resource_sha256=sha(members[source]), layout_id="eye_anim", fps=row["eaf"]["fps"],
            loop=row["eaf"]["loop"], implicit_action_id=mapping.get(sid)))
    listen = [row for row in index["icon_collection"] if row["name"] == "listen"]
    if len(listen) != 1 or expected["listen"]["resource_id"] != listen[0]["file"]:
        raise ValueError("listen_status_mapping_changed")
    # Its 15 FPS/loop come from the locked emote_op.c voice-state path.
    listen = listen[0]["file"]
    entries.append(dict(kind="status", id="listen", resource_id=listen, bytes=len(members[listen]),
        resource_sha256=sha(members[listen]), layout_id="listen_anim", fps=15, loop=True, implicit_action_id=None))
    for lid in ("eye_anim", "listen_anim"):
        row = layouts[lid]
        entries.append(dict(kind="layout", id=lid, align=row["align"], x=row["x"], y=row["y"], mirror=row["anim"]["mirror"]))
    for row in saved["actions"]:
        entries.append(dict(kind="action", id=row["action_id"], wire_command=row["wire_command"],
            wire_value=row["wire_value"], completion=row["completion"]))
    entries.sort(key=lambda row: (row["kind"], row["id"]))
    differences = []
    defaults = {row["emote"]: row for row in default_emotes}
    for row in ordinary:
        before = defaults[row["name"]]
        if (before["src"], before["fps"], before["loop"]) != (row["file"], row["eaf"]["fps"], row["eaf"]["loop"]):
            differences.append(dict(semantic_id=row["name"], source_default=before["src"], installed=row["file"]))
    return dict(entries=deepcopy(entries), default_differences=differences,
        expression_count=len(ordinary), status_animation_count=1,
        unique_eaf_count=len({row["resource_id"] for row in entries if "resource_id" in row}),
        expressions_with_actions=len(mapping), action_count=len(saved["actions"]),
        shared_happy_semantics=[row["name"] for row in ordinary if row["file"] == "Happy.eaf"])
