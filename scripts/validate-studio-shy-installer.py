#!/usr/bin/env python3
"""Validate the E3f-1 installer with simulated commands and retain its evidence."""
from contextlib import redirect_stderr, redirect_stdout
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / "tmp/studio-shy-installer-e3f1"
PUBLIC = ROOT / "artifacts/studio-shy-e3f1"
SCHEMA = "deskbot.studio_shy_installer_validation/v1"
SUITES = {
    "tests/test_studio_shy_installer.py": 18,
    "tests/test_studio_e3f1_deployment.py": 19,
    "tests/test_studio_app_deployment.py": 19,
}
SOURCES = (
    "protocol/studio_shy_installer.py", "protocol/studio_shy_esptool.py",
    "scripts/deploy-studio-shy-e3f1.py", "scripts/validate-studio-shy-installer.py",
    *SUITES,
    "protocol/studio_e3f1_deployment.py", "protocol/studio_e3d2b_deployment.py",
    "protocol/studio_app_deployment.py", "protocol/studio_resource_installation.py",
    "protocol/studio_resource_package.py",
    "scripts/deploy-deskbot-studio-e3c.py", "scripts/deploy-deskbot-studio-e3b.py",
    "scripts/run-expression-continuous-mmap-upper-e1-43b.py",
    "protocol/storage_upper_experiment_esptool.py",
    "protocol/storage_continuous_mmap_experiment.py",
    "protocol/storage_upper_migration_esptool.py",
    "protocol/storage_continuous_mmap_e3c3b_baseline.py",
)
FIXTURES = (
    "tmp/resource-read-20261108-live1/logs/001-flash-id.log",
    "tmp/resource-read-20261108-live1/logs/002-security-info.log",
    "tmp/resource-read-20261108-live1/captures/080-catalog-restored.log",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(paths):
    return {name: sha(ROOT / name) for name in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--check", action="store_true", help="Verify the retained report without rerunning tests")
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("attempt must be positive")
    suffix = "" if args.attempt == 1 else "-" + str(args.attempt)
    log = WORK / ("installer-tests" + suffix + ".log")
    report_path = PUBLIC / ("installer-validation" + suffix + ".json")
    source_before, fixtures_before = hashes(SOURCES), hashes(FIXTURES)
    if args.check:
        report = json.loads(report_path.read_bytes())
        if (report.get("schema") != SCHEMA or report.get("result") != "passed"
                or report.get("tests") != sum(SUITES.values()) or report.get("suites") != SUITES
                or any(report.get(key) != 0 for key in ("failures", "errors", "skipped"))
                or report.get("source_unchanged") is not True or report.get("fixtures_unchanged") is not True
                or report.get("source_sha256") != source_before or report.get("fixture_sha256") != fixtures_before
                or report.get("log") != log.relative_to(ROOT).as_posix() or report.get("log_sha256") != sha(log)
                or report.get("hardware_accessed") is not False or report.get("device_written") is not False):
            raise ValueError("installer_validation_evidence_changed")
        print(json.dumps(dict(result="checked", tests=report["tests"], source_files=len(source_before))))
        return 0
    WORK.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    if log.exists() or report_path.exists():
        raise FileExistsError("choose a new installer validation attempt")
    suite = unittest.TestSuite()
    for name, count in SUITES.items():
        path = ROOT / name
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tests = unittest.defaultTestLoader.loadTestsFromModule(module)
        if tests.countTestCases() != count:
            raise ValueError("installer_test_count_changed: " + name)
        suite.addTests(tests)
    started = time.monotonic()
    with log.open("x", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    source_after, fixtures_after = hashes(SOURCES), hashes(FIXTURES)
    passed = (result.wasSuccessful() and result.testsRun == sum(SUITES.values()) and not result.skipped
              and source_before == source_after and fixtures_before == fixtures_after)
    report = dict(schema=SCHEMA, result="passed" if passed else "failed", suites=SUITES,
        tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        elapsed_seconds=round(time.monotonic() - started, 3), runtime=sys.version,
        source_sha256=source_after, source_unchanged=source_before == source_after,
        fixture_sha256=fixtures_after, fixtures_unchanged=fixtures_before == fixtures_after,
        log=log.relative_to(ROOT).as_posix(), log_sha256=sha(log),
        hardware_accessed=False, device_written=False)
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in ("result", "tests", "failures", "errors", "skipped")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
