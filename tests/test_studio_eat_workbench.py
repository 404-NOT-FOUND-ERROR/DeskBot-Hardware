"""Reproducible eating page built from the shared production UI."""
import importlib.util
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
class EatingWorkbenchTests(unittest.TestCase):
    def test_local_products_reproduce_and_expose_current_request(self):
        spec = importlib.util.spec_from_file_location("eat_page_test", ROOT / "scripts/build-deskbot-studio-e3f4a.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        for name, raw in builder.products(local=True).items():
            self.assertEqual((builder.LOCAL / name).read_bytes(), raw, name)
        html = (builder.LOCAL / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-e3c-action="eat"', html)
        self.assertIn('播放进食', html)
        self.assertIn('e3c-eat-binding', html)
