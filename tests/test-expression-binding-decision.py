#!/usr/bin/env python3
"""Validate the E1.14 decision artifact without hardware or browser access."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts/expression-binding-decision-e1-14-20261024"


class ExpressionBindingDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ARTIFACT / "decision.json").read_text(encoding="utf-8"))

    def test_schema_and_scope(self):
        self.assertEqual(self.data["schema"], "deskbot.expression_binding_decision/v1")
        self.assertEqual(self.data["edition"], "E1.14")
        self.assertFalse(self.data["safety"]["hardware_accessed"])
        self.assertFalse(self.data["safety"]["firmware_changed"])
        self.assertFalse(self.data["safety"]["device_package_changed"])
        self.assertFalse(self.data["safety"]["candidate_media_included"])

    def test_capacity_is_current_e1_13_baseline(self):
        capacity = self.data["capacity"]
        self.assertEqual(capacity["partition_bytes"], 6_144_000)
        self.assertEqual(capacity["package_bytes"], 5_538_349)
        self.assertEqual(capacity["remaining_bytes"], 605_651)
        self.assertEqual(capacity["member_count"], 29)
        self.assertEqual(capacity["changed_members"], ["insert.eaf", "eat.eaf"])

    def test_shared_resources_are_protected(self):
        resources = {item["resource_id"]: item for item in self.data["resource_decisions"]}
        self.assertEqual(resources["Happy.eaf"]["kind"], "protected_shared")
        self.assertTrue(resources["Happy.eaf"]["current_profile_member"])
        self.assertEqual(len(resources["Happy.eaf"]["semantic_ids"]), 10)
        self.assertEqual(resources["confused.eaf"]["kind"], "protected_shared")
        self.assertEqual(resources["neutral.eaf"]["kind"], "protected_shared")

    def test_candidate_is_metadata_only(self):
        candidates = [item for item in self.data["resource_decisions"] if item["kind"] == "offline_candidate"]
        self.assertEqual([item["resource_id"] for item in candidates], ["shy_legacy.eaf"])
        self.assertTrue(all(not item["current_profile_member"] for item in candidates))
        self.assertTrue(all(item["reference_bytes"] == 661_508 for item in candidates))
        self.assertNotIn("shy_legacy.eaf", {output["src"] for route in self.data["route_projections"] for output in route["outputs"]})
        self.assertNotIn("data:image", (ARTIFACT / "index.html").read_text(encoding="utf-8"))

    def test_action_branch_is_present_and_separate(self):
        happy = next(item for item in self.data["resource_decisions"] if item["resource_id"] == "Happy.eaf")
        self.assertIn("shark_head", happy["action_ids"])
        self.assertTrue(self.data["policy"]["action_mapping_is_separate_from_eaf"])
        route = next(item for item in self.data["route_projections"] if item["event_id"] == "pair-detect")
        self.assertIn("Happy.eaf", route["resources"])
        self.assertIn("shark_head", route["actions"])
        self.assertEqual(route["source_branch_actions"], ["look_around"])
        tool = next(item for item in self.data["route_projections"] if item["event_id"] == "tool-action")
        self.assertEqual(tool["actions"], [])
        self.assertEqual(len(tool["available_action_options"]), 5)

    def test_route_and_resource_counts(self):
        self.assertEqual(self.data["summary"]["route_count"], 56)
        self.assertEqual(self.data["summary"]["resource_count"], 19)
        self.assertEqual(len(self.data["route_projections"]), 56)
        self.assertEqual(len(self.data["resource_decisions"]), 19)

    def test_dynamic_pet_head_has_no_invented_fixed_animation(self):
        route = next(item for item in self.data["route_projections"] if item["event_id"] == "pet-head")
        self.assertEqual(route["resolution"], "server_dependent")
        self.assertEqual(route["outputs"], [])
        self.assertEqual(route["actions"], [])
        self.assertTrue(any(step["label"] == "WakeWordInvoke" for step in route["source_steps"]))

    def test_layouts_keep_actual_channels_and_offsets(self):
        screen = next(item for item in self.data["route_projections"] if item["event_id"] == "screen-tap")
        self.assertEqual([(item["src"], item["layout"]) for item in screen["outputs"]],
                         [("winking.eaf", "eye_anim"), ("listen.eaf", "listen_anim")])
        self.assertEqual(self.data["layout_catalog"]["eye_anim"]["anim"]["mirror"], "auto")
        self.assertEqual(self.data["layout_catalog"]["listen_anim"]["y"], 25)

    def test_accepted_optimization_is_not_still_a_pending_task(self):
        item = next(item for item in self.data["review_queue"] if item["id"] == "optimize-large-protected-resources")
        self.assertEqual(item["status"], "accepted_body_scope")
        encoded = [item for item in self.data["resource_decisions"] if item["e1_13_member_reencoded"]]
        self.assertEqual({item["resource_id"] for item in encoded}, {"insert.eaf", "eat.eaf"})
        self.assertTrue(all(item["size_basis"] == "upstream_source_not_deployed_member_size" for item in encoded))

    def test_review_does_not_authorize_removal_or_replacement(self):
        self.assertFalse(self.data["deployment_eligible"])
        self.assertTrue(all(not item["removal_authorized"] and not item["replacement_authorized"]
                            for item in self.data["resource_decisions"]))

    def test_embedded_json_matches_standalone_evidence(self):
        page = (ARTIFACT / "index.html").read_text(encoding="utf-8")
        payload = re.search(r'<script id="decision-data" type="application/json">(.*?)</script>', page, re.S)
        self.assertIsNotNone(payload)
        self.assertEqual(json.loads(payload[1]), self.data)

    def test_artifact_is_reproducible_from_public_inputs(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build-expression-binding-decision-e1-14.py"), "--check"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_page_controller_in_dom_double(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js required for page controller smoke")
        result = subprocess.run([node, str(ROOT / "tests/expression-binding-decision-ui-smoke.cjs")],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
