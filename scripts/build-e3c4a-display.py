#!/usr/bin/env python3
"""Build display-query off/on from retained E3c-4 and accepted E3c-3a inputs."""
import argparse
import difflib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import yaml

from e3c4a_display import (ACCEPTED, DISPLAY_FILES, HISTORY, OPTION, PUBLIC, ROOT,
                           WORK, candidate_sources, pinned_reports, record,
                           verify_sources)

sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import TARGET_DIR, json_bytes, load_target

spec = importlib.util.spec_from_file_location("e3c4a_build_base", ROOT / "scripts/build-e3c3a-usb-path.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
BASE, release = previous.BASE, previous.release


def inspect(build, enabled, manifest, env):
    reference = ACCEPTED / "on"
    config = json.loads((build / "config/sdkconfig.json").read_bytes())
    old_config = json.loads((reference / "config/sdkconfig.json").read_bytes())
    delta = {key for key in config.keys() | old_config.keys() if config.get(key) != old_config.get(key)}
    if delta != {OPTION} or config.get(OPTION) is not enabled:
        raise ValueError("configuration differs outside display query")
    boot_config = json.loads((build / "bootloader/config/sdkconfig.json").read_bytes())
    if boot_config != json.loads((reference / "bootloader/config/sdkconfig.json").read_bytes()):
        raise ValueError("bootloader configuration differs")
    release.effective_octal_config(config, 32)
    release.effective_octal_config(boot_config, 32)
    artifacts = {name: record(build / name) for name in release.FILES}
    protected = {name: record(reference / name) == artifacts[name] for name in (
        "bootloader/bootloader.bin", "partition_table/partition-table.bin",
        "srmodels/srmodels.bin", "ota_data_initial.bin")}
    if not all(protected.values()):
        raise ValueError("protected build outputs changed")
    target = load_target()
    headroom = 0x5DC000 - artifacts["xiaozhi.bin"]["bytes"]
    if (headroom < target["minimum_app_headroom_bytes"] or
            artifacts["partition_table/partition-table.bin"]["sha256"] != target["partition_table_sha256"]):
        raise ValueError("App budget or partition layout differs")
    if json.loads((build / "flasher_args.json").read_bytes()) != json.loads((reference / "flasher_args.json").read_bytes()):
        raise ValueError("generated flash map changed")
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    size = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    if not nm or not size:
        raise ValueError("pinned Xtensa inspection tools missing")
    symbols = subprocess.check_output([nm, "-S", "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    image = (build / "xiaozhi.bin").read_bytes()
    if (release.image_facts(image)["configured_flash_bytes"] != target["flash_bytes"] or
            ("deskbot_studio_runtime::ReadDisplay()" in symbols) != enabled or
            (b"deskbot.studio_display/v1" in image) != enabled or b"USB input ready: %s" not in image):
        raise ValueError("display or USB feature markers differ")
    if "deskbot_studio_runtime::Read()" not in symbols or any(word in symbols for word in (
            "deskbot_studio_expression::", "deskbot_studio_catalog::", "RunContinuousMappingOnce",
            "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce")):
        raise ValueError("unrelated feature linked")
    player_rows = [line for line in symbols.splitlines()
                   if line.endswith("deskbot_studio_runtime::(anonymous namespace)::player_store")]
    player_bytes = int(player_rows[0].split()[1], 16) if len(player_rows) == 1 else 0
    if player_bytes != (8 if enabled else 0):
        raise ValueError("player snapshot storage differs")
    def internal_bytes(folder):
        sections = subprocess.check_output([size, "-A", str(folder / "xiaozhi.elf")], env=env, text=True)
        return sum(int(line.split()[1]) for line in sections.splitlines()
                   if line.split() and line.split()[0] in (".dram0.data", ".dram0.bss"))
    internal = internal_bytes(build)
    internal_delta = internal - internal_bytes(reference)
    if not (0 < internal < 60000 and 0 <= internal_delta <= (128 if enabled else 0)):
        raise ValueError("internal static data budget differs")
    verify_sources(WORK / "p", manifest)
    return {"display_query_enabled": enabled, "build_directory": build.relative_to(ROOT).as_posix(),
            "artifacts": artifacts, "app_headroom_bytes": headroom, "player_store_bytes": player_bytes,
            "protected_equal": protected, "config_delta": [OPTION], "app_elf_sha256": image[176:208].hex(),
            "internal_static_data_bytes": internal, "internal_static_data_delta_bytes": internal_delta}


def compare_disabled():
    reference = (ACCEPTED / "on/xiaozhi.bin").read_bytes()
    disabled = (WORK / "off/xiaozhi.bin").read_bytes()
    before, after = release.image_facts(reference), release.image_facts(disabled)
    layout = lambda facts: [(row["load_address"], row["bytes"]) for row in facts["segments"]]
    if len(reference) != len(disabled) or layout(before) != layout(after):
        raise ValueError("disabled App layout differs from accepted query-on")
    first = after["segments"][0]["file_offset"]
    if struct.unpack_from("<I", disabled, first)[0] != 0xABCD5432:
        raise ValueError("App description header")
    elf = first + 144
    allowed = set(range(elf, elf + 32)) | {after["checksum_offset"]} | set(range(len(disabled)-32, len(disabled)))
    differences = [i for i, (a, b) in enumerate(zip(reference, disabled)) if a != b]
    if not set(differences) <= allowed:
        raise ValueError("disabled executable or data differs")
    return {"reference": "E3c-3a query-on", "bytes": len(disabled), "differing_bytes": len(differences),
            "executable_and_other_data_equal": True}


def prepare(source, manifest):
    project = WORK / "p"
    BASE.HELPER.private_directory(project)
    for name, raw in source.items():
        destination = project / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if name == "dependencies.lock":
            lock = yaml.safe_load(raw)
            old_path = lock["dependencies"]["espressif/touch_sensor_fsm"]["source"]["path"]
            raw = raw.replace(old_path.encode("utf-8"), (project / "components/espressif__touch_sensor_fsm").as_posix().encode("utf-8"))
        destination.write_bytes(raw)
    if not (project / "managed_components").exists():
        shutil.copytree(HISTORY / "p/managed_components", project / "managed_components")
    lock = yaml.safe_load((project / "dependencies.lock").read_bytes())
    components = BASE.HELPER.HELPER.validate_components(project, lock)
    (project / "partitions/deskbot-upper-assets-8m.csv").write_bytes((TARGET_DIR / "partitions.csv").read_bytes())
    verify_sources(project, manifest)
    return project, components


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify existing builds without building")
    parser.add_argument("--resume", action="store_true", help="continue the same retained candidate")
    args = parser.parse_args()
    reports = pinned_reports()
    source, manifest = candidate_sources(reports)
    for row in reports["accepted"]["builds"]:
        for name, item in row["artifacts"].items():
            if record(ROOT / row["build_directory"] / name) != item:
                raise ValueError("accepted build changed: " + name)
    env, python, idf = BASE.environment()
    path = PUBLIC / "firmware-build-report.json"
    if args.check:
        saved = json.loads(path.read_bytes())
        if saved["source_sha256"] != manifest or saved["target"] != load_target():
            raise ValueError("saved candidate differs")
        for row in saved["builds"]:
            if inspect(ROOT / row["build_directory"], row["display_query_enabled"], manifest, env) != row:
                raise ValueError("build verification changed")
        if saved["disabled_comparison"] != compare_disabled():
            raise ValueError("disabled comparison changed")
        for step in saved["steps"]:
            if record(ROOT / step["log"])["sha256"] != step["sha256"]:
                raise ValueError("saved build log changed")
        print("E3c-4a off/on builds and accepted baseline comparison verified")
        return
    if (path.exists() or WORK.exists()) and not args.resume:
        raise ValueError("candidate directory already exists; use --check or --resume")
    project, components = prepare(source, manifest)
    steps, rows = [], []
    for enabled in (False, True):
        label = "on" if enabled else "off"
        build = WORK / label
        BASE.HELPER.private_directory(build)
        sdkconfig = build / "sdkconfig"
        overlay = f"CONFIG_{OPTION}=y\n" if enabled else f"# CONFIG_{OPTION} is not set\n"
        sdkconfig.write_text(release.apply_overlay((ACCEPTED / "on/sdkconfig").read_text(encoding="utf-8"), overlay), encoding="utf-8")
        command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
                   "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
                   "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
        for action in ("reconfigure", "build"):
            log = WORK / f"{label}-{action}.log"
            if log.exists():
                index = 1
                while log.with_name(f"{label}-{action}-attempt-{index}.log").exists():
                    index += 1
                log.rename(log.with_name(f"{label}-{action}-attempt-{index}.log"))
            if action == "build":
                BASE.HELPER.HELPER.quote_image_effects_link_path(build, project)
            print(f"E3c-4a {label}: {action}", flush=True)
            BASE.run(command + [action], project, env, log)
            steps.append({"configuration": label, "action": action, "log": log.relative_to(ROOT).as_posix(),
                          "sha256": record(log)["sha256"], "returncode": 0})
        rows.append(inspect(build, enabled, manifest, env))
    comparison = compare_disabled()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    patch = ""
    for name in sorted(DISPLAY_FILES):
        before = (ACCEPTED / "p" / name).read_bytes().decode("utf-8").splitlines() if (ACCEPTED / "p" / name).exists() else []
        patch += "\n".join(difflib.unified_diff(before, source[name].decode("utf-8").splitlines(), lineterm="",
                                           fromfile="E3c-3a/"+name, tofile="E3c-4a/"+name))
        patch += "\n"
    (PUBLIC / "source.patch").write_text(patch, encoding="utf-8", newline="\n")
    report = {"schema": "deskbot.studio_display_e3c4a_build/v1", "edition": "E3c-4a", "result": "off_on_builds_verified",
              "reference": "E3c-3a query-on", "source_sha256": manifest, "source_delta": sorted(DISPLAY_FILES),
              "components": components, "target": load_target(), "builds": rows, "steps": steps,
              "disabled_comparison": comparison, "new_tasks": 0, "reads_per_iteration": 1, "yield_ms": 10,
              "player_store_max_bytes": 8, "hardware_accessed": False, "device_written": False,
              "hardware_acceptance": "pending", "expression_controls": False}
    path.write_bytes(json_bytes(report))
    print(json.dumps({"result": report["result"], "app_bytes": [row["artifacts"]["xiaozhi.bin"]["bytes"] for row in rows]}))


if __name__ == "__main__":
    main()
