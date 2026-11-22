#!/usr/bin/env python3
"""Record host, HTTP and native role-layout regressions without serial I/O."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / "tmp/studio-role-layout-e3f6"
PUBLIC = ROOT / "artifacts/studio-role-layout-e3f6"
MODULES = ("test_studio_role_layout_e3f6", "test_studio_role_layout_native_e3f6",
           "test_studio_role_expression", "test_studio_role_overview", "test_studio_control_link",
           "test_studio_display_query", "test_studio_catalog_query", "test_studio_timing")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("positive attempt required")
    frozen = ROOT / "tmp/studio-shy-e3f5/p"
    if not frozen.is_dir():
        raise FileNotFoundError("E3f-5 frozen source required for native checks")
    package = ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    os.environ["DESKBOT_NATIVE_SOURCE_ROOT"] = str(frozen)
    native = importlib.import_module("tests.test_studio_expression_control")
    native.MAIN, native.BOARD = frozen / "main", frozen / "main/boards/esp_vocat"
    display = importlib.import_module("tests.test_studio_display_query")
    display.MAIN, display.BOARD = native.MAIN, native.BOARD
    layout = importlib.import_module("tests.test_studio_asset_layout_native")
    layout.BOARD = native.BOARD
    catalog = importlib.import_module("tests.test_studio_catalog_native")
    catalog.BOARD = native.BOARD
    names = {"protocol/studio_role_layout.py", "protocol/studio_role_expression.py", "protocol/studio_role_overview.py",
             "protocol/studio_resource_package.py", "protocol/studio_installed_catalog.py",
             "protocol/expression-layout-requirements-v1.json", "scripts/build-deskbot-studio-e3f6.py",
             "scripts/serve-deskbot-studio-e3f6.py", "scripts/serve-deskbot-studio-e3e.py",
             "scripts/serve-deskbot-studio-e3f2.py", "scripts/deskbot-studio-e3f6.js",
             "scripts/deskbot-studio-role-owner-e3f6.js", "scripts/deskbot-studio-play-readiness.js",
             "tests/serve_studio_role_layout_e3f6_fixture.py", "tests/studio-role-layout-e3f6-browser.cjs",
             "scripts/validate-studio-role-layout-e3f6.py", "scripts/start-deskbot-studio-e3f6.ps1",
             "docs/role-node-layout-e3f6.md", "artifacts/studio-resources-e3f4d/expected-directory.json",
             "tmp/deskbot-studio-e3f6-20261110/index.html",
             "tests/test_studio_shy_e3f5.py", "tests/test_studio_control_native.py",
             "tests/test_studio_expression_control.py", "tests/test_studio_asset_layout_native.py",
             "tests/test_studio_reflow_e3f4e.py", "tests/test_studio_catalog_native.py",
             "tests/test_deskbot_studio_e3e.py", "tests/studio_source_config.py",
             "tests/serve_studio_play_fixture.py", "tests/serve_studio_e3e_fixture.py",
             "tests/studio-play-readiness.test.cjs", "scripts/test-expression-e1-26-cpp.py",
             "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin",
             "tmp/expression-e1-45-staging-20261104/rollback-reference-e1-18-assets-8m.bin",
             *('tests/'+n+'.py' for n in MODULES)}
    # Freeze the generated edition and its inherited dependencies with the native source.
    product_report = "artifacts/deskbot-studio-e3f6-20261110/report.json"
    names.add(product_report)
    names.update(row["path"] for row in json.loads((ROOT / product_report).read_bytes())["sources"])
    names.add((native.MAIN / "device_state.h").relative_to(ROOT).as_posix())
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests/cpp").rglob("*") if p.is_file())
    names.update(p.relative_to(ROOT).as_posix() for p in native.BOARD.glob("studio*") if p.is_file())
    names.update(p.relative_to(ROOT).as_posix() for p in (frozen / "main/boards/esp_vocat").glob("studio*") if p.is_file())
    before = {name: sha(ROOT / name) for name in sorted(names)}
    modules = [importlib.import_module("tests." + name) for name in MODULES]
    # Loading this module via unittest also discovers the imported parent class.
    # Select only classes defined in the module, keeping native coverage explicit.
    suite = unittest.TestSuite()
    for module in modules:
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ == module.__name__:
                suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(value))
    WORK.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    log, target = WORK / f"tests-{args.attempt}.log", PUBLIC / f"offline-validation-{args.attempt}.json"
    if log.exists() or target.exists():
        raise FileExistsError("retained run; choose a new attempt")
    started = time.monotonic()
    node = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    with log.open("x", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        stream.flush()
        js = subprocess.run([str(node), "--test", "--test-reporter=spec", "tests/studio-play-readiness.test.cjs"],
                            cwd=ROOT, stdout=stream, stderr=stream)
    unchanged = before == {name: sha(ROOT / name) for name in before}
    browser_path = WORK / "browser/validation.json"
    browser = json.loads(browser_path.read_bytes())
    passed = result.wasSuccessful() and not result.skipped and js.returncode == 0 and unchanged and browser.get("result") == "passed"
    report = dict(result="passed" if passed else "failed", tests_run=result.testsRun,
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        failed_cases=[case.id() for case, _ in result.failures+result.errors], node_tests=19, node_exit=js.returncode,
        browser_checks=browser["checks_passed"], browser_validation=browser_path.relative_to(ROOT).as_posix(),
        browser_validation_sha256=sha(browser_path), source_sha256=before, source_unchanged=unchanged,
        native_source=frozen.relative_to(ROOT).as_posix(),
        modules=MODULES, elapsed_seconds=round(time.monotonic()-started, 3), runtime=sys.version,
        log=log.relative_to(ROOT).as_posix(), log_sha256=sha(log), hardware_accessed=False, device_written=False)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("result", "tests_run", "failures", "errors", "skipped", "node_tests", "failed_cases")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
