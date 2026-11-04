#!/usr/bin/env python3
"""Validate a fixed-range E1.43a CPU diagnostic capture; no device I/O."""
from __future__ import annotations

import argparse
import hashlib
import json
import jsonschema
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_upper_pattern import (PAGE_START, REFERENCE_SHA256, build_pattern,
                                            manifest_for, sha)
from protocol.storage_upper_pattern_runtime import extract_records, validate_capture

DEFAULT_REFERENCE = ROOT / "tmp/e140-capacity-boot-20261030/current-full-32MiB.bin"
DEFAULT_MANIFEST = ROOT / "artifacts/expression-upper-pattern-e1-43a-20261102/pattern-manifest.json"
OUTPUT_ROOT = ROOT / "tmp/e143a-upper-pattern-20261102/runtime-validation"


def reference_facts(path: Path, pattern: bytes) -> dict:
    if not path.is_file() or path.stat().st_size != 32 * 1024 * 1024:
        raise ValueError("reference must be the exact 32 MiB E1.40 full readback")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != REFERENCE_SHA256:
        raise ValueError("E1.40 reference digest differs from the locked anchor")
    with path.open("rb") as stream:
        low = stream.read(65536)
        stream.seek(PAGE_START - 16)
        boundary_low = stream.read(16)
        stream.seek(PAGE_START)
        target = stream.read(len(pattern))
    if (len(low) != 65536 or len(boundary_low) != 16 or len(target) != len(pattern)
            or sha(low) != "7abcf5bc8921aadf1c80f834cf0bfd4cfa5e7b22773de9b1c609ad114a8eab74"
            or target != b"\xff" * len(pattern)):
        raise ValueError("historical alias/boundary or upper-page baseline does not match")
    return {"sha256": digest.hexdigest(), "low_alias_sha256": sha(low), "low_protected_sha256": sha(low[:0x9000]),
            "boundary_hex": (boundary_low + pattern[:16]).hex(), "upper_original_sha256": sha(target)}


def validate(log: str, reference: Path, manifest: dict) -> dict:
    pattern = build_pattern()
    facts = reference_facts(reference, pattern)
    records = extract_records(log)
    checked = validate_capture(records, manifest, facts["low_alias_sha256"], facts["boundary_hex"], facts["low_protected_sha256"])
    result_name = ("probe_aborted" if "aborted" in records else
                   "runtime_probe_passed" if checked["probe_passed"] else "runtime_probe_failed")
    return {"schema": "deskbot.upper_pattern.runtime_validation.e1_43a/v1", "edition": "E1.43a",
            "result": result_name, "source_log_sha256": hashlib.sha256(log.encode("utf-8")).hexdigest(),
            "pattern_sha256": manifest["sha256"], "reference": facts,
            "capacity": checked["capacity"], "checks": checked["checks"], "raw_records": records,
            "observations": checked.get("observations", {}),
            "safety": {"writes_performed_by_probe": False, "nvs_written_by_probe": False,
                       "mcp_visibility_verified": False, "hardware_accepted": False},
            "claim_limits": "This fixed-page test does not prove 8 MiB contiguous mapping, EAF playback, resource migration or application behavior."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--check", action="store_true", help="Recompute and verify an existing output")
    args = parser.parse_args()
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", args.run_label) is None:
        parser.error("run-label must be a short lowercase label")
    output = OUTPUT_ROOT / (args.run_label + ".json")
    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    result = validate(args.log.read_text(encoding="utf-8-sig"), args.reference, manifest)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    schema = json.loads((ROOT / "protocol/storage-upper-pattern-runtime-e1-43a.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise ValueError("runtime validation artifact is missing or differs from the raw capture")
    else:
        if output.exists():
            raise ValueError("refuse to overwrite runtime evidence; select a new --run-label")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(json.dumps({"result": result["result"], "pattern_sha256": result["pattern_sha256"],
                      "device_written": False, "mcp_visibility_verified": False}))
    return 0 if result["result"] == "runtime_probe_passed" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print("E1.43a probe validation failed: " + str(error), file=sys.stderr)
        raise SystemExit(2)
