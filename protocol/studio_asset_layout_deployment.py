"""Reviewed layout App with E3f-4a as its same-run rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_eat_deployment as baseline
from protocol import studio_resource_package as resources
from protocol import storage_continuous_mmap_e3c3b_baseline as paths
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3f-4c", "E3f-4a"
SOURCE = Path("tmp/studio-layout-e3f4c-device/on/xiaozhi.bin")
PROJECT = SOURCE.parent.parent / "p"
BUILD_REPORT = Path("artifacts/studio-layout-e3f4c-device/firmware-build-report.json")
OFFLINE_REPORT = Path("artifacts/studio-layout-e3f4c-device/offline-validation.json")
PEER_REPORT = Path("artifacts/studio-layout-e3f4c-device/peer-review.json")
# Filled from the paired build before preparing an install plan.
BUILD_REPORT_SHA = 'f9590fa0ceaad3316ea4e1900d97a3ad42f64428fda86bff6e03b0a5335761d9'
APP_SIZE, APP_SHA, APP_ELF_SHA = 4521744, '36a6d8518281a57b1f15389b8b54b41fb6cf09d5f772e761f91cd3c6a6ec233d', 'e621cd55020aac48c0887f95c05c035ccfe912b043a454d87f66d1b4227d5392'
ERASE_BYTES, ORIGINAL_UNION_SHA = 4521984, '921cce9ceadb82cc9e7a2008f03029583f30bf89dbddd6510b515998953ad007'
ORIGINAL_SIZE, ORIGINAL_SHA = baseline.APP_SIZE, baseline.APP_SHA
IDENTITY_SHA = baseline.IDENTITY_SHA
REFERENCE = Path("tmp/e3c-live-20261109-e3f4a-live2")
REFERENCE_PLAN_ID = "7039e2cfb082d7b3a683234838b6bbde89548f9fc9e749d3ead0f64cad58df16"


def load_reference(root=ROOT):
    plan = baseline.check_plan(tx.load(paths.safe_path(root / REFERENCE, root)), root=root)
    if plan.id != REFERENCE_PLAN_ID:
        raise ValueError("accepted_E3f4a_reference_changed")
    snapshot = bytearray(plan.snapshot)
    snapshot[tx.START:tx.START + len(plan.candidate)] = plan.candidate
    raw = bytes(snapshot)
    validate_baseline(raw)
    return dict(snapshot=raw, identity_sha256=IDENTITY_SHA,
                app_union=raw[tx.START:tx.START + ((ORIGINAL_SIZE + 4095)//4096*4096)],
                report=dict(path=(REFERENCE / "plan.json").as_posix(),
                            sha256=tx.sha((root / REFERENCE / "plan.json").read_bytes())))


def validate_baseline(raw):
    if (type(raw) is not bytes or len(raw) != tx.FLASH
            or tx.sha(raw[tx.START:tx.START + ORIGINAL_SIZE]) != ORIGINAL_SHA
            or tx.sha(raw[0x8000:0x8C00]) != resources.TABLE_SHA
            or raw[0x8C00:0x9000] != b"\xff" * 1024
            or tx.sha(raw[0x1000000:0x1800000]) != resources.BASE_IMAGE_SHA):
        raise ValueError("accepted_E3f4a_E118_snapshot_required")


def check_current(raw, reference):
    validate_baseline(raw)
    expected = reference["snapshot"]
    if raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]:
        raise ValueError("current_E3f4a_static_bytes_changed")


def reviewed_candidate(raw=None, root=ROOT):
    image = paths.repo_bytes(root, SOURCE) if raw is None else raw
    if len(image) != APP_SIZE or tx.sha(image) != APP_SHA:
        raise ValueError("reviewed_layout_App_changed")
    facts = image_facts(image)
    offset = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != tx.FLASH or image[offset + 144:offset + 176].hex() != APP_ELF_SHA:
        raise ValueError("layout_App_capacity_or_ELF_changed")
    return image


def check_build_evidence(root=ROOT):
    raw = paths.repo_bytes(root, BUILD_REPORT)
    if tx.sha(raw) != BUILD_REPORT_SHA:
        raise ValueError("layout_build_report_changed")
    build = json.loads(raw)
    if (build.get("schema") != "deskbot.studio_asset_layout_build/v1"
            or build.get("baseline") != ROLLBACK_TARGET or build.get("result") != "off_on_builds_verified"
            or build.get("disabled_comparison", {}).get("executable_and_data_equal") is not True
            or (build.get("new_tasks"), build.get("usb_reads_per_iteration"), build.get("usb_yield_ms"),
                build.get("usb_stack_bytes"), build.get("usb_priority")) != (0, 1, 10, 6144, 1)
            or [b.get("enabled") for b in build.get("builds", [])] != [False, True]):
        raise ValueError("paired_layout_build_required")
    baseline.baseline.previous._source_manifest(root, PROJECT, build["source_sha256"], EDITION)
    for row in build["steps"]:
        if tx.sha(paths.repo_bytes(root, Path(row["log"]))) != row["sha256"]:
            raise ValueError("layout_build_log_changed")
    for label, row in zip(("off", "on"), build["builds"]):
        for name, record in row["artifacts"].items():
            data = paths.repo_bytes(root, SOURCE.parent.parent / label / name)
            if dict(bytes=len(data), sha256=tx.sha(data)) != record:
                raise ValueError("layout_build_artifact_changed")
        if (not all(row["protected_equal"].values()) or row["app_headroom_bytes"] < 1048576
                or row["internal_static_data_bytes"] > 65136 + 128):
            raise ValueError("layout_build_budget_changed")
    tests_raw = paths.repo_bytes(root, OFFLINE_REPORT)
    tests = json.loads(tests_raw)
    if tests.get("result") != "passed" or tests.get("tests_run", 0) < 30 or tests.get("skipped") != 0:
        raise ValueError("layout_offline_validation_required")
    for name, digest in tests["source_sha256"].items():
        if tx.sha(paths.repo_bytes(root, Path(name))) != digest:
            raise ValueError("layout_tested_source_changed: " + name)
    if tx.sha(paths.repo_bytes(root, Path(tests["log"]))) != tests["log_sha256"]:
        raise ValueError("layout_test_log_changed")
    peer_raw = paths.repo_bytes(root, PEER_REPORT)
    peer = json.loads(peer_raw)
    if peer.get("result") != "approved" or peer.get("build_report_sha256") != BUILD_REPORT_SHA:
        raise ValueError("layout_peer_review_required")
    image = reviewed_candidate(root=root)
    if build["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA):
        raise ValueError("layout_candidate_build_mismatch")
    return dict(image=image, candidate=dict(variant="asset-layout", bytes=APP_SIZE, sha256=APP_SHA,
                app_elf_sha256=APP_ELF_SHA), build_report_sha256=BUILD_REPORT_SHA,
                offline_report_sha256=tx.sha(tests_raw), path_audit_sha256=tx.sha(peer_raw))


def prepare_plan(snapshot, identity, image, root=ROOT):
    check_current(snapshot, load_reference(root))
    if identity != IDENTITY_SHA:
        raise ValueError("layout_plan_device_mismatch")
    plan = tx.prepare(snapshot, reviewed_candidate(image, root), identity, ORIGINAL_SIZE, edition="E3c")
    return check_plan(plan, root=root)


def check_plan(plan, *, candidate=True, root=ROOT):
    plan.validate()
    m = plan.metadata
    validate_baseline(plan.snapshot)
    if (m["edition"] != "E3c" or m["identity_sha256"] != IDENTITY_SHA
            or m["original_bytes"] != ORIGINAL_SIZE or m["candidate_bytes"] != APP_SIZE
            or m["candidate_sha256"] != APP_SHA or m["erase_bytes"] != ERASE_BYTES
            or tx.sha(plan.rollback) != ORIGINAL_UNION_SHA):
        raise ValueError("layout_plan_or_original_changed")
    if candidate:
        reviewed_candidate(plan.image, root)
        check_current(plan.snapshot, load_reference(root))
    return plan
