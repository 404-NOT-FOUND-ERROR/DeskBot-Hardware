"""Run offline native/transaction regressions using the host Python with jsonschema."""
import argparse
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    # ESP-IDF's Python is for builds/serial tools. Host tests use Python 3.13.
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        raise SystemExit(r'Use D:\Program Files (x86)\Python\Python 3.13.0\python.exe for offline tests')
    package = types.ModuleType("tests")
    package.__path__ = [str(ROOT / "tests")]
    sys.modules["tests"] = package
    names = ("test_head_touch_wifi", "test_wifi_expression", "test_body_wifi", "test_wifi_trial_deployment",
             "test_body_network_settings", "test_body_credentials", "test_body_network_workbench", "test_studio_usb_session")
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName("tests." + name) for name in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(dict(tests=result.testsRun, failures=len(result.failures),
            errors=len(result.errors), passed=result.wasSuccessful(), hardware_accessed=False,
            modules=list(names), python=sys.executable), indent=2) + "\n", encoding="utf-8")
    sys.exit(not result.wasSuccessful())
