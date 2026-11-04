#!/usr/bin/env python3
"""Build the non-deployable E1.26 minimal ESP-IDF preflight in both modes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "tests/idf-expression-observer"
DEFAULT_OUTPUT = "tmp/e1-26-idf-preflight-report.json"
INPUTS = (
    "firmware/body/main/display/expression_runtime_observer.cc",
    "firmware/body/main/display/expression_runtime_observer.h",
    "tests/idf-expression-observer/CMakeLists.txt",
    "tests/idf-expression-observer/main/CMakeLists.txt",
    "tests/idf-expression-observer/main/Kconfig.projbuild",
    "tests/idf-expression-observer/main/app_main.cc",
    "tests/idf-expression-observer/sdkconfig.defaults.off",
    "tests/idf-expression-observer/sdkconfig.defaults.on",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def locate() -> tuple[Path, Path, Path]:
    idf_path = Path(os.environ.get("IDF_PATH", r"D:\Espressif\frameworks\esp-idf-v5.5.5"))
    python_path = Path(os.environ.get(
        "IDF_PYTHON_ENV_PATH", r"D:\Espressif\python_env\idf5.5_py3.11_env"
    )) / "Scripts/python.exe"
    idf_script = idf_path / "tools/idf.py"
    if not idf_script.is_file():
        raise ValueError(f"idf.py not found: {idf_script}")
    if not python_path.is_file():
        fallback = shutil.which("python")
        if not fallback:
            raise ValueError(f"IDF Python not found: {python_path}")
        python_path = Path(fallback)
    return idf_path.resolve(), python_path.resolve(), idf_script.resolve()


def build_one(idf_path: Path, python_path: Path, idf_script: Path,
              enabled: bool, root_tmp: Path) -> dict[str, Any]:
    label = "on" if enabled else "off"
    build = root_tmp / f"e1-26-idf-{label}-20261026"
    defaults = PROJECT / ("sdkconfig.defaults.on" if enabled else "sdkconfig.defaults.off")
    build.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "IDF_PATH": str(idf_path),
        "IDF_TOOLS_PATH": os.environ.get("IDF_TOOLS_PATH", r"D:\Espressif"),
        "IDF_PYTHON_ENV_PATH": str(python_path.parent.parent),
    })
    tool_bins = [
        r"D:\Espressif\tools\cmake\3.30.2\bin",
        r"D:\Espressif\tools\ninja\1.12.1",
        r"D:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin",
        r"D:\Espressif\tools\riscv32-esp-elf\esp-14.2.0_20260121\riscv32-esp-elf\bin",
    ]
    env["PATH"] = os.pathsep.join([item for item in tool_bins if Path(item).is_dir()] + [env.get("PATH", "")])
    sdkconfig = build / "sdkconfig"
    common = [str(python_path), str(idf_script), "-B", str(build),
              "-D", "IDF_TARGET=esp32s3", "-D", f"SDKCONFIG={sdkconfig}",
              "-D", f"SDKCONFIG_DEFAULTS={defaults}"]
    steps: list[dict[str, Any]] = []
    for task in ("reconfigure", "build"):
        print(f"minimal IDF {label}: {task}", flush=True)
        completed = subprocess.run(common + [task], cwd=PROJECT, env=env, text=True,
                                   encoding="utf-8", errors="replace", capture_output=True,
                                   check=False)
        local_log = build / f"preflight-{task}.log"
        local_log.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
        steps.append({"task": task, "returncode": completed.returncode,
                      "local_log": local_log.relative_to(ROOT).as_posix(),
                      "log_sha256": sha256(local_log)})
        if completed.returncode != 0:
            break
    config_json = build / "config/sdkconfig.json"
    config_value = None
    if config_json.is_file():
        try:
            config_value = json.loads(config_json.read_text(encoding="utf-8"))\
                .get("DESKBOT_EXPRESSION_OBSERVER_ENABLE")
        except (OSError, ValueError, TypeError):
            config_value = None
    commands = build / "compile_commands.json"
    source_member = False
    if commands.is_file():
        source_member = any(Path(item["file"]).name == "expression_runtime_observer.cc"
                            for item in json.loads(commands.read_text(encoding="utf-8")))
    artifacts = {name: (build / name).is_file() for name in (
        "bootloader/bootloader.bin", "partition_table/partition-table.bin")}
    app = build / "e1_26_expression_observer_preflight.bin"
    artifacts[app.name] = app.is_file()
    return {"enabled": enabled, "build_directory": str(build.relative_to(ROOT)),
            "steps": steps, "build_pass": bool(steps and steps[-1]["task"] == "build" and steps[-1]["returncode"] == 0),
            "expected_artifacts": artifacts, "config_value": config_value,
            "config_matches_requested": config_value is (True if enabled else False),
            "observer_source_member": source_member,
            "source_membership_matches_requested": source_member is enabled,
            "sdkconfig_sha256": sha256(sdkconfig) if sdkconfig.is_file() else None,
            "binary": {"bytes": app.stat().st_size, "sha256": sha256(app)} if app.is_file() else None,
            "flash_attempted": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        output = Path(args.output)
        if not output.is_absolute():
            output = (ROOT / output).resolve()
        relative = output.relative_to(ROOT.resolve())
        if not relative.parts or relative.parts[0] not in {"tmp", "artifacts"} or output.suffix != ".json":
            raise ValueError("output must be inside tmp/ or artifacts/")
        if not PROJECT.is_dir():
            raise ValueError(f"missing minimal project: {PROJECT}")
        idf_path, python_path, idf_script = locate()
        results = [build_one(idf_path, python_path, idf_script, enabled, ROOT / "tmp")
                   for enabled in (False, True)]
        passed = all(item["build_pass"] and item["config_matches_requested"]
                     and item["source_membership_matches_requested"]
                     and all(item["expected_artifacts"].values()) for item in results)
        version = subprocess.run([str(python_path), str(idf_script), "--version"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        report = {
            "schema": "deskbot.expression.e1_26_idf_preflight/v1",
            "edition": "E1.26",
            "result": "minimal_idf_preflight_passed" if passed else "minimal_idf_preflight_blocked",
            "audit_pass": passed,
            "target": "esp32s3",
            "idf_version": version,
            "source_sha256": {name: sha256(ROOT / name) for name in INPUTS},
            "runner_sha256": sha256(Path(__file__)),
            "deployable_deskbot_image": False,
            "full_firmware_build_passed": False,
            "configurations": results,
            "safety": {"hardware_accessed": False, "serial_accessed": False,
                        "device_written": False, "nvs_changed": False,
                        "flash_executed": False},
            "limitations": ["This is a minimal preflight, not the full DeskBot body build or a deployable image."],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{report['result']}: {relative.as_posix()}")
        return 0 if report["audit_pass"] else 1
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"E1.26 IDF preflight failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
