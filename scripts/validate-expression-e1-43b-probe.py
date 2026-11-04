#!/usr/bin/env python3
"""Validate saved E1.43b logs against a bound same-run 32 MiB capture; offline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_continuous_mmap import START, SIZE, extract_records, reference_for, validate_capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--identity-sha256", required=True)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.relative_to(ROOT).parts[0] not in {"tmp", "artifacts"} or output.exists():
        parser.error("output must be a new file inside repository tmp or artifacts")
    if args.snapshot.is_symlink() or args.snapshot.stat().st_size != 32 * 1024 * 1024:
        parser.error("snapshot must be a saved 32 MiB file")
    digest = hashlib.sha256()
    with args.snapshot.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != args.snapshot_sha256:
            parser.error("snapshot SHA-256 mismatch")
        stream.seek(START)
        raw = stream.read(SIZE)
    reference = reference_for(raw, identity_sha256=args.identity_sha256, run_label=args.run_label,
                              source_sha256=args.snapshot_sha256)
    if args.log.stat().st_size > 2 * 1024 * 1024:
        parser.error("capture exceeds two MiB")
    log = args.log.read_bytes()
    if len(log) > 2 * 1024 * 1024:
        parser.error("capture grew beyond two MiB")
    checked = validate_capture(extract_records(log.decode("utf-8", errors="strict")), reference)
    checked["binding"] = {"association": "host_collected_same_run", "log_identity_authenticated": False,
                           "source_log_sha256": hashlib.sha256(log).hexdigest()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(checked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(checked["result"])
    return 0 if checked["probe_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
