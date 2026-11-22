#!/usr/bin/env python3
"""Prepare, check, install or restore the E3f-1 App on COM4."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_shy_installer as installer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare-offline", "prepare-live", "check", "install", "recover", "resume-original"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--resource-run", help="installed E1.45 resource transaction used by prepare-live")
    parser.add_argument("--confirm-plan", help="saved live plan ID for install, recover or resume-original")
    args = parser.parse_args(argv)
    if (args.phase == "prepare-live") != (args.resource_run is not None):
        parser.error("prepare-live requires --resource-run; other phases use only --run")
    if (args.phase in {"install", "recover", "resume-original"}) != (args.confirm_plan is not None):
        parser.error("install, recover and resume-original require --confirm-plan")
    result = installer.run(args.phase, installer.folder_for(args.run),
        resource_folder=ROOT / "tmp" / args.resource_run if args.resource_run is not None else None,
        confirm_plan=args.confirm_plan)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    main()
