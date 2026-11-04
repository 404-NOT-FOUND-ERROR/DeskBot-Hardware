#!/usr/bin/env python3
"""Isolated, locked E1.42.3 build; never opens a device or writes Flash."""
from __future__ import annotations

import importlib.util
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e142_build", ROOT / "scripts/build-expression-e1-42-flash-probe.py")
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)
WORK = ROOT / "tmp/e1423-serial-probe-20261102"
OUT = ROOT / "artifacts/expression-capacity-serial-e1-42-3-20261102"
FLAG = "DESKBOT_FLASH_PROBE_BOOT_EXPORT"
BASELINE = ROOT / "artifacts/expression-capacity-read-mmap-e1-42-20261031/build-report.json"
BASE_BUILD = ROOT / "tmp/e142-flash-probe-20261031/o-32m"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-built-off", action="store_true",
                        help="Audit an already completed default-off build before building the enabled variant.")
    parser.add_argument("--run-label", default="",
                        help="Unique evidence prefix when reusing this stage's isolated build cache.")
    args = parser.parse_args()
    if args.run_label and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.run_label):
        raise ValueError("run label must be lowercase words and digits separated by hyphens")
    prefix = args.run_label + "-" if args.run_label else ""
    report_path = OUT / (prefix + "build-report.json")
    if report_path.exists():
        raise ValueError("refuse to overwrite build evidence; choose a new --run-label")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    inputs = BASE.HELPER.HELPER.inventory()
    changed = sorted(k for k in inputs.keys() | baseline["source_sha256"].keys()
                     if inputs.get(k) != baseline["source_sha256"].get(k))
    if changed != ["main/Kconfig.projbuild", "main/boards/esp_vocat/dev_tools.cc"]:
        raise ValueError("unexpected source delta: " + repr(changed))
    reference = BASE.inspect_full_readback(BASE.BASELINE_FULL_READBACK)
    locked = __import__("yaml").safe_load((BASE.SOURCE / "dependencies.lock").read_bytes())
    project, components = BASE.HELPER.stage(WORK, BASE.DONOR, inputs, locked)
    env, python, idf = BASE.environment()
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    if not nm:
        raise ValueError("ESP32-S3 nm.exe is unavailable in the pinned toolchain")
    build = WORK / "o-32m"
    build.mkdir(parents=True, exist_ok=True)
    sdkconfig = build / "sdkconfig"
    baseline_sdk = (BASE_BUILD / "sdkconfig").read_text(encoding="utf-8")
    baseline_configs = {name: json.loads((BASE_BUILD / name).read_text(encoding="utf-8"))
                        for name in ("config/sdkconfig.json", "bootloader/config/sdkconfig.json")}
    common = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
              "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
              "-D", f"SDKCONFIG={sdkconfig}",
              "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
    rounds = {}
    for enabled in (False, True):
        label = "on" if enabled else "off"
        text = re.sub(r"(?m)^(?:# )?CONFIG_" + FLAG + r".*\n?", "", baseline_sdk)
        text += f"\nCONFIG_{FLAG}=y\n" if enabled else f"\n# CONFIG_{FLAG} is not set\n"
        resume = args.resume_built_off and not enabled
        if resume:
            if "Project build complete." not in (WORK / f"{prefix}off-build.log").read_text(encoding="utf-8"):
                raise ValueError("completed default-off build log is missing")
            effective = json.loads((build / "config/sdkconfig.json").read_text(encoding="utf-8"))
            if effective.get(FLAG, False):
                raise ValueError("cannot resume a build with the diagnostic enabled")
            BASE.HELPER.verify_source(project, inputs, inputs[BASE.HELPER.PARTITION])
        else:
            sdkconfig.write_text(text, encoding="utf-8")
        for action in (() if resume else ("reconfigure", "build")):
            print(f"E1.42.3 {label}: {action}", flush=True)
            if action == "build":
                BASE.HELPER.HELPER.quote_image_effects_link_path(build, project)
            log = WORK / f"{prefix}{label}-{action}.log"
            if log.exists():
                raise ValueError("refuse to overwrite build log: " + log.name)
            BASE.run(common + [action], project, env, log)
        artifacts = {name: {"bytes": (build / name).stat().st_size, "sha256": BASE.sha(build / name)}
                     for name in ("xiaozhi.bin", "bootloader/bootloader.bin", "partition_table/partition-table.bin",
                                  "expression_assets.bin", "srmodels/srmodels.bin",
                                  "config/sdkconfig.json", "bootloader/config/sdkconfig.json")}
        protected = {name: artifacts[name]["sha256"] == baseline["artifacts"][name]["sha256"]
                     for name in artifacts if name.endswith(".bin") and name != "xiaozhi.bin"}
        if not all(protected.values()):
            raise ValueError("protected binary changed: " + repr(protected))
        config_delta = {}
        for name, old in baseline_configs.items():
            new = json.loads((build / name).read_text(encoding="utf-8"))
            delta = {k: {"old": old.get(k), "new": new.get(k)} for k in old.keys() | new.keys()
                     if old.get(k) != new.get(k)}
            allowed = {FLAG} if name == "config/sdkconfig.json" else set()
            expected_flag = enabled if name == "config/sdkconfig.json" else False
            if set(delta) - allowed or new.get(FLAG, False) is not expected_flag:
                raise ValueError("unexpected effective config delta: " + repr(delta))
            config_delta[name] = delta
        symbols = subprocess.check_output([nm, "-C", str(build / "xiaozhi.elf")],
                                          env=env, text=True)
        has_task = "ExportFlashDiagnosticsOnce" in symbols
        if has_task != enabled:
            raise ValueError("default-off diagnostic symbol isolation failed")
        app_bytes = artifacts["xiaozhi.bin"]["bytes"]
        if app_bytes > BASE.APP_PARTITION_BYTES - BASE.MINIMUM_APP_HEADROOM:
            raise ValueError("less than 1 MiB App headroom")
        if not enabled:
            shutil.copy2(build / "xiaozhi.bin", WORK / (prefix + "default-off-app.bin"))
        rounds[label] = {"artifacts": artifacts, "protected_match_e1_42": protected,
                         "config_delta": config_delta, "diagnostic_task_symbol_present": has_task,
                         "app_headroom_bytes": BASE.APP_PARTITION_BYTES - app_bytes}
    # Detect staging/build helpers modifying any source except the known local lock path.
    BASE.HELPER.verify_source(project, inputs, inputs[BASE.HELPER.PARTITION])
    if BASE.HELPER.HELPER.inventory() != inputs:
        raise ValueError("source changed during build")
    report = {"schema": "deskbot.expression.capacity_serial.e1_42_3_build/v1",
              "result": "isolated_default_off_and_diagnostic_builds_passed", "source_delta": changed,
              "source_sha256": inputs, "source_manifest_sha256": BASE.sha(BASE.SOURCE / "SOURCE_MANIFEST.sha256"),
              "build_directory": build.relative_to(ROOT).as_posix(), "components": components,
              "reference_readback": reference, "rounds": rounds,
              "runtime_probe_verified": False, "mcp_visibility_verified": False,
              "device_written": False, "partition_table_changed": False, "nvs_written": False}
    OUT.mkdir(parents=True, exist_ok=True)
    report["run_label"] = args.run_label
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("E1.42.3 build audit passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
