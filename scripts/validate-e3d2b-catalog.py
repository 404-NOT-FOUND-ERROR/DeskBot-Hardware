#!/usr/bin/env python3
"""Record tests against the staged catalog firmware and its deployment entry."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import json
from pathlib import Path
import os
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PUBLIC = ROOT / "artifacts/studio-catalog-e3d2b-20261108"
PROJECT = Path("tmp/studio-stable-ladder-20261108/catalog/p")
BUILD = Path("artifacts/studio-stable-ladder-20261108/catalog-build.json")
COMPILED = ("main/assets.cc",) + tuple("main/boards/esp_vocat/" + name for name in (
    "studio_device_link.cc", "studio_device_link.h", "studio_display_readiness.h",
    "studio_expression_adapter.cc", "studio_expression_adapter.h", "studio_expression_control.h",
    "studio_expression_request.h", "studio_request_framer.h", "studio_runtime_query.h",
    "studio_runtime_snapshot.h", "studio_runtime_state.cc", "studio_runtime_state.h",
    "studio_usb_diagnostic.cc", "studio_usb_diagnostic.h", "studio_usb_session.h",
    "studio_catalog.h", "studio_catalog_actions.h", "studio_catalog_builder.cc",
    "studio_catalog_adapter.h", "studio_catalog_adapter.cc"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    names = {"scripts/validate-e3d2b-catalog.py", "scripts/test-expression-e1-26-cpp.py",
             "scripts/audit-expression-package-candidate.py", "scripts/audit-expression-resources.py",
             "tests/cpp/studio_expression_control_test.cc", "tests/cpp/studio_catalog_test.cc",
             "protocol/deskbot-studio-catalog-page-v1.schema.json",
             "firmware/body/main/display/emote_display.cc",
             "tmp/expression-e1-45-staging-20261104/rollback-reference-e1-18-assets-8m.bin",
             "tools/observe-studio-body-e3c.py",
             "artifacts/deskbot-studio-e2a-20261104/index.html",
             "artifacts/deskbot-studio-e3d1-20261105/installed-directory.json"}
    component = "tmp/e3d2-catalog-20261105/p/managed_components/espressif2022__esp_vocat_base_control/include/vocat_base_control.h"
    if (ROOT / component).is_file():
        names.add(component)
    for folder in ("tests/cpp/catalog_stubs", "tests/vendor/studio_catalog"):
        names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / folder).rglob("*") if p.is_file())
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
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("attempt must be positive")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.attempt == 1 else "-" + str(args.attempt)
    report_path = PUBLIC / (args.phase + "-validation" + suffix + ".json")
    log_path = report_path.with_suffix(".log")
    if report_path.exists() or log_path.exists():
        raise FileExistsError("release evidence already exists")
    package = ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    if args.phase == "native":
        os.environ["DESKBOT_NATIVE_SOURCE_ROOT"] = str(ROOT / PROJECT)
        catalog = importlib.import_module("tests.test_studio_catalog_native")
        # Use the frozen catalog candidate for both the builder and USB endpoint.
        catalog.BOARD = ROOT / PROJECT / "main/boards/esp_vocat"
        control = importlib.import_module("tests.test_studio_control_native")
        # Resource tests load this parser lazily. Load it before the inventory so
        # an added module is not mistaken for an edited source file.
        catalog.packages.load_legacy_tools()
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module)
                                   for module in (catalog, control))
    else:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_e3d2b_deployment.py")
    before = sources()
    compiled = {name: digest(ROOT / PROJECT / name) for name in COMPILED}
    with log_path.open("x", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    after = sources()
    changed = sorted(name for name in before.keys() & after.keys() if before[name] != after[name])
    inventory_delta = dict(added=sorted(after.keys() - before.keys()), removed=sorted(before.keys() - after.keys()), changed=changed)
    unchanged = before == after and compiled == {name: digest(ROOT / PROJECT / name) for name in COMPILED}
    passed = result.wasSuccessful() and result.testsRun == 20 and not result.skipped and unchanged
    report = dict(result="passed" if passed else "failed", tests=result.testsRun,
                  skipped=len(result.skipped), failures=len(result.failures), errors=len(result.errors),
                  source_unchanged=unchanged, hardware_accessed=False, device_written=False,
                  source_inventory_delta=inventory_delta,
                  candidate_source=PROJECT.as_posix(), build_report=BUILD.as_posix(),
                  build_report_sha256=digest(ROOT / BUILD), source_sha256=before,
                  compiled_source_sha256=compiled, log=log_path.relative_to(ROOT).as_posix(),
                  log_sha256=digest(log_path), runtime=dict(python=sys.version.split()[0]))
    with report_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(result=report["result"], tests=result.testsRun, sha256=digest(report_path))))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
