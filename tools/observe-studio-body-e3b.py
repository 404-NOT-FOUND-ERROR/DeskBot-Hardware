#!/usr/bin/env python3
"""E3b worker: same serial ownership, E3a line framing plus validated hello."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_identity_observation import IdentityObservation

spec = importlib.util.spec_from_file_location("studio_e3b_console_worker", ROOT / "tools/observe-studio-body-e3a.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
parent.Observation = IdentityObservation

if __name__ == "__main__":
    parent.main()
