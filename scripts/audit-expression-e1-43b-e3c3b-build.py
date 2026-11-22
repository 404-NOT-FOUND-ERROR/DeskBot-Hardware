#!/usr/bin/env python3
"""Independently recheck the E1.43b E3c-3b dual-build evidence."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "e143b_e3c3b_build", ROOT / "scripts/build-expression-e1-43b-e3c3b.py")
if SPEC is None or SPEC.loader is None:
    raise ImportError("E3c-3b E1.43b build module is unavailable")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
helpers = builder.helpers


def _read_report(path: Path) -> tuple[Path, bytes, dict]:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT / target
    helpers.ensure_safe_path(target, ROOT / "artifacts")
    resolved = target.resolve(strict=True)
    if resolved.parent != builder.ARTIFACT_ROOT.resolve(strict=True):
        raise ValueError("E3c-3b build report is outside its artifact directory")
    raw = resolved.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("build report must be a JSON object")
    label = value.get("run_label")
    if builder.RUN_LABEL_RE.fullmatch(label or "") is None:
        raise ValueError("invalid E3c-3b build run label")
    if resolved.name != label + "-build-report.json":
        raise ValueError("build report path differs from its run label")
    if (value.get("schema") != "deskbot.continuous_mmap.build.e1_43b_e3c3b/v1"
            or value.get("edition") != "E1.43b-2"
            or value.get("baseline") != "E3c-3b"
            or value.get("result") != "e3c3b_probe_off_on_builds_verified"):
        raise ValueError("unexpected E1.43b E3c-3b build report")
    return resolved, raw, value


def validate_source_binding(report: dict, metadata: dict, expected_work: Path) -> None:
    source = report.get("source")
    if (report.get("target") != builder.TARGET
            or not isinstance(source, dict)
            or source.get("project") != builder.FROZEN.relative_to(ROOT).as_posix()
            or source.get("build_project") != (expected_work / "source").relative_to(ROOT).as_posix()
            or type(source.get("files_checked")) is not int
            or source["files_checked"] != len(metadata["source_sha256"])
            or source.get("probe") != metadata["probe"]
            or source.get("input_inventory") != metadata["input_inventory"]
            or source.get("changed") is not False):
        raise ValueError("E3c-3b target or frozen source binding differs")
    digest = hashlib.sha256(json.dumps(metadata["source_sha256"], sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    if source.get("source_sha256_digest") != digest:
        raise ValueError("E3c-3b source manifest digest differs")


def audit_report(path: Path) -> dict:
    report_path, report_raw, report = _read_report(path)
    _, reference_row, metadata = builder.load_reference()
    if report.get("reference_report") != {
            "path": builder.REFERENCE_REPORT.relative_to(ROOT).as_posix(),
            "sha256": metadata["report_sha256"]}:
        raise ValueError("E3c-3b reference binding differs")

    run_label = report["run_label"]
    expected_work = builder.WORK_ROOT / run_label
    helpers.ensure_safe_path(expected_work, ROOT / "tmp")
    validate_source_binding(report, metadata, expected_work)

    project = expected_work / "source"
    helpers.ensure_safe_path(project, ROOT / "tmp")
    helpers.verify_frozen_sources(project, metadata["source_sha256"])
    locked = helpers.load_locked_dependencies(project)
    if helpers.verify_project_file_inventory(project, metadata["source_sha256"], locked) != metadata["input_inventory"]:
        raise ValueError("isolated E3c-3b source inventory differs")
    if builder.validate_reviewed_components(project, locked) != metadata["components"]:
        raise ValueError("isolated E3c-3b managed components differ")

    rounds = report.get("rounds")
    if not isinstance(rounds, dict) or set(rounds) != {"off", "on"}:
        raise ValueError("probe-off and probe-on build records are required")
    env, _, _ = builder.build_runtime.BASE.environment()
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    size_tool = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    if not nm or not size_tool:
        raise ValueError("pinned ESP32-S3 inspection tools are unavailable")

    recalculated = {}
    for enabled, label in ((False, "off"), (True, "on")):
        row = rounds[label]
        build_dir = expected_work / label / "build"
        evidence = expected_work / label / "evidence"
        for directory in (build_dir, evidence):
            helpers.ensure_safe_path(directory, ROOT / "tmp")
            for name in helpers.ARTIFACTS:
                helpers.ensure_safe_path(directory / name, ROOT / "tmp")
        if (not isinstance(row, dict) or row.get("probe_enabled") is not enabled
                or row.get("build_directory") != build_dir.relative_to(ROOT).as_posix()
                or row.get("evidence_directory") != evidence.relative_to(ROOT).as_posix()):
            raise ValueError("build paths or probe state differ: " + label)
        current = helpers.audit_round(build_dir, reference_row, metadata["config"],
                                       metadata["boot_config"], enabled, nm, size_tool, env)
        current["protected_equal_e3c3b"] = current.pop("protected_equal_e3c3a")
        current["evidence_directory"] = evidence.relative_to(ROOT).as_posix()
        if current != row:
            raise ValueError("recomputed build facts differ: " + label)
        for name, expected in current["artifacts"].items():
            if helpers.file_record(evidence / name) != expected:
                raise ValueError("archived evidence differs: " + label + "/" + name)
        recalculated[label] = current

    expected_steps = {(configuration, action) for configuration in ("off", "on")
                      for action in ("reconfigure", "build")}
    steps = report.get("steps")
    if not isinstance(steps, list) or len(steps) != len(expected_steps):
        raise ValueError("four successful build steps are required")
    observed_steps = set()
    for step in steps:
        if (not isinstance(step, dict) or type(step.get("returncode")) is not int
                or step["returncode"] != 0):
            raise ValueError("invalid build step record")
        key = (step.get("configuration"), step.get("action"))
        if key not in expected_steps or key in observed_steps:
            raise ValueError("duplicate or unexpected build step")
        observed_steps.add(key)
        log = ROOT / step.get("log", "")
        helpers.ensure_safe_path(log, ROOT / "tmp")
        expected_log = expected_work / key[0] / (key[1] + ".log")
        if log.resolve(strict=True) != expected_log.resolve(strict=True):
            raise ValueError("build log path differs from run label")
        if helpers.sha256(log.read_bytes()) != step.get("sha256"):
            raise ValueError("build log digest differs")
        helpers.validate_step_log(log.read_bytes(), key[1])
    if observed_steps != expected_steps:
        raise ValueError("build step set is incomplete")

    if (recalculated["off"]["artifacts"]["xiaozhi.bin"] != reference_row["artifacts"]["xiaozhi.bin"]
            or recalculated["off"]["embedded_elf_sha256"] != reference_row["app_elf_sha256"]):
        raise ValueError("probe-off App differs from accepted E3c-3b")
    if recalculated["off"]["protected_equal_e3c3b"] != recalculated["on"]["protected_equal_e3c3b"]:
        raise ValueError("protected build outputs differ between configurations")
    for field in ("device_accessed", "device_written", "partition_table_changed",
                  "runtime_verified", "physical_interaction_accepted"):
        if report.get(field) is not False:
            raise ValueError("offline build report marks a device action as complete: " + field)

    return {
        "schema": "deskbot.continuous_mmap.build_audit.e1_43b_e3c3b/v1",
        "edition": "E1.43b-2",
        "baseline": "E3c-3b",
        "result": "passed",
        "run_label": run_label,
        "build_report": {"path": report_path.relative_to(ROOT).as_posix(),
                         "sha256": hashlib.sha256(report_raw).hexdigest()},
        "probe_off_matches_e3c3b": True,
        "probe_on_isolated": recalculated["on"]["symbols"]["probe_present"]
        and not recalculated["on"]["symbols"]["unrelated_probe_present"],
        "protected_outputs_match": all(recalculated["off"]["protected_equal_e3c3b"].values())
        and all(recalculated["on"]["protected_equal_e3c3b"].values()),
        "device_accessed": False,
        "device_written": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_report(args.report)
    output = args.output or (builder.ARTIFACT_ROOT / (result["run_label"] + "-audit.json"))
    if not output.is_absolute():
        output = ROOT / output
    helpers.ensure_safe_path(output, ROOT / "artifacts")
    if output.exists():
        raise FileExistsError("audit output exists; preserve it and select another path")
    if not output.resolve().is_relative_to(builder.ARTIFACT_ROOT.resolve(strict=True)):
        raise ValueError("audit output is outside its artifact directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
