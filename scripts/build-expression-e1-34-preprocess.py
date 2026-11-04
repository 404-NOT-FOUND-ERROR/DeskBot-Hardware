#!/usr/bin/env python3
"""Generate reproducible public metadata and private E1.34 animation previews."""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deskbot_e134_audit_for_builder", ROOT / "scripts/audit-expression-e1-34-preprocess.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
OUT = ROOT / "artifacts/expression-e1-34-preprocess-20261029"
LOCAL = ROOT / "tmp/expression-e1-34-preprocess-20261029-preview"
TEMPLATE = ROOT / "scripts/expression-e1-34-preprocess.template.html"


def write(path, raw, check):
    if check:
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError("stale generated E1.34 file: " + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def payload(report, local):
    AUDIT.validate_report(report)
    e131 = ROOT / "artifacts/role-expression-workbench-e1-31-20261029/index.html"
    match = re.search(r'<script id="e131-data" type="application/json">(.*?)</script>', e131.read_text(encoding="utf-8"), re.S)
    current = json.loads(match.group(1))["assets"]["Happy.eaf"]
    raw_current = base64.b64decode(current["atlas"].split(",", 1)[1], validate=True)
    if (AUDIT.sha(raw_current) != current["atlas_sha256"]
            or current["deployed_sha256"] != "2ed3a64ecdb7a78e2c9959bfc3758c2742d5447d4a2bcdc6d2bf072763deba5c"):
        raise ValueError("current Happy preview/source anchor changed")
    best = next(row for row in report["lossless_variants"] if row["id"] == report["smallest_lossless_id"])
    candidates = {}
    for key, row in (("full", best), ("eye", report["crop_research"])):
        atlas = report["preview_atlases"][key]
        data_url = None
        if local:
            png = (AUDIT.OUTPUT / atlas["path"]).read_bytes()
            if len(png) != atlas["bytes"] or AUDIT.sha(png) != atlas["sha256"]:
                raise ValueError("candidate atlas hash/size drift")
            data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        candidates[key] = {"id": row["id"], "eaf_bytes": row["eaf_bytes"],
                           "eaf_sha256": row["eaf_sha256"], "capacity": row["capacity"],
                           "atlas": {**atlas, "data_url": data_url},
                           "publication_media_omitted": not local}
    return {"schema": "deskbot.expression_e1_34_preprocess_workbench/v1", "edition": "E1.34",
            "media_scope": "local_research" if local else "public_metadata_only",
            "hardware_accessed": False, "firmware_changed": False, "deployment_eligible": False,
            "flash_authorized": False, "source": report["source"], "baseline": report["baseline"],
            "current": {key: current[key] for key in ("atlas", "atlas_sha256", "frame_width", "frame_height", "columns", "frame_count", "fps", "loop")},
            "candidates": candidates,
            "grid": [{key: row[key] for key in ("id", "encoding", "block_height", "deduplicate_payloads", "eaf_bytes", "capacity", "native_checked")} for row in report["lossless_variants"]],
            "crop": {key: report["crop_research"][key] for key in ("crop", "dimensions", "lossless_to_full_source", "visual_accepted", "full_screen_reference_difference_frames")},
            "timing": report["timing_research"],
            "native": {"frame_layout_checks": 752, "selected_candidate_count": 2,
                       "last_image_not_scheduled_by_product_default": 187,
                       "product_scheduled_images": 187, "source_loop": [50, 150],
                       "local_segment_config_supported_by_product": False},
            "report_sha256": AUDIT.sha(AUDIT.json_bytes(report))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    path = AUDIT.OUTPUT / "report.json"
    if not path.is_file():
        path = OUT / "report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    data = payload(report, args.local)
    html = TEMPLATE.read_text(encoding="utf-8").replace("__E134_DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    if "__E134_DATA__" in html:
        raise ValueError("E1.34 template token was not expanded")
    output = LOCAL if args.local else OUT
    write(output / "index.html", html.encode("utf-8"), args.check)
    write(output / "report.json", AUDIT.json_bytes(report), args.check)
    readme = ("DeskBot E1.34 candidate preprocessing / 表情预处理与布局审阅\n\n"
              "Open index.html. The public copy contains metadata and current official Happy preview only.\n"
              "Candidate media stays in the ignored local tmp preview pending redistribution review.\n"
              "13 lossless recoding trials; 2 retained candidates checked in the official C player.\n"
              "No device IO, mapping/firmware/NVS/C61 change or flashable package.\n").encode("utf-8")
    write(output / "README.txt", readme, args.check)
    print(json.dumps({"result": "checked" if args.check else "built", "edition": "E1.34",
                      "output": str(output), "media_scope": data["media_scope"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
