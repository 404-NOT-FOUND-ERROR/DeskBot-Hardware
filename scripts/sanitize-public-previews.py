#!/usr/bin/env python3
"""Withhold unlicensed candidate pixels in this publication copy only.

Original metadata, hashes and mapping relationships remain historical evidence.
This does not grant permission for redistributing third-party source media.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
NOTICE = "候选图像未随公开副本发布（素材再分发许可待确认）；映射与容量元数据保留。"
PAGES = (
    ("expression-node-workbench-e1-4d-20261021", "workbench-data"),
    ("expression-e1-10-review-20261022", "data"),
)


def main() -> int:
    total = 0
    for directory, script_id in PAGES:
        path = ROOT / "artifacts" / directory / "index.html"
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(r'(<script id="' + re.escape(script_id) + r'" type="application/json">)(.*?)(</script>)', re.S)
        match = pattern.search(text)
        if match is None:
            raise ValueError("missing page payload: " + directory)
        data = json.loads(match[2])
        if script_id == "workbench-data":
            entries = [v for v in data["preview"]["assets"].values() if v.get("origin") != "official"]
            media_key = "atlas"
        else:
            entries = [v for k, v in data["preview"].items() if k.startswith("shy-")]
            media_key = "data_url"
        for entry in entries:
            total += bool(entry.pop(media_key, None))
            entry["publication_media_omitted"] = True
            entry["publication_notice"] = NOTICE
        data["publication"] = {"candidate_media": "withheld_pending_redistribution_license", "historical_hashes_unchanged": True}
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        text = text[:match.start(2)] + encoded + text[match.end(2):]
        path.write_text(text, encoding="utf-8", newline="\n")
    print(f"Candidate media omitted: {total}; metadata retained; original workspace untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
