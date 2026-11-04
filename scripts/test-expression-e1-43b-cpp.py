#!/usr/bin/env python3
"""Exercise production E1.43b control flow with host-side hardware stubs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e143b_cpp_toolchain", ROOT / "scripts/test-expression-e1-26-cpp.py")
HELPER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(HELPER)


def run() -> dict:
    found = HELPER.find_compiler()
    if found is None:
        raise ValueError("C++17 compiler unavailable; use the architecture toolchain guide")
    compiler, kind = found
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e143b-cpp-") as folder:
        binary = Path(folder) / "continuous-mmap-test.exe"
        command = compiler + (["c++"] if kind == "zig-c++" else []) + [
            "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-I", str(ROOT / "firmware/body/main/boards/esp_vocat"),
            str(ROOT / "tests/cpp/continuous_mmap_probe_test.cc"), "-o", str(binary)]
        build = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
        if build.returncode:
            raise ValueError(build.stdout[-4000:] + build.stderr[-4000:])
        result = subprocess.run([str(binary)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    passed = result.returncode == 0 and "continuous_mmap_cpp_tests_passed:22" in result.stdout
    cases = [line.split(":", 1)[1] for line in result.stdout.splitlines() if line.startswith("case_passed:")]
    return {"schema": "deskbot.continuous_mmap.cpp_tests/v1", "passed": passed,
            "scenario_count": 22, "cases": cases + ["quiet_timeout", "task_allocation_failure"],
            "compiler_kind": kind, "returncode": result.returncode,
            "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                "firmware/body/main/boards/esp_vocat/continuous_mmap_probe.h",
                "tests/cpp/continuous_mmap_probe_test.cc")},
            "stderr": result.stderr[-2000:], "mocked": ["Flash", "RTOS", "hash API", "audio state", "memory"],
            "device_accessed": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.relative_to(ROOT).parts[0] not in {"tmp", "artifacts"}:
        parser.error("output must be inside repository tmp or artifacts")
    if output.exists():
        parser.error("use a new output path to retain previous evidence")
    report = run()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)
