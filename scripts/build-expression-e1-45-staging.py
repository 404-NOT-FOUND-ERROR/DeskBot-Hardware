#!/usr/bin/env python3
"""Build the selected-resource staging view from verified E1.45 images."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGE = load(ROOT / "scripts/prepare-expression-e1-45-staging.py", "deskbot_e145_for_builder")
PREVIEW = load(ROOT / "scripts/build-expression-e1-44-adaptation.py", "deskbot_e144_for_e145_builder")
LOCAL = ROOT / "tmp/expression-e1-45-staging-preview-20261104"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = json.loads((STAGE.PUBLIC / "report.json").read_bytes())
    STAGE.check_saved(report)
    prior = json.loads((STAGE.E144.PUBLIC / "report.json").read_bytes())
    media = PREVIEW.payload(prior, args.local)
    eye = next(row for row in media["variants"] if row["id"] == "eye")
    if eye["eaf_sha256"] != report["selection"]["eaf_sha256"]:
        raise ValueError("preview source differs from the selected package asset")
    data = {"edition": "E1.45", "media_scope": media["media_scope"],
            "current": media["current"], "eye": eye["atlas"],
            "report": report, "report_sha256": STAGE.sha(STAGE.json_bytes(report))}
    template = (ROOT / "scripts/expression-e1-45-staging.template.html").read_text(encoding="utf-8")
    html = template.replace("__E145_DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    output = LOCAL if args.local else STAGE.PUBLIC
    target = output / "index.html"
    raw = html.encode("utf-8")
    if args.check:
        if not target.is_file() or target.read_bytes() != raw:
            raise ValueError("E1.45 staging page differs")
    else:
        output.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    print(json.dumps({"result": "checked" if args.check else "built", "output": str(target),
                      "media_scope": data["media_scope"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
