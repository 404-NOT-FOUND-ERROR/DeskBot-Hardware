#!/usr/bin/env python3
"""Validate an E1.42 device response against the E1.40 full readback."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_capacity_probe import (  # noqa: E402
    LOWER_ADDRESS,
    PHYSICAL_FLASH_BYTES,
    SAMPLE_BYTES,
    UPPER_ADDRESS,
    parse_probe,
)

DEFAULT_REFERENCE = ROOT / "tmp/e140-capacity-boot-20261030/current-full-32MiB.bin"


def reference_samples(path: Path) -> tuple[bytes, bytes, str]:
    if not path.is_file() or path.stat().st_size != PHYSICAL_FLASH_BYTES:
        raise ValueError("reference must be the exact 32 MiB E1.40 full readback")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    with path.open("rb") as stream:
        stream.seek(LOWER_ADDRESS)
        lower = stream.read(SAMPLE_BYTES)
        stream.seek(UPPER_ADDRESS)
        upper = stream.read(SAMPLE_BYTES)
        stream.seek(UPPER_ADDRESS)
        upper_is_erased = True
        while chunk := stream.read(1024 * 1024):
            if any(byte != 0xFF for byte in chunk):
                upper_is_erased = False
                break
    if len(lower) != SAMPLE_BYTES or len(upper) != SAMPLE_BYTES or not upper_is_erased:
        raise ValueError("E1.40 boundary or erased-upper-half reference is invalid")
    return lower, upper, digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe_json", type=Path, help="raw JSON returned by self.probe_flash_addressing")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    args = parser.parse_args()
    lower, upper, digest = reference_samples(args.reference)
    result = parse_probe(
        args.probe_json.read_bytes(),
        expected_lower_sample=lower,
        expected_upper_sample=upper,
    )
    output = {
        "schema": "deskbot.expression.capacity_read_mmap.e1_42_runtime_validation/v1",
        "result": "runtime_probe_passed" if result["probe_passed"] else "runtime_probe_failed",
        "reference": {
            "path": args.reference.resolve().relative_to(ROOT).as_posix(),
            "sha256": digest,
            "bytes": PHYSICAL_FLASH_BYTES,
            "upper_half_all_ff": True,
        },
        "probe": result,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result["probe_passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print("E1.42 probe validation failed: " + str(error), file=sys.stderr)
        raise SystemExit(2)
