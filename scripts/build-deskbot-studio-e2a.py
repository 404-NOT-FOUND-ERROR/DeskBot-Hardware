#!/usr/bin/env python3
"""Build the unified offline studio from the accepted catalog and E1.45 report."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "artifacts/deskbot-studio-e2a-20261104"
LOCAL = ROOT / "tmp/deskbot-studio-e2a-20261104"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def source_module():
    spec = importlib.util.spec_from_file_location("studio_e131", ROOT / "scripts/build-role-expression-workbench-e1-31.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_staging(report):
    spec = importlib.util.spec_from_file_location("studio_staging", ROOT / "scripts/prepare-expression-e1-45-staging.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_report(report)


RESOURCE_NAMES = {
    "Happy.eaf": "开心", "Sad.eaf": "难过", "cry.eaf": "哭泣", "sleep.eaf": "困倦",
    "angry.eaf": "生气", "shocked.eaf": "惊讶", "confused.eaf": "困惑", "neutral.eaf": "平静",
    "winking.eaf": "眨眼", "insert.eaf": "配件插入", "look_left.eaf": "向左看",
    "look_right.eaf": "向右看", "look_around.eaf": "环顾", "question.eaf": "疑问",
    "beat_swing.eaf": "摇摆", "eat.eaf": "进食", "tried.eaf": "尝试", "listen.eaf": "聆听",
}


def build(local=False):
    source = source_module()
    prior, _ = source.build()
    node = source.load_e119()
    stage_path = ROOT / "artifacts/expression-e1-45-staging-20261104/report.json"
    stage_raw = stage_path.read_bytes()
    stage = json.loads(stage_raw)
    validate_staging(stage)
    if (stage["result"] != "offline_package_prepared_hardware_preflight_pending"
            or stage["selection"]["selected_id"] != "eye"
            or stage["rollback_reference"]["package_sha256"] != prior["baseline"]["package_sha256"]):
        raise ValueError("studio baseline or selected candidate differs")
    assets = {}
    for name, row in prior["assets"].items():
        atlas_raw = base64.b64decode(row["atlas"].split(",", 1)[1], validate=True)
        if sha(atlas_raw) != row["atlas_sha256"]:
            raise ValueError("official atlas checksum differs: " + name)
        assets[name] = {key: row[key] for key in (
            "atlas", "atlas_sha256", "columns", "frame_width", "frame_height", "frame_count",
            "object_kind", "fps", "loop", "bytes", "deployed_sha256")}
        assets[name].update(id=name, label=RESOURCE_NAMES[name], origin="official", tail_hold_ticks=1)
    candidate = {
        "id": "shy_candidate.eaf", "label": "害羞 · 镜像版", "origin": "candidate",
        "atlas": None, "atlas_sha256": "11a7daa40d66673a92be99c5d8d6e0669ec275e1c327e97bb3bccf55897c0fcc",
        "columns": 8, "frame_width": 160, "frame_height": 200, "frame_count": 188,
        "object_kind": "eye", "fps": 25, "loop": True, "tail_hold_ticks": 1, "bytes": 443238,
        "deployed_sha256": stage["selection"]["eaf_sha256"],
    }
    if local:
        path = ROOT / "tmp/expression-e1-45-staging-preview-20261104/index.html"
        match = re.search(r'<script id="e145-data" type="application/json">(.*?)</script>', path.read_text(encoding="utf-8"), re.S)
        media = json.loads(match[1])
        if media["report"]["selection"]["eaf_sha256"] != candidate["deployed_sha256"]:
            raise ValueError("local preview is not the selected resource")
        atlas = media["eye"]["data_url"]
        if sha(base64.b64decode(atlas.split(",", 1)[1], validate=True)) != candidate["atlas_sha256"]:
            raise ValueError("candidate atlas checksum differs")
        candidate["atlas"] = atlas
    assets[candidate["id"]] = candidate
    semantics = json.loads(json.dumps(prior["semantics"]))
    for row in semantics.values():
        if row["layout_id"] == "eye_anim":
            row["allowed_resources"].append(candidate["id"])
    fingerprint = sha(packed({
        "baseline": prior["baseline"], "semantics": semantics,
        "assets": {key: {field: row[field] for field in ("deployed_sha256", "bytes", "object_kind", "fps", "loop")}
                   for key, row in assets.items()},
    }))
    roles = [{"id": "role-default", "name": "默认表达", "voice": "沿用当前声音", "example": False,
              "bindings": {key: row["resource_id"] for key, row in semantics.items()}}]
    for row in prior["initial"]["roles"]:
        profile = row["role_profile"]
        roles.append({"id": profile["role_id"], "name": profile["display_name"], "voice": profile["voice_id"],
                      "example": True, "bindings": row["semantic_resources"]})
    initial = {"schema": "deskbot.studio_draft/v1", "catalog_id": fingerprint, "scope": "offline_draft",
               "revision": 1, "selected_role_id": "role-default", "roles": roles}
    data = {
        "schema": "deskbot.studio/v1", "edition": "E2a", "date": "2026-11-04",
        "media_scope": "local" if local else "public", "catalog_id": fingerprint,
        "semantics": semantics, "assets": assets, "routes": node["routes"], "actions": node["actions"],
        "initial": initial, "release": stage,
        "device": {"source": "saved_release_record", "live": False, "app": "E1.42",
                   "bootloader": "E1.40", "assets": "E1.18", "layout": "upper-assets-8m",
                   "flash_bytes": 33554432, "psram_bytes": 16777216},
        "sources": [{"path": source.SOURCE.relative_to(ROOT).as_posix(), "sha256": sha(source.SOURCE.read_bytes())},
                    {"path": stage_path.relative_to(ROOT).as_posix(), "sha256": sha(stage_raw)}],
    }
    report = {"schema": "deskbot.studio_build/v1", "edition": "E2a", "date": "2026-11-04",
              "result": "offline_studio_built", "catalog_id": fingerprint, "media_scope": data["media_scope"],
              "counts": {"semantics": len(semantics), "official_resources": 18, "candidate_resources": 1,
                         "routes": len(data["routes"]), "actions": len(data["actions"]), "initial_roles": 3},
              "hardware_accessed": False, "firmware_changed": False, "sources": data["sources"]}
    return data, report


def products(local=False):
    data, report = build(local)
    template = (ROOT / "scripts/deskbot-studio-e2a.template.html").read_text(encoding="utf-8")
    replacements = {"__STUDIO_DATA__": json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"),
                    "__STUDIO_CSS__": (ROOT / "scripts/deskbot-studio-e2a.css").read_text(encoding="utf-8"),
                    "__STUDIO_CORE__": (ROOT / "scripts/deskbot-studio-e2a-core.js").read_text(encoding="utf-8"),
                    "__STUDIO_CONTROLLER__": (ROOT / "scripts/deskbot-studio-e2a-controller.js").read_text(encoding="utf-8")}
    for key, value in replacements.items():
        if template.count(key) != 1:
            raise ValueError("studio template token differs: " + key)
        template = template.replace(key, value)
    return {"index.html": template.encode("utf-8"), "report.json": json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    out = LOCAL if args.local else PUBLIC
    generated = products(args.local)
    if args.check:
        for name, raw in generated.items():
            if (out / name).read_bytes() != raw:
                raise ValueError("studio output differs: " + name)
    else:
        out.mkdir(parents=True, exist_ok=True)
        for name, raw in generated.items():
            (out / name).write_bytes(raw)
    print(json.dumps({"result": "checked" if args.check else "built", "path": str(out / "index.html"),
                      "bytes": len(generated["index.html"]), "hardware_accessed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
