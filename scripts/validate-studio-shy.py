#!/usr/bin/env python3
"""Record host, production C++ scheduler and HTTP tests without serial access."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / "tmp/studio-shy-e3f1"
PUBLIC = ROOT / "artifacts/studio-shy-e3f1"
PROJECT = WORK / "p"
MODULES = ("test_studio_shy", "test_studio_control_link", "test_studio_expression_control",
           "test_studio_role_expression", "test_deskbot_studio_e3e", "test_studio_display_query",
           "test_studio_catalog_query", "test_studio_catalog_native", "test_studio_timing")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("attempt must be positive")
    package = ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    os.environ["DESKBOT_NATIVE_SOURCE_ROOT"] = str(PROJECT)
    native = importlib.import_module("tests.test_studio_expression_control")
    native.MAIN, native.BOARD = PROJECT / "main", PROJECT / "main/boards/esp_vocat"
    catalog = importlib.import_module("tests.test_studio_catalog_native")
    catalog.BOARD = native.BOARD
    modules = [importlib.import_module("tests." + name) for name in MODULES]
    catalog.packages.load_legacy_tools()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module) for module in modules)
    names = set()
    for directory in (ROOT / "protocol", ROOT / "tests", ROOT / "scripts"):
        names.update(p for p in directory.glob("*.py") if "studio" in p.name or p.name == "test_studio_shy.py")
    names.update(PROJECT / "main/boards/esp_vocat" / n for n in (
        "studio_expression_control.h", "studio_expression_adapter.cc", "studio_display_readiness.h",
        "studio_usb_diagnostic.cc", "studio_usb_session.h"))
    names.update(ROOT / "tests/cpp" / n for n in ("studio_expression_control_test.cc", "studio_catalog_test.cc"))
    names.update(ROOT / "protocol" / n for n in ("deskbot-studio-control-link-v1.schema.json", "deskbot-studio-display-v1.schema.json"))
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(names)}
    WORK.mkdir(exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    log_path = WORK / f"offline-tests-{args.attempt}.log"
    report_path = PUBLIC / f"offline-validation-{args.attempt}.json"
    if log_path.exists() or report_path.exists():
        raise FileExistsError("retained validation already exists")
    started = time.monotonic()
    with log_path.open("x", encoding="utf-8") as log, redirect_stderr(log), redirect_stdout(log):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    unchanged = before == {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(names)}
    passed = result.wasSuccessful() and result.testsRun > 185 and not result.skipped and unchanged
    report = dict(result="passed" if passed else "failed", tests=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped), elapsed_seconds=round(time.monotonic()-started, 3),
                  modules=list(MODULES), source_sha256=before, source_unchanged=unchanged,
                  candidate_source=PROJECT.relative_to(ROOT).as_posix(), runtime=sys.version,
                  log=log_path.relative_to(ROOT).as_posix(), log_sha256=sha(log_path),
                  hardware_accessed=False, device_written=False)
    with report_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({k: report[k] for k in ("result", "tests", "failures", "errors", "skipped")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
