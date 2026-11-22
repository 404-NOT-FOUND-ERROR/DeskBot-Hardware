"""Pinned E3c-3a path-test App with the retained E3c-2 rollback reference."""
import json
import hashlib
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_e3c3_deployment as baseline
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("tmp/e3c3a-usb-path-20261106/off/xiaozhi.bin")
APP_SIZE = 4488224
APP_SHA = "a67aaec69b359039db52e1987ed6d9f2dc726243f2871d2b9718b6095c0d8375"
APP_ELF_SHA = "8dbe323dfc1ef79cfda12e5e9f42f8d217d4a86530d369d2599019ed55bac5bc"
E3C2_APP_UNION_SHA = "26973deb92efc6a0a95fb75fe78166ce295dc421941b02db0ed939a32f6180e1"
EDITION = "E3c-3a"
ROLLBACK_TARGET = "E3c-2"
BUILD_REPORT = Path("artifacts/ui-menu-regression-20261106/e3c3a-build.json")
OFFLINE_REPORT = Path("artifacts/ui-menu-regression-20261106/e3c3a-offline.json")
PATH_AUDIT = Path("artifacts/ui-menu-regression-20261106/e3c3-usb-path-audit.json")


def reviewed_candidate(raw=None, root=ROOT):
    if raw is None:
        raw = (root / SOURCE).read_bytes()
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError("reviewed E3c-3a menu-test App changed")
    if image_facts(raw)["configured_flash_bytes"] != tx.FLASH:
        raise ValueError("E3c-3a Flash capacity differs")
    start = image_facts(raw)["segments"][0]["file_offset"]
    if raw[start + 144:start + 176].hex() != APP_ELF_SHA:
        raise ValueError("E3c-3a ELF identity differs")
    return raw


def load_reference(root=ROOT):
    return baseline.load_reference(root)


def check_current(raw, reference):
    return baseline.check_current(raw, reference)


def check_plan(plan, *, candidate=True, reference=None):
    plan.validate()
    if (plan.metadata["edition"] != "E3c"
            or plan.metadata["original_bytes"] != baseline.ORIGINAL_SIZE
            or tx.sha(plan.rollback) != E3C2_APP_UNION_SHA):
        raise ValueError("E3c-3a plan must retain the verified E3c-2 App")
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError("complete E3c-3a candidate plan required")
        reference = reference or load_reference()
        if (plan.metadata["identity_sha256"] != reference["identity_sha256"]
                or plan.rollback != reference["app_union"]):
            raise ValueError("E3c-3a candidate plan reference differs")
        check_current(plan.snapshot, reference)
        reviewed_candidate(plan.image)
    elif not isinstance(plan, tx.RecoveryPlan):
        raise ValueError("E3c-3a recovery plan required")
    return plan


def check_build_evidence(root=ROOT):
    build_report = json.loads((root / BUILD_REPORT).read_bytes())
    offline_report = json.loads((root / OFFLINE_REPORT).read_bytes())
    path_audit = json.loads((root / PATH_AUDIT).read_bytes())
    if (build_report.get("edition") != EDITION or build_report.get("result") != "off_on_builds_verified"
            or build_report.get("hardware_accessed") is not False
            or build_report.get("device_written") is not False
            or build_report.get("source_delta") != ["main/boards/esp_vocat/studio_usb_diagnostic.cc"]
            or [row.get("query_enabled") for row in build_report.get("builds", [])] != [False, True]):
        raise ValueError("E3c-3a off/on build evidence required")
    if (offline_report.get("edition") != EDITION or offline_report.get("result") != "passed"
            or offline_report.get("tests") != 8 or offline_report.get("device_written") is not False
            or offline_report.get("hardware_accessed") is not False):
        raise ValueError("E3c-3a USB task tests required")
    if path_audit.get("result") != "historical_path_mismatch_confirmed":
        raise ValueError("E3c-3 USB path audit required")
    for row in build_report["builds"]:
        if (not row.get("config_equal_reference") or not all(row.get("protected_equal", {}).values())
                or row.get("app_headroom_bytes", 0) < 1048576):
            raise ValueError("E3c-3a configuration or protected output checks failed")
        for name, expected in row["artifacts"].items():
            path = root / row["build_directory"] / name
            raw = path.read_bytes()
            if expected != {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}:
                raise ValueError("E3c-3a build artifact changed: " + name)
    for step in build_report["steps"]:
        raw = (root / step["log"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != step["sha256"]:
            raise ValueError("E3c-3a build log changed")
    for name, expected in offline_report["tested_sources"].items():
        raw = (root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("E3c-3a test source changed: " + name)
    offline_log = (root / offline_report["log"]).read_bytes()
    if hashlib.sha256(offline_log).hexdigest() != offline_report["log_sha256"]:
        raise ValueError("E3c-3a test log changed")
    for name, expected in path_audit["evidence"].items():
        raw = (root / name).read_bytes()
        if (len(raw) != expected["bytes"]
                or hashlib.sha256(raw).hexdigest() != expected["sha256"]):
            raise ValueError("E3c-3 path audit input changed: " + name)
    row = build_report["builds"][0]
    image_record = row["artifacts"]["xiaozhi.bin"]
    raw = reviewed_candidate((root / SOURCE).read_bytes())
    if image_record != {"bytes": len(raw), "sha256": tx.sha(raw)}:
        raise ValueError("E3c-3a query-off image differs from audited build")
    return {"candidate": {"bytes": len(raw), "sha256": tx.sha(raw), "app_elf_sha256": APP_ELF_SHA},
            "build_report_sha256": tx.sha((root / BUILD_REPORT).read_bytes()),
            "offline_report_sha256": tx.sha((root / OFFLINE_REPORT).read_bytes()),
            "path_audit_sha256": tx.sha((root / PATH_AUDIT).read_bytes()),
            "image": raw}


def prepare_plan(snapshot, identity, image):
    plan = tx.prepare(snapshot, reviewed_candidate(image), identity, baseline.ORIGINAL_SIZE, edition="E3c")
    reference = {"snapshot": snapshot, "identity_sha256": identity,
                 "app_union": snapshot[tx.START:tx.START + plan.metadata["erase_bytes"]]}
    check_plan(plan, reference=reference)
    return plan
