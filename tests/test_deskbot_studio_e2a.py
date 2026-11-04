from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("studio_builder_test", ROOT / "scripts/build-deskbot-studio-e2a.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.report = BUILDER.build()
        cls.schema = json.loads((ROOT / "protocol/deskbot-studio-draft-v1.schema.json").read_bytes())

    def test_initial_draft_validates_against_versioned_schema(self):
        jsonschema.Draft202012Validator.check_schema(self.schema)
        jsonschema.validate(self.data["initial"], self.schema)

    def test_all_atlases_match_original_sha(self):
        for row in self.data["assets"].values():
            if row["atlas"]:
                raw = base64.b64decode(row["atlas"].split(",", 1)[1], validate=True)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row["atlas_sha256"])

    def test_catalog_and_source_counts(self):
        self.assertEqual(self.report["counts"], {"semantics": 29, "official_resources": 18,
                         "candidate_resources": 1, "routes": 56, "actions": 5, "initial_roles": 3})
        for row in self.data["sources"]:
            self.assertEqual(hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest(), row["sha256"])

    def test_public_candidate_media_separate_from_metadata(self):
        self.assertIsNone(self.data["assets"]["shy_candidate.eaf"]["atlas"])
        self.assertEqual(self.data["assets"]["shy_candidate.eaf"]["frame_count"], 188)
        self.assertFalse(self.report["hardware_accessed"])
        self.assertFalse(self.report["firmware_changed"])
        self.assertFalse(self.data["device"]["live"])

    def test_build_is_reproducible(self):
        first = BUILDER.products()
        self.assertEqual(first, BUILDER.products())
        self.assertEqual((BUILDER.PUBLIC / "index.html").read_bytes(), first["index.html"])
        self.assertNotIn(b"__STUDIO_", first["index.html"])

    def test_layout_and_shared_references_preserved(self):
        self.assertEqual(self.data["semantics"]["embarrassed"]["action_id"], "cat_nuzzle")
        self.assertEqual(sum(row["resource_id"] == "Happy.eaf" for row in self.data["semantics"].values()), 9)
        self.assertNotIn("shy_candidate.eaf", self.data["semantics"]["listen"]["allowed_resources"])
        self.assertEqual(self.data["semantics"]["surprised"]["resource_id"], "shocked.eaf")

    def test_schema_rejects_missing_semantic_and_unknown_resource(self):
        for mutation in (lambda d: d["roles"][0]["bindings"].pop("happy"),
                         lambda d: d["roles"][0]["bindings"].update(happy="unknown.eaf")):
            value = json.loads(json.dumps(self.data["initial"]))
            mutation(value)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(value, self.schema)

    @unittest.skipUnless((ROOT / "tmp/expression-e1-45-staging-preview-20261104/index.html").exists(), "local candidate media is private")
    def test_local_media_keeps_catalog_identity(self):
        local, _ = BUILDER.build(True)
        self.assertEqual(local["catalog_id"], self.data["catalog_id"])
        row = local["assets"]["shy_candidate.eaf"]
        self.assertTrue(row["atlas"].startswith("data:image/png;base64,"))
        self.assertEqual(hashlib.sha256(base64.b64decode(row["atlas"].split(",", 1)[1])).hexdigest(), row["atlas_sha256"])


if __name__ == "__main__":
    unittest.main()
