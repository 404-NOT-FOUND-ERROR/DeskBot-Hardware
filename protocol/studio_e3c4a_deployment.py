"""Reviewed display-query increment with the accepted query-on App as rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_e3c3a_query_deployment as accepted
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION = "E3c-4a"
ROLLBACK_TARGET = "E3c-3a-query-on"
SOURCE = Path("tmp/e3c4a-display-20261107/on/xiaozhi.bin")
LIVE_REFERENCE = Path("tmp/e3c-live-20261106-e3c3a-query-on-214637")
ACCEPTANCE = Path("artifacts/ui-menu-regression-20261106/e3c3a-query-on-live-result.json")
BUILD_REPORT = Path("artifacts/studio-display-e3c4a-20261107/firmware-build-report.json")
OFFLINE_REPORT = Path("artifacts/studio-display-e3c4a-20261107/offline-validation.json")
PATCH = Path("artifacts/studio-display-e3c4a-20261107/source.patch")
# Pinned after isolated off/on builds and comparison with the accepted receiver.
APP_SIZE = 4490272
APP_SHA = "41a1cd709120c9397779bdf3f2721607df7b460151fb743e03c834c03182eb97"
APP_ELF_SHA = "21e786a848bafc342ce2d4d3307766958d7ec4e36f4639149c37dbdbdbf9842f"
BUILD_REPORT_SHA = "1e7c1ff38d8a2814f5e1167b25019a3b2b0477c23f3fb3f3f93df32da827cb54"


def reviewed_candidate(raw=None, root=ROOT):
    raw = (root / SOURCE).read_bytes() if raw is None else raw
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError("reviewed E3c-4a App changed")
    facts = image_facts(raw)
    first = facts["segments"][0]["file_offset"]
    if (facts["configured_flash_bytes"] != tx.FLASH
            or raw[first+144:first+176].hex() != APP_ELF_SHA):
        raise ValueError("E3c-4a capacity or ELF identity differs")
    return raw


def load_reference(root=ROOT):
    folder = root / LIVE_REFERENCE
    plan = accepted.check_plan(tx.load(folder), root=root)
    journal = tx.Journal(folder, plan).load()
    result = json.loads((folder / "install-result.json").read_bytes())
    receipt = json.loads((root / ACCEPTANCE).read_bytes())
    checks = receipt.get("user_acceptance", {})
    if (not journal or journal.get("phase") != "installed"
            or result.get("result") != "candidate_installed"
            or not all(result.get("readback", {}).get(key) is True
                       for key in ("app_byte_equal", "static_outside_app_equal", "nvs_equal"))
            or result["readback"].get("app_sha256") != tx.sha(plan.candidate)
            or result.get("boot", {}).get("firmware_matches_candidate") is not True
            or result.get("boot", {}).get("hello", {}).get("firmware", {}).get("app_elf_sha256") != accepted.APP_ELF_SHA
            or receipt.get("installed_app_sha256") != accepted.APP_SHA
            or not all(checks.get(key) is True for key in
                       ("idle_60s", "menu_open_return_5x", "head_touch_three", "sleep_wake"))):
        raise ValueError("accepted E3c-3a query-on installation and interaction required")
    end = tx.START + len(plan.candidate)
    snapshot = plan.snapshot[:tx.START] + plan.candidate + plan.snapshot[end:]
    return {"snapshot": snapshot, "app_union": plan.candidate,
            "identity_sha256": plan.metadata["identity_sha256"], "original_bytes": accepted.APP_SIZE,
            "report": {"path": ACCEPTANCE.as_posix(), "sha256": tx.sha((root / ACCEPTANCE).read_bytes())}}


def check_current(raw, reference):
    expected = reference["snapshot"]
    if len(raw) != tx.FLASH or len(expected) != tx.FLASH:
        raise ValueError("complete Flash reference required")
    if raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]:
        raise ValueError("current Flash differs from accepted E3c-3a query-on")


def check_build_evidence(root=ROOT):
    raw_report = (root / BUILD_REPORT).read_bytes()
    if tx.sha(raw_report) != BUILD_REPORT_SHA:
        raise ValueError("reviewed E3c-4a build report changed")
    report = json.loads(raw_report)
    if (report.get("edition") != EDITION or report.get("result") != "off_on_builds_verified"
            or report.get("hardware_accessed") is not False or report.get("device_written") is not False
            or report.get("disabled_comparison", {}).get("executable_and_other_data_equal") is not True
            or [row.get("display_query_enabled") for row in report.get("builds", [])] != [False, True]):
        raise ValueError("E3c-4a off/on comparison required")
    for row in report["builds"]:
        if row["app_headroom_bytes"] < 1048576 or not all(row["protected_equal"].values()):
            raise ValueError("E3c-4a budget or protected output differs")
        for name, saved in row["artifacts"].items():
            data = (root / row["build_directory"] / name).read_bytes()
            if saved != {"bytes": len(data), "sha256": tx.sha(data)}:
                raise ValueError("E3c-4a build artifact changed: " + name)
    for step in report["steps"]:
        if tx.sha((root / step["log"]).read_bytes()) != step["sha256"]:
            raise ValueError("E3c-4a build log changed")
    tests_raw = (root / OFFLINE_REPORT).read_bytes()
    tests = json.loads(tests_raw)
    if (tests.get("result") != "passed" or tests.get("tests") != 51
            or tests.get("new_integration_tests") != 9 or tests.get("hardware_accessed") is not False
            or tests.get("device_written") is not False):
        raise ValueError("E3c-4a native integration tests required")
    for name, digest in tests["tested_sources"].items():
        if tx.sha((root / name).read_bytes()) != digest:
            raise ValueError("E3c-4a tested source changed: " + name)
    if tx.sha((root / tests["log"]).read_bytes()) != tests["log_sha256"]:
        raise ValueError("E3c-4a test log changed")
    image = reviewed_candidate(root=root)
    candidate = {"variant": "display_query", "bytes": APP_SIZE, "sha256": APP_SHA, "app_elf_sha256": APP_ELF_SHA}
    if report["builds"][1]["artifacts"]["xiaozhi.bin"] != {"bytes": APP_SIZE, "sha256": APP_SHA}:
        raise ValueError("E3c-4a candidate differs from build report")
    return {"candidate": candidate, "image": image, "build_report_sha256": BUILD_REPORT_SHA,
            "offline_report_sha256": tx.sha(tests_raw), "path_audit_sha256": tx.sha((root / PATCH).read_bytes())}


def prepare_plan(snapshot, identity, image, root=ROOT):
    reference = load_reference(root)
    if identity != reference["identity_sha256"]:
        raise ValueError("display plan targets another device")
    check_current(snapshot, reference)
    plan = tx.prepare(snapshot, reviewed_candidate(image, root), identity, accepted.APP_SIZE, edition="E3c")
    check_plan(plan, reference=reference, root=root)
    return plan


def check_plan(plan, *, candidate=True, reference=None, root=ROOT):
    plan.validate()
    m = plan.metadata
    if (m["edition"] != "E3c" or m["original_bytes"] != accepted.APP_SIZE
            or m["candidate_bytes"] != APP_SIZE or m["candidate_sha256"] != APP_SHA
            or tx.sha(plan.rollback[:accepted.APP_SIZE]) != accepted.APP_SHA):
        raise ValueError("E3c-4a plan or query-on rollback differs")
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError("complete E3c-4a plan required")
        reference = reference or load_reference(root)
        if m["identity_sha256"] != reference["identity_sha256"]:
            raise ValueError("E3c-4a plan device differs")
        check_current(plan.snapshot, reference)
        reviewed_candidate(plan.image, root)
    elif not isinstance(plan, tx.RecoveryPlan):
        raise ValueError("saved E3c-4a recovery plan required")
    return plan
