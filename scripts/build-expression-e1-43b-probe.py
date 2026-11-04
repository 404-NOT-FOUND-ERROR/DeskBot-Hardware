#!/usr/bin/env python3
"""Build and audit default-off/on E1.43b images with locked dependencies."""
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
spec = importlib.util.spec_from_file_location("e143b_build_base", ROOT / "scripts/build-expression-e1-43a-probe.py")
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
BASE = A.BASE
WORK = ROOT / "tmp/e143b-probe-20261103"
PUBLIC = ROOT / "artifacts/expression-continuous-mmap-e1-43b-20261103"
FLAG = "DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT"
FLAGS = (FLAG, A.FLAG, A.OLD_FLAG)
EXPECTED_SOURCE_DELTA = sorted(A.EXPECTED_SOURCE_DELTA + ["main/boards/esp_vocat/continuous_mmap_probe.h"])


def audit_build(build: Path, baseline: dict, configs: dict, enabled: bool, env: dict, nm: str) -> dict:
    artifacts = {name: {"bytes": (build / name).stat().st_size, "sha256": BASE.sha(build / name)}
                 for name in A.ARTIFACTS}
    protected = {}
    for name in A.ARTIFACTS:
        if name.endswith(".bin") and name != "xiaozhi.bin":
            old = baseline["artifacts"].get(name) or {
                "bytes": (A.BASE_BUILD / name).stat().st_size, "sha256": BASE.sha(A.BASE_BUILD / name)}
            protected[name] = artifacts[name] == old
    if not all(protected.values()):
        raise ValueError("protected output changed: " + repr(protected))
    deltas = {}
    for name, old in configs.items():
        new = json.loads((build / name).read_text(encoding="utf-8"))
        delta = {key: {"old": old.get(key), "new": new.get(key)} for key in old.keys() | new.keys()
                 if old.get(key) != new.get(key)}
        if name == "config/sdkconfig.json":
            if (set(delta) - set(FLAGS) or new.get(FLAG, False) is not enabled
                    or any(new.get(key, False) for key in FLAGS[1:])):
                raise ValueError("unexpected App configuration delta: " + repr(delta))
        elif delta:
            raise ValueError("bootloader configuration changed")
        deltas[name] = delta
    symbols = subprocess.check_output([nm, "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    if (("RunContinuousMappingOnce" in symbols) != enabled
            or any(name in symbols for name in ("RunUpperPatternOnce", "ExportFlashDiagnosticsOnce"))):
        raise ValueError("diagnostic task symbol isolation failed")
    if not enabled and "deskbot_continuous_mmap::" in symbols:
        raise ValueError("default-off image contains E1.43b implementation")
    headroom = BASE.APP_PARTITION_BYTES - artifacts["xiaozhi.bin"]["bytes"]
    if headroom < BASE.MINIMUM_APP_HEADROOM:
        raise ValueError("less than 1 MiB App headroom")
    return {"artifacts": artifacts, "protected_match_e1_42": protected, "config_delta": deltas,
            "diagnostic_task_present": enabled, "other_diagnostic_tasks_present": False,
            "app_headroom_bytes": headroom}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--resume-built-off", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", args.run_label):
        parser.error("run-label must be a short lowercase label")
    evidence = WORK / "evidence" / args.run_label
    output = PUBLIC / (args.run_label + "-build-report.json")
    if output.exists() or (evidence.exists() and not args.resume_built_off):
        raise ValueError("use a unique run label; previous evidence is retained")
    baseline = json.loads(A.BASELINE.read_text(encoding="utf-8"))
    inputs = BASE.HELPER.HELPER.inventory()
    changed = sorted(key for key in inputs.keys() | baseline["source_sha256"].keys()
                     if inputs.get(key) != baseline["source_sha256"].get(key))
    if changed != EXPECTED_SOURCE_DELTA:
        raise ValueError("unexpected source delta: " + repr(changed))
    if BASE.sha(A.BASE_BUILD / "xiaozhi.bin") != baseline["artifacts"]["xiaozhi.bin"]["sha256"]:
        raise ValueError("retained baseline App differs from its report")
    locked = __import__("yaml").safe_load((BASE.SOURCE / "dependencies.lock").read_bytes())
    project, components = BASE.HELPER.stage(WORK, BASE.DONOR, inputs, locked)
    env, python, idf = BASE.environment()
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    if nm is None:
        raise ValueError("pinned ESP32-S3 nm.exe unavailable")
    evidence.mkdir(parents=True, exist_ok=True)
    build = WORK / "o-32m"
    build.mkdir(parents=True, exist_ok=True)
    sdkconfig = build / "sdkconfig"
    old_sdk = (A.BASE_BUILD / "sdkconfig").read_text(encoding="utf-8")
    configs = {name: json.loads((A.BASE_BUILD / name).read_text(encoding="utf-8"))
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
            for flag in FLAGS:
                text = re.sub(r"(?m)^(?:# )?CONFIG_" + flag + r".*\n?", "", text)
            text += "\n" + "".join(f"# CONFIG_{key} is not set\n" for key in FLAGS[1:])
            text += f"CONFIG_{FLAG}=y\n" if enabled else f"# CONFIG_{FLAG} is not set\n"
            sdkconfig.write_text(text, encoding="utf-8")
            for action in ("reconfigure", "build"):
                print(f"E1.43b {label}: {action}", flush=True)
                log = evidence / f"{label}-{action}.log"
                if log.exists():
                    raise ValueError("refuse to overwrite build log: " + log.name)
                if action == "build":
                    BASE.HELPER.HELPER.quote_image_effects_link_path(build, project)
                BASE.run(common + [action], project, env, log)
        inspected = snapshot if resume else build
        rounds[label] = audit_build(inspected, baseline, configs, enabled, env, nm)
        if not enabled:
            rounds[label]["disabled_image_comparison"] = A.compare_disabled(
                (inspected / "xiaozhi.bin").read_bytes(), (A.BASE_BUILD / "xiaozhi.bin").read_bytes())
        for name in A.ARTIFACTS:
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
    report = {"schema": "deskbot.continuous_mmap.build.e1_43b/v1", "edition": "E1.43b",
              "result": "isolated_default_off_and_diagnostic_builds_passed", "run_label": args.run_label,
              "source_delta": changed, "source_sha256": inputs, "components": components,
              "source_manifest_sha256": BASE.sha(BASE.SOURCE / "SOURCE_MANIFEST.sha256"),
              "rounds": rounds, "build_directory": build.relative_to(ROOT).as_posix(),
              "device_written": False, "runtime_verified": False, "hardware_accepted": False,
              "partition_table_changed": False, "content_reference": "same_run_live_capture_required",
              "claim_limits": "Build assets are references; live E1.18 remains the device package."}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("E1.43b default-off/on build audit passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
