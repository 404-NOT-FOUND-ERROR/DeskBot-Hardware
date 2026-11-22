#!/usr/bin/env python3
"""Install/recover the config-only shy App using the existing COM4 transaction."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_shy_e3f5_deployment as reviewed

spec = importlib.util.spec_from_file_location("shy5_transaction", ROOT / "scripts/deploy-studio-eat.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry.reviewed = reviewed
entry.base.reviewed = reviewed
entry.base.entry.reviewed = reviewed
entry.base.entry.ROLLBACK_TARGET = reviewed.ROLLBACK_TARGET
base = entry.base
ShyDevice = entry.EatingDevice

if __name__ == "__main__":
    entry.main()
