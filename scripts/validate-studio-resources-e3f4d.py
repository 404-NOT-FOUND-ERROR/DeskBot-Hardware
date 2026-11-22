#!/usr/bin/env python3
"""Resource transaction/CLI regressions and native E1.45 catalog/layout checks."""
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
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
WORK = ROOT / "tmp/studio-resources-e3f4d"
PUBLIC = ROOT / "artifacts/studio-resources-e3f4d"
MODULES = ("test_studio_resource_installation", "test_studio_resource_adapter",
           "test_studio_resource_e3d2b", "test_studio_resource_e3d2b_adapter",
           "test_studio_resource_e3f4c", "test_studio_resource_e3f4c_native")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    package = ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    native = importlib.import_module("tests.test_studio_asset_layout_native")
    native.BOARD = ROOT / "tmp/studio-layout-e3f4c-device/p/main/boards/esp_vocat"
    suite = unittest.TestSuite()
    for name in MODULES:
        module = importlib.import_module(name)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    sources = {ROOT / "tests" / (name + ".py") for name in MODULES}
    sources.update((ROOT / "protocol").glob("studio_resource*.py"))
    sources.update(ROOT / "scripts" / name for name in (
        "deploy-studio-resources.py", "deploy-e3d2b-resources.py", "deploy-e3f4c-resources.py",
        "validate-studio-resources-e3f4d.py"))
    sources.update(ROOT / "tests" / name for name in (
        "test_studio_asset_layout_native.py", "cpp/studio_asset_layout_test.cc", "cpp/studio_layout_gfx_mock.cc"))
    sources.update(native.BOARD / name for name in (
        "studio_asset_layout.cc", "studio_asset_layout.h", "studio_catalog_adapter.cc", "studio_catalog_builder.cc"))
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(sources)}
    WORK.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    log = WORK / f"offline-tests-{args.attempt}.log"
    output = PUBLIC / f"offline-validation-{args.attempt}.json"
    if log.exists() or output.exists():
        raise FileExistsError("choose a new validation attempt")
    started = time.monotonic()
    with log.open("x", encoding="utf-8") as stream, redirect_stderr(stream), redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    unchanged = before == {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(sources)}
    passed = result.wasSuccessful() and result.testsRun >= 90 and not result.skipped and unchanged
    report = dict(schema="deskbot.studio_resource_e3f4d_validation/v1", result="passed" if passed else "failed",
        tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        elapsed_seconds=round(time.monotonic()-started, 3), modules=MODULES, source_sha256=before,
        source_unchanged=unchanged, runtime=sys.version, log=log.relative_to(ROOT).as_posix(), log_sha256=sha(log),
        hardware_accessed=False, device_written=False)
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    output.write_bytes(raw)
    if passed:
        (PUBLIC / "offline-validation.json").write_bytes(raw)
    print(json.dumps({k: report[k] for k in ("result", "tests_run", "failures", "errors", "skipped")}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
