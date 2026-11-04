#!/usr/bin/env python3
"""Build the N32R16 upper-assets release target in an isolated project."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import TARGET_DIR, load_target, json_bytes, sha
from protocol.storage_capacity import image_facts
from protocol.storage_octal import effective_octal_config

SPEC = importlib.util.spec_from_file_location("studio_release_build_base", ROOT / "scripts/build-expression-e1-42-flash-probe.py")
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
REFERENCE = ROOT / "tmp/e142-flash-probe-20261031/o-32m"
PUBLIC = ROOT / "artifacts/deskbot-studio-e2b-20261104"
FILES = ("xiaozhi.bin", "bootloader/bootloader.bin", "partition_table/partition-table.bin",
         "srmodels/srmodels.bin", "ota_data_initial.bin", "sdkconfig", "flasher_args.json",
         "config/sdkconfig.json", "bootloader/config/sdkconfig.json")


def record(path):
    return {"bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}


def apply_overlay(text, overlay):
    for line in overlay.splitlines():
        match = re.match(r"(?:# )?(CONFIG_\w+)(?:=| is not set)", line)
        if match:
            text = re.sub(r"(?m)^(?:# )?" + match[1] + r"(?:=.*| is not set)\r?\n?", "", text)
            text += "\n" + line + "\n"
    return text


def inspect(build, project, inputs, components, steps, env):
    target = load_target()
    actual = json.loads((build / "config/sdkconfig.json").read_bytes())
    boot = json.loads((build / "bootloader/config/sdkconfig.json").read_bytes())
    effective_octal_config(actual, 32)
    effective_octal_config(boot, 32)
    expected = {"PARTITION_TABLE_CUSTOM_FILENAME": "partitions/deskbot-upper-assets-8m.csv",
                "FLASH_NONE_ASSETS": True, "FLASH_EXPRESSION_ASSETS": False,
                "DESKBOT_EXPRESSION_OBSERVER_ENABLE": True, "MMAP_FILE_NAME_LENGTH": 32,
                "MMU_PAGE_SIZE": 65536, "BOARD_TYPE_ESP_VOCAT": True, "USE_EMOTE_MESSAGE_STYLE": True}
    if any(actual.get(key, False) != value for key, value in expected.items()):
        raise ValueError("effective release configuration differs")
    if any(actual.get(flag, False) for flag in ("DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT",
            "DESKBOT_UPPER_PATTERN_BOOT_EXPORT", "DESKBOT_FLASH_PROBE_BOOT_EXPORT")):
        raise ValueError("temporary diagnostic task enabled")
    artifacts = {name: record(build / name) for name in FILES}
    if artifacts["partition_table/partition-table.bin"]["sha256"] != target["partition_table_sha256"]:
        raise ValueError("generated partition table differs from the installed upper-assets table")
    headroom = 0x5DC000 - artifacts["xiaozhi.bin"]["bytes"]
    if headroom < target["minimum_app_headroom_bytes"]:
        raise ValueError("App headroom is below 1 MiB")
    protected = {name: record(REFERENCE / name) == artifacts[name] for name in
                 ("bootloader/bootloader.bin", "srmodels/srmodels.bin", "ota_data_initial.bin")}
    if not all(protected.values()):
        raise ValueError("bootloader, model or OTA reference changed: " + repr(protected))
    flash = json.loads((build / "flasher_args.json").read_bytes())
    flash_files = {int(address, 16): name for address, name in flash["flash_files"].items()}
    if any(0x5EC000 <= address < 0xBC8000 or address >= 0x1000000 for address in flash_files):
        raise ValueError("App build unexpectedly added expression resources to flash arguments")
    for name in ("xiaozhi.bin", "bootloader/bootloader.bin"):
        if image_facts((build / name).read_bytes())["configured_flash_bytes"] != target["flash_bytes"]:
            raise ValueError("binary header Flash capacity differs")
    BASE.HELPER.verify_source(project, inputs, inputs[BASE.HELPER.PARTITION])
    if BASE.HELPER.HELPER.inventory() != inputs:
        raise ValueError("firmware source changed during release build")
    locked = yaml.safe_load((BASE.SOURCE / "dependencies.lock").read_bytes())
    staged_lock = yaml.safe_load((project / "dependencies.lock").read_bytes())
    if BASE.HELPER.HELPER.normalized_lock(locked) != BASE.HELPER.HELPER.normalized_lock(staged_lock):
        raise ValueError("staged dependency lock changed")
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    symbols = subprocess.check_output([nm, "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    if any(name in symbols for name in ("RunContinuousMappingOnce", "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce")):
        raise ValueError("temporary task present in release App")
    old = json.loads((REFERENCE / "config/sdkconfig.json").read_bytes())
    delta = {key: {"before": old.get(key), "after": actual.get(key)}
             for key in sorted(old.keys() | actual.keys()) if old.get(key) != actual.get(key)}
    allowed = {"PARTITION_TABLE_CUSTOM_FILENAME", "PARTITION_TABLE_FILENAME",
               "FLASH_EXPRESSION_ASSETS", "FLASH_NONE_ASSETS"}
    disabled_added = {key for key in ("DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT",
                      "DESKBOT_UPPER_PATTERN_BOOT_EXPORT", "DESKBOT_FLASH_PROBE_BOOT_EXPORT")
                      if key not in old and actual.get(key) is False}
    if set(delta) - allowed - disabled_added:
        raise ValueError("unexpected release config change: " + repr(delta))
    return {"schema": "deskbot.studio_firmware_build/v1", "edition": "E2b",
            "result": "isolated_release_build_verified", "target": target,
            "source_sha256": inputs, "components": components,
            "release_config_sha256": {name: sha((TARGET_DIR / name).read_bytes())
                                      for name in ("target.json", "partitions.csv", "sdkconfig.defaults")},
            "build_directory": build.relative_to(ROOT).as_posix(), "artifacts": artifacts,
            "app_headroom_bytes": headroom, "effective_config_delta": delta,
            "inactive_diagnostic_options_added": sorted(disabled_added),
            "protected_reference_matches": protected, "expression_assets_in_app_flash_args": False,
            "temporary_tasks_present": False, "steps": steps,
            "source_unchanged": True, "hardware_accessed": False, "device_written": False,
            "hardware_acceptance": "pending"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    report_path = PUBLIC / "firmware-build-report.json"
    work = ROOT / "tmp/e2b-release-20261104"
    build = work / "o"
    if args.check:
        report = json.loads(report_path.read_bytes())
        if report["source_sha256"] != BASE.HELPER.HELPER.inventory():
            raise ValueError("source differs from saved build")
        for name, expected in report["artifacts"].items():
            if record(ROOT / report["build_directory"] / name) != expected:
                raise ValueError("saved build differs: " + name)
        for step in report["steps"]:
            if sha((ROOT / step["log"]).read_bytes()) != step["sha256"]:
                raise ValueError("saved build log differs")
        if report["target"] != load_target():
            raise ValueError("release target differs")
        print("E2b saved release build verified")
        return
    if report_path.exists() and not args.resume:
        raise ValueError("saved release build exists; use --check or --resume")
    inputs = BASE.HELPER.HELPER.inventory()
    locked = yaml.safe_load((BASE.SOURCE / "dependencies.lock").read_bytes())
    project, components = BASE.HELPER.stage(work, BASE.DONOR, inputs, locked)
    target_table = project / "partitions/deskbot-upper-assets-8m.csv"
    target_table.write_bytes((TARGET_DIR / "partitions.csv").read_bytes())
    BASE.HELPER.private_directory(build)
    sdkconfig = build / "sdkconfig"
    overlay = (TARGET_DIR / "sdkconfig.defaults").read_text(encoding="utf-8")
    sdkconfig.write_text(apply_overlay((REFERENCE / "sdkconfig").read_text(encoding="utf-8"), overlay), encoding="utf-8")
    env, python, idf = BASE.environment()
    command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
               "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
               "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
    steps = []
    for action in ("reconfigure", "build"):
        log = work / f"release-{action}.log"
        if log.exists():
            count = 1
            while (work / f"release-{action}-attempt-{count}.log").exists():
                count += 1
            log.rename(work / f"release-{action}-attempt-{count}.log")
        if action == "build":
            BASE.HELPER.HELPER.quote_image_effects_link_path(build, project)
        print("E2b release: " + action, flush=True)
        BASE.run(command + [action], project, env, log)
        steps.append({"action": action, "returncode": 0, "log": log.relative_to(ROOT).as_posix(),
                      "sha256": sha(log.read_bytes())})
    report = inspect(build, project, inputs, components, steps, env)
    report_path.write_bytes(json_bytes(report))
    print(json.dumps({"result": report["result"], "app_bytes": report["artifacts"]["xiaozhi.bin"]["bytes"],
                      "app_headroom_bytes": report["app_headroom_bytes"]}))


if __name__ == "__main__":
    main()
