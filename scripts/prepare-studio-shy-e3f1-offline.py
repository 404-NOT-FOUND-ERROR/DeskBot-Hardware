#!/usr/bin/env python3
"""Validate the offline E1.45 then E3f-1 install and recovery sequence."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_e3f1_deployment as deployment


def check_build_sources():
    spec = importlib.util.spec_from_file_location("studio_shy_build_check",
        ROOT / "scripts/build-studio-shy.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("E3f-1 build checker unavailable")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.build(check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/studio-shy-e3f1/offline-deployment-3.json")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "artifacts").resolve()):
        parser.error("output must stay inside this worktree artifacts directory")
    if output.exists():
        raise FileExistsError(f"retained offline result exists: {output}")
    check_build_sources()
    result = deployment.rehearse(ROOT)
    result["evidence"]["build_source_check"] = "passed"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"result": result["result"], "output": str(output),
        "app_range": [result["app"]["offset"], result["app"]["end_exclusive"]],
        "hardware_accessed": False, "device_written": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
