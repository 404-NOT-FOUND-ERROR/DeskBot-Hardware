from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-role-expression-workbench-e1-31.py"
OUT = ROOT / "artifacts" / "role-expression-workbench-e1-31-20261029"
SPEC = importlib.util.spec_from_file_location("role_expression_e1_31_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


def generated_data() -> dict:
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True, capture_output=True, text=True)
    html = (OUT / "index.html").read_text(encoding="utf-8")
    match = re.search(r'<script id="e131-data" type="application/json">(.*?)</script>', html, re.S)
    assert match is not None
    return json.loads(match.group(1))


class RoleExpressionWorkbenchE131Tests(unittest.TestCase):
    def test_layout_whitelist_separates_listen_from_eye_resources(self):
        data = generated_data()
        for semantic, info in data["semantics"].items():
            expected_kind = "listen" if info["layout_id"] == "listen_anim" else "eye"
            self.assertTrue(info["allowed_resources"])
            self.assertTrue(
                all(data["assets"][resource]["object_kind"] == expected_kind
                    for resource in info["allowed_resources"]),
                semantic,
            )
        self.assertEqual(data["semantics"]["listen"]["allowed_resources"], ["listen.eaf"])
        self.assertNotIn("listen.eaf", data["semantics"]["happy"]["allowed_resources"])

    def test_disconnect_is_a_valid_offline_draft_but_incompatible_resource_is_rejected(self):
        data = generated_data()
        context = {
            "baseline": data["baseline"],
            "asset_version": data["asset_version"],
            "binding_version": data["binding_version"],
            "semantics": data["semantics"],
        }
        from protocol.role_expression_draft import validate_draft

        disconnected = copy.deepcopy(data["initial"])
        disconnected["roles"][0]["semantic_resources"]["happy"] = None
        self.assertIs(validate_draft(disconnected, context), disconnected)

        incompatible = copy.deepcopy(data["initial"])
        incompatible["roles"][0]["semantic_resources"]["listen"] = "Happy.eaf"
        with self.assertRaisesRegex(ValueError, "incompatible"):
            validate_draft(incompatible, context)

    def test_javascript_core_can_disconnect_and_export_without_device_io(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        script = r'''
const fs = require('fs');
const html = fs.readFileSync('artifacts/role-expression-workbench-e1-31-20261029/index.html', 'utf8');
const marker = '<script id="e131-data" type="application/json">';
const start = html.indexOf(marker) + marker.length;
const data = JSON.parse(html.slice(start, html.indexOf('</script>', start)));
const Draft = require('./scripts/role-expression-e1-31-core.js');
const ctx = {baseline:data.baseline, asset_version:data.asset_version, binding_version:data.binding_version, semantics:data.semantics, assets:data.assets};
const editor = Draft.create(ctx, data.initial);
if (!editor.disconnect('happy')) throw new Error('disconnect did not change draft');
if (editor.active().semantic_resources.happy !== null) throw new Error('disconnect was not persisted');
const exported = editor.exportDocument();
if (exported.roles[0].semantic_resources.happy !== null) throw new Error('export changed disconnect');
if (!editor.history().some(row => row.operation === 'rewire')) throw new Error('rewire was not journaled');
console.log('E1.31 core disconnect/export passed');
'''
        result = subprocess.run([node, "-"], cwd=ROOT, input=script, text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("disconnect/export passed", result.stdout)

    def test_javascript_core_action_allowlist_matches_protocol(self):
        generated_data()
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        from protocol.deskbot_role_profile import SAFE_ACTIONS

        script = r'''
const fs = require('fs');
const Draft = require('./scripts/role-expression-e1-31-core.js');
process.stdout.write(JSON.stringify(Draft.SAFE_ACTIONS));
'''
        result = subprocess.run([node, "-"], cwd=ROOT, input=script, text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(json.loads(result.stdout)), SAFE_ACTIONS)

    def test_generator_check_rejects_stale_output(self):
        generated_data()
        path = OUT / "README.txt"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\nstale")
            result = subprocess.run([sys.executable, str(SCRIPT), "--check"], cwd=ROOT,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale generated artifact", result.stderr + result.stdout)
        finally:
            path.write_bytes(original)
            subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True,
                            capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
