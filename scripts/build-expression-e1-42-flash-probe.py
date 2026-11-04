#!/usr/bin/env python3
"""Build the read-only E1.42 boundary-probe increment with locked components."""
from __future__ import annotations

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
BASELINE_REPORT = ROOT / "artifacts/expression-capacity-tool-e1-41-20261031/report.json"
BASELINE_FULL_READBACK = ROOT / "tmp/e140-capacity-boot-20261030/current-full-32MiB.bin"
DEFAULT_WORK = "tmp/e142-flash-probe-20261031"
DEFAULT_OUTPUT = "artifacts/expression-capacity-read-mmap-e1-42-20261031/build-report.json"
APP_PARTITION_BYTES = 0x5DC000
MINIMUM_APP_HEADROOM = 1024 * 1024
PROTECTED_ARTIFACTS = (
    "bootloader/bootloader.bin",
    "partition_table/partition-table.bin",
    "expression_assets.bin",
    "srmodels/srmodels.bin",
    "config/sdkconfig.json",
    "bootloader/config/sdkconfig.json",
)


def load_storage_helper():
    path = ROOT / "scripts/build-expression-e1-35a-storage.py"
    spec = importlib.util.spec_from_file_location("e142_storage_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load locked build helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_storage_helper()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_full_readback(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size != 32 * 1024 * 1024:
        raise ValueError("E1.40 full Flash readback is missing or not exactly 32 MiB")
    with path.open("rb") as stream:
        stream.seek(0x00FFFFF0)
        lower_sample = stream.read(32)
        stream.seek(0x01000000)
        upper_sample = stream.read(32)
        stream.seek(0x01000000)
        upper_is_erased = True
        while chunk := stream.read(1024 * 1024):
            if any(byte != 0xFF for byte in chunk):
                upper_is_erased = False
                break
    if len(lower_sample) != 32 or len(upper_sample) != 32 or not upper_is_erased:
        raise ValueError("E1.40 boundary reference does not match the expected erased upper half")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha(path),
        "lower_boundary_sample_hex": lower_sample.hex(),
        "upper_boundary_sample_hex": upper_sample.hex(),
        "upper_half_all_ff": upper_is_erased,
    }


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
    parser = __import__("argparse").ArgumentParser(description=__doc__)
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
    if not BASELINE_REPORT.is_file():
        raise ValueError(f"E1.41 build baseline is missing: {BASELINE_REPORT}")

    baseline = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
    full_readback = inspect_full_readback(BASELINE_FULL_READBACK)
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
    sdk = (build / "sdkconfig").read_text(encoding="utf-8")
    if "CONFIG_BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH=y" not in sdk:
        raise ValueError("Octal 32-bit Flash mapping configuration is not enabled")
    if 'CONFIG_ESPTOOLPY_FLASHSIZE="32MB"' not in sdk or "# CONFIG_SPI_FLASH_SIZE_OVERRIDE is not set" not in sdk:
        raise ValueError("Flash size/override configuration differs from the accepted E1.40 build")
    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    if sum(Path(item["file"]).name == "dev_tools.cc" for item in commands) != 1:
        raise ValueError("dev_tools.cc is not represented exactly once in compile commands")
    changes = [name for name, digest in inputs.items()
               if baseline["source_sha256"].get(name) != digest]
    if changes != ["main/boards/esp_vocat/dev_tools.cc"]:
        raise ValueError("firmware source delta is broader than the E1.42 probe: " + repr(changes))
    protected_match = {
        name: artifacts[name]["sha256"] == baseline["artifacts"][name]["sha256"]
        for name in PROTECTED_ARTIFACTS
    }
    if not all(protected_match.values()):
        raise ValueError("E1.42 changed protected build outputs: " + repr(protected_match))
    app_bytes = artifacts["xiaozhi.bin"]["bytes"]
    if app_bytes > APP_PARTITION_BYTES - MINIMUM_APP_HEADROOM:
        raise ValueError("E1.42 App leaves less than 1 MiB headroom in the accepted partition")

    report = {
        "schema": "deskbot.expression.capacity_read_mmap.e1_42_build/v1",
        "edition": "E1.42",
        "result": "read_only_addressing_probe_build_passed_runtime_pending",
        "source_delta": changes,
        "source_manifest_sha256": sha(SOURCE / "SOURCE_MANIFEST.sha256"),
        "source_sha256": inputs,
        "components": components,
        "configured_flash_bytes": 32 * 1024 * 1024,
        "reference_readback": full_readback,
        "octal_cache32_enabled": True,
        "flash_size_override_enabled": False,
        "build_directory": build.relative_to(ROOT).as_posix(),
        "steps": steps,
        "artifacts": artifacts,
        "protected_artifacts_match_e1_41": protected_match,
        "app_partition_bytes": APP_PARTITION_BYTES,
        "app_headroom_bytes": APP_PARTITION_BYTES - app_bytes,
        "probe": {
            "tool": "self.probe_flash_addressing",
            "boundary": 16 * 1024 * 1024,
            "sample_bytes": 32,
            "reads_cross_boundary": True,
            "cpu_mmap_cross_boundary": True,
            "out_of_range_read_rejection_test": True,
            "mmap_handles_released": True,
            "writes_flash": False,
            "queries_mcp_visibility_verified": False,
            "runtime_probe_verified": False,
        },
        "deployment_eligible": False,
        "flash_authorized": False,
        "safety": {"hardware_accessed": False, "serial_accessed": False,
                    "device_written": False, "nvs_changed": False, "flash_executed": False},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("E1.42 read-only boundary probe build passed: " + output.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print("E1.42 Flash probe build blocked: " + str(error), file=sys.stderr)
        raise SystemExit(2)
