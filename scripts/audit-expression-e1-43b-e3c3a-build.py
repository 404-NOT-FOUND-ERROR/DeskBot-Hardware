#!/usr/bin/env python3
"""Independently recheck the E1.43b E3c-3a dual-build evidence."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "e143b_e3c3a_build_audit", ROOT / "scripts/build-expression-e1-43b-e3c3a.py")
BUILD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BUILD)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_steps(steps: object, run_label: str) -> list[dict]:
    expected = [("off", "reconfigure"), ("off", "build"),
                ("on", "reconfigure"), ("on", "build")]
    if not isinstance(steps, list) or len(steps) != len(expected):
        raise ValueError("all four isolated build steps are required")
    checked = []
    for row, (configuration, action) in zip(steps, expected):
        expected_log = (BUILD.WORK_ROOT / run_label / configuration / (action + ".log")).relative_to(ROOT).as_posix()
        if (not isinstance(row, dict) or row.get("configuration") != configuration
                or row.get("action") != action or type(row.get("returncode")) is not int
                or row.get("returncode") != 0
                or row.get("log") != expected_log
                or not isinstance(row.get("sha256"), str)
                or len(row["sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in row["sha256"])):
            raise ValueError("build step sequence or evidence differs")
        checked.append(row)
    return checked


def validate_report_binding(report_path: Path, run_label: str) -> None:
    expected = BUILD.ARTIFACT_ROOT / (run_label + "-build-report.json")
    if Path(report_path) != expected.resolve():
        raise ValueError("build report path does not match its run label")


def validate_round_paths(round_report: object, run_label: str, label: str) -> tuple[Path, Path]:
    if not isinstance(round_report, dict):
        raise ValueError("build round report is missing: " + label)
    expected_build = BUILD.WORK_ROOT / run_label / label / "build"
    expected_evidence = BUILD.WORK_ROOT / run_label / label / "evidence"
    if (round_report.get("build_directory") != expected_build.relative_to(ROOT).as_posix()
            or round_report.get("evidence_directory") != expected_evidence.relative_to(ROOT).as_posix()):
        raise ValueError("build round path does not match report run label: " + label)
    return expected_build, expected_evidence


def audit_report(report_path: Path) -> dict:
    report_path = Path(report_path)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    BUILD.ensure_safe_path(report_path, ROOT / "artifacts")
    report_path = report_path.resolve(strict=True)
    if not report_path.is_relative_to(BUILD.ARTIFACT_ROOT.resolve(strict=True)):
        raise ValueError("build report must be inside the E1.43b archive")
    reference_report, reference_row, metadata = BUILD.load_reference()
    report = json.loads(report_path.read_bytes())
    if (report.get("schema") != "deskbot.continuous_mmap.build.e1_43b_e3c3a/v1"
            or report.get("edition") != "E1.43b-2"
            or report.get("result") != "e3c3a_probe_off_on_builds_verified"
            or report.get("device_accessed") is not False
            or report.get("device_written") is not False):
        raise ValueError("unexpected E1.43b E3c-3a build report")
    if report.get("reference_report") != {
            "path": BUILD.REFERENCE_REPORT.relative_to(ROOT).as_posix(),
            "sha256": BUILD.REFERENCE_REPORT_SHA256}:
        raise ValueError("E3c-3a reference report differs")
    source = report.get("source")
    source_hash = sha256(json.dumps(reference_report["source_sha256"], sort_keys=True,
                                    separators=(",", ":")).encode("utf-8"))
    if (not isinstance(source, dict)
            or source.get("project") != BUILD.FROZEN.relative_to(ROOT).as_posix()
            or source.get("files_checked") != len(reference_report["source_sha256"])
            or source.get("source_sha256_digest") != source_hash
            or source.get("changed") is not False
            or source.get("probe") != metadata["probe"]):
        raise ValueError("frozen source identity differs")
    run_label = report.get("run_label", "")
    if not isinstance(run_label, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", run_label) is None:
        raise ValueError("invalid E1.43b run label")
    validate_report_binding(report_path, run_label)
    expected_source = BUILD.WORK_ROOT / run_label / "source"
    if source.get("build_project") != expected_source.relative_to(ROOT).as_posix():
        raise ValueError("isolated build source path differs")
    build_project = BUILD.ensure_safe_path(expected_source, BUILD.WORK_ROOT)
    BUILD.verify_frozen_sources(build_project, reference_report["source_sha256"])
    BUILD.verify_probe_source(build_project)
    frozen_locked = BUILD.load_locked_dependencies(BUILD.FROZEN)
    frozen_inventory = BUILD.verify_project_file_inventory(
        BUILD.FROZEN, reference_report["source_sha256"], frozen_locked)
    if source.get("input_inventory") != frozen_inventory:
        raise ValueError("reported frozen input inventory differs")
    locked = BUILD.load_locked_dependencies(build_project)
    source_inventory = BUILD.verify_project_file_inventory(
        build_project, reference_report["source_sha256"], locked)
    if source_inventory != frozen_inventory:
        raise ValueError("isolated source inventory differs from frozen project")

    spec_runtime = importlib.util.spec_from_file_location(
        "e143b_e3c3a_runtime", ROOT / "scripts/build-expression-e1-42-flash-probe.py")
    runtime = importlib.util.module_from_spec(spec_runtime)
    spec_runtime.loader.exec_module(runtime)
    env, _, _ = runtime.environment()
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    size_tool = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    if not nm or not size_tool:
        raise ValueError("pinned ESP32-S3 audit tools are required")
    reference_config = metadata["config"]
    reference_boot_config = json.loads(
        (BUILD.REFERENCE_BUILD / "bootloader/config/sdkconfig.json").read_bytes())
    component_report = runtime.HELPER.HELPER.validate_components(build_project, locked)
    if component_report != reference_report.get("components"):
        raise ValueError("isolated managed components differ from E3c-3a")

    rounds = report.get("rounds")
    if not isinstance(rounds, dict) or set(rounds) != {"off", "on"}:
        raise ValueError("both probe configurations are required")
    checked = {}
    for enabled, label in ((False, "off"), (True, "on")):
        old = rounds[label]
        expected_build, expected_evidence = validate_round_paths(old, run_label, label)
        if old.get("probe_enabled") is not enabled:
            raise ValueError("probe configuration label differs: " + label)
        build_dir = BUILD.ensure_safe_path(expected_build, BUILD.WORK_ROOT).resolve(strict=True)
        evidence_dir = BUILD.ensure_safe_path(expected_evidence, BUILD.WORK_ROOT).resolve(strict=True)
        current = BUILD.audit_round(build_dir, reference_row, reference_config,
                                    reference_boot_config, enabled, nm, size_tool, env)
        evidence = BUILD.audit_round(evidence_dir, reference_row, reference_config,
                                     reference_boot_config, enabled, nm, size_tool, env)
        expected = {key: value for key, value in old.items()
                    if key not in {"evidence_directory"}}
        if current != expected or any(current[key] != evidence[key] for key in current if key != "build_directory"):
            raise ValueError("build output or archived copy changed: " + label)
        checked[label] = {"app_bytes": current["artifacts"]["xiaozhi.bin"]["bytes"],
                          "app_headroom_bytes": current["app_headroom_bytes"],
                          "protected_outputs_match": all(current["protected_equal_e3c3a"].values()),
                          "probe_symbol_present": current["symbols"]["probe_present"]}

    steps = validate_steps(report.get("steps"), run_label)
    for step in steps:
        relative = Path(step.get("log", ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("build log path escapes repository")
        log = BUILD.ensure_safe_path(ROOT / relative, BUILD.WORK_ROOT).resolve(strict=True)
        raw = log.read_bytes()
        log_hash = BUILD.validate_step_log(raw, step["action"])
        if log_hash != step.get("sha256"):
            raise ValueError("build log evidence differs")
    if rounds["off"]["artifacts"]["xiaozhi.bin"] != reference_row["artifacts"]["xiaozhi.bin"]:
        raise ValueError("probe-off App differs from E3c-3a")
    return {"schema": "deskbot.continuous_mmap.build_audit.e1_43b_e3c3a/v1",
            "result": "passed", "report": report_path.relative_to(ROOT).as_posix(),
            "report_sha256": sha256(report_path.read_bytes()),
            "frozen_source_files_checked": source["files_checked"],
            "rounds": checked, "device_accessed": False, "device_written": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = args.output.absolute()
    try:
        BUILD.ensure_safe_path(output, ROOT / "tmp")
    except ValueError:
        BUILD.ensure_safe_path(output, ROOT / "artifacts")
    if not output.is_relative_to(ROOT / "tmp") and not output.is_relative_to(ROOT / "artifacts"):
        parser.error("output must be inside repository tmp or artifacts")
    result = audit_report(args.report)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if json.loads(output.read_text(encoding="utf-8")) != result:
            parser.error("saved E1.43b build audit differs")
    else:
        if output.exists():
            parser.error("retain previous audit; choose a new output")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
