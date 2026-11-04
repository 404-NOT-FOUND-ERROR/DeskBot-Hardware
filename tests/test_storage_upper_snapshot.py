"""Same-run reference and CPU validator regression tests; no hardware access."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from protocol.storage_upper_experiment import sha
from protocol.storage_upper_snapshot import load_snapshot, persist_snapshot
from upper_probe_test_support import make_plan, make_snapshot, runtime_log, temporary_run

spec = importlib.util.spec_from_file_location("a7_snapshot_validator", ROOT / "scripts/validate-expression-e1-43a7-probe.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.run = self.enterContext(temporary_run("e143a7-snapshot-test-"))
        self.plan = make_plan()
        self.snapshot = make_snapshot(self.plan)

    def persist(self):
        self.reference = persist_snapshot(self.run, self.plan, self.snapshot)
        return self.reference

    def load(self, **changes):
        values = dict(identity_sha256=self.plan.expected_identity_sha256, plan_id=self.plan.plan_id,
                      expected_manifest_sha256=self.reference.manifest_sha256)
        values.update(changes)
        return load_snapshot(self.run, **values)

    def validate(self, log=None):
        return validator.validate(runtime_log() if log is None else log, self.run,
                                  identity_sha256=self.plan.expected_identity_sha256, plan_id=self.plan.plan_id,
                                  expected_manifest_sha256=self.reference.manifest_sha256)

    def rewrite_manifest(self, mutate):
        path = self.run / "snapshot/manifest.json"
        value = json.loads(path.read_text())
        mutate(value)
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        path.write_bytes(raw)
        return sha(raw)

    def test_roundtrip_preserves_exact_reference_and_binding(self):
        reference = self.persist()
        self.assertEqual(self.load(), reference)
        self.assertEqual(reference.low_page, self.snapshot.runtime_low_page)
        self.assertEqual(reference.boundary_lower, self.snapshot.runtime_boundary_lower)
        self.assertEqual(reference.low_protected_sha256, sha(self.snapshot.runtime_low_page[:0x9000]))
        self.assertEqual(reference.manifest_sha256, sha((self.run / "snapshot/manifest.json").read_bytes()))

    def test_persist_is_exclusive(self):
        self.persist()
        with self.assertRaises(FileExistsError):
            self.persist()

    def test_missing_wrong_length_and_inconsistent_low_page_are_rejected(self):
        cases = [replace(self.snapshot, runtime_low_page=None),
                 replace(self.snapshot, runtime_low_page=b"x" * 65535),
                 replace(self.snapshot, runtime_boundary_lower=None),
                 replace(self.snapshot, runtime_boundary_lower=b"x" * 17),
                 replace(self.snapshot, runtime_low_page=b"x" * 65536)]
        for snapshot in cases:
            with self.subTest(snapshot=len(snapshot.runtime_low_page or b"")), self.assertRaises(ValueError):
                persist_snapshot(self.run, self.plan, snapshot)
        self.assertFalse((self.run / "snapshot").exists())

    def test_direct_a7_run_path_is_required(self):
        for path in (self.run / "nested", ROOT / "tmp/e143a6-old-test", Path("tmp/e143a7-relative")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                persist_snapshot(path, self.plan, self.snapshot)

    def test_identity_plan_and_run_are_bound(self):
        self.persist()
        for change in ({"identity_sha256": sha(b"other")}, {"plan_id": sha(b"other")}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "mismatch"):
                self.load(**change)
        digest = self.rewrite_manifest(lambda value: value.update(run_label="e143a7-other"))
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.load(expected_manifest_sha256=digest)

    def test_pinned_manifest_hash_is_mandatory_and_checked_before_parsing(self):
        self.persist()
        with self.assertRaises(TypeError):
            load_snapshot(self.run, identity_sha256=self.plan.expected_identity_sha256, plan_id=self.plan.plan_id)
        (self.run / "snapshot/manifest.json").write_bytes(b"not JSON")
        with self.assertRaisesRegex(ValueError, "changed after"):
            self.load()

    def test_manifest_and_data_replacement_cannot_replace_the_pinned_reference(self):
        self.persist()
        raw = b"x" * 65536
        (self.run / "snapshot/low-page.bin").write_bytes(raw)
        self.rewrite_manifest(lambda value: value["files"]["low_page"].update(sha256=sha(raw)))
        with self.assertRaisesRegex(ValueError, "changed after"):
            self.load()

    def test_data_tamper_and_missing_file_are_rejected(self):
        self.persist()
        path = self.run / "snapshot/low-page.bin"
        path.write_bytes(b"x" * 65536)
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.load()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing or has a wrong length"):
            self.load()

    def test_wrong_geometry_is_rejected_even_when_manifest_digest_is_supplied(self):
        self.persist()
        digest = self.rewrite_manifest(lambda value: value["files"]["boundary_lower"].update(bytes=True))
        with self.assertRaisesRegex(ValueError, "geometry"):
            self.load(expected_manifest_sha256=digest)

    def test_snapshot_schema_and_exact_file_set_are_required(self):
        self.persist()
        original = (self.run / "snapshot/manifest.json").read_bytes()
        for mutate in (lambda v: v.update(schema="old"),
                       lambda v: v["files"].update(extra={}),
                       lambda v: v["files"]["low_page"].update(file="../low-page.bin")):
            (self.run / "snapshot/manifest.json").write_bytes(original)
            digest = self.rewrite_manifest(mutate)
            with self.assertRaises(ValueError):
                self.load(expected_manifest_sha256=digest)

    def test_duplicate_manifest_fields_are_rejected(self):
        self.persist()
        path = self.run / "snapshot/manifest.json"
        raw = path.read_bytes().replace(b'{"files":', b'{"schema":"duplicate","files":', 1)
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.load(expected_manifest_sha256=sha(raw))

    def test_same_run_runtime_passes_and_records_host_association(self):
        self.persist()
        result = self.validate()
        self.assertEqual(result["result"], "runtime_probe_passed")
        self.assertTrue(all(result["checks"].values()))
        self.assertTrue(result["observations"]["nvs_containing_low_page_matches_write_snapshot"])
        self.assertEqual(result["binding"]["snapshot_manifest_sha256"], self.reference.manifest_sha256)
        self.assertEqual(result["binding"]["source_log_sha256"], sha(runtime_log().encode()))
        self.assertEqual(result["binding"]["association"], "host_collected_same_run")
        self.assertFalse(result["binding"]["log_identity_authenticated"])

    def test_historical_prefix_and_boundary_drift_fail(self):
        self.persist()
        changed_low = b"x" + self.snapshot.runtime_low_page[1:]
        for log in (runtime_log(low_page=changed_low), runtime_log(boundary_lower=b"x" * 16)):
            with self.subTest(log_sha=sha(log.encode())):
                self.assertEqual(self.validate(log)["result"], "runtime_probe_failed")

    def test_nvs_containing_page_change_is_observation_not_immutable_failure(self):
        self.persist()
        low = bytearray(self.snapshot.runtime_low_page)
        low[0x9000] ^= 1
        result = self.validate(runtime_log(low_page=bytes(low)))
        self.assertEqual(result["result"], "runtime_probe_passed")
        self.assertFalse(result["observations"]["nvs_containing_low_page_matches_write_snapshot"])

    def test_runtime_aborted_record_stays_aborted(self):
        self.persist()
        from test_storage_upper_pattern_runtime import synthetic_records, log_for
        records, _, _ = synthetic_records()
        del records["summary"]
        records["blocks"] = []
        records["aborted"] = {"error": "not_quiet", "writes_performed": False}
        self.assertEqual(self.validate(log_for(records))["result"], "probe_aborted")

    def test_cli_requires_pinned_digest_and_same_run_capture(self):
        self.persist()
        captures = self.run / "captures"
        captures.mkdir()
        log = captures / "001-probe.log"
        log.write_text(runtime_log(), encoding="utf-8")
        command = [sys.executable, str(ROOT / "scripts/validate-expression-e1-43a7-probe.py"), str(log),
                   "--run-dir", str(self.run), "--identity-sha256", self.plan.expected_identity_sha256,
                   "--plan-id", self.plan.plan_id]
        missing = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--snapshot-manifest-sha256", missing.stderr)
        command += ["--snapshot-manifest-sha256", self.reference.manifest_sha256]
        success = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)["result"], "runtime_probe_passed")
        command[2] = str(self.run / "outside.log")
        rejected = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("direct capture", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
