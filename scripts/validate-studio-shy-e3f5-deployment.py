#!/usr/bin/env python3
"""Record shy App deployment tests against an in-memory device."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULES = ("test_studio_shy_e3f5_deployment", "test_studio_app_deployment")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("positive attempt required")
    package = ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    from protocol import studio_shy_e3f5_deployment as release
    public, work = ROOT / "artifacts/studio-shy-e3f5", ROOT / "tmp/studio-shy-e3f5"
    target = public / f"deployment-validation-{args.attempt}.json"
    log = work / f"deployment-tests-{args.attempt}.log"
    if target.exists() or log.exists():
        raise FileExistsError("retained validation; select a new attempt")
    sources = {"protocol/studio_shy_e3f5_deployment.py", "scripts/deploy-studio-shy-e3f5.py",
        "scripts/validate-studio-shy-e3f5-deployment.py", "tests/test_studio_shy_e3f5_deployment.py",
        "tests/test_studio_app_deployment.py", "tests/test_e3d2b_deployment.py",
        "protocol/studio_app_deployment.py", "protocol/studio_reflow_deployment.py",
        "scripts/deploy-studio-eat.py", "scripts/deploy-deskbot-studio-e3c3a.py",
        "scripts/deploy-deskbot-studio-e3c.py", "scripts/deploy-deskbot-studio-e3b.py",
        release.SOURCE.as_posix(), release.BUILD_REPORT.as_posix(), release.OFFLINE_REPORT.as_posix(),
        release.PEER_REPORT.as_posix(), release.ACCEPTANCE.as_posix(),
        (release.REFERENCE / "plan.json").as_posix(), (release.REFERENCE / "install-result.json").as_posix()}
    sources.update(json.loads((ROOT / release.OFFLINE_REPORT).read_bytes())["source_sha256"])
    before = {name:sha(ROOT / name) for name in sorted(sources)}
    suite = unittest.TestSuite()
    for name in MODULES:
        module = importlib.import_module("tests." + name)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    started = time.monotonic()
    with log.open("x", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    unchanged = before == {name:sha(ROOT / name) for name in before}
    passed = result.wasSuccessful() and not result.skipped and unchanged
    report = dict(result="passed" if passed else "failed", tests_run=result.testsRun,
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        failed_cases=[case.id() for case, _ in result.failures + result.errors],
        source_sha256=before, source_unchanged=unchanged, modules=MODULES,
        log=log.relative_to(ROOT).as_posix(), log_sha256=sha(log),
        elapsed_seconds=round(time.monotonic()-started, 3), runtime=sys.version,
        device_boundary="memory", hardware_accessed=False, device_written=False)
    with target.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({k:report[k] for k in ("result", "tests_run", "failures", "errors", "skipped", "failed_cases")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
