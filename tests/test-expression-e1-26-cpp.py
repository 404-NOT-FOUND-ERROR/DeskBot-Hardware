from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-expression-e1-26-cpp.py"


class ExpressionObserverE126CppTests(unittest.TestCase):
    def test_both_build_configurations_compile_and_run(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            output = Path(directory) / "cpp-report.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output)], cwd=ROOT,
                text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(report["audit_pass"])
        self.assertEqual({item["enabled"] for item in report["configurations"]}, {False, True})
        self.assertTrue(all(item["compile_pass"] and item["run_pass"]
                            for item in report["configurations"]))
        self.assertTrue(report["json_description_schema_valid"])
        self.assertFalse(report["safety"]["device_written"])

    def test_rejects_output_outside_workspace_artifact_scopes(self):
        output = ROOT / "e1-26-invalid-output.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(output)], cwd=ROOT,
            text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("inside artifacts/ or tmp/", result.stderr)


if __name__ == "__main__":
    unittest.main()
