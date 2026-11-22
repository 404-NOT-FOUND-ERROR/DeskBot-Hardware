"""Contract checks for mixed full-frame and mirrored-eye assets."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from protocol.studio_expression_layout import inspect_layout, validate_requirement

ROOT = Path(__file__).resolve().parents[1]


def inputs(name="eat"):
    rows = json.loads((ROOT/"protocol/expression-layout-requirements-v1.json").read_bytes())["assets"]
    requirement = next(row for row in rows if row["resource_id"] == name+".eaf")
    asset = dict(deployed_sha256=requirement["resource_sha256"], bytes=400,
                 frame_width=requirement["width"], frame_height=requirement["height"])
    expression = dict(kind="expression", id=name, resource_id=requirement["resource_id"],
        resource_sha256=requirement["resource_sha256"], bytes=400,
        layout_id="eye_anim", fps=20, loop=False, implicit_action_id=None)
    layout = dict(kind="layout", id="eye_anim", align="GFX_ALIGN_LEFT_MID", x=10, y=10, mirror="auto")
    return asset, expression, layout, requirement


class ExpressionLayoutTests(unittest.TestCase):
    def test_complete_frame_needs_independent_route(self):
        plan = inspect_layout(*inputs())
        self.assertEqual(plan["status"], "requires_player_route")
        self.assertEqual(plan["current"]["mirror"], "auto")
        self.assertEqual(plan["target"], dict(align="GFX_ALIGN_CENTER", x=0, y=0, mirror="false"))
        self.assertFalse(plan["request_authorized"])

    def test_eye_layout_already_matches(self):
        self.assertEqual(inspect_layout(*inputs("winking"))["status"], "matched")

    def test_mirror_false_without_centering_still_needs_route(self):
        args = inputs(); args[2]["mirror"] = "false"
        self.assertIn("align", inspect_layout(*args)["changes"])

    def test_explicit_nonmirrored_layout_matches(self):
        args = inputs(); args[2].update(align="GFX_ALIGN_CENTER", x=0, y=0, mirror="false")
        self.assertEqual(inspect_layout(*args)["status"], "matched")

    def test_metadata_does_not_infer_mode(self):
        args = inputs("winking"); args[3]["mode"] = "full_frame"
        self.assertEqual(inspect_layout(*args)["requirement"]["mode"], "full_frame")

    def test_shared_layout_and_requirements_not_mutated(self):
        args = inputs(); before = deepcopy(args)
        plan = inspect_layout(*args); plan["target"]["mirror"] = "auto"
        self.assertEqual(args, before)
        self.assertEqual(inspect_layout(*args)["target"]["mirror"], "false")

    def test_layout_switch_back_uses_eyes_own_profile(self):
        eye = inputs("winking"); eye[2].update(align="GFX_ALIGN_CENTER", x=0, y=0, mirror="false")
        plan = inspect_layout(*eye)
        self.assertEqual(plan["target"]["mirror"], "auto")
        self.assertEqual(plan["target"]["x"], 10)
        self.assertEqual(plan["status"], "requires_player_route")

    def test_content_changes_require_new_review(self):
        for index, field in ((0,"deployed_sha256"),(0,"bytes"),(1,"resource_sha256"),(1,"resource_id")):
            with self.subTest(index=index, field=field):
                args = inputs(); args[index][field] = 401 if field=="bytes" else "other.eaf" if field=="resource_id" else "a"*64
                with self.assertRaisesRegex(ValueError, "content_changed"):
                    inspect_layout(*args)

    def test_geometry_changes_require_new_review(self):
        args = inputs(); args[0]["frame_width"] = 359
        with self.assertRaisesRegex(ValueError, "dimensions_changed"): inspect_layout(*args)

    def test_layout_reference_must_match(self):
        args = inputs(); args[1]["layout_id"] = "listen_anim"
        with self.assertRaises(ValueError): inspect_layout(*args)

    def test_mirrored_full_frame_is_rejected(self):
        args = inputs(); args[3]["mode"] = "mirrored_eye"
        with self.assertRaisesRegex(ValueError, "exceeds_display"): inspect_layout(*args)

    def test_invalid_modes_sizes_and_flags(self):
        for field, value in (("mode","automatic"),("mode",None),("width",True),("height",361),
                             ("width",0),("resource_sha256","x"),("resource_id","../eat.eaf")):
            with self.subTest(field=field, value=value):
                requirement = inputs()[3]; requirement[field] = value
                with self.assertRaises(ValueError): validate_requirement(requirement)

    def test_registry_has_unique_and_valid_resources(self):
        registry = json.loads((ROOT/'protocol/expression-layout-requirements-v1.json').read_bytes())
        self.assertEqual(registry['schema'], 'deskbot.asset_layout_requirements/v1')
        rows = [validate_requirement(row) for row in registry['assets']]
        self.assertEqual(len({row['resource_id'] for row in rows}), len(rows))

    def test_real_preview_keeps_device_and_target_separate(self):
        from protocol.studio_installed_catalog import validate_manifest
        manifest = validate_manifest(json.loads((ROOT/'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json').read_bytes()))
        report = json.loads((ROOT/'artifacts/expression-layout-e3f4c/report.json').read_bytes())
        self.assertEqual(report['sources']['directory']['catalog_id'], manifest['catalog_id'])
        self.assertFalse(report['device_written'])
        self.assertFalse(report['firmware_changed'])
        eat, neutral = report['previews']
        self.assertEqual(eat['image_frames'], 75)
        self.assertEqual(eat['stored_frames'], 76)
        self.assertEqual(eat['metadata_frames'], 1)
        self.assertEqual(eat['comparison'], 'different')
        self.assertEqual(neutral['comparison'], 'identical')
        self.assertEqual(eat['plan']['current']['mirror'], 'auto')
        self.assertEqual(eat['plan']['target']['mirror'], 'false')
        for preview in report['previews']:
            for kind in ('current', 'target'):
                import hashlib, struct
                atlas = preview[kind]
                raw = (ROOT/'artifacts/expression-layout-e3f4c'/atlas['file']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), atlas['sha256'])
                self.assertEqual(struct.unpack_from('>II', raw, 16), (atlas['width'], atlas['height']))

    def test_preview_report_matches_sources(self):
        import hashlib
        report = json.loads((ROOT/'artifacts/expression-layout-e3f4c/report.json').read_bytes())
        for name, expected in report['sources']['code'].items():
            # This preview predates the device's full_frame protocol extension.
            path = (ROOT/'artifacts/expression-layout-e3f4c/source-snapshot/studio_installed_catalog.py'
                    if name == 'protocol/studio_installed_catalog.py' else ROOT/name)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected, name)


if __name__ == "__main__": unittest.main()
