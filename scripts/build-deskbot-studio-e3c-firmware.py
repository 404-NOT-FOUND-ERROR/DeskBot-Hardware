#!/usr/bin/env python3
"""Build USB session diagnostics off/on against the frozen E3b revision 2."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import TARGET_DIR, load_target, json_bytes

spec = importlib.util.spec_from_file_location("e3c_identity_builder", ROOT / "scripts/build-deskbot-studio-e3b-firmware.py")
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
release, BASE = identity.release, identity.BASE
PUBLIC = ROOT / "artifacts/deskbot-studio-e3c-20261104"
WORK = ROOT / "tmp/e3c-usb-20261104"
REFERENCE = ROOT / "tmp/e3b-identity-20261104/on"
BASELINE_REPORT = ROOT / "artifacts/deskbot-studio-e3b-20261104/firmware-build-report.json"
SOURCE_DELTA = {
    "main/Kconfig.projbuild", "main/boards/esp_vocat/studio_device_link.cc",
    "main/boards/esp_vocat/studio_request_framer.h", "main/boards/esp_vocat/studio_usb_session.h",
    "main/boards/esp_vocat/studio_usb_diagnostic.h", "main/boards/esp_vocat/studio_usb_diagnostic.cc",
}


def baseline(inputs):
    report = json.loads(BASELINE_REPORT.read_bytes())
    if report["revision"] != 2:
        raise ValueError("E3b revision 2 required")
    changed = {name for name in inputs.keys() | report["source_sha256"].keys()
               if inputs.get(name) != report["source_sha256"].get(name)}
    if changed != SOURCE_DELTA:
        raise ValueError("unexpected firmware source delta: " + repr(sorted(changed)))
    row = next(item for item in report["builds"] if item["enabled"])
    for name, item in row["artifacts"].items():
        if release.record(REFERENCE / name) != item:
            raise ValueError("frozen E3b output changed: " + name)
    return report


def inspect(build, enabled, inputs, env):
    actual = json.loads((build / "config/sdkconfig.json").read_bytes())
    previous = json.loads((REFERENCE / "config/sdkconfig.json").read_bytes())
    delta = {key: {"before": previous.get(key), "after": actual.get(key)}
             for key in sorted(actual.keys() | previous.keys()) if actual.get(key) != previous.get(key)}
    if set(delta) != {"DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE"}:
        raise ValueError("unexpected configuration delta: " + repr(delta))
    if actual.get("DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE", False) != enabled:
        raise ValueError("diagnostic option differs")
    if not all(actual.get(key) for key in ("DESKBOT_STUDIO_IDENTITY_ENABLE", "ESP_CONSOLE_UART_DEFAULT",
                                           "ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG")):
        raise ValueError("identity or console topology differs")
    release.effective_octal_config(actual, 32)
    release.effective_octal_config(json.loads((build / "bootloader/config/sdkconfig.json").read_bytes()), 32)
    artifacts = {name: release.record(build / name) for name in release.FILES}
    protected = {name: release.record(REFERENCE / name) == artifacts[name] for name in (
        "bootloader/bootloader.bin", "partition_table/partition-table.bin", "srmodels/srmodels.bin", "ota_data_initial.bin")}
    if not all(protected.values()):
        raise ValueError("protected output changed: " + repr(protected))
    target = load_target()
    if artifacts["partition_table/partition-table.bin"]["sha256"] != target["partition_table_sha256"]:
        raise ValueError("partition table differs")
    headroom = 0x5DC000 - artifacts["xiaozhi.bin"]["bytes"]
    if headroom < target["minimum_app_headroom_bytes"]:
        raise ValueError("App headroom below 1 MiB")
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    if not nm:
        raise ValueError("Xtensa nm missing")
    symbols = subprocess.check_output([nm, "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    diagnostic_present = "deskbot_studio_session::Start(" in symbols
    if diagnostic_present != enabled or "deskbot_studio_link::" not in symbols:
        raise ValueError("feature symbols differ from configuration")
    if any(name in symbols for name in ("RunContinuousMappingOnce", "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce")):
        raise ValueError("temporary Flash diagnostic task present")
    flash = json.loads((build / "flasher_args.json").read_bytes())
    expected_flash = {0: "bootloader/bootloader.bin", 0x8000: "partition_table/partition-table.bin",
                      0xD000: "ota_data_initial.bin", 0x10000: "xiaozhi.bin", 0xBC8000: "srmodels/srmodels.bin"}
    if {int(address, 16): name for address, name in flash["flash_files"].items()} != expected_flash:
        raise ValueError("generated flash map differs")
    if release.image_facts((build / "xiaozhi.bin").read_bytes())["configured_flash_bytes"] != target["flash_bytes"]:
        raise ValueError("App Flash header differs")
    if inputs != BASE.HELPER.HELPER.inventory():
        raise ValueError("source changed during build")
    return {"enabled": enabled, "build_directory": build.relative_to(ROOT).as_posix(), "artifacts": artifacts,
            "config_delta": delta, "app_headroom_bytes": headroom, "protected_matches": protected,
            "identity_symbols_present": True, "session_symbols_present": diagnostic_present,
            "printf_portability": identity.printf_portability((build / "sdkconfig").read_text(encoding="utf-8"), inputs),
            "console_topology_unchanged": True, "expression_controls": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    path = PUBLIC / "firmware-build-report.json"
    inputs = BASE.HELPER.HELPER.inventory()
    baseline(inputs)
    if args.check:
        report = json.loads(path.read_bytes())
        if report["source_sha256"] != inputs or report["target"] != load_target():
            raise ValueError("saved source or target differs")
        for row in report["builds"]:
            for name, item in row["artifacts"].items():
                if release.record(ROOT / row["build_directory"] / name) != item:
                    raise ValueError("saved artifact differs: " + name)
        for step in report["steps"]:
            if release.record(ROOT / step["log"])["sha256"] != step["sha256"]:
                raise ValueError("saved build log differs")
        print("E3c-1 saved off/on builds checked")
        return
    if path.exists() and not args.resume:
        raise ValueError("saved build exists; use --check or --resume")
    identity.printf_portability((REFERENCE / "sdkconfig").read_text(encoding="utf-8"), inputs)
    locked = yaml.safe_load((BASE.SOURCE / "dependencies.lock").read_bytes())
    project, components = BASE.HELPER.stage(WORK, BASE.DONOR, inputs, locked)
    (project / "partitions/deskbot-upper-assets-8m.csv").write_bytes((TARGET_DIR / "partitions.csv").read_bytes())
    env, python, idf = BASE.environment()
    steps, rows = [], []
    for enabled in (False, True):
        label = "on" if enabled else "off"
        build = WORK / label
        BASE.HELPER.private_directory(build)
        sdkconfig = build / "sdkconfig"
        overlay = ("CONFIG_DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE=y\n" if enabled
                   else "# CONFIG_DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE is not set\n")
        sdkconfig.write_text(release.apply_overlay((REFERENCE / "sdkconfig").read_text(encoding="utf-8"), overlay), encoding="utf-8")
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
            print(f"E3c-1 {label}: {action}", flush=True)
            BASE.run(command + [action], project, env, log)
            steps.append({"configuration": label, "action": action, "returncode": 0,
                          "log": log.relative_to(ROOT).as_posix(), "sha256": release.record(log)["sha256"]})
        BASE.HELPER.verify_source(project, inputs, inputs[BASE.HELPER.PARTITION])
        staged_lock = yaml.safe_load((project / "dependencies.lock").read_bytes())
        if BASE.HELPER.HELPER.normalized_lock(locked) != BASE.HELPER.HELPER.normalized_lock(staged_lock):
            raise ValueError("dependency lock changed")
        rows.append(inspect(build, enabled, inputs, env))
    report = {"schema": "deskbot.studio_session_build/v1", "edition": "E3c-1", "revision": 1,
              "result": "isolated_off_on_builds_verified", "reference": "E3b revision 2 identity-on",
              "source_sha256": inputs, "source_delta": sorted(SOURCE_DELTA), "builds": rows,
              "steps": steps, "components": components, "target": load_target(),
              "task": {"priority": 1, "stack_bytes": 6144, "poll_interval_ms": 10, "read_bytes_per_poll": 64},
              "limits": {"max_frame_bytes": 2048, "lease_ms": 30000, "reply_interval_ms": 250, "partial_timeout_ms": 2000},
              "device_written": False, "hardware_accessed": False, "hardware_acceptance": "pending",
              "expression_controls": False}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(report))
    print(json.dumps({"result": report["result"], "app_bytes": [r["artifacts"]["xiaozhi.bin"]["bytes"] for r in rows]}))


if __name__ == "__main__":
    main()
