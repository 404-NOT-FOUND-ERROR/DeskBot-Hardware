#!/usr/bin/env python3
"""Validate E1.15 single-semantic proposal and its explicit release locks."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/expression-e1-15-surprised-shocked-20261024"
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util

SPEC = importlib.util.spec_from_file_location("e115_builder", ROOT / "scripts/build-expression-e1-15-surprised-shocked.py")
BUILDER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BUILDER)


class ExpressionE115Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ARTIFACT / "decision.json").read_text(encoding="utf-8"))

    def test_single_semantic_change_and_exact_logical_restore(self):
        before = self.data["semantic_snapshots"]["before"]
        proposed = self.data["semantic_snapshots"]["proposed"]
        self.assertEqual([a["id"] for a, b in zip(before, proposed) if a != b], ["surprised"])
        self.assertEqual(next(x for x in before if x["id"] == "surprised")["src"], "Happy.eaf")
        self.assertEqual(next(x for x in proposed if x["id"] == "surprised")["src"], "shocked.eaf")
        self.assertEqual(BUILDER.restore(proposed, before), before)
        self.assertTrue(self.data["rollback"]["logical_snapshot_exact"])

    def test_shared_happy_and_existing_shocked_are_retained(self):
        impact = self.data["shared_impact"]
        self.assertTrue(impact["Happy.eaf"]["retained"])
        self.assertEqual(impact["Happy.eaf"]["bytes_freed"], 0)
        self.assertEqual(len(impact["Happy.eaf"]["proposed"]), 9)
        self.assertTrue(impact["shocked.eaf"]["already_present"])
        self.assertEqual(self.data["member_diff"]["added"], [])
        self.assertEqual(self.data["member_diff"]["removed"], [])
        self.assertEqual(self.data["member_diff"]["proposed_changed"], ["index.json"])

    def test_action_timing_and_layout_are_unchanged(self):
        self.assertEqual(self.data["proposal"]["action_before"], "look_around")
        self.assertEqual(self.data["proposal"]["action_after"], "look_around")
        self.assertTrue(self.data["validation"]["action_map_unchanged"])
        self.assertTrue(self.data["validation"]["timing_unchanged"])
        self.assertTrue(self.data["validation"]["layout_unchanged"])
        self.assertEqual(self.data["proposal"]["layout_id"], "eye_anim")
        self.assertEqual(self.data["layout"]["before"], self.data["layout"]["proposed"])

    def test_uses_accepted_e113_baseline_and_labels_projection(self):
        capacity = self.data["capacity"]
        baseline = capacity["baseline"]
        self.assertEqual((baseline["partition_bytes"], baseline["package_bytes"], baseline["remaining_bytes"]),
                         (6_144_000, 5_538_349, 605_651))
        projection = capacity["historical_index_projection"]
        self.assertEqual((projection["delta_bytes"], projection["package_bytes"], projection["remaining_bytes"]),
                         (-381, 5_537_968, 606_032))
        self.assertEqual(projection["status"], "projection_not_repacked")
        self.assertIsNone(projection["package_sha256"])
        self.assertFalse(projection["package_built"])
        self.assertEqual(capacity["filename_only_scenario"]["delta_bytes"], 2)
        self.assertEqual(capacity["filename_only_scenario"]["package_bytes"], 5_538_351)

    def test_release_locks_and_hardware_boundary(self):
        self.assertFalse(self.data["deployment_eligible"])
        self.assertFalse(self.data["replacement_authorized"])
        self.assertFalse(self.data["hardware_accessed"])
        self.assertFalse(self.data["safety"]["package_built"])
        self.assertFalse(self.data["safety"]["firmware_changed"])
        self.assertFalse(self.data["safety"]["nvs_changed"])
        self.assertFalse(self.data["safety"]["c61_changed"])
        self.assertFalse(self.data["validation"]["user_visual_approval"])
        self.assertFalse(self.data["validation"]["device_playback_verified"])

    def test_official_previews_are_embedded_and_workbench_payload_matches(self):
        page = (ARTIFACT / "index.html").read_text(encoding="utf-8")
        payload = re.search(r'<script id="e115-data" type="application/json">(.*?)</script>', page, re.S)
        self.assertIsNotNone(payload)
        embedded = json.loads(payload[1])
        self.assertEqual(embedded["decision"], self.data)
        self.assertEqual(set(embedded["preview"]), {"happy", "shocked"})
        self.assertTrue(all(item["data_url"].startswith("data:image/png;base64,")
                            for item in embedded["preview"].values()))
        self.assertNotIn("shy_candidate", page)

    def test_reproducible_build_and_dom_controller_smoke(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build-expression-e1-15-surprised-shocked.py"), "--check"],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js required for DOM-controller smoke")
        result = subprocess.run([node, str(ROOT / "tests/expression-e1-15-ui-smoke.cjs")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, (result.stdout or "") + (result.stderr or ""))

    def test_replay_uses_fixed_capacity_evidence_and_preserves_artifact_bytes(self):
        original_read = Path.read_bytes

        def reject_live_document(path):
            if path == ROOT / "docs/baseline-status.md":
                raise AssertionError("historical replay must not consume the living status document")
            return original_read(path)

        with patch.object(Path, "read_bytes", reject_live_document):
            outputs = BUILDER.render_outputs()
        for name, raw in outputs.items():
            self.assertEqual(raw, (ARTIFACT / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
