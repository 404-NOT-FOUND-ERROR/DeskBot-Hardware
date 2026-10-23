#!/usr/bin/env python3
"""Check the public snapshot, documentation links and offline visualizations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    failures = []
    # Only public inputs: never scan ignored local logs, caches or .git objects.
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    files = [ROOT / name for name in set(names) if name and (ROOT / name).is_file()]
    private_path = re.compile(r"[A-Za-z]:[\\/][^\n\"']*(?:Codex Project|Users[\\/])", re.I)
    credential = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")
    device_id = re.compile(r'"(?:mac|device_id)"\s*:\s*"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}"|"device_id"\s*:\s*"esp32s3-[0-9a-f]{12}"', re.I)
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if path.stat().st_size > 10 * 1024 * 1024:
            failures.append("large public file: " + relative)
        if path.suffix.lower() in {".md", ".json", ".jsonl", ".py", ".ps1", ".yml", ".yaml", ".lock", ".html", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            if private_path.search(text) or credential.search(text) or device_id.search(text):
                failures.append("private path, credential or device ID: " + relative)
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8-sig"))
            except ValueError:
                failures.append("invalid JSON: " + relative)

    for name, other in (("README.md", "README.en.md"), ("README.en.md", "README.md")):
        text = (ROOT / name).read_text(encoding="utf-8")
        if other not in text or "O2f2" not in text or "E1.13" not in text:
            failures.append("missing language link or current baseline: " + name)
    # Check local links in project-owned documentation, not imported upstream docs.
    docs = [ROOT / "README.md", ROOT / "README.en.md", ROOT / "THIRD_PARTY.md", *(ROOT / "docs").glob("*.md")]
    for path in docs:
        for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if re.match(r"(?:[a-z]+:|#)", link):
                continue
            if not (path.parent / link.split("#", 1)[0]).exists():
                failures.append(f"broken local link: {path.relative_to(ROOT)} -> {link}")

    body = ROOT / "firmware/body"
    manifest = body / "SOURCE_MANIFEST.sha256"
    if manifest.is_file():
        listed = set()
        for line in manifest.read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            listed.add(relative)
            path = body / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                failures.append("source manifest mismatch: " + relative)
        expected = {p.relative_to(body).as_posix() for p in files if p.is_relative_to(body) and p != manifest}
        if listed != expected:
            failures.append("source manifest membership mismatch")
    else:
        failures.append("source manifest missing")
    for name in ("expression-node-workbench-e1-4d-20261021", "expression-binding-matrix-e1-9-20261022", "expression-e1-10-review-20261022"):
        page = ROOT / "artifacts" / name / "index.html"
        if not page.is_file() or "connect-src 'none'" not in page.read_text(encoding="utf-8"):
            failures.append("missing offline visualization: " + name)
    for directory, script_id in (("expression-node-workbench-e1-4d-20261021", "workbench-data"),
                                 ("expression-e1-10-review-20261022", "data")):
        page = ROOT / "artifacts" / directory / "index.html"
        text = page.read_text(encoding="utf-8")
        match = re.search(r'<script id="' + script_id + r'" type="application/json">(.*?)</script>', text, re.S)
        if match is None:
            failures.append("missing workbench payload: " + directory)
            continue
        data = json.loads(match[1])
        if script_id == "workbench-data":
            candidates = [v for v in data["preview"]["assets"].values() if v.get("origin") != "official"]
            media_key = "atlas"
        else:
            candidates = [v for k, v in data["preview"].items() if k.startswith("shy-")]
            media_key = "data_url"
        if not candidates or any(v.get(media_key) or not v.get("publication_media_omitted") for v in candidates):
            failures.append("unlicensed candidate media must be withheld: " + directory)
    for failure in failures:
        print("FAIL:", failure)
    if failures:
        return 1
    print(f"publication checks passed: {len(files)} files; links, JSON, source hashes and offline pages")
    print("Pattern scans are safeguards, not a guarantee against all possible secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
