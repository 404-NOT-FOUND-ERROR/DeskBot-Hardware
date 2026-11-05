#!/usr/bin/env python3
"""Build scheduled neutral requests off/on against frozen E3c-4 display-on output."""
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

spec = importlib.util.spec_from_file_location("e3c2_previous_builder", ROOT / "scripts/build-deskbot-studio-e3c-firmware.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
release, BASE, identity = previous.release, previous.BASE, previous.identity
PUBLIC = ROOT / "artifacts/deskbot-studio-e3c5-20261105"
WORK = ROOT / "tmp/e3c5-expression-20261105"
REFERENCE = ROOT / "tmp/e3c4-display-20261105/on"
BASELINE_REPORT = ROOT / "artifacts/deskbot-studio-e3c4-20261105/firmware-build-report.json"
SOURCE_DELTA = {
    "main/Kconfig.projbuild", "main/boards/esp_vocat/studio_usb_session.h",
    "main/boards/esp_vocat/studio_usb_diagnostic.cc", "main/boards/esp_vocat/studio_expression_request.h",
    "main/boards/esp_vocat/studio_expression_control.h", "main/boards/esp_vocat/studio_expression_adapter.h",
    "main/boards/esp_vocat/studio_expression_adapter.cc",
}
OPTION = "DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE"


def baseline(inputs):
    report = json.loads(BASELINE_REPORT.read_bytes())
    if report["edition"] != "E3c-4" or report["revision"] != 1:
        raise ValueError("frozen E3c-4 required")
    changed = {name for name in inputs.keys() | report["source_sha256"].keys()
               if inputs.get(name) != report["source_sha256"].get(name)}
    if changed != SOURCE_DELTA:
        raise ValueError("unexpected firmware source delta: " + repr(sorted(changed)))
    row = next(item for item in report["builds"] if item["enabled"])
    for name, item in row["artifacts"].items():
        if release.record(REFERENCE / name) != item:
            raise ValueError("frozen E3c-4 output changed: " + name)
    return report


def inspect(build, enabled, inputs, env):
    actual = json.loads((build / "config/sdkconfig.json").read_bytes())
    old = json.loads((REFERENCE / "config/sdkconfig.json").read_bytes())
    delta = {key: {"before": old.get(key), "after": actual.get(key)}
             for key in sorted(actual.keys() | old.keys()) if actual.get(key) != old.get(key)}
    if set(delta) != {OPTION} or actual.get(OPTION, False) != enabled:
        raise ValueError("unexpected configuration delta: " + repr(delta))
    if not all(actual.get(key) for key in ("DESKBOT_STUDIO_IDENTITY_ENABLE", "DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE",
                                           "DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE", "COMPILER_CXX_RTTI",
                                           "ESP_CONSOLE_UART_DEFAULT", "ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG")):
        raise ValueError("identity, session or console topology differs")
    release.effective_octal_config(actual, 32)
    boot = json.loads((build / "bootloader/config/sdkconfig.json").read_bytes())
    if boot != json.loads((REFERENCE / "bootloader/config/sdkconfig.json").read_bytes()):
        raise ValueError("bootloader configuration changed")
    release.effective_octal_config(boot, 32)
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
    symbols = subprocess.check_output([nm, "-S", "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    present = "deskbot_studio_expression::AdapterHooks()" in symbols
    marker = b"deskbot.expression_control/v1" in (build / "xiaozhi.bin").read_bytes()
    if marker != enabled or present != enabled or "deskbot_studio_session::Start(" not in symbols:
        raise ValueError("feature symbols differ from configuration")
    store_rows = [line for line in symbols.splitlines() if line.endswith("deskbot_studio_runtime::(anonymous namespace)::store")]
    store_bytes = int(store_rows[0].split()[1], 16) if len(store_rows) == 1 else 0
    display_rows = [line for line in symbols.splitlines() if line.endswith("deskbot_studio_runtime::(anonymous namespace)::player_store")]
    display_bytes = int(display_rows[0].split()[1], 16) if len(display_rows) == 1 else 0
    if display_bytes != 8:
        raise ValueError("display store bounds differ")
    if not (0 < store_bytes <= 80):
        raise ValueError("snapshot store missing or too large")
    if any(name in symbols for name in ("RunContinuousMappingOnce", "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce")):
        raise ValueError("temporary Flash diagnostic present")
    control_rows = [line for line in symbols.splitlines() if line.endswith("deskbot_studio_expression::(anonymous namespace)::control")]
    control_bytes = int(control_rows[0].split()[1], 16) if len(control_rows) == 1 else 0
    scratch_rows = [line for line in symbols.splitlines() if line.endswith("deskbot_studio_expression::(anonymous namespace)::input")]
    scratch_bytes = int(scratch_rows[0].split()[1], 16) if len(scratch_rows) == 1 else 0
    if enabled and not (0 < control_bytes <= 12000 and 0 < scratch_bytes <= 2200):
        raise ValueError("expression state bounds differ")
    if not enabled and (control_bytes or scratch_bytes):
        raise ValueError("expression state present when disabled")
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
            "expression_adapter_present": present, "expression_schema_present": marker,
            "control_store_bytes": control_bytes, "input_workspace_bytes": scratch_bytes, "snapshot_store_bytes": store_bytes,
            "display_store_bytes": display_bytes,
            "printf_portability": identity.printf_portability((build / "sdkconfig").read_text(encoding="utf-8"), inputs),
            "bootloader_configuration_equal": True, "console_topology_unchanged": True, "neutral_request_enabled": enabled}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    path = PUBLIC / "firmware-build-report.json"
    inputs = BASE.HELPER.HELPER.inventory()
    baseline(inputs)
    env, python, idf = BASE.environment()
    if args.check:
        report = json.loads(path.read_bytes())
        if report["source_sha256"] != inputs or report["target"] != load_target():
            raise ValueError("saved source or target differs")
        for row in report["builds"]:
            if inspect(ROOT / row["build_directory"], row["enabled"], inputs, env) != row:
                raise ValueError("saved build checks differ")
        for step in report["steps"]:
            if release.record(ROOT / step["log"])["sha256"] != step["sha256"]:
                raise ValueError("saved build log differs")
        print("E3c-5 saved off/on builds checked")
        return
    if path.exists() and not args.resume:
        raise ValueError("saved build exists; use --check or --resume")
    identity.printf_portability((REFERENCE / "sdkconfig").read_text(encoding="utf-8"), inputs)
    locked = yaml.safe_load((BASE.SOURCE / "dependencies.lock").read_bytes())
    project, components = BASE.HELPER.stage(WORK, BASE.DONOR, inputs, locked)
    (project / "partitions/deskbot-upper-assets-8m.csv").write_bytes((TARGET_DIR / "partitions.csv").read_bytes())
    steps, rows = [], []
    for enabled in (False, True):
        label = "on" if enabled else "off"
        build = WORK / label
        BASE.HELPER.private_directory(build)
        sdkconfig = build / "sdkconfig"
        overlay = f"CONFIG_{OPTION}=y\n" if enabled else f"# CONFIG_{OPTION} is not set\n"
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
            print(f"E3c-5 {label}: {action}", flush=True)
            BASE.run(command + [action], project, env, log)
            steps.append({"configuration": label, "action": action, "returncode": 0,
                          "log": log.relative_to(ROOT).as_posix(), "sha256": release.record(log)["sha256"]})
        BASE.HELPER.verify_source(project, inputs, inputs[BASE.HELPER.PARTITION])
        staged_lock = yaml.safe_load((project / "dependencies.lock").read_bytes())
        if BASE.HELPER.HELPER.normalized_lock(locked) != BASE.HELPER.HELPER.normalized_lock(staged_lock):
            raise ValueError("dependency lock changed")
        rows.append(inspect(build, enabled, inputs, env))
    report = {"schema": "deskbot.studio_expression_request_build/v1", "edition": "E3c-5", "revision": 1,
              "result": "isolated_off_on_builds_verified", "reference": "E3c-4 query-on",
              "source_sha256": inputs, "source_delta": sorted(SOURCE_DELTA), "builds": rows,
              "steps": steps, "components": components, "target": load_target(),
              "new_tasks": 0, "snapshot_store_max_bytes": 80, "snapshot_max_bytes": 24,
              "sleep_state": "unknown", "snapshot_transport_integrated": True, "display_query_integrated": True, "screen_playback_confirmed": False,
              "device_written": False, "hardware_accessed": False, "hardware_acceptance": "pending",
              "neutral_request_enabled": True, "new_control_max_bytes": 12000, "input_workspace_max_bytes": 2200}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(report))
    print(json.dumps({"result": report["result"], "app_bytes": [r["artifacts"]["xiaozhi.bin"]["bytes"] for r in rows]}))


if __name__ == "__main__":
    main()
