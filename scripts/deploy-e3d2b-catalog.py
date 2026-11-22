#!/usr/bin/env python3
"""Prepare, install or restore the actual catalog candidate on COM4."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_e3d2b_deployment as reviewed

spec = importlib.util.spec_from_file_location("e3d2b_transaction", ROOT / "scripts/deploy-deskbot-studio-e3c3a.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.reviewed = reviewed
base.entry.reviewed = reviewed
base.entry.ROLLBACK_TARGET = reviewed.ROLLBACK_TARGET


def folder_for(label):
    if re.fullmatch(r"e3c-live-[0-9]{8}-e3d2b-[a-z0-9-]{1,24}", label) is None:
        raise ValueError("run label must be e3c-live-YYYYMMDD-e3d2b-<name>")
    folder = ROOT / "tmp" / label
    reviewed.paths.safe_path(folder, ROOT / "tmp", exists=False)
    if folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("run must stay inside repository tmp")
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "check", "prepare-live", "install", "recover", "restart"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--from-run")
    args = parser.parse_args(argv)
    if (args.phase == "prepare-live") != (args.from_run is not None):
        parser.error("prepare-live requires --from-run; other phases use only --run")
    result = base.run(args.phase, folder_for(args.run),
                      source=folder_for(args.from_run) if args.from_run else None)
    print(json.dumps({key: value for key, value in result.items()
                      if key in {"phase", "result", "edition", "plan_id", "erase_bytes", "hardware_accessed"}},
                     sort_keys=True))


if __name__ == "__main__":
    main()
