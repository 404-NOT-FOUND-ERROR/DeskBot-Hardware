from __future__ import annotations

import importlib.util
import hashlib
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit-expression-e1-26-observation.py"
SPEC = importlib.util.spec_from_file_location("e1_26_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class ExpressionObserverE126AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tmp").mkdir(exist_ok=True)

    def require_local_report(self, relative: str) -> Path:
        path = ROOT / relative
        if not path.is_file():
            raise unittest.SkipTest(
                f"local build evidence is not present in this checkout: {relative}"
            )
        return path

    def test_historical_integration_rejects_later_source_changes(self):
        with self.assertRaisesRegex(ValueError, "official source drift outside E1.26 guard"):
            AUDIT.audit_integration_files()

    @contextmanager
    def full_body_fixture(self):
        """Synthetic audit inputs; independent of local builds and later sources."""
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e126-validator-") as folder:
            root = Path(folder)
            body = root / "firmware/body"
            body.mkdir(parents=True)
            digest = lambda raw: hashlib.sha256(raw).hexdigest()
            manifest, lock = b"fixture-hash  main/fixture.cc\n", b"synthetic-lock\n"
            (body / "SOURCE_MANIFEST.sha256").write_bytes(manifest)
            (body / "dependencies.lock").write_bytes(lock)
            shared = ["partition_table/partition-table.bin", "bootloader/bootloader.bin",
                      "expression_assets.bin", "srmodels/srmodels.bin"]
            rows = []
            for enabled in (False, True):
                build = root / "tmp" / ("on" if enabled else "off")
                records = {}
                for name in ["xiaozhi.bin", "xiaozhi.elf", *shared]:
                    raw = (f"synthetic:{name}:" + (str(enabled) if name.startswith("xiaozhi.") else "shared")).encode()
                    path = build / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                    records[name] = dict(bytes=len(raw), sha256=digest(raw))
                sdk = b"CONFIG_APP_REPRODUCIBLE_BUILD=y\n"
                (build / "sdkconfig").write_bytes(sdk)
                (build / "config").mkdir()
                (build / "config/sdkconfig.json").write_text('{"APP_REPRODUCIBLE_BUILD":true}', encoding="utf-8")
                rows.append(dict(enabled=enabled, build_directory=str(build.resolve()), build_pass=True,
                    config_value=enabled, observer_source_member=enabled, reproducible_build=True,
                    observer_elf_symbol_count=int(enabled), artifacts=records, app_partition_bytes=6000*1024,
                    app_partition_free_bytes=6000*1024-records["xiaozhi.bin"]["bytes"], sdkconfig_sha256=digest(sdk)))
            data = dict(schema="deskbot.expression.e1_26_full_body_build/v1", edition="E1.26",
                result="locked_full_body_builds_passed", audit_pass=True, full_firmware_build_passed=True,
                deployment_eligible=False, flash_authorized=False,
                safety={key: False for key in ("hardware_accessed", "serial_accessed", "device_written", "nvs_changed", "flash_executed")},
                source_manifest_sha256=digest(manifest), source_sha256={"main/fixture.cc": "fixture-hash"},
                source_lock_sha256=digest(lock), effective_lock_sha256=digest(b"isolated-synthetic-lock"),
                components=dict(actual_contents_match_lock=True, components=["synthetic-component"], count=1),
                configurations=rows, unchanged_outputs=shared)
            report = root / "report.json"
            report.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(AUDIT, "ROOT", root):
                yield report

    def test_source_manifest_uses_git_snapshot_check(self):
        report = AUDIT.audit_manifest()
        self.assertEqual(report["check"], "scripts/update-body-source-manifest.py --check passed")
        self.assertGreater(report["entries"], 0)

    def test_schema_contract_uses_schema_id_and_payload_schema_separately(self):
        contract = AUDIT.audit_contract()
        self.assertTrue(contract["default_enabled"] is False)
        self.assertEqual(contract["capacity"], 24)

    def test_host_cpp_report_matches_current_sources(self):
        report = AUDIT.audit_cpp_report(self.require_local_report("tmp/e1-26-cpp-observer-report.json"))
        self.assertEqual(report["result"], "host_cpp_observer_tests_passed")
        self.assertTrue(report["source_hashes_match"])

    def test_host_cpp_report_rejects_stale_source_hashes(self):
        # This rejection remains covered even in a clone without local builds.
        data = {"result": "host_cpp_observer_tests_passed", "audit_pass": True,
                "source_sha256": {"observer_cc": "0" * 64}}
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e1-26-audit-") as folder:
            altered = Path(folder) / "cpp-report.json"
            altered.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                AUDIT.audit_cpp_report(altered)

    def test_minimal_idf_report_verifies_both_distinct_configurations(self):
        report = AUDIT.audit_minimal_idf_report(self.require_local_report("tmp/e1-26-idf-preflight-report.json"))
        self.assertEqual(report["result"], "minimal_idf_preflight_passed")
        self.assertEqual({item["enabled"] for item in report["configurations"]}, {False, True})

    def test_minimal_idf_report_rejects_config_mode_mismatch(self):
        source = self.require_local_report("tmp/e1-26-idf-preflight-report.json")
        data = json.loads(source.read_text(encoding="utf-8"))
        data["configurations"][0]["config_value"] = True
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e1-26-audit-") as folder:
            altered = Path(folder) / "idf-report.json"
            altered.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "observer state"):
                AUDIT.audit_minimal_idf_report(altered)

    def test_locked_full_body_report_verifies_both_builds_and_partition_margin(self):
        with self.full_body_fixture() as path:
            report = AUDIT.audit_full_body_report(path)
        self.assertEqual(report["result"], "locked_full_body_builds_passed")
        self.assertEqual({item["enabled"] for item in report["configurations"]}, {False, True})
        self.assertTrue(report["non_app_outputs_identical"])

    def test_locked_full_body_report_rejects_flash_authorization(self):
        data = {"schema": "deskbot.expression.e1_26_full_body_build/v1",
                "edition": "E1.26", "result": "locked_full_body_builds_passed",
                "audit_pass": True, "full_firmware_build_passed": True,
                "deployment_eligible": False, "flash_authorized": True}
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e1-26-audit-") as folder:
            altered = Path(folder) / "full-build-report.json"
            altered.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must not authorize"):
                AUDIT.audit_full_body_report(altered)

    def test_locked_full_body_report_requires_reproducible_configuration(self):
        with self.full_body_fixture() as path:
            data = json.loads(path.read_bytes())
            data["configurations"][0]["reproducible_build"] = False
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "observer/reproducibility"):
                AUDIT.audit_full_body_report(path)

    def test_missing_local_build_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="e1-26-missing-") as folder:
            for audit in (AUDIT.audit_cpp_report, AUDIT.audit_minimal_idf_report,
                          AUDIT.audit_full_body_report):
                with self.subTest(audit=audit.__name__):
                    with self.assertRaisesRegex(ValueError, "missing .* report"):
                        audit(Path(folder) / "missing-report.json")


if __name__ == "__main__":
    unittest.main()
