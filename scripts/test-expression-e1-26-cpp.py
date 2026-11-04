#!/usr/bin/env python3
"""Compile and run the E1.26 observer against a small host-side FreeRTOS stub."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
OBSERVER_CC = ROOT / "firmware/body/main/display/expression_runtime_observer.cc"
OBSERVER_H = ROOT / "firmware/body/main/display/expression_runtime_observer.h"
TEST_CC = ROOT / "tests/cpp/expression_runtime_observer_test.cc"
STUBS = ROOT / "tests/cpp/stubs"
SCHEMA = ROOT / "protocol/expression-runtime-observation-e1-26.schema.json"
DEFAULT_OUTPUT = "tmp/e1-26-cpp-observer-report.json"


def find_compiler() -> tuple[list[str], str] | None:
    configured_zig = os.environ.get("DESKBOT_ZIG")
    if configured_zig:
        candidate = Path(configured_zig)
        if candidate.is_file():
            return [str(candidate.resolve())], "zig-c++"
    project_root = ROOT.parent.parent
    bundled_zig = project_root / "tmp/zig-x86_64-windows-0.15.2/zig.exe"
    if bundled_zig.is_file():
        return [str(bundled_zig.resolve())], "zig-c++"
    found = shutil.which("zig")
    if found:
        return [str(Path(found).resolve())], "zig-c++"

    configured_cxx = os.environ.get("DESKBOT_CXX") or os.environ.get("CXX")
    if configured_cxx:
        words = shlex.split(configured_cxx, posix=os.name != "nt")
        if words:
            resolved = shutil.which(words[0])
            if resolved:
                words[0] = resolved
                return words, "native-c++"
            if Path(words[0]).is_file():
                words[0] = str(Path(words[0]).resolve())
                return words, "native-c++"
    for name in ("g++", "clang++", "c++"):
        found = shutil.which(name)
        if found:
            return [found], "native-c++"
    return None


def compile_and_run(compiler: list[str], compiler_kind: str, enabled: bool,
                    output_dir: Path) -> dict[str, Any]:
    label = "on" if enabled else "off"
    binary = output_dir / f"expression-runtime-observer-{label}{'.exe' if os.name == 'nt' else ''}"
    command = compiler + (["c++"] if compiler_kind == "zig-c++" else []) + [
        "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-DDESKBOT_EXPRESSION_OBSERVER_TESTING=1",
        f"-DCONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE={1 if enabled else 0}",
        "-I", str(STUBS), "-I", str(ROOT / "firmware/body/main/display"),
        str(TEST_CC), str(OBSERVER_CC), "-o", str(binary),
    ]
    build = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8",
                           errors="replace", capture_output=True, check=False)
    if build.returncode != 0:
        return {
            "enabled": enabled,
            "compile_pass": False,
            "run_pass": False,
            "returncode": build.returncode,
            "stdout": build.stdout[-4000:],
            "stderr": build.stderr[-4000:],
        }
    run = subprocess.run([str(binary)], cwd=ROOT, text=True, encoding="utf-8",
                         errors="replace", capture_output=True, check=False)
    observer_json: dict[str, Any] | None = None
    for line in run.stdout.splitlines():
        if line.startswith("observer_json:"):
            try:
                observer_json = json.loads(line.removeprefix("observer_json:"))
            except json.JSONDecodeError:
                pass
    return {
        "enabled": enabled,
        "compile_pass": True,
        "run_pass": run.returncode == 0 and "expression_runtime_observer_cpp_tests_passed" in run.stdout,
        "returncode": run.returncode,
        "stdout": run.stdout[-4000:],
        "stderr": run.stderr[-4000:],
        "observer_json": observer_json,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                     dir=path.parent, prefix=path.name + ".",
                                     suffix=".tmp", delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output = output.resolve()
    try:
        (ROOT / "tmp").mkdir(exist_ok=True)
        relative = output.relative_to(ROOT.resolve())
        if not relative.parts or relative.parts[0] not in {"artifacts", "tmp"}:
            raise ValueError("output must be inside artifacts/ or tmp/")
        for path in (OBSERVER_CC, OBSERVER_H, TEST_CC, STUBS / "freertos/FreeRTOS.h",
                     STUBS / "freertos/portmacro.h", STUBS / "esp_attr.h", SCHEMA):
            if not path.is_file():
                raise ValueError(f"missing C++ test input: {path.relative_to(ROOT)}")
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        compiler = find_compiler()
        if compiler is None:
            raise ValueError("C++17 compiler not found; set DESKBOT_ZIG, DESKBOT_CXX, or CXX")
        compiler_command, compiler_kind = compiler
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e1-26-cpp-") as directory:
            results = [compile_and_run(compiler_command, compiler_kind, enabled,
                                       Path(directory)) for enabled in (False, True)]
        passed = all(item["compile_pass"] and item["run_pass"] for item in results)
        enabled_result = next(item for item in results if item["enabled"])
        schema_errors = []
        if enabled_result.get("observer_json") is None:
            schema_errors.append("enabled C++ test did not emit a JSON description")
        else:
            validator = Draft202012Validator(schema)
            schema_errors = [error.message for error in validator.iter_errors(
                enabled_result["observer_json"])]
        passed = passed and not schema_errors
        version_arg = "version" if compiler_kind == "zig-c++" else "--version"
        version_result = subprocess.run(compiler_command + [version_arg], cwd=ROOT,
                                        text=True, encoding="utf-8", errors="replace",
                                        capture_output=True, check=False)
        report = {
            "schema": "deskbot.expression.e1_26_cpp_test/v1",
            "edition": "E1.26",
            "result": "host_cpp_observer_tests_passed" if passed else "host_cpp_observer_tests_failed",
            "audit_pass": passed,
            "compiler": {
                "executable": Path(compiler_command[0]).name,
                "kind": compiler_kind,
                "standard": "c++17",
                "version": (version_result.stdout or version_result.stderr).splitlines()[:2],
            },
            "configurations": results,
            "json_description_schema_valid": not schema_errors,
            "json_description_errors": schema_errors[:8],
            "source_sha256": {
                "observer_cc": hashlib.sha256(OBSERVER_CC.read_bytes()).hexdigest(),
                "observer_h": hashlib.sha256(OBSERVER_H.read_bytes()).hexdigest(),
                "test_cc": hashlib.sha256(TEST_CC.read_bytes()).hexdigest(),
            },
            "safety": {
                "hardware_accessed": False,
                "serial_accessed": False,
                "device_written": False,
                "nvs_changed": False,
            },
            "limitations": [
                "Host FreeRTOS and portMUX stubs exercise the observer contract but do not prove ESP-IDF SMP timing.",
                "This test does not render EAF resources or validate LCD output.",
            ],
        }
        write_json(output, report)
        print(f"{report['result']}: {relative.as_posix()}")
        return 0 if passed else 1
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"E1.26 C++ test failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
