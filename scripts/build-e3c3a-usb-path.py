#!/usr/bin/env python3
"""Build a single-source USB path correction from retained E3c-3 files."""
import argparse
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

from e3c3a_usb_path import SOURCE_NAME, candidate_bytes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import TARGET_DIR, json_bytes, load_target

WORK = ROOT / "tmp/e3c3a-usb-path-20261106"
FROZEN = ROOT / "tmp/e3c3-state-20261105"
PUBLIC = ROOT / "artifacts/ui-menu-regression-20261106"
REPORT = PUBLIC / "e3c3a-build.json"
PRIOR = ROOT / "artifacts/deskbot-studio-e3c3-20261105/firmware-build-report.json"


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


previous = module("e3c3a_previous", "scripts/build-deskbot-studio-e3c3-firmware.py")
path_audit = module("e3c3a_path_audit", "scripts/audit-e3c3-usb-path.py")
BASE, release = previous.BASE, previous.release


def prepare(prior):
    project = WORK / "p"
    BASE.HELPER.private_directory(project)
    inputs = dict(prior["source_sha256"])
    original = (FROZEN / "p" / SOURCE_NAME).read_bytes()
    revised = candidate_bytes(original)
    inputs[SOURCE_NAME] = hashlib.sha256(revised).hexdigest()
    for name in inputs:
        destination = project / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if name == SOURCE_NAME:
            destination.write_bytes(revised)
        else:
            shutil.copy2(FROZEN / "p" / name, destination)
    lock_raw = (FROZEN / "p/dependencies.lock").read_bytes()
    old_path = str(FROZEN / "p/components/espressif__touch_sensor_fsm").encode("utf-8")
    if lock_raw.count(old_path) != 1:
        raise ValueError("frozen local component path")
    lock_raw = lock_raw.replace(old_path, (project / "components/espressif__touch_sensor_fsm").as_posix().encode("utf-8"))
    (project / "dependencies.lock").write_bytes(lock_raw)
    locked = yaml.safe_load(lock_raw)
    if not (project / "managed_components").is_dir():
        shutil.copytree(FROZEN / "p/managed_components", project / "managed_components")
    print("E3c-3a: verifying private dependency contents", flush=True)
    components = BASE.HELPER.HELPER.validate_components(project, locked)
    (project / "partitions/deskbot-upper-assets-8m.csv").write_bytes((TARGET_DIR / "partitions.csv").read_bytes())
    verify_sources(project, inputs)
    patch = "".join(difflib.unified_diff(original.decode().splitlines(keepends=True),
                                         revised.decode().splitlines(keepends=True),
                                         fromfile="E3c-3/" + SOURCE_NAME, tofile="E3c-3a/" + SOURCE_NAME))
    (PUBLIC / "e3c3a-usb-path.patch").write_text(patch, encoding="utf-8", newline="\n")
    return project, inputs, components


def verify_sources(project, inputs):
    for name, digest in inputs.items():
        raw = (project / name).read_bytes()
        if name == "dependencies.lock":
            path = yaml.safe_load(raw)["dependencies"]["espressif/touch_sensor_fsm"]["source"]["path"]
            if Path(path).resolve() != (project / "components/espressif__touch_sensor_fsm").resolve():
                raise ValueError("local touch component must use private source")
            raw = raw.replace(path.encode(), b"components/espressif__touch_sensor_fsm")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("candidate source differs: " + name)


def inspect(build, reference, enabled, inputs, env):
    actual = json.loads((build / "config/sdkconfig.json").read_bytes())
    if actual != json.loads((reference / "config/sdkconfig.json").read_bytes()):
        raise ValueError("effective App configuration changed")
    boot = json.loads((build / "bootloader/config/sdkconfig.json").read_bytes())
    if boot != json.loads((reference / "bootloader/config/sdkconfig.json").read_bytes()):
        raise ValueError("effective bootloader configuration changed")
    release.effective_octal_config(actual, 32)
    release.effective_octal_config(boot, 32)
    artifacts = {name: release.record(build / name) for name in release.FILES}
    protected = {name: release.record(reference / name) == artifacts[name] for name in (
        "bootloader/bootloader.bin", "partition_table/partition-table.bin", "srmodels/srmodels.bin", "ota_data_initial.bin")}
    if not all(protected.values()):
        raise ValueError("protected output changed")
    target = load_target()
    if artifacts["partition_table/partition-table.bin"]["sha256"] != target["partition_table_sha256"]:
        raise ValueError("partition table target")
    headroom = 0x5DC000 - artifacts["xiaozhi.bin"]["bytes"]
    if headroom < target["minimum_app_headroom_bytes"]:
        raise ValueError("App headroom")
    flash = json.loads((build / "flasher_args.json").read_bytes())
    if flash != json.loads((reference / "flasher_args.json").read_bytes()):
        raise ValueError("flash map changed")
    image = (build / "xiaozhi.bin").read_bytes()
    if release.image_facts(image)["configured_flash_bytes"] != target["flash_bytes"]:
        raise ValueError("App Flash size")
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    size = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    if not nm or not size:
        raise ValueError("pinned Xtensa tools")
    symbols = subprocess.check_output([nm, "-S", "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    if ("deskbot_studio_runtime::Read()" in symbols) != enabled:
        raise ValueError("state reader feature")
    if (b"deskbot.studio_state/v1" in image) != enabled or b"USB input ready: %s" not in image:
        raise ValueError("state or USB path marker")
    if any(name in symbols for name in ("RunContinuousMappingOnce", "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce",
                                       "deskbot_studio_expression::", "deskbot_studio_catalog::")):
        raise ValueError("later feature present")
    sections = subprocess.check_output([size, "-A", str(build / "xiaozhi.elf")], env=env, text=True)
    internal = sum(int(line.split()[1]) for line in sections.splitlines()
                   if line.split() and line.split()[0] in (".dram0.data", ".dram0.bss"))
    if not 0 < internal < 60000:
        raise ValueError("internal static data unexpectedly large")
    verify_sources(WORK / "p", inputs)
    return {"query_enabled": enabled, "build_directory": build.relative_to(ROOT).as_posix(),
            "artifacts": artifacts, "config_equal_reference": True, "protected_equal": protected,
            "app_headroom_bytes": headroom, "internal_static_data_bytes": internal,
            "app_elf_sha256": image[176:208].hex()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    env, python, idf = BASE.environment()
    print("E3c-3a: verifying retained source and build evidence", flush=True)
    path_audit.audit(idf)
    prior = json.loads(PRIOR.read_bytes())
    if args.check:
        saved = json.loads(REPORT.read_bytes())
        expected_inputs = dict(prior["source_sha256"])
        expected_inputs[SOURCE_NAME] = hashlib.sha256(candidate_bytes((FROZEN / "p" / SOURCE_NAME).read_bytes())).hexdigest()
        if (saved["source_sha256"] != expected_inputs or saved["source_delta"] != [SOURCE_NAME]
                or saved["prior_report"] != release.record(PRIOR)):
            raise ValueError("candidate source or historical reference changed")
        verify_sources(WORK / "p", saved["source_sha256"])
        for row in saved["builds"]:
            reference = FROZEN / ("on" if row["query_enabled"] else "off")
            if inspect(ROOT / row["build_directory"], reference, row["query_enabled"], saved["source_sha256"], env) != row:
                raise ValueError("candidate build audit differs")
        for step in saved["steps"]:
            if release.record(ROOT / step["log"])["sha256"] != step["sha256"]:
                raise ValueError("candidate build log changed")
        print("E3c-3a isolated path correction builds checked; no hardware access")
        return
    if REPORT.exists() and not args.resume:
        raise ValueError("candidate report exists; use --check or --resume")
    project, inputs, components = prepare(prior)
    steps, rows = [], []
    for enabled in (False, True):
        label = "on" if enabled else "off"
        reference, build = FROZEN / label, WORK / label
        BASE.HELPER.private_directory(build)
        sdkconfig = build / "sdkconfig"
        sdkconfig.write_bytes((reference / "sdkconfig").read_bytes())
        command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
                   "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
                   "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
        for action in ("reconfigure", "build"):
            log = WORK / f"{label}-{action}.log"
            if log.exists():
                count = 1
                while log.with_name(f"{label}-{action}-attempt-{count}.log").exists():
                    count += 1
                log.rename(log.with_name(f"{label}-{action}-attempt-{count}.log"))
            if action == "build":
                BASE.HELPER.HELPER.quote_image_effects_link_path(build, project)
            print(f"E3c-3a {label}: {action}", flush=True)
            BASE.run(command + [action], project, env, log)
            steps.append({"configuration": label, "action": action, "log": log.relative_to(ROOT).as_posix(),
                          "sha256": release.record(log)["sha256"], "returncode": 0})
        rows.append(inspect(build, reference, enabled, inputs, env))
    report = {"schema": "deskbot.usb_path_build/v1", "edition": "E3c-3a", "result": "off_on_builds_verified",
              "reference": "retained_E3c-3", "source_sha256": inputs, "source_delta": [SOURCE_NAME],
              "prior_report": release.record(PRIOR), "components": components, "target": load_target(),
              "builds": rows, "steps": steps, "task_stack_bytes": 6144, "task_priority": 1,
              "reads_per_iteration": 1, "yield_ms": 10, "new_tasks": 0,
              "hardware_accessed": False, "device_written": False, "hardware_acceptance": "pending"}
    REPORT.write_bytes(json_bytes(report))
    print(json.dumps({"result": report["result"], "app_bytes": [row["artifacts"]["xiaozhi.bin"]["bytes"] for row in rows]}))


if __name__ == "__main__":
    main()
