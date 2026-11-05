#!/usr/bin/env python3
"""Run the explicitly scoped portable tests used by the public repository."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FILES = (
    "test_deskbot_studio_e2a.py",
    "test_deskbot_studio_e2b.py",
    "test_expression_control.py",
    "test_deskbot_studio_e2c.py",
    "test_body_console_observation.py",
    "test_deskbot_studio_e3a.py",
    "test_device_link.py",
    "test_deskbot_studio_e3b.py",
    "test_studio_app_deployment.py",
    "test_studio_e3c_deployment.py",
    "test_studio_e3c8_deployment.py",
    "test_studio_expression_receiver.py",
    "test_studio_request_framer_cpp.py",
    "test_studio_usb_session.py",
    "test_studio_usb_transport.py",
    "test_studio_runtime_snapshot.py",
    "test_studio_runtime_query.py",
    "test_studio_display_query.py",
    "test_studio_expression_control.py",
    "test_studio_control_link.py",
    "test_studio_control_native.py",
    "test_deskbot_studio_e3c.py",
    "test_studio_installed_catalog.py",
    "test_deskbot_studio_e3d.py",
    "test_studio_recovery.py",
    "base_event_protocol_test.py", "test-expression-capabilities.py",
    "test-expression-preview.py", "test_printf_portability_guard.py",
    "test_body_facts_consumer.py", "test_body_facts_protocol.py",
    "test_eaf_compression.py", "test_eaf_encode.py", "test_eaf_preview.py",
    "test_magnetic_recognition.py", "test_magnetic_telemetry.py",
    "test-expression-binding-decision.py",
    "test-expression-e1-15-surprised-shocked.py",
    "test-expression-e1-16-repack.py",
    "test-expression-e1-17-player.py",
    "test-expression-node-preview-e1-19.py",
    "test-expression-e1-20-mapping.py",
    "test-expression-e1-21-package-player.py",
    "test-expression-e1-22-candidate-preview.py",
    "test-expression-e1-23-deployment-preflight.py",
    "test-expression-e1-24-coverage.py",
    "test-expression-e1-25-observation.py", "test-expression-e1-25-audit.py",
    "test-expression-e1-26-observation.py", "test-expression-e1-26-cpp.py",
    "test-expression-e1-26-audit.py",
    "test-expression-e1-27-observation.py",
    "test-expression-e1-28-semantic-review.py",
    "test-expression-e1-29-capture.py",
    "test-expression-e1-29-workbench.py",
    "test_o2a_semantic_mapping.py", "test_o2b_event_journal.py", "test_role_profile.py",
    "test-role-expression-workbench-e1-31.py",
    "test-role-expression-audit-e1-32.py", "test_eaf_preprocess.py",
    "test-expression-e1-34-preprocess.py",
    "test-expression-e1-33-import.py",
    "test_storage_budget.py",
    "test_storage_n32r16.py",
    "test_storage_capacity.py",
    "test_storage_addressing.py",
    "test_storage_capacity_boot.py",
    "test_capacity_query.py",
    "test_capacity_tool_contract.py",
    "test_storage_octal.py",
    "test_storage_migration.py",
    "test_storage_live_backup.py",
    "test_mcp_visibility_e1_42.py",
    "test_mcp_host_visibility_e1_42.py",
    "test_capacity_serial_e1_42_3.py",
    "test_storage_upper_pattern.py",
    "test_storage_upper_pattern_runtime.py",
    "test_storage_upper_experiment.py",
    "test_storage_upper_experiment_esptool.py",
    "test_storage_upper_snapshot.py",
    "test_upper_experiment_entrypoint.py",
    "test_upper_backup_gate.py",
    "test_upper_live_audit.py",
    "test_storage_continuous_mmap.py",
    "test_storage_continuous_mmap_experiment.py",
    "test_storage_continuous_mmap_baseline.py",
    "test_storage_upper_migration_preflight.py",
    "test_storage_upper_migration_transaction.py",
    "test_storage_upper_migration_esptool.py",
    "test_storage_upper_migration_live_audit.py",
    "test_eaf_legacy_terminal.py", "test-expression-e1-44-adaptation.py",
    "test_expression_resource_staging.py",
)


def main() -> int:
    import jsonschema  # Schema coverage must not silently disappear.
    (ROOT / "tmp").mkdir(exist_ok=True)
    suite = unittest.TestSuite()
    for index, name in enumerate(FILES):
        spec = importlib.util.spec_from_file_location(f"public_tests_{index}", ROOT / "tests" / name)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load test file: " + name)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"Portable scope: {result.testsRun} tests, {len(result.skipped)} skipped; no hardware or full firmware build.")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
