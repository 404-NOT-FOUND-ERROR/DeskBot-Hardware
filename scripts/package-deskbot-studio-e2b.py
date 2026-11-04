#!/usr/bin/env python3
"""Build a verified resource package from a Studio JSON draft."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import generate, parse_draft, RELEASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--output", type=Path, default=RELEASES)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to((ROOT / "tmp").resolve())
    report, path = generate(parse_draft(args.draft.read_bytes()), output=output)
    print(json.dumps({"result": report["result"], "directory": str(path),
                      "package_bytes": report["package"]["bytes"],
                      "free_bytes": report["budget"]["free_bytes"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
