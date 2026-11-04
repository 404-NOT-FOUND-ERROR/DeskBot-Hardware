#!/usr/bin/env python3
"""Audit old/8-MiB layouts in a private body copy; build only, never use a device."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_budget import valid_build_checkpoint

SOURCE = ROOT / "firmware/body"
PARTITION = "partitions/v2/16m.csv"
OLD_TABLE = "f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c"
NEW_TABLE = "0a55d50b8b2fb845c205c475e1cd19aad6801b81320a5ebb4f9b3f31269945fa"
FILES = ("xiaozhi.bin", "xiaozhi.elf", "bootloader/bootloader.bin",
         "partition_table/partition-table.bin", "expression_assets.bin",
         "srmodels/srmodels.bin", "ota_data_initial.bin", "sdkconfig", "flasher_args.json")


def load_helper():
    spec = importlib.util.spec_from_file_location("storage_build_helper", ROOT / "scripts/build-expression-e1-26-body.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_helper()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record(path: Path) -> dict:
    return {"bytes": path.stat().st_size, "sha256": sha(path)}


def private_directory(path: Path) -> None:
    path.resolve().relative_to((ROOT / "tmp").resolve())
    if path.exists() and path.is_symlink():
        raise ValueError("private build path must not be a symlink")
    if path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("private build path must not be a reparse point")
    path.mkdir(parents=True, exist_ok=True)


def diff_config(left: Path, right: Path) -> dict:
    a = HELPER.config_map(left.read_text(encoding="utf-8"))
    b = HELPER.config_map(right.read_text(encoding="utf-8"))
    return {key: {"old": a.get(key), "candidate": b.get(key)}
            for key in sorted(a.keys() | b.keys()) if a.get(key) != b.get(key)}


def stage(work: Path, dependency_project: Path, inputs: dict, locked: dict) -> tuple[Path, dict]:
    project = work / "p"
    private_directory(project)
    for name in inputs:
        destination = project / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, destination)
    lock_text = (SOURCE / "dependencies.lock").read_text(encoding="utf-8")
    local = locked["dependencies"]["espressif/touch_sensor_fsm"]["source"]["path"]
    lock_text, count = re.subn(r"(?m)^      path: " + re.escape(local) + r"(?=\r?$)",
                              "      path: " + (project / "components/espressif__touch_sensor_fsm").as_posix(),
                              lock_text)
    if count != 1:
        raise ValueError("expected one local touch component path")
    (project / "dependencies.lock").write_text(lock_text, encoding="utf-8")
    dependency_lock = yaml.safe_load((dependency_project / "dependencies.lock").read_bytes())
    if HELPER.normalized_lock(dependency_lock) != HELPER.normalized_lock(locked):
        raise ValueError("dependency donor does not match pinned lock")
    print("Checking cached dependency contents against pinned lock", flush=True)
    validated = HELPER.validate_components(dependency_project, locked)
    managed = project / "managed_components"
    private_directory(managed)
    for name, item in locked["dependencies"].items():
        if "component_hash" not in item:
            continue
        relative = name.replace("/", "__")
        destination = managed / relative
        if not destination.exists():
            shutil.copytree(dependency_project / "managed_components" / relative, destination)
    HELPER.validate_components(project, locked)
    return project, {"count": validated["count"], "actual_contents_match_lock": True,
                     "local_touch_path_is_only_lock_difference": True}


def environment() -> tuple[dict, Path, Path]:
    idf = Path(os.environ.get("IDF_PATH", r"D:\Espressif\frameworks\esp-idf-v5.5.5"))
    python = Path(os.environ.get("IDF_PYTHON_ENV_PATH", r"D:\Espressif\python_env\idf5.5_py3.11_env")) / "Scripts/python.exe"
    bins = [r"D:\Espressif\tools\cmake\3.30.2\bin", r"D:\Espressif\tools\ninja\1.12.1",
            r"D:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin"]
    env = os.environ.copy()
    env.update({"IDF_PATH": str(idf), "IDF_TOOLS_PATH": r"D:\Espressif",
                "IDF_PYTHON_ENV_PATH": str(python.parent.parent), "ESP_IDF_VERSION": "5.5.5",
                "IDF_TARGET": "esp32s3", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "IDF_COMPONENT_CHECK_NEW_VERSION": "0", "CMAKE_NINJA_FORCE_RESPONSE_FILE": "1"})
    env["PATH"] = os.pathsep.join(bins + [str(python.parent), env.get("PATH", "")])
    return env, python, idf


def verify_source(project: Path, inputs: dict, table_sha: str) -> None:
    for name, expected in inputs.items():
        if name == "dependencies.lock":
            continue
        actual = sha(project / name)
        if actual != (table_sha if name == PARTITION else expected):
            raise ValueError("isolated source unexpectedly changed: " + name)


def build_layout(project: Path, work: Path, enabled: bool, layout: str,
                 table: bytes, inputs: dict, locked: dict, env: dict,
                 python: Path, idf: Path) -> dict:
    label = "on" if enabled else "off"
    # Compare layouts at the same source/build paths.  A fresh reconfigure
    # and the exact generated partition-table digest are required each time.
    build = work / ("o-" + label)
    private_directory(build)
    (project / PARTITION).write_bytes(table)
    verify_source(project, inputs, hashlib.sha256(table).hexdigest())
    sdkconfig = build / "sdkconfig"
    if layout == "old":
        snapshot = (SOURCE / "sdkconfig.defaults.o2f2").read_text(encoding="utf-8")
        for key in ("DESKBOT_EXPRESSION_OBSERVER_ENABLE", "APP_REPRODUCIBLE_BUILD"):
            snapshot = re.sub(r"(?m)^(?:# )?CONFIG_" + key + r".*\n?", "", snapshot)
        snapshot += "\nCONFIG_APP_REPRODUCIBLE_BUILD=y\n"
        snapshot += ("CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE=y\n" if enabled else
                     "# CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE is not set\n")
        sdkconfig.write_text(snapshot, encoding="utf-8")
    common = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
              "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
              "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
    prefix = f"{label}-{layout}"
    steps = [HELPER.run(common + ["reconfigure"], project, env, work / (prefix + "-configure.log"))]
    if HELPER.normalized_lock(yaml.safe_load((project / "dependencies.lock").read_bytes())) != HELPER.normalized_lock(locked):
        raise ValueError("component manager changed pinned lock")
    quoted = HELPER.quote_image_effects_link_path(build, project)
    steps.append(HELPER.run(common + ["build"], project, env, work / (prefix + "-build.log")))
    config = json.loads((build / "config/sdkconfig.json").read_text())
    for key, expected in {"DESKBOT_EXPRESSION_OBSERVER_ENABLE": enabled, "APP_REPRODUCIBLE_BUILD": True,
                          "USE_EMOTE_MESSAGE_STYLE": True, "BOARD_TYPE_ESP_VOCAT": True,
                          "MMAP_FILE_NAME_LENGTH": 32, "MMU_PAGE_SIZE": 65536,
                          "ESPTOOLPY_FLASHSIZE": "16MB"}.items():
        if config.get(key) != expected:
            raise ValueError(f"unexpected effective config {key}: {config.get(key)!r}")
    archive = work / "outputs" / prefix
    private_directory(archive)
    for name in FILES:
        destination = archive / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build / name, destination)
    expected_table = OLD_TABLE if layout == "old" else NEW_TABLE
    if sha(archive / "partition_table/partition-table.bin") != expected_table:
        raise ValueError("built partition table does not match reviewed proposal")
    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    observer_members = sum(Path(command["file"]).name == "expression_runtime_observer.cc" for command in commands)
    if bool(observer_members) != enabled:
        raise ValueError("wrong observer source membership")
    verify_source(project, inputs, hashlib.sha256(table).hexdigest())
    result = {"observer_enabled": enabled, "layout": layout,
              "steps": [{"returncode": step["returncode"], "log": "tmp/" + work.name + "/" + Path(step["log"]).name,
                         "log_sha256": step["log_sha256"]} for step in steps],
              "archive": archive.relative_to(ROOT).as_posix(),
              "artifacts": {name: record(archive / name) for name in FILES},
              "observer_source_members": observer_members, "generated_link_quoting": quoted}
    print(f"{prefix} build passed; App={result['artifacts']['xiaozhi.bin']['bytes']} bytes", flush=True)
    return result


def cached_result_is_valid(result: dict, work: Path, enabled: bool, layout: str) -> bool:
    """Accept only a complete, hash-matching result from this exact audit."""
    return valid_build_checkpoint(result, ROOT, work, enabled, layout, FILES,
                                  OLD_TABLE if layout == "old" else NEW_TABLE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependency-project", type=Path, required=True,
                        help="existing local body project containing lock-matching managed components")
    parser.add_argument("--work-dir", default="tmp/e135a-storage")
    parser.add_argument("--output", default="artifacts/expression-storage-preflight-e1-35a-20261029/build-report.json")
    parser.add_argument("--resume", action="store_true",
                        help="reuse complete hash-verified build entries from progress.json")
    args = parser.parse_args()
    work = ROOT / args.work_dir
    private_directory(work)
    output = ROOT / args.output
    output.resolve().relative_to((ROOT / "artifacts").resolve())
    if output.suffix != ".json":
        raise ValueError("output must be an artifact JSON")
    inputs = HELPER.inventory()
    previous = json.loads((ROOT / "tmp/e1-26-full-body-report.json").read_text(encoding="utf-8"))
    if inputs != previous["source_sha256"]:
        raise ValueError("formal source differs from accepted E1.26 build inputs")
    locked = yaml.safe_load((SOURCE / "dependencies.lock").read_bytes())
    old_table = (SOURCE / PARTITION).read_bytes()
    proposal = ROOT / "artifacts/expression-storage-strategy-e1-35-20261029/assets-8m-proposal.csv"
    new_table = proposal.read_bytes()
    project, components = stage(work, args.dependency_project.resolve(), inputs, locked)
    env, python, idf = environment()
    results = []
    progress_path = work / "progress.json"
    if args.resume and progress_path.is_file():
        prior_progress = json.loads(progress_path.read_text(encoding="utf-8"))
        order = [(False, "old"), (False, "8m"), (True, "old"), (True, "8m")]
        candidates = prior_progress.get("builds", [])
        if len(candidates) > len(order):
            raise ValueError("unexpected progress entry count")
        for candidate, (enabled, layout) in zip(candidates, order):
            if not cached_result_is_valid(candidate, work, enabled, layout):
                raise ValueError("resume checkpoint failed digest/path/order validation")
            results.append(candidate)
        if results:
            print(f"Resuming {len(results)} complete build(s) from {progress_path}", flush=True)
    for enabled in (False, True):
        for layout, table in (("old", old_table), ("8m", new_table)):
            cached = next((row for row in results
                           if row["observer_enabled"] is enabled and row["layout"] == layout), None)
            if cached is not None:
                print(f"Skipping cached {('on' if enabled else 'off')}-{layout}", flush=True)
                continue
            result = build_layout(project, work, enabled, layout, table, inputs, locked, env, python, idf)
            results.append(result)
            write_json(progress_path, {"builds": results, "hardware_accessed": False})
    comparisons = []
    for enabled in (False, True):
        pair = [row for row in results if row["observer_enabled"] is enabled]
        old, new = pair
        same_names = ("xiaozhi.bin", "xiaozhi.elf", "bootloader/bootloader.bin", "expression_assets.bin",
                      "srmodels/srmodels.bin", "ota_data_initial.bin", "sdkconfig")
        same = {name: old["artifacts"][name] == new["artifacts"][name] for name in same_names}
        config_changes = diff_config(ROOT / old["archive"] / "sdkconfig", ROOT / new["archive"] / "sdkconfig")
        if not all(same.values()) or config_changes:
            raise ValueError("layout-only build changed non-layout artifacts: " + json.dumps(same))
        prior = next(row for row in previous["configurations"] if row["enabled"] is enabled)
        comparisons.append({"observer_enabled": enabled, "unchanged_between_layouts": same,
                            "effective_config_changes": config_changes,
                            "app_matches_archived_e126": old["artifacts"]["xiaozhi.bin"] == prior["artifacts"]["xiaozhi.bin"],
                            "app_partition_free_bytes": 6000 * 1024 - new["artifacts"]["xiaozhi.bin"]["bytes"]})
    if HELPER.inventory() != inputs:
        raise ValueError("formal source changed during audit")
    HELPER.validate_components(project, locked)
    report = {"schema": "deskbot.expression.storage-build-preflight.v1", "edition": "E1.35a",
              "result": "four_isolated_builds_passed_layout_only_change",
              "source_manifest_sha256": sha(SOURCE / "SOURCE_MANIFEST.sha256"),
              "formal_inputs_match_accepted_e126": True, "formal_inputs_unchanged": True,
              "staged_changes": ["local touch component path in lock", "reviewed partition CSV in private copy only"],
              "proposal_csv_sha256": sha(proposal), "components": components,
              "builds": results, "comparisons": comparisons,
              "build_pass": True, "deployment_eligible": False, "flash_authorized": False,
              "safety": {"hardware_accessed": False, "serial_accessed": False, "device_written": False,
                          "nvs_changed": False, "flash_executed": False, "formal_partition_changed": False},
              "limitations": ["Build outputs use original source assets, not the accepted E1.18 deployment payload.",
                              "No runtime mmap, live backup, partition migration or physical regression is proven."]}
    write_json(output, report)
    print(report["result"] + ": " + output.relative_to(ROOT).as_posix(), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("E1.35a storage build blocked: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
