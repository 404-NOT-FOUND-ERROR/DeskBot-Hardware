#!/usr/bin/env python3
"""Install/recover the common-expression App with the existing COM4 transaction."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_common_e3f7_deployment as reviewed

spec = importlib.util.spec_from_file_location("common7_transaction", ROOT / "scripts/deploy-studio-eat.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry.reviewed = reviewed
entry.base.reviewed = reviewed
entry.base.entry.reviewed = reviewed
entry.base.entry.ROLLBACK_TARGET = reviewed.ROLLBACK_TARGET
base = entry.base
CommonDevice = entry.EatingDevice

if __name__ == "__main__":
    entry.main()
