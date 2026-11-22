#!/usr/bin/env python3
"""Run and record the offline E1.45/E3f-1 installation rehearsal tests."""
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
WORK = ROOT / "tmp/studio-shy-deployment-e3f1"
PUBLIC = ROOT / "artifacts/studio-shy-e3f1"
SOURCES = (ROOT / "protocol/studio_e3f1_deployment.py",
           ROOT / "tests/test_studio_e3f1_deployment.py",
           ROOT / "scripts/prepare-studio-shy-e3f1-offline.py",
           ROOT / "scripts/validate-studio-e3f1-deployment.py",
           ROOT / "protocol/studio_app_deployment.py",
           ROOT / "protocol/studio_resource_installation.py",
           ROOT / "protocol/studio_resource_package.py",
           ROOT / "protocol/storage_continuous_mmap_e3c3b_baseline.py",
           ROOT / "protocol/studio_e3d2b_deployment.py")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("attempt must be positive")
    WORK.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.attempt == 1 else "-" + str(args.attempt)
    log = WORK / ("deployment-tests" + suffix + ".log")
    report_path = PUBLIC / ("deployment-validation" + suffix + ".json")
    if log.exists() or report_path.exists():
        raise FileExistsError("retained E3f-1 deployment validation already exists")
    spec = importlib.util.spec_from_file_location("test_studio_e3f1_deployment", SOURCES[1])
    if spec is None or spec.loader is None:
        raise RuntimeError("E3f-1 deployment test module unavailable")
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    before = {path.relative_to(ROOT).as_posix(): sha(path) for path in SOURCES}
    suite = unittest.defaultTestLoader.loadTestsFromModule(tests)
    started = time.monotonic()
    with log.open("x", encoding="utf-8") as stream, redirect_stderr(stream), redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = {path.relative_to(ROOT).as_posix(): sha(path) for path in SOURCES}
    report = dict(schema="deskbot.studio_shy_deployment_validation/v1",
        result="passed" if result.wasSuccessful() and result.testsRun == 19 and not result.skipped
            and before == after else "failed",
        tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        elapsed_seconds=round(time.monotonic() - started, 3), runtime=sys.version,
        source_sha256=after, source_unchanged=before == after,
        log=log.relative_to(ROOT).as_posix(), log_sha256=sha(log),
        hardware_accessed=False, device_written=False)
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in ("result", "tests", "failures", "errors", "skipped")}))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
