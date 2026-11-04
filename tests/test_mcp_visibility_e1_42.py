from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit-mcp-visibility-e1-42.py"


class McpVisibilityE142Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="deskbot-mcp-visibility-")
        report = Path(cls.temp_dir.name) / "report.json"
        subprocess.run([sys.executable, str(SCRIPT), "--output", str(report)], check=True)
        cls.data = json.loads(report.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_target_tools_are_ordinary_and_registered_first(self) -> None:
        registration = self.data["registration"]
        self.assertEqual(registration["dev_tools_registration_order"], self.data["target_tools"])
        self.assertTrue(registration["ordinary_add_tool"])
        self.assertTrue(registration["ordinary_tools_pass_default_user_only_filter"])

    def test_pagination_contract_is_explicit(self) -> None:
        registration = self.data["registration"]
        self.assertEqual(registration["payload_limit_bytes"], 8000)
        self.assertTrue(registration["next_cursor_supported"])
        self.assertTrue(registration["cursor_is_first_unreturned_tool"])
        self.assertFalse(registration["pages_overlap_at_cursor"])

    def test_static_audit_does_not_claim_startup_or_host_evidence(self) -> None:
        self.assertFalse(self.data["startup_log"]["provided"])
        host = self.data["host_evidence"]
        self.assertFalse(host["tools_list_pages"])
        self.assertFalse(host["target_tools_visible"])
        self.assertFalse(host["raw_tools_call"])
        self.assertEqual(host["status"], "pending")


if __name__ == "__main__":
    unittest.main()
