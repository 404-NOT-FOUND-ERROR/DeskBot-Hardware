"""E1.43b build baseline checks for the accepted E3c-3b App."""
from __future__ import annotations

import importlib.util
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "e143b_e3c3b_builder", ROOT / "scripts/build-expression-e1-43b-e3c3b.py")
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
AUDIT_SPEC = importlib.util.spec_from_file_location(
    "e143b_e3c3b_auditor", ROOT / "scripts/audit-expression-e1-43b-e3c3b-build.py")
assert AUDIT_SPEC is not None and AUDIT_SPEC.loader is not None
auditor = importlib.util.module_from_spec(AUDIT_SPEC)
AUDIT_SPEC.loader.exec_module(auditor)


class E3c3bBuildReferenceTests(unittest.TestCase):
    def test_reference_binds_accepted_app_source_and_hardware_record(self):
        report, row, metadata = builder.load_reference()
        self.assertEqual(report["edition"], "E3c-3b")
        self.assertEqual(report["result"], "query_enabled_build_verified")
        self.assertEqual(row["artifacts"]["xiaozhi.bin"]["bytes"], 4_489_232)
        self.assertEqual(row["artifacts"]["xiaozhi.bin"]["sha256"],
                         "180aa8cbcc98e3d5b95329175ff2e3f2ee1d35f806b03cbe0778aa2fc2d0f42a")
        self.assertEqual(row["app_elf_sha256"],
                         "e0941ae3c68528763764f918a62b508fcb990008936d7dd1c69228f283345098")
        self.assertEqual(metadata["input_inventory"]["source_manifest_files"],
                         len(metadata["source_sha256"]))
        self.assertEqual(metadata["acceptance_sha256"], builder.MANUAL_ACCEPTANCE_SHA256)
        self.assertFalse(metadata["config"][builder.FLAG])

    def test_changed_build_report_is_rejected(self):
        with patch.object(builder, "REFERENCE_REPORT_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "build report digest"):
                builder.load_reference()

    def test_changed_hardware_acceptance_is_rejected(self):
        with patch.object(builder, "MANUAL_ACCEPTANCE_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "acceptance record"):
                builder.load_reference()

    def test_only_probe_flag_may_change(self):
        reference = {key: True for key in builder.helpers.QUERY_FLAGS}
        reference.update({key: False for key in builder.helpers.OTHER_PROBE_FLAGS})
        reference[builder.FLAG] = False
        reference["SPIRAM"] = True
        off = {**reference, builder.FLAG: False}
        on = {**reference, builder.FLAG: True}
        self.assertEqual(builder.helpers.validate_config(reference, off, False)["changed_keys"], [])
        self.assertEqual(builder.helpers.validate_config(reference, on, True)["changed_keys"],
                         [builder.FLAG])
        altered = {**on, "SPIRAM": False}
        with self.assertRaisesRegex(ValueError, "configuration delta"):
            builder.helpers.validate_config(reference, altered, True)

    def test_run_label_is_bound_to_this_baseline_and_date(self):
        self.assertIsNotNone(builder.RUN_LABEL_RE.fullmatch("e143b-e3c3b-20261107-probe-a"))
        self.assertIsNone(builder.RUN_LABEL_RE.fullmatch("e143b-e3c3a-20261107-probe-a"))
        self.assertIsNone(builder.RUN_LABEL_RE.fullmatch("e143b-e3c3b-20261108-probe-a"))


class ReviewedDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="e143b-components-", dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.component = self.project / "managed_components/espressif__esp_lcd_st77916"
        self.component.mkdir(parents=True)
        original = builder.helpers.safe_read(builder.e3c3b_display_dma.FROZEN,
                                             builder.e3c3b_display_dma.DRIVER)
        self.patched = builder.e3c3b_display_dma.patch_driver(original)
        files = {"esp_lcd_st77916.c": original, "include/sample.h": b"registry header"}
        aggregate = hashlib.sha256()
        entries = []
        for name, raw in sorted(files.items()):
            digest = builder.helpers.sha256(raw)
            aggregate.update(name.encode("utf-8"))
            aggregate.update(digest.encode("ascii"))
            entries.append({"path": name, "size": len(raw), "hash": digest})
            path = self.component / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.driver = self.component / "esp_lcd_st77916.c"
        self.driver.write_bytes(self.patched)
        self.digest = aggregate.hexdigest()
        (self.component / ".component_hash").write_text(self.digest, encoding="utf-8")
        (self.component / "CHECKSUMS.json").write_text(json.dumps({
            "version": "1.0", "algorithm": "sha256", "files": entries}), encoding="utf-8")
        self.locked = {"dependencies": {"espressif/esp_lcd_st77916": {
            "version": "1.0.1", "component_hash": self.digest}}}

    def test_exact_display_patch_retains_verified_upstream_identity(self):
        result = builder.validate_reviewed_components(self.project, self.locked)
        self.assertEqual(result["upstream"]["components"][0]["component_hash"], self.digest)
        self.assertEqual(result["reviewed_replacements"][builder.e3c3b_display_dma.DRIVER][
            "accepted_sha256"], builder.helpers.sha256(self.patched))

    def test_mutated_replacement_is_rejected(self):
        self.driver.write_bytes(self.patched + b"\n")
        with self.assertRaisesRegex(ValueError, "replacement differs"):
            builder.validate_reviewed_components(self.project, self.locked)

    def test_non_patched_registry_file_still_matches_the_lock(self):
        (self.component / "include/sample.h").write_bytes(b"changed header!")
        with self.assertRaisesRegex(ValueError, "contents differ from lock"):
            builder.validate_reviewed_components(self.project, self.locked)

    def test_wrong_registry_marker_is_rejected(self):
        (self.component / ".component_hash").write_text("0" * 64, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "marker differs"):
            builder.validate_reviewed_components(self.project, self.locked)


class PostBuildInputTests(unittest.TestCase):
    def test_registry_change_after_build_is_rejected_before_report(self):
        metadata = {"source_sha256": {"main/a.cc": "a" * 64},
                    "input_inventory": {"files": 2}, "components": {"expected": True}}
        with patch.object(builder.helpers, "verify_frozen_sources"), \
                patch.object(builder.helpers, "load_locked_dependencies", return_value={}), \
                patch.object(builder.helpers, "verify_project_file_inventory",
                             return_value=metadata["input_inventory"]), \
                patch.object(builder, "validate_reviewed_components",
                             side_effect=[metadata["components"], {"changed": True}]):
            with self.assertRaisesRegex(ValueError, "registry inputs changed"):
                builder.verify_final_inputs(builder.WORK_ROOT / "unit/source", metadata)

    def test_source_and_registry_inventories_are_rechecked_in_both_trees(self):
        metadata = {"source_sha256": {"main/a.cc": "a" * 64},
                    "input_inventory": {"files": 2}, "components": {"expected": True}}
        project = builder.WORK_ROOT / "unit/source"
        with patch.object(builder.helpers, "verify_frozen_sources") as source, \
                patch.object(builder.helpers, "load_locked_dependencies", return_value={}), \
                patch.object(builder.helpers, "verify_project_file_inventory",
                             return_value=metadata["input_inventory"]), \
                patch.object(builder, "validate_reviewed_components",
                             return_value=metadata["components"]) as components:
            builder.verify_final_inputs(project, metadata)
        self.assertEqual([call.args[0] for call in source.call_args_list], [builder.FROZEN, project])
        self.assertEqual([call.args[0] for call in components.call_args_list], [builder.FROZEN, project])


class AuditBindingTests(unittest.TestCase):
    def setUp(self):
        self.label = "e143b-e3c3b-20261107-audit-test"
        self.work = builder.WORK_ROOT / self.label
        self.metadata = {"source_sha256": {"main/sample.cc": "a" * 64},
                         "probe": {"stack_bytes": 4096},
                         "input_inventory": {"files": 2, "auxiliary_sha256": "b" * 64}}
        digest = builder.helpers.sha256(json.dumps(
            self.metadata["source_sha256"], sort_keys=True, separators=(",", ":")).encode())
        self.report = {
            "target": dict(builder.TARGET),
            "source": {"project": builder.FROZEN.relative_to(ROOT).as_posix(),
                       "build_project": (self.work / "source").relative_to(ROOT).as_posix(),
                       "files_checked": 1, "probe": self.metadata["probe"],
                       "input_inventory": copy.deepcopy(self.metadata["input_inventory"]),
                       "source_sha256_digest": digest, "changed": False},
        }

    def validate(self, report):
        auditor.validate_source_binding(report, self.metadata, self.work)

    def test_exact_target_and_source_binding_are_accepted(self):
        self.validate(self.report)

    def test_missing_or_changed_input_inventory_is_rejected(self):
        for value in (None, {}, {**self.metadata["input_inventory"], "files": 3}):
            with self.subTest(value=value):
                document = copy.deepcopy(self.report)
                document["source"]["input_inventory"] = value
                with self.assertRaisesRegex(ValueError, "source binding"):
                    self.validate(document)

    def test_each_hardware_target_field_is_bound(self):
        for name in builder.TARGET:
            with self.subTest(name=name):
                document = copy.deepcopy(self.report)
                document["target"][name] = "changed"
                with self.assertRaisesRegex(ValueError, "target"):
                    self.validate(document)

    def test_source_manifest_digest_is_rechecked(self):
        document = copy.deepcopy(self.report)
        document["source"]["source_sha256_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "manifest digest"):
            self.validate(document)

    def test_source_path_is_bound_to_run_label(self):
        document = copy.deepcopy(self.report)
        document["source"]["build_project"] = (
            builder.WORK_ROOT / "e143b-e3c3b-20261107-another" / "source").relative_to(ROOT).as_posix()
        with self.assertRaisesRegex(ValueError, "source binding"):
            self.validate(document)

    def test_redirected_report_is_rejected_before_reading(self):
        with patch.object(auditor.helpers, "_has_reparse_ancestor", return_value=True):
            with self.assertRaisesRegex(ValueError, "redirected"):
                auditor._read_report(builder.ARTIFACT_ROOT / (self.label + "-build-report.json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
