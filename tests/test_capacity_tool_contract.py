from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "firmware" / "body" / "main" / "boards" / "esp_vocat" / "dev_tools.cc"


class CapacityToolContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SOURCE.read_text(encoding="utf-8")

    def test_capacity_tool_is_ai_visible_and_bounded(self):
        self.assertIn('mcp_server.AddTool("self.get_flash_capacity"', self.source)
        self.assertIn('"flash_size"', self.source)
        self.assertIn('"partition_table"', self.source)
        self.assertIn('"mmap_verified"', self.source)
        self.assertIn('"upper_assets_verified"', self.source)
        self.assertIn('SystemInfo::GetFlashSize()', self.source)
        self.assertIn('esp_partition_find', self.source)
        self.assertIn('cJSON_GetArraySize(partitions) == 0', self.source)
        self.assertIn("capacity_report_unavailable", self.source)

    def test_capacity_helper_does_not_build_sensitive_system_info(self):
        match = re.search(
            r"std::string BuildFlashCapacityReport\(\) \{(?P<body>.*?)\n\}",
            self.source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        for sensitive_field in ("mac_address", "uuid", "elf_sha256", "application"):
            self.assertNotIn(sensitive_field, body)
        self.assertNotIn("GetSystemInfoJson", body)
        self.assertIn('"label"', body)
        self.assertIn('"address"', body)
        self.assertIn('"size"', body)


if __name__ == "__main__":
    unittest.main()
