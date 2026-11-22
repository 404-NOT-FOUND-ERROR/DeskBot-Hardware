#!/usr/bin/env python3
"""Build and audit the bounded display-DMA repair from frozen E3c-3a."""
import argparse
import difflib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import TARGET_DIR, json_bytes, load_target
from e3c3b_display_dma import (BASE_REPORT, BOARD, DASH, DRIVER, FROZEN, FROZEN_BUILD,
                               PUBLIC, WORK, check_frozen, patch_board, patch_driver, sha)


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


base = module("e3c3b_base", "scripts/build-e3c3a-usb-path.py")
release = base.release
BUILD_REPORT = PUBLIC / "firmware-build-report.json"


def build_inputs():
    report = json.loads(BASE_REPORT.read_bytes())
    frozen = dict(report["source_sha256"])
    if frozen.get(BOARD) != DASH[BOARD]:
        raise ValueError("E3c-3a board source is not the pinned input")
    return report, frozen


def prepare_project(frozen_manifest):
    project = WORK / "p"
    base.BASE.HELPER.private_directory(project)
    for name in frozen_manifest:
        source = FROZEN / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("expected regular frozen source: " + name)
        destination = project / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    lock_raw = (FROZEN / "dependencies.lock").read_bytes()
    old_path = str(FROZEN / "components/espressif__touch_sensor_fsm").encode("utf-8")
    if lock_raw.count(old_path) != 1:
        raise ValueError("frozen local dependency path differs")
    lock_raw = lock_raw.replace(old_path, (project / "components/espressif__touch_sensor_fsm").as_posix().encode("utf-8"))
    (project / "dependencies.lock").write_bytes(lock_raw)
    if not (project / "managed_components").is_dir():
        shutil.copytree(FROZEN / "managed_components", project / "managed_components")
    locked = yaml.safe_load(lock_raw)
    components = base.BASE.HELPER.HELPER.validate_components(project, locked)
    (project / "partitions/deskbot-upper-assets-8m.csv").write_bytes((TARGET_DIR / "partitions.csv").read_bytes())
    base.verify_sources(project, frozen_manifest)

    driver = project / DRIVER
    driver.write_bytes(patch_driver(driver.read_bytes()))
    board = project / BOARD
    board.write_bytes(patch_board(board.read_bytes()))
    candidate_manifest = dict(frozen_manifest)
    candidate_manifest[BOARD] = sha(board.read_bytes())
    candidate_manifest[DRIVER] = sha(driver.read_bytes())
    base.verify_sources(project, candidate_manifest)
    return project, components, candidate_manifest


def inspect(build, manifest, env):
    actual = json.loads((build / "config/sdkconfig.json").read_bytes())
    reference = json.loads((FROZEN_BUILD / "config/sdkconfig.json").read_bytes())
    if actual != reference:
        raise ValueError("effective App configuration changed")
    boot_config = json.loads((build / "bootloader/config/sdkconfig.json").read_bytes())
    if boot_config != json.loads((FROZEN_BUILD / "bootloader/config/sdkconfig.json").read_bytes()):
        raise ValueError("bootloader configuration changed")
    release.effective_octal_config(actual, 32)
    release.effective_octal_config(boot_config, 32)
    artifacts = {name: release.record(build / name) for name in release.FILES}
    protected_names = ("bootloader/bootloader.bin", "partition_table/partition-table.bin",
                       "srmodels/srmodels.bin", "ota_data_initial.bin")
    protected = {name: artifacts[name] == release.record(FROZEN_BUILD / name) for name in protected_names}
    if not all(protected.values()):
        raise ValueError("protected build output differs")
    target = load_target()
    headroom = 0x5DC000 - artifacts["xiaozhi.bin"]["bytes"]
    if (headroom < target["minimum_app_headroom_bytes"]
            or artifacts["partition_table/partition-table.bin"]["sha256"] != target["partition_table_sha256"]):
        raise ValueError("App budget or partition table differs")
    if json.loads((build / "flasher_args.json").read_bytes()) != json.loads((FROZEN_BUILD / "flasher_args.json").read_bytes()):
        raise ValueError("generated flash map changed")
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    size = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    if not nm or not size:
        raise ValueError("pinned Xtensa inspection tools unavailable")
    symbols = subprocess.check_output([nm, "-S", "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    image = (build / "xiaozhi.bin").read_bytes()
    if (release.image_facts(image)["configured_flash_bytes"] != target["flash_bytes"]
            or "deskbot_studio_runtime::Read()" not in symbols
            or b"deskbot.studio_state/v1" not in image
            or any(feature in symbols for feature in ("deskbot_studio_expression::", "deskbot_studio_catalog::",
                                                        "RunContinuousMappingOnce", "RunUpperPatternOnce"))):
        raise ValueError("App features differ from E3c-3a query-enabled baseline")
    sections = subprocess.check_output([size, "-A", str(build / "xiaozhi.elf")], env=env, text=True)
    internal = sum(int(line.split()[1]) for line in sections.splitlines()
                   if line.split() and line.split()[0] in (".dram0.data", ".dram0.bss"))
    baseline_internal = sum(int(line.split()[1]) for line in subprocess.check_output(
        [size, "-A", str(FROZEN_BUILD / "xiaozhi.elf")], env=env, text=True).splitlines()
        if line.split() and line.split()[0] in (".dram0.data", ".dram0.bss"))
    if not (0 < internal < 60000) or internal != baseline_internal:
        raise ValueError("internal static data differs from the frozen App")
    base.verify_sources(WORK / "p", manifest)
    if sha((WORK / "p" / DRIVER).read_bytes()) != sha(patch_driver(check_frozen_driver())):
        raise ValueError("ST77916 patch changed")
    if sha((WORK / "p" / BOARD).read_bytes()) != sha(patch_board(check_frozen_board())):
        raise ValueError("board DMA patch changed")
    return {"query_enabled": True, "build_directory": build.relative_to(ROOT).as_posix(),
            "artifacts": artifacts, "config_equal_reference": True, "protected_equal": protected,
            "app_headroom_bytes": headroom, "internal_static_data_bytes": internal,
            "internal_static_data_delta_bytes": internal - baseline_internal,
            "app_elf_sha256": image[176:208].hex()}


def check_frozen_driver():
    return check_frozen(FROZEN / DRIVER, DASH[DRIVER])


def check_frozen_board():
    return check_frozen(FROZEN / BOARD, DASH[BOARD])


def build():
    report, frozen_manifest = build_inputs()
    env, python, idf = base.BASE.environment()
    base.path_audit.audit(idf)
    if (WORK.exists() and any(WORK.iterdir())) or BUILD_REPORT.exists():
        raise FileExistsError("candidate artifacts already exist; inspect them instead of rebuilding in place")
    project, components, manifest = prepare_project(frozen_manifest)
    build_dir = WORK / "on"
    base.BASE.HELPER.private_directory(build_dir)
    sdkconfig = build_dir / "sdkconfig"
    shutil.copy2(FROZEN_BUILD / "sdkconfig", sdkconfig)
    command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build_dir),
               "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
               "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
    steps = []
    for action in ("reconfigure", "build"):
        log = WORK / f"query-on-{action}.log"
        if action == "build":
            base.BASE.HELPER.HELPER.quote_image_effects_link_path(build_dir, project)
        print(f"E3c-3b query-on: {action}", flush=True)
        base.BASE.run(command + [action], project, env, log)
        steps.append({"action": action, "log": log.relative_to(ROOT).as_posix(),
                      "sha256": release.record(log)["sha256"], "returncode": 0})
    row = inspect(build_dir, manifest, env)
    patch = ""
    for name in (DRIVER, BOARD):
        before = check_frozen_driver() if name == DRIVER else check_frozen_board()
        after = (project / name).read_bytes()
        patch += "".join(difflib.unified_diff(before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True),
                                              fromfile="E3c-3a/" + name, tofile="E3c-3b/" + name))
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "source.patch").write_text(patch, encoding="utf-8", newline="\n")
    build_report = {
        "schema": "deskbot.display_dma_repair_build/v1", "edition": "E3c-3b",
        "result": "query_enabled_build_verified", "reference": "E3c-3a query-enabled",
        "reference_build_sha256": sha(BASE_REPORT.read_bytes()),
        "source_sha256": manifest, "source_delta": [DRIVER, BOARD], "components": components,
        "target": load_target(), "build": row, "steps": steps,
        "dma": {"max_transfer_bytes": 4092, "queue_depth": 2, "psram_direct": False},
        "hardware_accessed": False, "device_written": False, "hardware_acceptance": "pending",
    }
    BUILD_REPORT.write_bytes(json_bytes(build_report))
    print(json.dumps({"result": build_report["result"], "app": row["artifacts"]["xiaozhi.bin"]}, sort_keys=True))


def check():
    report, frozen_manifest = build_inputs()
    saved = json.loads(BUILD_REPORT.read_bytes())
    if saved["reference_build_sha256"] != sha(BASE_REPORT.read_bytes()):
        raise ValueError("pinned E3c-3a build report changed")
    expected_manifest = dict(frozen_manifest)
    expected_manifest[BOARD] = sha(patch_board(check_frozen_board()))
    expected_manifest[DRIVER] = sha(patch_driver(check_frozen_driver()))
    if saved["source_sha256"] != expected_manifest or saved["source_delta"] != [DRIVER, BOARD]:
        raise ValueError("saved source delta differs")
    if saved["dma"] != {"max_transfer_bytes": 4092, "queue_depth": 2, "psram_direct": False}:
        raise ValueError("DMA settings differ")
    env, _, _ = base.BASE.environment()
    if inspect(ROOT / saved["build"]["build_directory"], expected_manifest, env) != saved["build"]:
        raise ValueError("candidate build differs from report")
    for step in saved["steps"]:
        if release.record(ROOT / step["log"])["sha256"] != step["sha256"]:
            raise ValueError("build log changed")
    print("E3c-3b frozen inputs, single App build and protected outputs verified; no hardware access")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
    else:
        build()
