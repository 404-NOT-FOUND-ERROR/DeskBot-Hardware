#!/usr/bin/env python3
"""Record layout, directory, playback and rollback tests using Offline Python."""
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
WORK = ROOT / "tmp/studio-layout-e3f4c-device"
PUBLIC = ROOT / "artifacts/studio-layout-e3f4c-device"
PROJECT = WORK / "p"
MODULES = (
    "test_studio_asset_layout_native", "test_studio_asset_layout_deployment",
    "test_studio_expression_layout", "test_studio_catalog_native", "test_studio_catalog_query",
    "test_studio_display_query", "test_studio_control_link", "test_studio_expression_control",
    "test_studio_source_config",
    "test_studio_play_workbench", "test_e3c3b_display_dma",
)


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
    layout = importlib.import_module("tests.test_studio_asset_layout_native")
    catalog = importlib.import_module("tests.test_studio_catalog_native")
    scheduler = importlib.import_module("tests.test_studio_expression_control")
    layout.BOARD = catalog.BOARD = scheduler.BOARD = PROJECT / "main/boards/esp_vocat"
    scheduler.MAIN = PROJECT / "main"
    suite = unittest.TestSuite()
    for name in MODULES:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module("tests." + name)))
    sources = {ROOT / "tests" / (name + ".py") for name in MODULES}
    sources.update((ROOT / "protocol").glob("studio*.py"))
    sources.update((ROOT / "protocol").glob("deskbot-studio-*.schema.json"))
    sources.update((ROOT / "tests/cpp").glob("studio*"))
    sources.update((ROOT / "tests/cpp/layout_stubs").rglob("*.h"))
    sources.update((ROOT / "tests/cpp/catalog_stubs").rglob("*.h"))
    sources.update(PROJECT / name for name in json.loads((PUBLIC / "firmware-build-report.json").read_bytes())["source_delta"])
    sources.update(ROOT / name for name in (
        "scripts/validate-studio-layout-e3f4c.py", "scripts/deploy-studio-layout-e3f4c.py",
        "scripts/build-studio-layout-e3f4c.py", "scripts/build-deskbot-studio-e3d.py",
        "scripts/build-deskbot-studio-e3f4b.py", "scripts/deskbot-studio-e3c.js",
        "scripts/deskbot-studio-play-readiness.js", "tests/studio-layout-device-browser.cjs",
        "tests/studio_source_config.py",
        "tests/serve_studio_layout_fixture.py", "artifacts/studio-layout-e3f4c-device/expected-directory.json",
    ))
    sources = sorted(p for p in sources if p.is_file())
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}
    WORK.mkdir(exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    log = WORK / f"offline-tests-{args.attempt}.log"
    output = PUBLIC / f"offline-validation-{args.attempt}.json"
    if log.exists() or output.exists():
        raise FileExistsError("validation attempt already retained")
    started = time.monotonic()
    with log.open("x", encoding="utf-8") as stream, redirect_stderr(stream), redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    unchanged = before == {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}
    browser_path = PUBLIC / "browser/browser-validation.json"
    browser = json.loads(browser_path.read_bytes())
    browser_valid = browser.get("result") == "passed" and browser.get("checks_passed", 0) >= 19
    browser_valid = browser_valid and all(sha(ROOT / n) == digest for n, digest in browser["source_sha256"].items())
    passed = result.wasSuccessful() and result.testsRun >= 30 and not result.skipped and unchanged and browser_valid
    report = dict(schema="deskbot.studio_asset_layout_validation/v1", result="passed" if passed else "failed",
        tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        elapsed_seconds=round(time.monotonic()-started, 3), modules=list(MODULES), source_sha256=before,
        source_unchanged=unchanged, candidate_source=PROJECT.relative_to(ROOT).as_posix(), runtime=sys.version,
        log=log.relative_to(ROOT).as_posix(), log_sha256=sha(log), browser_checks=browser["checks_passed"],
        browser_report_sha256=sha(browser_path), browser_sources_verified=browser_valid,
        hardware_accessed=False, device_written=False)
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output.write_bytes(raw)
    if passed:
        (PUBLIC / "offline-validation.json").write_bytes(raw)
    print(json.dumps({k: report[k] for k in ("result", "tests_run", "failures", "errors", "skipped", "browser_checks")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
