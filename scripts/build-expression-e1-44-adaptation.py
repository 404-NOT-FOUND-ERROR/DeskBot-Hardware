#!/usr/bin/env python3
"""Build E1.44 public metadata and local three-node animated comparison."""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deskbot_e144_for_preview", ROOT / "scripts/audit-expression-e1-44-adaptation.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
LOCAL = ROOT / "tmp/expression-e1-44-adaptation-preview-20261104"


def write(path, raw, check):
    if check:
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError("generated E1.44 preview differs: " + str(path))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def payload(report, local):
    AUDIT.validate_report(report)
    original_page = (ROOT / "artifacts/role-expression-workbench-e1-31-20261029/index.html").read_text(encoding="utf-8")
    match = re.search(r'<script id="e131-data" type="application/json">(.*?)</script>', original_page, re.S)
    current = json.loads(match[1])["assets"]["Happy.eaf"]
    current_png = base64.b64decode(current["atlas"].split(",", 1)[1], validate=True)
    if (AUDIT.sha(current_png) != current["atlas_sha256"]
            or current["deployed_sha256"] != report["terminal"]["source_eaf_sha256"]):
        raise ValueError("current Happy preview differs")
    variants = []
    for item in report["variants"]:
        row = {key: item[key] for key in ("id", "eaf_bytes", "eaf_sha256", "dimensions", "layout", "capacity")}
        row["atlas"] = dict(item["atlas"])
        row["atlas"]["data_url"] = None
        if local:
            raw = (AUDIT.OUTPUT / item["atlas"]["path"]).read_bytes()
            if AUDIT.sha(raw) != item["atlas"]["sha256"] or len(raw) != item["atlas"]["bytes"]:
                raise ValueError("local atlas differs")
            row["atlas"]["data_url"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        variants.append(row)
    return {"edition": "E1.44", "media_scope": "local" if local else "public",
            "current": {key: current[key] for key in ("atlas", "frame_width", "frame_height", "columns", "frame_count", "fps")},
            "variants": variants, "baseline": report["baseline"], "timing": report["timing"],
            "report_sha256": AUDIT.sha(AUDIT.json_bytes(report)),
            "hardware_accessed": False, "device_mapping_changed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = json.loads((AUDIT.PUBLIC / "report.json").read_bytes())
    data = payload(report, args.local)
    template = (ROOT / "scripts/expression-e1-44-adaptation.template.html").read_text(encoding="utf-8")
    html = template.replace("__E144_DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    if "__E144_DATA__" in html:
        raise ValueError("unexpanded E1.44 template")
    out = LOCAL if args.local else AUDIT.PUBLIC
    write(out / "index.html", html.encode("utf-8"), args.check)
    write(out / "README.txt", ("E1.44 · 害羞表情资源适配 / Shy resource adaptation\n\n"
          "Open index.html for the current, original full-face and mirrored-eye comparison.\n"
          "The public edition includes metadata and current Happy preview; candidate media stays local.\n"
          "Draft selection only; the device retains E1.42 App and E1.18 assets.\n").encode("utf-8"), args.check)
    print(json.dumps({"result": "checked" if args.check else "built", "edition": "E1.44",
                      "output": str(out), "media_scope": data["media_scope"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
