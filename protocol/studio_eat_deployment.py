"""Eating App on the accepted E3d-2b/E1.18 snapshot, with exact App recovery."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_e3d2b_deployment as baseline
from protocol import studio_resource_installation as resources
from protocol import storage_continuous_mmap_e3c3b_baseline as paths
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3f-4a", "E3d-2b"
SOURCE = Path("tmp/studio-eat-e3f4a/on/xiaozhi.bin")
PROJECT = SOURCE.parent.parent / "p"
BUILD_REPORT = Path("artifacts/studio-eat-e3f4a/firmware-build-report.json")
OFFLINE_REPORT = Path("artifacts/studio-eat-e3f4a/offline-validation-5.json")
BROWSER_REPORT = Path("tmp/studio-eat-e3f4a/browser/validation.json")
BUILD_REPORT_SHA = 'b870e0181b262eaa8bff844f5f64e18aebebd4432ec366ef3a28992ee1d17c67'
APP_SIZE = 4518160
APP_SHA = '473d0803c8de38bffe4e2de7f9926079f9af77bd2c3622dae0cf4de2db0560e5'
APP_ELF_SHA = 'e55d9b4f60f80e23ea6e8fcaf64483a83beab93b28c1b7b43ed5d1a3539c878e'
ERASE_BYTES = 4521984
ORIGINAL_UNION_SHA = 'a461a4d431296b35a04150c267caba3ffde700fc56a40c27d3560edde35d321f'
ORIGINAL_SIZE, ORIGINAL_SHA = baseline.APP_SIZE, baseline.APP_SHA
IDENTITY_SHA = baseline.IDENTITY_SHA
REFERENCE = Path("tmp/e145-assets-20261108-e3d2b-offline1")
REFERENCE_SHA = "e99011225ef74874d105b5ab2653392d3a27aec39e8585e61850c490acd11615"


def load_reference(root=ROOT):
    plan = resources.load(paths.safe_path(root / REFERENCE, root))
    if (resources.plan_baseline(plan) != ROLLBACK_TARGET
            or tx.sha(plan.snapshot) != REFERENCE_SHA
            or plan.metadata["identity_sha256"] != IDENTITY_SHA):
        raise ValueError("accepted_E3d2b_E118_reference_required")
    return dict(snapshot=plan.snapshot, identity_sha256=IDENTITY_SHA,
                app_union=plan.snapshot[tx.START:tx.START + ((ORIGINAL_SIZE + 4095)//4096*4096)],
                report=dict(path=(REFERENCE / "plan.json").as_posix(),
                            sha256=tx.sha((root / REFERENCE / "plan.json").read_bytes())))


def check_current(raw, reference):
    expected = reference["snapshot"]
    if (len(raw) != tx.FLASH or len(expected) != tx.FLASH
            or raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]):
        raise ValueError("current_E3d2b_E118_static_bytes_changed")


def reviewed_candidate(raw=None, root=ROOT):
    image = paths.repo_bytes(root, SOURCE) if raw is None else raw
    if len(image) != APP_SIZE or tx.sha(image) != APP_SHA:
        raise ValueError("reviewed_eating_App_changed")
    facts = image_facts(image)
    offset = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != tx.FLASH or image[offset+144:offset+176].hex() != APP_ELF_SHA:
        raise ValueError("eating_App_capacity_or_ELF_changed")
    return image


def check_build_evidence(root=ROOT):
    raw = paths.repo_bytes(root, BUILD_REPORT)
    if tx.sha(raw) != BUILD_REPORT_SHA:
        raise ValueError("eating_build_report_changed")
    build = json.loads(raw)
    if (build.get("schema") != "deskbot.studio_eat_build/v1"
            or build.get("result") != "off_on_builds_verified"
            or build.get("baseline") != ROLLBACK_TARGET
            or build.get("disabled_comparison", {}).get("executable_and_data_equal") is not True
            or build.get("new_tasks") != 0 or build.get("usb_reads_per_iteration") != 1
            or build.get("usb_yield_ms") != 10 or build.get("usb_stack_bytes") != 6144
            or build.get("usb_priority") != 1 or build.get("device_written") is not False
            or build.get("device_accessed") is not False
            or [row.get("enabled") for row in build.get("builds", [])] != [False, True]):
        raise ValueError("eating_paired_build_required")
    baseline.previous._source_manifest(root, PROJECT, build["source_sha256"], EDITION)
    for step in build["steps"]:
        if tx.sha(paths.repo_bytes(root, Path(step["log"]))) != step["sha256"]:
            raise ValueError("eating_build_log_changed")
    for label, row in zip(("off", "on"), build["builds"]):
        folder = SOURCE.parent.parent / label
        for name, record in row["artifacts"].items():
            data = paths.repo_bytes(root, folder / name)
            if dict(bytes=len(data), sha256=tx.sha(data)) != record:
                raise ValueError("eating_build_artifact_changed: " + name)
        if (row["internal_static_data_bytes"] != 65136 or row["app_headroom_bytes"] < 1048576
                or not all(row["protected_equal"].values())
                or any(s["bytes"] > 2048 for s in row["compiler_stack_usage"].values())):
            raise ValueError("eating_memory_or_protected_outputs_changed")
    tests_raw = paths.repo_bytes(root, OFFLINE_REPORT)
    tests = json.loads(tests_raw)
    if (tests.get("result") != "passed" or tests.get("tests", 0) < 300
            or any(tests.get(k) != 0 for k in ("errors", "failures", "skipped"))
            or tests.get("source_unchanged") is not True
            or tests.get("candidate_source") != PROJECT.as_posix()
            or tests.get("hardware_accessed") is not False or tests.get("device_written") is not False):
        raise ValueError("eating_offline_validation_required")
    for name, digest in tests["source_sha256"].items():
        if tx.sha(paths.repo_bytes(root, Path(name))) != digest:
            raise ValueError("eating_tested_source_changed: " + name)
    if tx.sha(paths.repo_bytes(root, Path(tests["log"]))) != tests["log_sha256"]:
        raise ValueError("eating_test_log_changed")
    browser_raw = paths.repo_bytes(root, BROWSER_REPORT)
    browser = json.loads(browser_raw)
    if (browser.get("result") != "passed" or browser.get("checks_passed", 0) < 24
            or browser.get("hardware_accessed") is not False or browser.get("device_written") is not False):
        raise ValueError("eating_browser_validation_required")
    image = reviewed_candidate(root=root)
    if build["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA):
        raise ValueError("eating_candidate_build_mismatch")
    return dict(image=image, candidate=dict(variant="eat", bytes=APP_SIZE, sha256=APP_SHA,
                app_elf_sha256=APP_ELF_SHA), build_report_sha256=BUILD_REPORT_SHA,
                offline_report_sha256=tx.sha(tests_raw), path_audit_sha256=tx.sha(browser_raw))


def prepare_plan(snapshot, identity, image, root=ROOT):
    reference = load_reference(root)
    check_current(snapshot, reference)
    if identity != IDENTITY_SHA:
        raise ValueError("eating_plan_device_mismatch")
    plan = tx.prepare(snapshot, reviewed_candidate(image, root), identity, ORIGINAL_SIZE, edition="E3c")
    return check_plan(plan, root=root)


def check_plan(plan, *, candidate=True, root=ROOT):
    plan.validate()
    meta = plan.metadata
    resources.validate_snapshot(plan.snapshot, baseline=ROLLBACK_TARGET)
    if (meta["edition"] != "E3c" or meta["identity_sha256"] != IDENTITY_SHA
            or meta["original_bytes"] != ORIGINAL_SIZE or meta["candidate_bytes"] != APP_SIZE
            or meta["candidate_sha256"] != APP_SHA
            or meta["erase_bytes"] != ERASE_BYTES
            or tx.sha(plan.rollback) != ORIGINAL_UNION_SHA
            or tx.sha(plan.rollback[:ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("eating_App_plan_or_original_changed")
    # Recovery uses only durable same-run originals and fixed release identities.
    if candidate:
        reviewed_candidate(plan.image, root)
        check_current(plan.snapshot, load_reference(root))
    return plan
