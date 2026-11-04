#!/usr/bin/env python3
"""Independently recheck retained E1.43b images, symbols, configs and source."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("continuous_build_audit", ROOT / "scripts/build-expression-e1-43b-probe.py")
BUILD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BUILD)


def audit(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("schema") != "deskbot.continuous_mmap.build.e1_43b/v1"
            or report.get("result") != "isolated_default_off_and_diagnostic_builds_passed"
            or report.get("source_delta") != BUILD.EXPECTED_SOURCE_DELTA):
        raise ValueError("unexpected E1.43b build report")
    inputs = BUILD.BASE.HELPER.HELPER.inventory()
    if report["source_sha256"] != inputs:
        raise ValueError("current source differs from this build")
    if report["source_manifest_sha256"] != BUILD.BASE.sha(BUILD.BASE.SOURCE / "SOURCE_MANIFEST.sha256"):
        raise ValueError("source manifest differs from this build")
    baseline = json.loads(BUILD.A.BASELINE.read_text(encoding="utf-8"))
    configs = {name: json.loads((BUILD.A.BASE_BUILD / name).read_text(encoding="utf-8"))
               for name in ("config/sdkconfig.json", "bootloader/config/sdkconfig.json")}
    env, _, _ = BUILD.BASE.environment()
    nm = BUILD.shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    if nm is None:
        raise ValueError("pinned nm.exe missing")
    checked = {}
    for enabled in (False, True):
        label = "on" if enabled else "off"
        old = report["rounds"][label]
        archive = (ROOT / old["evidence_directory"]).resolve()
        archive.relative_to(BUILD.WORK.resolve())
        current = BUILD.audit_build(archive, baseline, configs, enabled, env, nm)
        if any(current[key] != old[key] for key in current):
            raise ValueError("archived build changed: " + label)
        if not enabled:
            comparison = BUILD.A.compare_disabled((archive / "xiaozhi.bin").read_bytes(),
                                                  (BUILD.A.BASE_BUILD / "xiaozhi.bin").read_bytes())
            if comparison != old["disabled_image_comparison"]:
                raise ValueError("default-off image comparison changed")
        checked[label] = {"artifacts_checked": len(current["artifacts"]),
                          "app_bytes": current["artifacts"]["xiaozhi.bin"]["bytes"],
                          "app_headroom_bytes": current["app_headroom_bytes"],
                          "protected_artifacts_match": all(current["protected_match_e1_42"].values()),
                          "diagnostic_task_present": enabled}
    return {"schema": "deskbot.continuous_mmap.build_verification.v1", "result": "passed",
            "source_files_checked": len(inputs), "source_manifest_sha256": report["source_manifest_sha256"],
            "build_report": report_path.relative_to(ROOT).as_posix(),
            "build_report_sha256": BUILD.BASE.sha(report_path), "rounds": checked,
            "device_accessed": False, "cpu_verified": False, "physical_interaction_accepted": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.relative_to(ROOT).parts[0] not in {"tmp", "artifacts"}:
        parser.error("output must be inside repository tmp or artifacts")
    result = audit(args.report.resolve())
    if args.check:
        if json.loads(output.read_text(encoding="utf-8")) != result:
            parser.error("retained verification differs")
    else:
        if output.exists():
            parser.error("retain existing evidence; choose a new output")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
