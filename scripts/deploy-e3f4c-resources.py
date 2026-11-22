#!/usr/bin/env python3
"""Install/recover E1.45 resources while retaining the accepted E3f-4c App."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_e3f4c_reference import IDENTITY_SHA, load_reference

spec = importlib.util.spec_from_file_location("e3f4c_resource_entry", ROOT / "scripts/deploy-studio-resources.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def run(phase, folder, **kwargs):
    if phase == "prepare-offline" and kwargs.get("snapshot") is None:
        kwargs["snapshot"] = load_reference()["snapshot"]
    if phase in ("prepare-offline", "prepare-live") and kwargs.get("identity") is None:
        kwargs["identity"] = IDENTITY_SHA
    return entry.run(phase, folder, baseline="E3f-4c", **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare-offline", "prepare-live", "check", "install", "recover"))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--identity")
    parser.add_argument("--confirm-plan")
    args = parser.parse_args()
    result = run(args.phase, ROOT / "tmp" / args.run_label, snapshot=args.snapshot,
                 identity=args.identity, confirm_plan=args.confirm_plan)
    print(json.dumps(result, ensure_ascii=False), flush=True)
