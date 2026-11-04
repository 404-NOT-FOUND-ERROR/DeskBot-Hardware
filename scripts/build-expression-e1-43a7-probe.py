#!/usr/bin/env python3
"""Build the corrected A7 probe in an independent directory; no device I/O."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("upper_a7_build", ROOT / "scripts/build-expression-e1-43a-probe.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
build.WORK = ROOT / "tmp/e143a7-probe-20261103"
build.PUBLIC = ROOT / "artifacts/expression-upper-snapshot-e1-43a7-20261103"
build.EDITION = "E1.43a-7"

if __name__ == "__main__":
    raise SystemExit(build.main())
