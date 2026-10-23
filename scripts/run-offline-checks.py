#!/usr/bin/env python3
"""Run the explicitly scoped portable tests used by the public repository."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FILES = (
    "base_event_protocol_test.py", "test-expression-capabilities.py",
    "test-expression-preview.py", "test_printf_portability_guard.py",
    "test_body_facts_consumer.py", "test_body_facts_protocol.py",
    "test_eaf_compression.py", "test_eaf_encode.py", "test_eaf_preview.py",
    "test_magnetic_recognition.py", "test_magnetic_telemetry.py",
    "test_o2a_semantic_mapping.py", "test_o2b_event_journal.py", "test_role_profile.py",
)


def main() -> int:
    import jsonschema  # Schema coverage must not silently disappear.
    suite = unittest.TestSuite()
    for index, name in enumerate(FILES):
        spec = importlib.util.spec_from_file_location(f"public_tests_{index}", ROOT / "tests" / name)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load test file: " + name)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"Portable scope: {result.testsRun} tests, {len(result.skipped)} skipped; no hardware or full firmware build.")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
