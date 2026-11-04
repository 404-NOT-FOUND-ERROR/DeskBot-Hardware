#!/usr/bin/env python3
"""Build E1.19 from archived official previews and measured package evidence."""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/expression-node-preview-e1-19-20261024"
BINDINGS = "artifacts/expression-binding-e1-4a-20261021/bindings.json"
ARCHIVE = "artifacts/expression-node-workbench-e1-4d-20261021/index.html"
REPACK = "artifacts/expression-e1-16-repack-20261024/report.json"
PREVIEW_PIN = "e39f54123a375ab38939b2891a368eb688a0c79b7c20dc2f15c3afb2b6f45b7d"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def build():
    raw = {key: (ROOT / key).read_bytes() for key in (BINDINGS, ARCHIVE, REPACK)}
    if sha(raw[BINDINGS]) != "98d57c67e52e240cb7ef0ed8c99cfc088f834fbf662424cd48c2c03c50bd4921":
        raise ValueError("binding source drift")
    binding, repack = json.loads(raw[BINDINGS]), json.loads(raw[REPACK])
    match = re.search(r'<script id="workbench-data" type="application/json">(.*?)</script>',
                      raw[ARCHIVE].decode("utf-8"), re.S)
    if not match:
        raise ValueError("missing archived preview")
    archived = json.loads(match[1])["preview"]
    members = {item["name"]: item for item in repack["members"]}
    assets = {}
    for entry in archived["assets"].values():
        if entry.get("origin") != "official":
            continue
        name = entry["src"]
        if not entry.get("atlas", "").startswith("data:image/png;base64,"):
            raise ValueError("official atlas missing: " + name)
        atlas = base64.b64decode(entry["atlas"].split(",", 1)[1], validate=True)
        if sha(atlas) != entry["atlas_sha256"]:
            raise ValueError("atlas checksum mismatch: " + name)
        # These are source-frame atlases, NOT screenshots of the deployed LCD.
        assets[name] = {key: copy.deepcopy(value) for key, value in entry.items()
                        if key not in ("atlas_file", "asset_key")}
        assets[name].update({"id": name, "preview_sha256": entry["sha256"],
                            "deployed_sha256": members[name]["candidate_sha256"],
                            "deployed_bytes": members[name]["candidate_bytes"],
                            "preview_basis": "archived official decoded source frames; E1.13 insert/eat losslessly reencoded"})
    if len(assets) != 18:
        raise ValueError("official asset inventory drift")
    preview_digest = sha(json.dumps({k: v["atlas_sha256"] for k, v in assets.items()},
                                    sort_keys=True).encode())
    if preview_digest != PREVIEW_PIN:
        raise ValueError("official preview set drift")
    before = {}
    for semantic in binding["semantics"]:
        sid = semantic["id"]
        before[sid] = {"resource_id": semantic["src"], "fps": semantic["fps"], "loop": semantic["loop"],
                       "action_id": binding["emotion_action_map"].get(sid),
                       "layout_id": "listen_anim" if sid == "listen" else "eye_anim",
                       "evidence": semantic["evidence"], "reachability": semantic["reachability"]}
    deployed = copy.deepcopy(before)
    for name, item in assets.items():
        representative = next(row for row in before.values() if row["resource_id"] == name)
        item.update({"fps": representative["fps"], "loop": representative["loop"]})
    if (before["surprised"]["resource_id"], repack["index_patch"]["after"]) != ("Happy.eaf", "shocked.eaf"):
        raise ValueError("replacement drift")
    deployed["surprised"]["resource_id"] = "shocked.eaf"
    if len(before) != 29 or deployed["surprised"]["action_id"] != "look_around":
        raise ValueError("semantic/action inventory drift")
    # Source inventory contains 29 semantic entries (28 was a previous prose count).
    data = {"schema": "deskbot.expression_node_preview/v1", "edition": "E1.19", "date": "2026-10-24",
            "hardware_accessed": False, "firmware_changed": False, "deployed_state_source": "E1.18 release/readback record, not a live connection",
            "snapshots": {"before": before, "deployed": deployed}, "assets": assets,
            "routes": binding["routes"], "layouts": binding["layouts"], "actions": binding["actions"],
            "evidence": binding["evidence"], "official_commit": binding["source"]["official_commit"],
            "baselines": {
                "before": {"label": "E1.13 替换前", "package_sha256": repack["baseline"]["sha256"],
                           "index_sha256": repack["index_patch"]["before_sha256"]},
                "deployed": {"label": "E1.18 已部署快照", "package_sha256": repack["candidate"]["sha256"],
                             "index_sha256": repack["index_patch"]["after_sha256"], "user_acceptance": "body_regression_accepted; exact_surprised_comparison_pending"}},
            "sources": [{"path": path, "sha256": sha(value)} for path, value in raw.items()],
            "preview_set_sha256": preview_digest,
            "counts": {"semantics": len(before), "official_resources": len(assets), "routes": len(binding["routes"])},
            "change": {"semantic": "surprised", "disconnected": "Happy.eaf", "connected": "shocked.eaf",
                       "action_unchanged": "look_around", "deleted_resources": [], "other_Happy_references": 9},
            "limits": ["Only offline draft mapping changes; never device writes", "Dynamic routes have no invented fixed expression",
                       "Motor request labels are static protocol facts, not physical movement previews",
                       "Raw source frames and composed circular output are labeled separately"]}
    report = {key: value for key, value in data.items() if key not in ("assets", "evidence", "routes")}
    report["counts"] = {"semantics": len(before), "official_resources": len(assets), "routes": len(binding["routes"])}
    report["change"] = {"semantic": "surprised", "disconnected": "Happy.eaf", "connected": "shocked.eaf",
                        "action_unchanged": "look_around", "deleted_resources": [], "other_Happy_references": 9}
    report["media"] = [{"name": key, "atlas_sha256": value["atlas_sha256"], "source_eaf_sha256": value["sha256"],
                        "deployed_eaf_sha256": value["deployed_sha256"]} for key, value in assets.items()]
    return data, report

def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data, report = build()
    html = (ROOT / "scripts/expression-node-preview-e1-19.template.html").read_text(encoding="utf-8")
    html = html.replace("__E119_DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    html = html.replace("__E119_CORE__", (ROOT / "scripts/expression-node-preview-e1-19-core.js").read_text(encoding="utf-8"))
    html = html.replace("__E119_JS__", (ROOT / "scripts/expression-node-preview-e1-19-controller.js").read_text(encoding="utf-8"))
    products = {OUT / "index.html": html.encode("utf-8"), OUT / "report.json": (json.dumps(report, ensure_ascii=False, indent=2)+"\n").encode("utf-8")}
    for path, content in products.items():
        if args.check:
            if not path.exists() or path.read_bytes() != content:
                raise SystemExit("stale generated artifact: " + str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print(json.dumps({"result": "checked" if args.check else "built", "counts": report["counts"],
                      "preview_set_sha256": data["preview_set_sha256"], "html_bytes": len(products[OUT / "index.html"])}))

if __name__ == "__main__":
    main()
