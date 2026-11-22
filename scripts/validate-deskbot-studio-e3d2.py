#!/usr/bin/env python3
"""Run or recheck E3d-2 host/native tests; --full selects the portable suite."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PUBLIC = ROOT / "artifacts/deskbot-studio-e3d2-20261105"
FILES = ("test_studio_catalog_query.py", "test_studio_catalog_native.py",
         "test_deskbot_studio_e3d2.py", "test_publication_evidence.py")


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def inputs(files):
    names = {"scripts/run-offline-checks.py", *("tests/" + name for name in files)}
    names.update(row["path"] for row in json.loads((PUBLIC / "report.json").read_bytes())["sources"])
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "protocol").glob("*")
                 if path.suffix in (".py", ".json"))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def summary(result, files, sources, scope):
    return dict(edition="E3d-2", result="passed" if result.wasSuccessful() else "failed", scope=scope,
        tests_run=result.testsRun, passed=result.testsRun - len(result.skipped) - len(result.failures) - len(result.errors),
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        skip_reasons=sorted({reason for _, reason in result.skipped}), files=files,
        source_sha256=sources, hardware_accessed=False, device_written=False,
        firmware_built=False, physical_acceptance="pending")


def save(name, data):
    (PUBLIC / name).write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = module("e3d2_portable_runner", "scripts/run-offline-checks.py").FILES if args.full else FILES
    sources = inputs(files)
    output = "full-offline-validation.json" if args.full else "offline-validation.json"
    if args.check:
        report = json.loads((PUBLIC / output).read_bytes())
        if (report["source_sha256"] != sources or report["result"] != "passed"
                or report["tests_run"] != sum(report["files"].values())
                or report["tests_run"] != report["passed"] + report["skipped"]
                or report["failures"] or report["errors"]):
            raise ValueError("E3d-2 validation inputs or results changed")
        print("E3d-2 " + output + " checked")
        return 0
    counts, native = {}, None
    combined = unittest.TestResult()
    for index, name in enumerate(files):
        loaded = module(f"e3d2_validation_{index}", "tests/" + name)
        suite = unittest.defaultTestLoader.loadTestsFromModule(loaded)
        counts[name] = suite.countTestCases()
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        combined.testsRun += result.testsRun
        for field in ("failures", "errors", "skipped"):
            getattr(combined, field).extend(getattr(result, field))
        if name == "test_studio_catalog_native.py":
            native = summary(result, {name: counts[name]}, sources, "production_cpp_catalog")
    if sources != inputs(files):
        raise ValueError("source changed during validation")
    report = summary(combined, counts, sources, "portable_suite" if args.full else "catalog_host_and_native")
    save(output, report)
    if native is not None and not args.full:
        save("native-validation.json", native)
    print(json.dumps({key: report[key] for key in ("result", "tests_run", "passed", "skipped", "failures", "errors")}))
    return 0 if combined.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
