#!/usr/bin/env python3
"""Test the frozen E4e + shy configuration without opening a serial port."""
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
WORK, PUBLIC = ROOT / "tmp/studio-shy-e3f5", ROOT / "artifacts/studio-shy-e3f5"
PROJECT = WORK / "p"
MODULES = ("test_studio_shy_e3f5", "test_studio_control_link", "test_studio_display_query",
           "test_studio_reflow_e3f4e", "test_studio_catalog_native", "test_e3c3b_display_dma", "test_studio_timing")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("positive attempt required")
    if not PROJECT.is_dir():
        raise FileNotFoundError("prepare the frozen E3f-5 build project first")
    package = ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    os.environ["DESKBOT_NATIVE_SOURCE_ROOT"] = str(PROJECT)
    native = importlib.import_module("tests.test_studio_expression_control")
    native.MAIN, native.BOARD = PROJECT / "main", PROJECT / "main/boards/esp_vocat"
    layout = importlib.import_module("tests.test_studio_asset_layout_native")
    catalog = importlib.import_module("tests.test_studio_catalog_native")
    layout.BOARD = catalog.BOARD = native.BOARD
    display = importlib.import_module("tests.test_studio_display_query")
    display.MAIN, display.BOARD = native.MAIN, native.BOARD
    modules = [importlib.import_module("tests." + name) for name in MODULES]
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in modules)
    paths = {Path(__file__), ROOT / "scripts/build-studio-shy-e3f5.py",
             ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json",
             ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin"}
    for folder in (ROOT / "protocol", ROOT / "tests"):
        paths.update(p for p in folder.glob("*.py") if "studio" in p.name)
    paths.update((ROOT / "tests").glob("studio*.cjs"))
    paths.update((ROOT / "tests/cpp").rglob("*"))
    paths.update((PROJECT / "main/boards/esp_vocat").glob("studio*"))
    paths.update((ROOT / "scripts").glob("deskbot-studio*.js"))
    paths.add(ROOT / "tests/serve_studio_shy_e3f5_fixture.py")
    paths.add(PROJECT / "main/Kconfig.projbuild")
    paths = sorted(p for p in paths if p.is_file())
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    WORK.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    log, output = WORK / f"offline-tests-{args.attempt}.log", PUBLIC / f"offline-validation-{args.attempt}.json"
    if log.exists() or output.exists():
        raise FileExistsError("run retained; choose a new attempt")
    started = time.monotonic()
    with log.open("x", encoding="utf-8") as stream, redirect_stderr(stream), redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    unchanged = before == {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    passed = result.wasSuccessful() and result.testsRun > 150 and not result.skipped and unchanged
    report = dict(schema="deskbot.studio_shy_e3f5_validation/v1", result="passed" if passed else "failed",
                  tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped), failed_cases=[case.id() for case, _ in result.failures + result.errors],
                  modules=list(MODULES), elapsed_seconds=round(time.monotonic() - started, 3),
                  source_sha256=before, source_unchanged=unchanged,
                  candidate_source=PROJECT.relative_to(ROOT).as_posix(), log=log.relative_to(ROOT).as_posix(),
                  log_sha256=sha(log), runtime=sys.version, hardware_accessed=False, device_written=False)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("result", "tests_run", "failures", "errors", "skipped", "failed_cases")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
