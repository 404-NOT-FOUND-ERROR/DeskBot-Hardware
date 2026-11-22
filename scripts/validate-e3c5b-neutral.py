#!/usr/bin/env python3
"""Archive the staged neutral candidate's native or simulated-deployment tests."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PUBLIC = ROOT / "artifacts/studio-neutral-e3c5b-20261108"
PROJECT = Path("tmp/studio-stable-ladder-20261108/neutral/p")
BUILD = Path("artifacts/studio-stable-ladder-20261108/neutral-build.json")
COMPILED = tuple("main/boards/esp_vocat/" + name for name in (
    "studio_device_link.cc", "studio_device_link.h", "studio_display_readiness.h",
    "studio_expression_adapter.cc", "studio_expression_adapter.h", "studio_expression_control.h",
    "studio_expression_request.h", "studio_request_framer.h", "studio_runtime_query.h",
    "studio_runtime_snapshot.h", "studio_runtime_state.cc", "studio_runtime_state.h",
    "studio_usb_diagnostic.cc", "studio_usb_diagnostic.h", "studio_usb_session.h"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    names = {"scripts/validate-e3c5b-neutral.py", "scripts/test-expression-e1-26-cpp.py",
             "tests/cpp/studio_expression_control_test.cc", "tools/observe-studio-body-e3c.py",
             "artifacts/deskbot-studio-e2a-20261104/index.html",
             "artifacts/deskbot-studio-e3d1-20261105/installed-directory.json"}
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename:
            path = Path(filename).resolve()
            if path.is_relative_to(ROOT) and path.suffix == ".py":
                names.add(path.relative_to(ROOT).as_posix())
    return {name: digest(ROOT / name) for name in sorted(names)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("native", "deployment"))
    args = parser.parse_args()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    report_path = PUBLIC / (args.phase + "-validation.json")
    log_path = report_path.with_suffix(".log")
    if report_path.exists() or log_path.exists():
        raise FileExistsError("release evidence already exists: " + str(report_path))
    if args.phase == "native":
        os.environ["DESKBOT_NATIVE_SOURCE_ROOT"] = str(ROOT / PROJECT)
    # IDF Python also installs a third-party 'tests' package. Bind this suite's
    # local namespace explicitly before its cross-test fixture imports.
    package = ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    pattern = "test_studio_control_native.py" if args.phase == "native" else "test_e3c5b_deployment.py"
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
    source_sha = sources()
    compiled = {name: digest(ROOT / PROJECT / name) for name in COMPILED}
    with log_path.open("x", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    unchanged = sources() == source_sha and compiled == {name: digest(ROOT / PROJECT / name) for name in COMPILED}
    expected = 7 if args.phase == "native" else 20
    passed = result.wasSuccessful() and result.testsRun == expected and not result.skipped and unchanged
    report = dict(result="passed" if passed else "failed", tests=result.testsRun,
                  skipped=len(result.skipped), failures=len(result.failures), errors=len(result.errors),
                  source_unchanged=unchanged, hardware_accessed=False, device_written=False,
                  runtime=dict(executable=sys.executable, version=sys.version),
                  candidate_source=PROJECT.as_posix(), build_report=BUILD.as_posix(),
                  build_report_sha256=digest(ROOT / BUILD), source_sha256=source_sha,
                  compiled_source_sha256=compiled, log=log_path.relative_to(ROOT).as_posix(),
                  log_sha256=digest(log_path))
    with report_path.open("x", encoding="utf-8") as target:
        target.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(report=report_path.relative_to(ROOT).as_posix(), sha256=digest(report_path),
                          result=report["result"], tests=result.testsRun, skipped=len(result.skipped))))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
