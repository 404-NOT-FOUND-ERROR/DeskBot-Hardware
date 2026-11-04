#!/usr/bin/env python3
"""Offline contract tests for the E1.19 node-preview generator."""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SCRIPT = ROOT / "scripts/build-expression-node-preview-e1-19.py"
HTML = ROOT / "artifacts/expression-node-preview-e1-19-20261024/index.html"
REPORT = ROOT / "artifacts/expression-node-preview-e1-19-20261024/report.json"

def main() -> None:
    result = subprocess.run([PYTHON, str(SCRIPT), "--check"], cwd=ROOT, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise SystemExit(result.stderr or result.stdout)
    page = HTML.read_text(encoding="utf-8")
    match = re.search(r'<script id="e119-data" type="application/json">(.*?)</script>', page, re.S)
    assert match, "missing E1.19 data"
    data = json.loads(match.group(1))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["counts"] == {"semantics": 29, "official_resources": 18, "routes": 56}
    assert data["snapshots"]["before"]["surprised"]["resource_id"] == "Happy.eaf"
    assert data["snapshots"]["deployed"]["surprised"]["resource_id"] == "shocked.eaf"
    assert data["snapshots"]["deployed"]["surprised"]["action_id"] == "look_around"
    assert len(data["assets"]) == 18
    assert "__E119_" not in page
    assert data["hardware_accessed"] is False
    print("E1.19 offline contract passed: 29 semantic routes, 18 official assets, E1.13/E1.18 snapshots")

if __name__ == "__main__":
    main()
