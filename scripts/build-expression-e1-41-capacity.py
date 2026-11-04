#!/usr/bin/env python3
"""Build the bounded E1.41 capacity-query increment with locked components."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "firmware/body"
DONOR = ROOT / "tmp/e139-octal-20261030/p"
DEFAULT_WORK = "tmp/e141-capacity-tool-20261031"
DEFAULT_OUTPUT = "artifacts/expression-capacity-tool-e1-41-20261031/report.json"


def load_storage_helper():
    path = ROOT / "scripts/build-expression-e1-35a-storage.py"
    spec = importlib.util.spec_from_file_location("e141_storage_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load locked build helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_storage_helper()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def environment() -> tuple[dict[str, str], Path, Path]:
    idf = Path(os.environ.get("IDF_PATH", r"D:\Espressif\frameworks\esp-idf-v5.5.5"))
    python = Path(os.environ.get(
        "IDF_PYTHON_ENV_PATH", r"D:\Espressif\python_env\idf5.5_py3.11_env"
    )) / "Scripts/python.exe"
    if not idf.joinpath("tools/idf.py").is_file() or not python.is_file():
        raise ValueError("ESP-IDF 5.5 Python environment is unavailable")
    tool_bins = [
        r"D:\Espressif\tools\cmake\3.30.2\bin",
        r"D:\Espressif\tools\ninja\1.12.1",
        r"D:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin",
    ]
    env = os.environ.copy()
    env.update({
        "IDF_PATH": str(idf),
        "IDF_TOOLS_PATH": r"D:\Espressif",
        "IDF_PYTHON_ENV_PATH": str(python.parent.parent),
        "ESP_IDF_VERSION": "5.5.5",
        "IDF_TARGET": "esp32s3",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "IDF_COMPONENT_CHECK_NEW_VERSION": "0",
        "CMAKE_NINJA_FORCE_RESPONSE_FILE": "1",
    })
    env["PATH"] = os.pathsep.join(tool_bins + [str(python.parent), env.get("PATH", "")])
    return env, python, idf


def run(command: list[str], cwd: Path, env: dict[str, str], log: Path) -> None:
    with log.open("w", encoding="utf-8", newline="\n") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, text=True, check=False)
    if result.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        raise RuntimeError(f"build command failed ({result.returncode}):\n" + "\n".join(tail))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=DEFAULT_WORK)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    work = (ROOT / args.work_dir).resolve()
    work.relative_to((ROOT / "tmp").resolve())
    output = (ROOT / args.output).resolve()
    output.relative_to((ROOT / "artifacts").resolve())
    if output.suffix != ".json":
        raise ValueError("output must be JSON under artifacts/")
    if not DONOR.is_dir():
        raise ValueError(f"locked component donor is missing: {DONOR}")

    work.mkdir(parents=True, exist_ok=True)
    inputs = HELPER.HELPER.inventory()
    locked = __import__("yaml").safe_load((SOURCE / "dependencies.lock").read_bytes())
    project, components = HELPER.stage(work, DONOR, inputs, locked)
    env, python, idf = environment()

    build = work / "o-32m"
    build.mkdir(parents=True, exist_ok=True)
    sdkconfig = build / "sdkconfig"
    reference = ROOT / "tmp/e139-octal-20261030/outputs/32m/sdkconfig"
    if not reference.is_file():
        raise ValueError("accepted E1.39 32 MiB sdkconfig reference is missing")
    shutil.copy2(reference, sdkconfig)
    common = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
              "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
              "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
    steps = []
    for action in ("reconfigure", "build"):
        log = work / f"32m-{action}.log"
        if action == "build":
            HELPER.HELPER.quote_image_effects_link_path(build, project)
        run(common + [action], project, env, log)
        steps.append({"action": action, "log": log.relative_to(ROOT).as_posix(), "sha256": sha(log)})

    artifacts = {}
    for name in ("xiaozhi.bin", "bootloader/bootloader.bin", "partition_table/partition-table.bin",
                 "expression_assets.bin", "srmodels/srmodels.bin", "config/sdkconfig.json",
                 "bootloader/config/sdkconfig.json", "compile_commands.json"):
        path = build / name
        if not path.is_file():
            raise ValueError("missing build artifact: " + name)
        artifacts[name] = {"bytes": path.stat().st_size, "sha256": sha(path)}

    generated_config = json.loads((build / "config/sdkconfig.json").read_text(encoding="utf-8"))
    if generated_config.get("DESKBOT_EXPRESSION_OBSERVER_ENABLE") is not True:
        raise ValueError("the retained 32 MiB configuration did not enable E1.26")
    if generated_config.get("BOARD_TYPE_ESP_VOCAT") is not True:
        raise ValueError("wrong board configuration")
    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    if sum(Path(item["file"]).name == "dev_tools.cc" for item in commands) != 1:
        raise ValueError("dev_tools.cc is not represented exactly once in compile commands")
    report = {
        "schema": "deskbot.expression.capacity_tool.e1_41_build/v1",
        "edition": "E1.41",
        "result": "bounded_capacity_tool_build_passed",
        "source_manifest_sha256": sha(SOURCE / "SOURCE_MANIFEST.sha256"),
        "source_sha256": inputs,
        "components": components,
        "configured_flash_bytes": 32 * 1024 * 1024,
        "build_directory": build.relative_to(ROOT).as_posix(),
        "steps": steps,
        "artifacts": artifacts,
        "tool": {"name": "self.get_flash_capacity", "ai_visible": True,
                 "user_only_full_tool_unchanged": True, "device_identifiers_exposed": False,
                 "mmap_verified": False, "upper_assets_verified": False},
        "deployment_eligible": False,
        "flash_authorized": False,
        "safety": {"hardware_accessed": False, "serial_accessed": False,
                    "device_written": False, "nvs_changed": False, "flash_executed": False},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("E1.41 bounded capacity tool build passed: " + output.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print("E1.41 capacity tool build blocked: " + str(error), file=sys.stderr)
        raise SystemExit(2)
