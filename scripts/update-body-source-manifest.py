#!/usr/bin/env python3
"""Regenerate the body snapshot manifest from public Git inputs only."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BODY = ROOT / "firmware/body"
MANIFEST = BODY / "SOURCE_MANIFEST.sha256"


def render() -> str:
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z",
         "--", "firmware/body"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    files = sorted({ROOT / name for name in names if name
                    and (ROOT / name).is_file() and ROOT / name != MANIFEST},
                   key=lambda path: tuple(part.casefold() for part in path.relative_to(BODY).parts))
    return "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
                   f"{path.relative_to(BODY).as_posix()}\n" for path in files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        valid = MANIFEST.is_file() and MANIFEST.read_bytes() == expected.encode("utf-8")
        print("body source manifest current" if valid else "body source manifest stale")
        return 0 if valid else 1
    # Derive all content before opening the output, then replace atomically.
    temporary = MANIFEST.with_suffix(".sha256.tmp")
    temporary.write_bytes(expected.encode("utf-8"))
    temporary.replace(MANIFEST)
    print(f"body source manifest updated: {len(expected.splitlines())} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
