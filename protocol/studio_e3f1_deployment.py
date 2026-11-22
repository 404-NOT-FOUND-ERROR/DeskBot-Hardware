"""Offline E1.45 then E3f-1 installation rehearsal; no hardware transport."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from protocol import studio_app_deployment as app
from protocol import studio_e3d2b_deployment as e3d2b
from protocol import studio_resource_installation as resources

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_RUN = Path("tmp/e145-assets-20261108-e3d2b-offline1")
RESOURCE_PREPARATION = Path("artifacts/resource-installation-e3d2b-20261108/offline-preparation.json")
RESOURCE_READINESS = Path("artifacts/resource-installation-e3d2b-20261108/offline-readiness.json")
RESOURCE_READINESS_SHA = "c698e397ffe22b89cd69de01892000add4dcc0c930d18a63bc12618eeac66f42"
RESOURCE_CANDIDATE_REPORT = Path("artifacts/expression-e1-45-staging-20261104/report.json")
RESOURCE_CANDIDATE_REPORT_SHA = "da57f7a63a23a401a41ae70b068cc52409cc3a22d01395c01b7a58ce627c8b4d"
APP_BUILD = Path("tmp/studio-shy-e3f1/on/xiaozhi.bin")
APP_ELF = Path("tmp/studio-shy-e3f1/on/xiaozhi.elf")
BUILD_REPORT = Path("artifacts/studio-shy-e3f1/firmware-build-report.json")
BUILD_REPORT_SHA = "ecfe04320e825c55d90f1a60b003975194b53c96a3171739c5ce1fd5a5207cee"
TEST_REPORT = Path("artifacts/studio-shy-e3f1/offline-validation-5.json")
TEST_REPORT_SHA = "88e1a7082c423826063dcfebe5652507ee2f8ca73ab1396da015ba4355f44d9b"
RESOURCE_PLAN_ID = "e16fd9e0a1376373bbd47adc0bf4d8a1614337709a1913775adce2262899e228"
RESOURCE_SNAPSHOT_SHA = "e99011225ef74874d105b5ab2653392d3a27aec39e8585e61850c490acd11615"
APP_SHA = "7448965b325cd7e4e62e0342d216cc79636669758496f31e6bbc9995c3da669c"
APP_ELF_SHA = "d8e9bda4c0117ef6db8917d3eca4091865ab4b44719666bbf6a6244f23b88224"
APP_BYTES = 4_518_160
APP_ERASE_END = 0x460000


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(root: Path, path: Path) -> bytes:
    candidate = resources._safe(root, path)
    if path.is_absolute() or not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("offline_input_path_invalid")
    return candidate.read_bytes()


def inputs(root: Path = ROOT):
    build_raw = _read(root, BUILD_REPORT)
    tests_raw = _read(root, TEST_REPORT)
    if sha(build_raw) != BUILD_REPORT_SHA or sha(tests_raw) != TEST_REPORT_SHA:
        raise ValueError("reviewed_E3f1_evidence_changed")
    build, tests = map(json.loads, (build_raw, tests_raw))
    candidate = _read(root, APP_BUILD)
    elf = _read(root, APP_ELF)
    if (build.get("schema") != "deskbot.studio_shy_build/v1"
            or build.get("baseline") != "E3d-2b" or build.get("result") != "off_on_builds_verified"
            or build.get("device_accessed") is not False or build.get("device_written") is not False
            or build.get("source_delta") != sorted({
                "main/Kconfig.projbuild",
                "main/boards/esp_vocat/studio_expression_control.h",
                "main/boards/esp_vocat/studio_expression_adapter.cc",
                "main/boards/esp_vocat/studio_display_readiness.h"})
            or build.get("new_tasks") != 0 or build.get("hardware_acceptance") != "pending"
            or build.get("builds", [{}])[-1].get("enabled") is not True
            or build["builds"][-1].get("artifacts", {}).get("xiaozhi.bin") !=
                dict(bytes=APP_BYTES, sha256=APP_SHA)
            or build["builds"][-1].get("app_elf_sha256") != APP_ELF_SHA
            or len(candidate) != APP_BYTES or sha(candidate) != APP_SHA or sha(elf) != APP_ELF_SHA):
        raise ValueError("reviewed_E3f1_candidate_mismatch")
    if (tests.get("result") != "passed" or tests.get("tests") != 232
            or tests.get("failures") != 0 or tests.get("errors") != 0 or tests.get("skipped") != 0
            or tests.get("source_unchanged") is not True or tests.get("hardware_accessed") is not False
            or tests.get("device_written") is not False):
        raise ValueError("E3f1_offline_validation_missing")
    return build, tests, candidate


def resources_ready(root: Path = ROOT):
    plan = resources.load(root / RESOURCE_RUN)
    resource_report = json.loads(_read(root, RESOURCE_PREPARATION))
    readiness_raw = _read(root, RESOURCE_READINESS)
    resource_check = json.loads(readiness_raw)
    candidate_report_raw = _read(root, RESOURCE_CANDIDATE_REPORT)
    candidate_report = json.loads(candidate_report_raw)
    evidence = {item.get("path"): item for item in resource_check.get("evidence", [])}
    expected_checks = {
        "snapshot_32m", "app_e3d2b", "active_resources_e118", "partition_table",
        "candidate_8m", "candidate_e145", "candidate_package_boundary",
        "candidate_package_digest", "candidate_padding", "within_7m_budget",
        "legacy_version_mismatch_identified",
    }
    if (plan.id != RESOURCE_PLAN_ID or resources.plan_baseline(plan) != "E3d-2b"
            or plan.metadata["mode"] != "offline" or app.sha(plan.snapshot) != RESOURCE_SNAPSHOT_SHA
            or resource_report.get("result") != "prepared_and_checked"
            or resource_report.get("baseline") != "E3d-2b"
            or resource_report.get("run") != RESOURCE_RUN.as_posix()
            or resource_report.get("device_accessed") is not False
            or resource_report.get("device_written") is not False
            or resource_report.get("prepare", {}).get("plan_id") != plan.id
            or resource_report.get("check", {}).get("plan_id") != plan.id
            or any(record.get("result") != "prepared" or record.get("mode") != "offline"
                   or record.get("offset") != resources.START or record.get("bytes") != resources.SIZE
                   or record.get("rollback_sha256") != plan.metadata["rollback_sha256"]
                   or record.get("candidate_sha256") != plan.metadata["candidate_sha256"]
                   or record.get("device_accessed") is not False
                   for record in (resource_report["prepare"], resource_report["check"]))
            or resource_check.get("schema") != "deskbot.resource_installation_readiness/v1"
            or resource_check.get("result") != "inputs_verified_adapter_pending"
            or resource_check.get("mode") != "offline_reference"
            or resource_check.get("hardware_accessed") is not False
            or resource_check.get("device_written") is not False
            or sha(readiness_raw) != RESOURCE_READINESS_SHA
            or resource_check.get("app", {}).get("edition") != "E3d-2b"
            or resource_check.get("app", {}).get("bytes") != e3d2b.APP_SIZE
            or resource_check.get("app", {}).get("sha256") != e3d2b.APP_SHA
            or sha(candidate_report_raw) != RESOURCE_CANDIDATE_REPORT_SHA
            or candidate_report.get("schema") != "deskbot.expression_e1_45_staging/v1"
            or candidate_report.get("edition") != "E1.45"
            or candidate_report.get("result") != "offline_package_prepared_hardware_preflight_pending"
            or candidate_report.get("audit_pass") is not True
            or candidate_report.get("hardware_accessed") is not False
            or candidate_report.get("device_written") is not False
            or candidate_report.get("partition", {}).get("offset") != resources.START
            or candidate_report.get("partition", {}).get("bytes") != resources.SIZE
            or candidate_report.get("candidate", {}).get("partition_bytes") != resources.SIZE
            or candidate_report.get("candidate", {}).get("partition_sha256") != resources.CANDIDATE_SHA
            or candidate_report.get("candidate", {}).get("package_bytes") != 5_981_260
            or candidate_report.get("candidate", {}).get("package_sha256") !=
                "c6381793a88da27947651b0b7096963af80ccf6a2c8ee5f5e9df254e7c3a2c91"
            or candidate_report.get("selection", {}).get("semantic") != "embarrassed"
            or resource_check.get("current_resource") != "E1.18"
            or resource_check.get("candidate_resource") != "E1.45"
            or resource_check.get("intended_write") != dict(offset=resources.START,
                end_exclusive=resources.START + resources.SIZE, bytes=resources.SIZE,
                target="COM4_assets")
            or resource_check.get("budget") != dict(package_bytes=5_981_260,
                partition_bytes=resources.SIZE, free_bytes=2_407_348,
                soft_limit_free_bytes=1_358_772)
            or resource_check.get("budget", {}).get("free_bytes", 0) <
                resource_check.get("budget", {}).get("soft_limit_free_bytes", 1)
            or set(resource_check.get("checks", {})) != expected_checks
            or any(value is not True for value in resource_check.get("checks", {}).values())
            or evidence.get("tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin", {}).get("bytes") != resources.SIZE
            or evidence.get("tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin", {}).get("sha256") != resources.CANDIDATE_SHA
            or evidence.get("artifacts/expression-e1-45-staging-20261104/report.json", {}).get("bytes") != 5149
            or evidence.get("artifacts/expression-e1-45-staging-20261104/report.json", {}).get("sha256") !=
                RESOURCE_CANDIDATE_REPORT_SHA):
        raise ValueError("reviewed_E1.45_offline_plan_mismatch")
    return plan


def simulate_resource_install(plan):
    plan.validate()
    if plan.metadata["mode"] != "offline":
        raise ValueError("offline_resource_plan_required")
    simulated = plan.snapshot[:resources.START] + plan.image + plan.snapshot[resources.START + resources.SIZE:]
    if (len(simulated) != app.FLASH
            or resources.sha(simulated[0x8000:0x8C00]) != resources.TABLE_SHA
            or simulated[0x8C00:0x9000] != b"\xff" * 1024
            or app.sha(simulated[app.START:app.START + e3d2b.APP_SIZE]) != e3d2b.APP_SHA
            or resources.sha(simulated[resources.START:resources.START + resources.SIZE]) != resources.CANDIDATE_SHA):
        raise ValueError("simulated_E1.45_mismatch")
    return simulated


def make_app_plan(snapshot: bytes, candidate: bytes, identity: str):
    if (type(snapshot) is not bytes or type(candidate) is not bytes or len(snapshot) != app.FLASH
            or len(candidate) != APP_BYTES or sha(candidate) != APP_SHA):
        raise ValueError("E3f1_offline_plan_inputs_mismatch")
    if sha(snapshot[app.START:app.START + e3d2b.APP_SIZE]) != e3d2b.APP_SHA:
        raise ValueError("E3d2b_rollback_source_mismatch")
    plan = app.prepare(snapshot, candidate, identity, e3d2b.APP_SIZE, edition="E3c")
    check_app_plan(plan, expected_snapshot=snapshot, candidate=candidate)
    return plan


def check_recovery_plan(plan):
    """Check retained original bytes without reading a candidate or build tree."""
    plan.validate()
    metadata = plan.metadata
    if (metadata["edition"] != "E3c" or metadata["original_bytes"] != e3d2b.APP_SIZE
            or metadata["candidate_bytes"] != APP_BYTES or metadata["candidate_sha256"] != APP_SHA
            or metadata["identity_sha256"] != e3d2b.IDENTITY_SHA
            or metadata["erase_bytes"] != APP_ERASE_END - app.START
            or app.START + metadata["erase_bytes"] != APP_ERASE_END
            or app.sha(plan.rollback[:e3d2b.APP_SIZE]) != e3d2b.APP_SHA):
        raise ValueError("E3f1_App_write_or_rollback_range_mismatch")
    if (sha(plan.snapshot[0x8000:0x8C00]) != resources.TABLE_SHA
            or plan.snapshot[0x8C00:0x9000] != b"\xff" * 1024):
        raise ValueError("E3f1_partition_table_mismatch")
    if sha(plan.snapshot[resources.START:resources.START + resources.SIZE]) != resources.CANDIDATE_SHA:
        raise ValueError("E1.45_must_be_installed_before_E3f1_App")
    return True


def check_app_plan(plan, *, expected_snapshot: bytes | None = None, candidate: bytes | None = None):
    check_recovery_plan(plan)
    if not isinstance(plan, app.Plan) or app.sha(plan.image) != APP_SHA:
        raise ValueError("complete_E3f1_App_plan_required")
    if expected_snapshot is not None and plan.snapshot != expected_snapshot:
        raise ValueError("E3f1_App_snapshot_mismatch")
    if candidate is not None and plan.image != candidate:
        raise ValueError("E3f1_App_candidate_mismatch")
    return True


def rehearse(root: Path = ROOT):
    build, tests, candidate = inputs(root)
    resource_plan = resources_ready(root)
    after_resources = simulate_resource_install(resource_plan)
    app_plan = make_app_plan(after_resources, candidate, resource_plan.metadata["identity_sha256"])
    result = dict(schema="deskbot.studio_shy_deployment_offline/v1", stage="E3f-1",
        result="sequential_installation_and_recovery_ready_offline", mode="simulation_only",
        current_baseline=dict(app="E3d-2b", resources="E1.18"),
        install_order=["E1.45 resources", "read installed directory", "E3f-1 App"],
        resources=dict(plan_id=resource_plan.id, source="E1.18", candidate="E1.45",
            offset=resources.START, bytes=resources.SIZE, candidate_sha256=resources.CANDIDATE_SHA,
            expected_simulated_flash_sha256=app.sha(after_resources),
            install_plan_mode="offline", installable=False),
        app=dict(candidate="E3f-1", bytes=APP_BYTES, sha256=APP_SHA, app_elf_sha256=APP_ELF_SHA,
            offset=app.START, erase_bytes=app_plan.metadata["erase_bytes"], end_exclusive=APP_ERASE_END,
            rollback_app="E3d-2b", rollback_sha256=app.sha(app_plan.rollback),
            expected_snapshot_sha256=app.sha(after_resources), app_plan_id=app_plan.id,
            plan_mode="offline_simulation", installable=False),
        recovery=dict(app_failure="restore same-run E3d-2b App and preserve E1.45 resources",
            full_rollback_order=["restore E3d-2b App", "restore E1.18 resources"],
            resources_plan_id=resource_plan.id, offline_app_plan_requires_fresh_live_snapshot=True),
        evidence=dict(build_report_sha256=BUILD_REPORT_SHA, baseline_test_report_sha256=TEST_REPORT_SHA,
            build_result=build["result"], baseline_tests=tests["tests"],
            hardware_accessed=False, device_written=False))
    return result
