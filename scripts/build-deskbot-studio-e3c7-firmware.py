#!/usr/bin/env python3
"""Build the USB VFS correction separately from retained E3c-5 output."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import TARGET_DIR, load_target, json_bytes

spec = importlib.util.spec_from_file_location("e3c7_previous_builder", ROOT / "scripts/build-deskbot-studio-e3c5-firmware.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
BASE, release = previous.BASE, previous.release
WORK = ROOT / "tmp/e3c7-usb-secondary-20261105"
PUBLIC = ROOT / "artifacts/deskbot-studio-e3c7-20261105"
PRIOR_REPORT = ROOT / "artifacts/deskbot-studio-e3c5-20261105/firmware-build-report.json"
CHANGED = "main/boards/esp_vocat/studio_usb_diagnostic.cc"
EDITION, PRIOR_EDITION = "E3c-7", "E3c-5"


def baseline(inputs):
    report = json.loads(PRIOR_REPORT.read_bytes())
    if report["edition"] != PRIOR_EDITION or report["revision"] != 1:
        raise ValueError(f"retained {PRIOR_EDITION} revision 1 required")
    before = report["source_sha256"]
    if {name for name in before.keys() | inputs.keys() if before.get(name) != inputs.get(name)} != {CHANGED}:
        raise ValueError(f"only USB task source may change from {PRIOR_EDITION}")
    for row in report["builds"]:
        for name, item in row["artifacts"].items():
            if release.record(ROOT / row["build_directory"] / name) != item:
                raise ValueError(f"retained {PRIOR_EDITION} artifact differs: " + name)
    return report


def inspect(build, enabled, inputs, env):
    row = previous.inspect(build, enabled, inputs, env)
    source = (build.parent / "p" / CHANGED).read_text(encoding="utf-8")
    if '#if CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG' not in source or '"/dev/secondary"' not in source:
        raise ValueError("secondary console VFS selection missing")
    row["usb_input_vfs"] = "/dev/secondary"
    row["console_registration"] = "ESP-IDF 5.5.5 init_vfs_usj_sec"
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    inputs = BASE.HELPER.HELPER.inventory()
    prior = baseline(inputs)
    env, python, idf = BASE.environment()
    path = PUBLIC / "firmware-build-report.json"
    if args.check:
        report = json.loads(path.read_bytes())
        if report["source_sha256"] != inputs or report["prior_report"] != release.record(PRIOR_REPORT):
            raise ValueError("retained source or prior report differs")
        for row in report["builds"]:
            if inspect(ROOT / row["build_directory"], row["enabled"], inputs, env) != row:
                raise ValueError("retained output differs")
        for step in report["steps"]:
            if release.record(ROOT / step["log"])["sha256"] != step["sha256"]:
                raise ValueError("build log differs")
        print(f"{EDITION} USB correction off/on builds checked")
        return
    if path.exists() and not args.resume:
        raise ValueError("saved build exists; use --check or --resume")
    locked = yaml.safe_load((BASE.SOURCE / "dependencies.lock").read_bytes())
    project, components = BASE.HELPER.stage(WORK, BASE.DONOR, inputs, locked)
    (project / "partitions/deskbot-upper-assets-8m.csv").write_bytes((TARGET_DIR / "partitions.csv").read_bytes())
    rows, steps = [], []
    for enabled in (False, True):
        label = "on" if enabled else "off"
        reference = ROOT / next(row["build_directory"] for row in prior["builds"] if row["enabled"] == enabled)
        build = WORK / label
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
            print(f"{EDITION} {label}: {action}", flush=True)
            BASE.run(command + [action], project, env, log)
            steps.append({"configuration": label, "action": action, "returncode": 0,
                          "log": log.relative_to(ROOT).as_posix(), "sha256": release.record(log)["sha256"]})
        BASE.HELPER.verify_source(project, inputs, inputs[BASE.HELPER.PARTITION])
        if BASE.HELPER.HELPER.normalized_lock(locked) != BASE.HELPER.HELPER.normalized_lock(
                yaml.safe_load((project / "dependencies.lock").read_bytes())):
            raise ValueError("dependency lock changed")
        row = inspect(build, enabled, inputs, env)
        if json.loads((build / "config/sdkconfig.json").read_bytes()) != json.loads((reference / "config/sdkconfig.json").read_bytes()):
            raise ValueError(f"configuration changed from retained {PRIOR_EDITION}")
        rows.append(row)
    report = {"schema": "deskbot.studio_usb_transport_build/v1", "edition": EDITION, "revision": 1,
              "result": "isolated_off_on_builds_verified", "source_sha256": inputs, "source_delta": [CHANGED],
              "prior_report": release.record(PRIOR_REPORT), "builds": rows, "steps": steps,
              "components": components, "target": load_target(), "new_tasks": 0,
              "sleep_state": "unknown", "screen_playback_confirmed": False,
              "device_written": False, "hardware_accessed": False, "hardware_acceptance": "pending"}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(report))
    print(json.dumps({"result": report["result"], "app_bytes": [r["artifacts"]["xiaozhi.bin"]["bytes"] for r in rows]}))


if __name__ == "__main__":
    main()
