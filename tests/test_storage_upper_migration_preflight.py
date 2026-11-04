"""Upper migration contract tests plus retained-file integration checks."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_n32r16 import MIB, PACKAGE_BYTES, proposals  # noqa: E402
from protocol.storage_upper_migration_preflight import (  # noqa: E402
    EMBEDDED_SPEECH_MODEL_SHA256, FUTURE_SEQUENCE, INVENTORY_SHA256, LIVE_MODEL_SHA256,
    PENDING, RECOVERY_SEQUENCE, UPPER_BYTES, future_geometry, protected_geometry,
    upper_plan, validate_accepted_baseline, validate_deferred_mapping, validate_members,
    validate_preflight_report, validate_resource_images, validate_upper_layout,
)


def report_fixture():
    return {
        "schema": "deskbot.upper_assets.preflight.v1", "stage": "E1.43c",
        "result": "offline_preflight_passed_live_pending",
        **{k: False for k in ("serial_accessed", "device_written", "firmware_changed",
             "table_generated", "flash_bundle_generated", "deployment_eligible", "continuous_mapping_accepted")},
        "layout": upper_plan(), "future_affected_ranges": future_geometry(),
        "protected_ranges": protected_geometry(), "pending_before_deployment": list(PENDING),
        "future_sequence": list(FUTURE_SEQUENCE), "future_recovery": list(RECOVERY_SEQUENCE),
        "recovery_design_status": "specified_not_implemented_or_fault_tested",
        "historical_inplace_plan_reused": False,
        "resources": {"members": 29, "inventory_sha256": INVENTORY_SHA256,
             "index_sha256": "ba853dbe8388cb9dbfd992431d9f972302035de5fe5f574f984542b5cf76fe69",
             "embedded_model_sha256": EMBEDDED_SPEECH_MODEL_SHA256, "surprised": "shocked.eaf"},
        "budget": {"partition_bytes": UPPER_BYTES, "package_bytes": PACKAGE_BYTES,
             "free_bytes": UPPER_BYTES - PACKAGE_BYTES, "publish_soft_limit_bytes": 7 * MIB,
             "additional_publish_budget_bytes": 7 * MIB - PACKAGE_BYTES, "reserve_bytes": MIB},
        "standalone_model": {"offset": 0xBC8000, "bytes": 800 * 1024, "write": False,
             "accepted_live_sha256": LIVE_MODEL_SHA256,
             "source": "accepted_A7_readback_reference_fresh_backup_still_required"},
    }


class ContractTests(unittest.TestCase):
    def test_selected_route_is_upper(self):
        validate_preflight_report(report_fixture())

    def test_inplace_route_rejected(self):
        for plan in proposals():
            if plan["id"] != "upper-assets-8m":
                with self.subTest(route=plan["id"]), self.assertRaises(ValueError):
                    validate_upper_layout(plan, b"")

    def test_wrong_upper_start_rejected(self):
        plan = upper_plan()
        plan["rows"][-1]["start"] = 0x5EC000
        with self.assertRaises(ValueError):
            validate_upper_layout(plan, b"")

    def test_wrong_upper_capacity_rejected(self):
        plan = upper_plan()
        plan["rows"][-1]["size_bytes"] = 12 * MIB
        with self.assertRaises(ValueError):
            validate_upper_layout(plan, b"")

    def test_model_relocation_rejected(self):
        plan = upper_plan()
        plan["rows"][5]["start"] = 0xDEC000
        with self.assertRaises(ValueError):
            validate_upper_layout(plan, b"")

    def test_removing_old_assets_rejected(self):
        plan = upper_plan()
        del plan["rows"][4]
        with self.assertRaises(ValueError):
            validate_upper_layout(plan, b"")

    def test_invalid_table_rejected(self):
        for raw in (b"", b"\xff" * 3072, b"\xff" * 4096):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                validate_upper_layout(upper_plan(), raw)

    def test_pending_tests_cannot_be_erased(self):
        report = report_fixture()
        report["pending_before_deployment"] = []
        with self.assertRaises(ValueError):
            validate_preflight_report(report)

    def test_offline_flags_cannot_be_promoted(self):
        for field in ("serial_accessed", "device_written", "firmware_changed", "table_generated",
                      "flash_bundle_generated", "deployment_eligible", "continuous_mapping_accepted"):
            for bad in (True, None, 0):
                report = report_fixture()
                report[field] = bad
                with self.subTest(field=field, value=bad), self.assertRaises(ValueError):
                    validate_preflight_report(report)

    def test_geometry_does_not_include_model_write(self):
        ranges = future_geometry()
        self.assertEqual([r["id"] for r in ranges], ["upper_assets", "partition_table"])
        self.assertEqual(sum(r["bytes"] for r in ranges), 8 * MIB + 4096)
        for row in ranges:
            self.assertEqual(row["offset"] % 4096, 0)
            self.assertEqual(row["bytes"] % 4096, 0)

    def test_commit_order_cannot_be_reversed(self):
        for field in ("future_affected_ranges", "future_sequence"):
            report = report_fixture()
            report[field].reverse()
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_preflight_report(report)

    def test_recovery_cannot_be_claimed_implemented(self):
        report = report_fixture()
        report["recovery_design_status"] = "implemented"
        with self.assertRaises(ValueError):
            validate_preflight_report(report)

    def test_recovery_order_cannot_be_reversed(self):
        report = report_fixture()
        report["future_recovery"].reverse()
        with self.assertRaises(ValueError):
            validate_preflight_report(report)

    def test_legacy_write_plan_cannot_be_reused(self):
        report = report_fixture()
        report["historical_inplace_plan_reused"] = True
        with self.assertRaises(ValueError):
            validate_preflight_report(report)

    def test_resource_identity_cannot_change(self):
        report = report_fixture()
        report["resources"]["surprised"] = "Happy.eaf"
        with self.assertRaises(ValueError):
            validate_preflight_report(report)

    def test_protected_model_cannot_be_removed(self):
        report = report_fixture()
        report["protected_ranges"] = [r for r in report["protected_ranges"] if r["name"] != "model"]
        with self.assertRaises(ValueError):
            validate_preflight_report(report)

    def test_standalone_model_write_rejected(self):
        report = report_fixture()
        report["standalone_model"]["write"] = True
        with self.assertRaises(ValueError):
            validate_preflight_report(report)

    def test_budget_reserve_cannot_be_consumed(self):
        report = report_fixture()
        report["budget"]["reserve_bytes"] = 0
        with self.assertRaises(ValueError):
            validate_preflight_report(report)

    def test_invalid_package_rejected(self):
        with self.assertRaises(ValueError):
            validate_resource_images(b"not-E118", b"", b"")

    def test_incomplete_inventory_rejected(self):
        with self.assertRaises(ValueError):
            validate_members([], {})


class RetainedInputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = ROOT / "tmp/expression-e1-21-package-player-20261026-run2/rollback-e1-18-exact.bin"
        table = ROOT / "tmp/e136-n32r16-layout-20261030/tables/02-upper-assets-8m.bin"
        if not package.exists() or not table.exists():
            raise unittest.SkipTest("local retained E1.18/E1.36 inputs required")
        cls.package = package.read_bytes()
        cls.old = (ROOT / "tmp/e135b-migration-20261029/old/assets.bin").read_bytes()
        cls.image = (ROOT / "tmp/e135b-migration-20261029/candidate/assets.bin").read_bytes()
        cls.table = table.read_bytes()
        cls.records = json.loads((ROOT / "tmp/e135b-migration-20261029/manifest.json").read_bytes())["source_inputs"]["asset_members"]
        spec = importlib.util.spec_from_file_location("e143c_test_resource_parser", ROOT / "scripts/audit-expression-resources.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.members = module.parse_package_bytes(cls.package, modern=False)["members"]

    def test_retained_layout_and_resources(self):
        validate_upper_layout(upper_plan(), self.table)
        validate_resource_images(self.package, self.old, self.image)
        self.assertEqual(validate_members(self.records, self.members)["members"], 29)

    def test_inplace_table_rejected(self):
        table = (ROOT / "tmp/e135b-migration-20261029/candidate/partition-table.bin").read_bytes()[:3072]
        with self.assertRaises(ValueError):
            validate_upper_layout(upper_plan(), table)

    def test_package_byte_change_rejected(self):
        changed = bytes([self.package[0] ^ 1]) + self.package[1:]
        with self.assertRaises(ValueError):
            validate_resource_images(changed, self.old, self.image)

    def test_padding_byte_change_rejected(self):
        changed = self.image[:-1] + b"\x00"
        with self.assertRaisesRegex(ValueError, "padding"):
            validate_resource_images(self.package, self.old, changed)

    def test_member_byte_change_rejected(self):
        changed = dict(self.members)
        changed["shocked.eaf"] = b"\x00" + changed["shocked.eaf"][1:]
        with self.assertRaises(ValueError):
            validate_members(self.records, changed)

    def test_missing_embedded_model_rejected(self):
        changed = dict(self.members)
        del changed["srmodels.bin"]
        with self.assertRaises(ValueError):
            validate_members(self.records, changed)

    def test_changed_inventory_hash_rejected(self):
        changed = deepcopy(self.records)
        changed[0]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_members(changed, self.members)

    def test_b_remains_live_pending(self):
        path = ROOT / "artifacts/expression-continuous-mmap-e1-43b-20261103/readiness.json"
        readiness = json.loads(path.read_bytes())
        validate_deferred_mapping(readiness)
        for field in ("cpu_verified", "physical_interaction_accepted"):
            changed = deepcopy(readiness)
            changed[field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_deferred_mapping(changed)

    def test_accepted_a7_model_is_live_reference(self):
        base = ROOT / "artifacts/expression-upper-snapshot-e1-43a7-20261103"
        live = json.loads((base / "live-report.json").read_bytes())
        accepted = json.loads((base / "acceptance-20261103.json").read_bytes())
        validate_accepted_baseline(live, accepted)
        changed = deepcopy(live)
        # Old build-reference tail differs from the accepted live tail.
        changed["restored_ranges"]["model"]["sha256"] = "e33026406eba69154d1a9bb04de156e0a9b36c215f47276cd00503cebc93b97d"
        with self.assertRaises(ValueError):
            validate_accepted_baseline(changed, accepted)

    def test_historical_preflight_rejects_changed_source_manifest(self):
        spec = importlib.util.spec_from_file_location("e143c_auditor_test", ROOT / "scripts/audit-expression-e1-43c-upper-preflight.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        report = json.loads((ROOT / "artifacts/expression-upper-migration-e1-43c-20261103/preflight.json").read_bytes())
        validate_preflight_report(report)
        self.assertEqual(report["firmware_source_files_verified"], 1062)
        self.assertFalse(report["deployment_eligible"])
        with self.assertRaisesRegex(ValueError, "input digest changed: firmware/body/SOURCE_MANIFEST"):
            module.make_report()


if __name__ == "__main__":
    unittest.main()
