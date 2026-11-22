#!/usr/bin/env python3
"""Publish redacted copies of new JSON evidence while retaining local originals."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "tmp/publication-20261122/originals"
WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\n]*?(?:Codex Project|Users[\\/])[^\n]*", re.I)
MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", re.I)
DEVICE = re.compile(r"esp32s3-[0-9a-f]{12}", re.I)


def redact(value, fields, prefix=""):
    if isinstance(value, dict):
        return {key: redact(item, fields, prefix + "/" + key) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, fields, prefix + "/" + str(index)) for index, item in enumerate(value)]
    if not isinstance(value, str):
        return value
    cleaned = WINDOWS_PATH.sub("[local path omitted]", value)
    cleaned = DEVICE.sub("[device identifier omitted]", cleaned)
    if prefix.rsplit("/", 1)[-1] in {"mac", "device_id"} and MAC.fullmatch(cleaned):
        cleaned = "[device identifier omitted]"
    if cleaned != value:
        fields.append(prefix)
    return cleaned


def main():
    # Tracked historical evidence keeps its exact published bytes. Only new
    # records are copied, with their local original SHA256 attached for provenance.
    names = set(subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0"))
    if LOCAL.is_dir():
        names.update(path.relative_to(LOCAL).as_posix() for path in LOCAL.rglob("*.json"))
    count = 0
    for name in sorted(filter(None, names)):
        path = ROOT / name
        if not name.startswith("artifacts/") or path.suffix != ".json" or not path.is_file():
            continue
        original = LOCAL / name
        raw = original.read_bytes() if original.is_file() else path.read_bytes()
        document = json.loads(raw)
        fields = []
        public = redact(document, fields)
        if not fields:
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        if original.exists() and original.read_bytes() != raw:
            raise ValueError("local evidence already preserved with different bytes: " + name)
        if not original.exists():
            original.write_bytes(raw)
        public["publication_redaction"] = dict(
            original_sha256=hashlib.sha256(raw).hexdigest(), fields=fields,
            note="Local paths and device identifiers omitted; original retained locally; result fields preserved.")
        public_path = path.with_name(path.stem + ".public" + path.suffix)
        public_path.write_text(json.dumps(public, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if path.read_bytes() != raw:
            path.write_bytes(raw)
        count += 1
        print("redacted public record:", public_path.relative_to(ROOT).as_posix())
    print("Public records prepared:", count)


if __name__ == "__main__":
    main()
