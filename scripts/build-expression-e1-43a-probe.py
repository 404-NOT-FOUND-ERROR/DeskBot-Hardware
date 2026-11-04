#!/usr/bin/env python3
"""Build and audit default-off/on fixed-page diagnostics; never opens a device."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_capacity import image_facts

spec = importlib.util.spec_from_file_location("e143a_base_build", ROOT / "scripts/build-expression-e1-42-flash-probe.py")
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)
WORK = ROOT / "tmp/e143a-probe-20261102"
PUBLIC = ROOT / "artifacts/expression-upper-pattern-probe-e1-43a2-20261102"
EDITION = "E1.43a-2"
BASELINE = ROOT / "artifacts/expression-capacity-read-mmap-e1-42-20261031/build-report.json"
BASE_BUILD = ROOT / "tmp/e142-flash-probe-20261031/o-32m"
FLAG = "DESKBOT_UPPER_PATTERN_BOOT_EXPORT"
OLD_FLAG = "DESKBOT_FLASH_PROBE_BOOT_EXPORT"
EXPECTED_SOURCE_DELTA = ["main/Kconfig.projbuild", "main/boards/esp_vocat/dev_tools.cc",
                         "main/boards/esp_vocat/upper_pattern_probe.h"]
ARTIFACTS = ("xiaozhi.bin", "xiaozhi.elf", "bootloader/bootloader.bin", "partition_table/partition-table.bin",
             "expression_assets.bin", "srmodels/srmodels.bin", "ota_data_initial.bin",
             "config/sdkconfig.json", "bootloader/config/sdkconfig.json")


def compare_disabled(raw: bytes, baseline: bytes) -> dict:
    left, right = image_facts(baseline), image_facts(raw)
    if len(raw) != len(baseline) or left["checksum_offset"] != right["checksum_offset"]:
        raise ValueError("default-off image geometry changed")
    allowed = set(range(176, 208)) | {left["checksum_offset"]} | set(range(len(raw) - 32, len(raw)))
    changed = [offset for offset, pair in enumerate(zip(raw, baseline)) if pair[0] != pair[1]]
    if set(changed) - allowed:
        raise ValueError("default-off executable image differs outside ELF identity/checksum/digest")
    return {"bytes": len(raw), "changed_bytes": len(changed),
            "only_elf_identity_checksum_digest_changed": True, "baseline_sha256": BASE.sha(BASE_BUILD / "xiaozhi.bin")}


def audit_build(build: Path, baseline: dict, old_configs: dict, enabled: bool, env: dict, nm: str) -> dict:
    artifacts = {name: {"bytes": (build / name).stat().st_size, "sha256": BASE.sha(build / name)}
                 for name in ARTIFACTS}
    protected_names = [name for name in ARTIFACTS if name.endswith(".bin") and name != "xiaozhi.bin"]
    protected = {}
    for name in protected_names:
        expected = baseline["artifacts"].get(name)
        if expected is None:
            original = BASE_BUILD / name
            expected = {"bytes": original.stat().st_size, "sha256": BASE.sha(original)}
        protected[name] = artifacts[name] == expected
    if not all(protected.values()):
        raise ValueError("protected output changed: " + repr(protected))
    deltas = {}
    for name, old in old_configs.items():
        new = json.loads((build / name).read_text(encoding="utf-8"))
        delta = {key: {"old": old.get(key), "new": new.get(key)} for key in old.keys() | new.keys()
                 if old.get(key) != new.get(key)}
        if name == "config/sdkconfig.json":
            if set(delta) - {FLAG, OLD_FLAG} or new.get(FLAG, False) is not enabled or new.get(OLD_FLAG, False):
                raise ValueError("unexpected App configuration delta: " + repr(delta))
        elif delta:
            raise ValueError("bootloader configuration changed")
        deltas[name] = delta
    symbols = subprocess.check_output([nm, "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    task = "RunUpperPatternOnce" in symbols
    if task != enabled or "ExportFlashDiagnosticsOnce" in symbols:
        raise ValueError("diagnostic task symbol isolation failed")
    app_bytes = artifacts["xiaozhi.bin"]["bytes"]
    if app_bytes > BASE.APP_PARTITION_BYTES - BASE.MINIMUM_APP_HEADROOM:
        raise ValueError("less than 1 MiB App headroom")
    return {"artifacts": artifacts, "protected_match_e1_42": protected, "config_delta": deltas,
            "new_task_symbol_present": task, "old_diagnostic_task_present": False,
            "app_headroom_bytes": BASE.APP_PARTITION_BYTES - app_bytes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--resume-built-off", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", args.run_label):
        parser.error("run-label must be a short lowercase label")
    evidence = WORK / "evidence" / args.run_label
    report_path = PUBLIC / (args.run_label + "-build-report.json")
    if report_path.exists() or (evidence.exists() and not args.resume_built_off):
        raise ValueError("refuse to overwrite previous evidence; use a unique run label")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    inputs = BASE.HELPER.HELPER.inventory()
    changed = sorted(key for key in inputs.keys() | baseline["source_sha256"].keys()
                     if inputs.get(key) != baseline["source_sha256"].get(key))
    if changed != EXPECTED_SOURCE_DELTA:
        raise ValueError("unexpected source delta: " + repr(changed))
    if BASE.sha(BASE_BUILD / "xiaozhi.bin") != baseline["artifacts"]["xiaozhi.bin"]["sha256"]:
        raise ValueError("retained E1.42 App baseline differs from its locked report")
    locked = __import__("yaml").safe_load((BASE.SOURCE / "dependencies.lock").read_bytes())
    project, components = BASE.HELPER.stage(WORK, BASE.DONOR, inputs, locked)
    env, python, idf = BASE.environment()
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    if not nm:
        raise ValueError("pinned ESP32-S3 nm.exe unavailable")
    evidence.mkdir(parents=True, exist_ok=True)
    build = WORK / "o-32m"
    build.mkdir(parents=True, exist_ok=True)
    sdkconfig = build / "sdkconfig"
    old_sdk = (BASE_BUILD / "sdkconfig").read_text(encoding="utf-8")
    configs = {name: json.loads((BASE_BUILD / name).read_text(encoding="utf-8"))
               for name in ("config/sdkconfig.json", "bootloader/config/sdkconfig.json")}
    common = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
              "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
              "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
    rounds = {}
    for enabled in (False, True):
        label = "on" if enabled else "off"
        snapshot = evidence / label
        snapshot.mkdir(exist_ok=True)
        resume = args.resume_built_off and not enabled
        if resume:
            if "Project build complete." not in (evidence / "off-build.log").read_text(encoding="utf-8"):
                raise ValueError("completed default-off log missing")
        else:
            text = old_sdk
            for flag in (FLAG, OLD_FLAG):
                text = re.sub(r"(?m)^(?:# )?CONFIG_" + flag + r".*\n?", "", text)
            text += f"\n# CONFIG_{OLD_FLAG} is not set\n"
            text += f"CONFIG_{FLAG}=y\n" if enabled else f"# CONFIG_{FLAG} is not set\n"
            sdkconfig.write_text(text, encoding="utf-8")
            for action in ("reconfigure", "build"):
                print(f"E1.43a {label}: {action}", flush=True)
                log = evidence / f"{label}-{action}.log"
                if log.exists():
                    raise ValueError("refuse to overwrite build log: " + log.name)
                if action == "build":
                    BASE.HELPER.HELPER.quote_image_effects_link_path(build, project)
                BASE.run(common + [action], project, env, log)
        rounds[label] = audit_build(build, baseline, configs, enabled, env, nm)
        if not enabled:
            rounds[label]["disabled_image_comparison"] = compare_disabled((build / "xiaozhi.bin").read_bytes(),
                                                                          (BASE_BUILD / "xiaozhi.bin").read_bytes())
        for name in ARTIFACTS:
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not resume:
                shutil.copy2(build / name, target)
            if BASE.sha(target) != rounds[label]["artifacts"][name]["sha256"]:
                raise ValueError("archived build evidence differs: " + label + "/" + name)
        rounds[label]["evidence_directory"] = snapshot.relative_to(ROOT).as_posix()
    BASE.HELPER.verify_source(project, inputs, inputs[BASE.HELPER.PARTITION])
    if BASE.HELPER.HELPER.inventory() != inputs:
        raise ValueError("source changed during builds")
    report = {"schema": "deskbot.upper_pattern.build.e1_43a/v1", "edition": EDITION,
              "result": "isolated_default_off_and_diagnostic_builds_passed", "run_label": args.run_label,
              "source_delta": changed, "source_sha256": inputs, "components": components,
              "source_manifest_sha256": BASE.sha(BASE.SOURCE / "SOURCE_MANIFEST.sha256"), "rounds": rounds,
              "build_directory": build.relative_to(ROOT).as_posix(), "device_written": False,
              "runtime_verified": False, "hardware_accepted": False, "partition_table_changed": False,
              "claim_limits": "Generated expression_assets.bin is a build reference, not the accepted live E1.18 package."}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("E1.43a default-off/on build audit passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
