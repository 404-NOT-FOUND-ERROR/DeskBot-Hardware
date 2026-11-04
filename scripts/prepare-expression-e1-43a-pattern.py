#!/usr/bin/env python3
"""Generate/check offline E1.43a material; deliberately no flashing commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jsonschema
from protocol.storage_upper_pattern import build_pattern, manifest_for, offline_report

PUBLIC = ROOT / "artifacts/expression-upper-pattern-e1-43a-20261102"
PRIVATE = ROOT / "tmp/e143a-upper-pattern-20261102"
REFERENCE = ROOT / "tmp/e140-capacity-boot-20261030/current-full-32MiB.bin"


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def prepare(reference: bytes) -> dict[str, bytes]:
    pattern = build_pattern()
    manifest = manifest_for(pattern)
    schema = json.loads((ROOT / "protocol/storage-upper-pattern-e1-43a.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(manifest, schema)
    report = offline_report(pattern, manifest, reference)
    return {"upper-pattern-64k.bin": pattern, "pattern-manifest.json": encoded(manifest),
            "offline-report.json": encoded(report)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify existing private and public artifacts, without writing")
    parser.add_argument("--run-label", default="initial", help="Unique evidence label; refuses overwrite")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", args.run_label):
        parser.error("run-label must be a short lowercase label, not a path")
    destination = PRIVATE / args.run_label
    # All validation precedes output creation; missing or stale evidence fails closed.
    if not args.check and destination.exists():
        parser.error("run label already exists; use --check or a new unique label")
    files = prepare(REFERENCE.read_bytes())
    public_files = {"pattern-manifest.json": files["pattern-manifest.json"], "report.json": files["offline-report.json"]}
    if args.check:
        for path, expected in [(destination / name, data) for name, data in files.items()] + [
                (PUBLIC / name, data) for name, data in public_files.items()]:
            if path.read_bytes() != expected:
                raise ValueError("artifact differs from validated deterministic source: " + path.name)
    else:
        PRIVATE.mkdir(parents=True, exist_ok=True)
        # A partial run never occupies the final evidence label.
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=PRIVATE) as staging:
            for name, data in files.items():
                (Path(staging) / name).write_bytes(data)
            Path(staging).rename(destination)
        PUBLIC.mkdir(parents=True, exist_ok=True)
        for name, data in public_files.items():
            (PUBLIC / name).write_bytes(data)
    manifest = json.loads(files["pattern-manifest.json"])
    print(json.dumps({"result": "checked" if args.check else "prepared", "edition": "E1.43a",
                      "bytes": len(files["upper-pattern-64k.bin"]), "sha256": manifest["sha256"],
                      "unique_sectors": len({row["sha256"] for row in manifest["blocks"]}),
                      "serial_accessed": False, "device_written": False, "deployment_eligible": False}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, jsonschema.ValidationError) as error:
        print("E1.43a preparation failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
