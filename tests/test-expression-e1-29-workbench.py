from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build-expression-e1-29-capture.py"
SPEC = importlib.util.spec_from_file_location("e1_29_workbench_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


class ExpressionE129WorkbenchTests(unittest.TestCase):
    def test_synthetic_capture_round_trips_through_exporter(self):
        raw = (json.dumps(BUILDER.sample_capture(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        exporter = BUILDER._load_exporter()
        snapshot, provenance = exporter.normalize_capture(raw)
        self.assertEqual(len(snapshot["records"]), 2)
        self.assertEqual(snapshot["records"][0]["semantic"], "happy")
        self.assertEqual(provenance["source"]["request_id"], 29)
        self.assertIs(provenance["safety"]["device_written"], False)

    def test_page_explains_all_layers_and_keeps_offline_boundary(self):
        BUILDER.build()
        page = (BUILDER.OUT / "index.html").read_text(encoding="utf-8")
        report = json.loads((BUILDER.OUT / "report.json").read_text(encoding="utf-8"))
        for token in ("data-step=\"host\"", "data-step=\"mcp\"", "data-step=\"rpc\"",
                      "data-step=\"text\"", "data-step=\"snapshot\"", "e129-data",
                      "connect-src 'none'", "合成样例", "不连接设备", "SetEmotion", "session_id"):
            self.assertIn(token, page)
        for key in ("hardware_accessed", "serial_accessed", "device_written", "nvs_changed", "flash_authorized"):
            self.assertIs(report["boundaries"][key], False)
        self.assertIs(report["real_device_capture_available"], False)
        self.assertNotRegex(page, r"\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(")

    def test_check_mode_rejects_stale_generated_output(self):
        BUILDER.build()
        path = BUILDER.OUT / "README.txt"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"stale")
            with self.assertRaisesRegex(ValueError, "stale generated artifact"):
                BUILDER.build(check=True)
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
