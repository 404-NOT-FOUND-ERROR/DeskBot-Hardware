#!/usr/bin/env python3
"""Record common-interface regressions against the actual frozen candidate."""
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
WORK, PUBLIC = ROOT / "tmp/studio-common-e3f7", ROOT / "artifacts/studio-common-e3f7"
PROJECT = WORK / "p"
MODULES = ("test_studio_common_e3f7", "test_studio_shy_e3f5", "test_studio_role_layout_e3f6",
    "test_studio_role_layout_native_e3f6", "test_studio_control_link", "test_studio_display_query",
    "test_studio_role_expression", "test_studio_catalog_query", "test_e3c3b_display_dma", "test_studio_timing")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("positive attempt required")
    if not PROJECT.is_dir():
        raise FileNotFoundError("prepare E3f-7 frozen project first")
    package = ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    os.environ["DESKBOT_NATIVE_SOURCE_ROOT"] = str(PROJECT)
    native = importlib.import_module("tests.test_studio_expression_control")
    native.MAIN, native.BOARD = PROJECT / "main", PROJECT / "main/boards/esp_vocat"
    for name in ("test_studio_display_query",):
        module = importlib.import_module("tests." + name)
        module.MAIN, module.BOARD = native.MAIN, native.BOARD
    for name in ("test_studio_asset_layout_native", "test_studio_catalog_native"):
        importlib.import_module("tests." + name).BOARD = native.BOARD
    names = {Path(__file__).relative_to(ROOT).as_posix(), "scripts/build-studio-common-e3f7.py",
        "artifacts/deskbot-studio-e3f7-20261110/report.json",
        "tmp/deskbot-studio-e3f7-20261110/index.html", "artifacts/deskbot-studio-e3f7-20261110/index.html",
        "artifacts/studio-shy-e3f5/firmware-build-report.json",
        "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin",
        "scripts/test-expression-e1-26-cpp.py", "tests/studio_source_config.py"}
    product = json.loads((ROOT / "artifacts/deskbot-studio-e3f7-20261110/report.json").read_bytes())
    names.update(r["path"] for r in product["sources"])
    for folder in (ROOT / "protocol", ROOT / "tests"):
        names.update(p.relative_to(ROOT).as_posix() for p in folder.glob("*studio*.py"))
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "protocol").glob("*.schema.json"))
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests/cpp").rglob("*") if p.is_file())
    names.update(p.relative_to(ROOT).as_posix() for p in native.BOARD.glob("studio*") if p.is_file())
    names.add((PROJECT / "main/Kconfig.projbuild").relative_to(ROOT).as_posix())
    before = {name: sha(ROOT / name) for name in sorted(names)}
    modules = [importlib.import_module("tests." + name) for name in MODULES]
    suite = unittest.TestSuite()
    for module in modules:
        for cls in vars(module).values():
            if isinstance(cls, type) and issubclass(cls, unittest.TestCase) and cls.__module__ == module.__name__:
                suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    WORK.mkdir(parents=True, exist_ok=True); PUBLIC.mkdir(parents=True, exist_ok=True)
    log, target = WORK / f"tests-{args.attempt}.log", PUBLIC / f"offline-validation-{args.attempt}.json"
    if log.exists() or target.exists():
        raise FileExistsError("retained validation exists; use a new attempt")
    node = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    if not node.is_file():
        raise FileNotFoundError("bundled Node runtime required")
    started = time.monotonic()
    with log.open("x", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        stream.flush()
        js = subprocess.run([str(node), "--test", "--test-reporter=spec", "tests/studio-play-readiness.test.cjs"],
                            cwd=ROOT, stdout=stream, stderr=stream)
    unchanged = before == {name: sha(ROOT / name) for name in before}
    browser_path = WORK / "browser/validation.json"
    browser = json.loads(browser_path.read_bytes())
    passed = result.wasSuccessful() and not result.skipped and unchanged and js.returncode == 0 and browser["result"] == "passed"
    report = dict(schema="deskbot.studio_common_e3f7_validation/v1", result="passed" if passed else "failed",
        tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        failed_cases=[case.id() for case, _ in result.failures+result.errors], modules=MODULES,
        node_tests=19, node_exit=js.returncode, browser_checks=browser["checks_passed"],
        browser_validation=browser_path.relative_to(ROOT).as_posix(), browser_validation_sha256=sha(browser_path),
        source_sha256=before, source_unchanged=unchanged, candidate_source=PROJECT.relative_to(ROOT).as_posix(),
        elapsed_seconds=round(time.monotonic()-started, 3), log=log.relative_to(ROOT).as_posix(), log_sha256=sha(log),
        runtime=sys.version, hardware_accessed=False, device_written=False)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("result", "tests_run", "failures", "errors", "skipped", "browser_checks", "failed_cases")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
