"""Reproduce the current page and parse every generated executable script."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = Path(os.environ.get('DESKBOT_TEST_NODE', str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')))


class ReadinessWorkbenchTests(unittest.TestCase):
    def test_current_products_reproduce(self):
        spec = importlib.util.spec_from_file_location('ready_page_test', ROOT/'scripts/build-deskbot-studio-e3f4b.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        for name, raw in builder.products(True).items():
            self.assertEqual((builder.LOCAL/name).read_bytes(), raw, name)
        report = json.loads((builder.LOCAL/'report.json').read_bytes())
        self.assertFalse(report['firmware_changed'])
        self.assertFalse(report['automatic_expression_commands'])
        self.assertEqual(report['automatic_read_only_catalog'], 'once_per_enabled_session')

    def test_older_editions_keep_explicit_directory_queries(self):
        spec = importlib.util.spec_from_file_location('ready_old_page_test', ROOT/'scripts/build-deskbot-studio-e3f4a.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertNotIn('automaticDirectory:true', builder.products(True)['index.html'].decode('utf-8'))
        current = (ROOT/'tmp/deskbot-studio-e3f4b-20261109/index.html').read_text(encoding='utf-8')
        self.assertIn('automaticDirectory:true', current)

    def test_generated_scripts_parse(self):
        self.assertTrue(NODE.is_file(), 'Set DESKBOT_TEST_NODE to the verified Node runtime')
        html = (ROOT/'tmp/deskbot-studio-e3f4b-20261109/index.html').read_text(encoding='utf-8')
        scripts = [m[2] for m in re.finditer(r'<script([^>]*)>(.*?)</script>', html, re.S)
                   if 'application/json' not in m[1]]
        self.assertGreaterEqual(len(scripts), 1)
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp', prefix='readiness-parse-') as folder:
            for n, script in enumerate(scripts):
                target = Path(folder)/f'inline-{n}.js'
                target.write_text(script, encoding='utf-8')
                process = subprocess.run([str(NODE), '--check', str(target)], capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == '__main__':
    unittest.main()
