"""Display-query release with the accepted E3c-3b App as exact rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import storage_continuous_mmap_e3c3b_baseline as accepted
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3c-4b", "E3c-3b"
SOURCE = Path("tmp/studio-stable-ladder-20261108/display/on/xiaozhi.bin")
PROJECT = Path("tmp/studio-stable-ladder-20261108/display/p")
LIVE_REFERENCE = Path("tmp/e143b-e3c3b-20261107-live1")
SNAPSHOT_SHA = "51b99672357cef3ffa427ae7ac14094cded121b7b44cabb16206b793a79877a6"
BUILD_REPORT = Path("artifacts/studio-stable-ladder-20261108/display-build.json")
OFFLINE_REPORT = BUILD_REPORT.with_name("display-offline-validation.json")
PATCH = BUILD_REPORT.with_name("display.patch")
APP_SIZE = 4490272
APP_SHA = "cb4d8f943109f4625dbb3f810e21b5ce78272aef557a681b2f939e51696f5afb"
APP_ELF_SHA = "32d15b1cf2108b51e9f14a4574edee0596c2ae822e6d536cf247387f6d39df7b"
BUILD_REPORT_SHA = "37284b49239e7670cde6ef8bbde9fdee3fd1f4bb2aa605b2cc68ced99fbfe99d"
OFFLINE_REPORT_SHA = "2dc8931a1e010c365e3f5742a8e1e8a3147438b574c42e7dc7f728f6f176ae56"
ORIGINAL_SIZE, ORIGINAL_SHA = accepted.APP_BYTES_EXPECTED, accepted.APP_SHA256_EXPECTED


def reviewed_candidate(raw=None, root=ROOT):
    raw = (root / SOURCE).read_bytes() if raw is None else raw
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError("reviewed E3c-4b App changed")
    facts = image_facts(raw)
    first = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != tx.FLASH or raw[first+144:first+176].hex() != APP_ELF_SHA:
        raise ValueError("E3c-4b capacity or ELF identity differs")
    return raw


def load_reference(root=ROOT):
    snapshot = (root / LIVE_REFERENCE / "snapshot-32m.bin").read_bytes()
    if tx.sha(snapshot) != SNAPSHOT_SHA:
        raise ValueError("retained E3c-3b snapshot changed")
    saved = json.loads((root / LIVE_REFERENCE / "plan.json").read_bytes())
    identity = saved["metadata"]["identity_sha256"]
    accepted.validate_e3c3b_snapshot(snapshot, identity)
    receipt = (root / accepted.ACCEPTANCE).read_bytes()
    if tx.sha(receipt) != accepted.ACCEPTANCE_SHA256:
        raise ValueError("accepted E3c-3b interaction record changed")
    return dict(snapshot=snapshot, app_union=snapshot[tx.START:tx.START+ORIGINAL_SIZE],
        identity_sha256=identity, original_bytes=ORIGINAL_SIZE,
        report=dict(path=accepted.ACCEPTANCE.as_posix(), sha256=tx.sha(receipt)))


def check_current(raw, reference):
    expected = reference["snapshot"]
    if len(raw) != tx.FLASH or len(expected) != tx.FLASH:
        raise ValueError("complete Flash reference required")
    # Runtime NVS may change; App, resources and every other byte stay bound.
    if raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]:
        raise ValueError("current Flash differs from accepted E3c-3b")


def check_build_evidence(root=ROOT):
    raw = (root / BUILD_REPORT).read_bytes()
    if tx.sha(raw) != BUILD_REPORT_SHA:
        raise ValueError("reviewed E3c-4b build report changed")
    report = json.loads(raw)
    if (report.get("stage") != "display" or report.get("baseline") != ROLLBACK_TARGET
            or report.get("result") != "off_on_builds_verified"
            or report.get("device_accessed") is not False or report.get("device_written") is not False
            or report.get("disabled_comparison", {}).get("executable_and_data_equal") is not True
            or [row.get("enabled") for row in report.get("builds", [])] != [False, True]):
        raise ValueError("E3c-4b isolated display build required")
    for row in report["builds"]:
        if row["app_headroom_bytes"] < 1048576 or not all(row["protected_equal"].values()):
            raise ValueError("E3c-4b budget or protected output differs")
        for name, item in row["artifacts"].items():
            data = (root / row["build_directory"] / name).read_bytes()
            if item != dict(bytes=len(data), sha256=tx.sha(data)):
                raise ValueError("E3c-4b build artifact changed: " + name)
    for step in report["steps"]:
        if tx.sha((root / step["log"]).read_bytes()) != step["sha256"]:
            raise ValueError("E3c-4b build log changed")
    tests_raw = (root / OFFLINE_REPORT).read_bytes()
    if tx.sha(tests_raw) != OFFLINE_REPORT_SHA:
        raise ValueError("reviewed E3c-4b native report changed")
    tests = json.loads(tests_raw)
    if (tests.get("result") != "passed" or tests.get("tests") != 9
            or tests.get("hardware_accessed") is not False or tests.get("device_written") is not False
            or tests.get("build_report_sha256") != BUILD_REPORT_SHA):
        raise ValueError("candidate production USB native tests required")
    for name, digest in tests["source_sha256"].items():
        if tx.sha((root / name).read_bytes()) != digest:
            raise ValueError("E3c-4b tested source changed: " + name)
    for name, digest in tests["compiled_source_sha256"].items():
        if tx.sha((root / PROJECT / name).read_bytes()) != digest:
            raise ValueError("E3c-4b staged source changed: " + name)
    if tx.sha((root / tests["log"]).read_bytes()) != tests["log_sha256"]:
        raise ValueError("E3c-4b native log changed")
    image = reviewed_candidate(root=root)
    if report["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA):
        raise ValueError("E3c-4b candidate differs from build")
    return dict(candidate=dict(variant="display_query", bytes=APP_SIZE, sha256=APP_SHA, app_elf_sha256=APP_ELF_SHA),
        image=image, build_report_sha256=BUILD_REPORT_SHA, offline_report_sha256=tx.sha(tests_raw),
        path_audit_sha256=tx.sha((root / PATCH).read_bytes()))


def prepare_plan(snapshot, identity, image, root=ROOT):
    reference = load_reference(root)
    if identity != reference["identity_sha256"]:
        raise ValueError("E3c-4b plan targets another device")
    check_current(snapshot, reference)
    plan = tx.prepare(snapshot, reviewed_candidate(image, root), identity, ORIGINAL_SIZE, edition="E3c")
    check_plan(plan, reference=reference, root=root)
    return plan


def check_plan(plan, *, candidate=True, reference=None, root=ROOT):
    plan.validate()
    m = plan.metadata
    if (m["edition"] != "E3c" or m["original_bytes"] != ORIGINAL_SIZE
            or m["candidate_bytes"] != APP_SIZE or m["candidate_sha256"] != APP_SHA
            or tx.sha(plan.rollback[:ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("E3c-4b plan or E3c-3b rollback differs")
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError("complete E3c-4b plan required")
        reference = reference or load_reference(root)
        if m["identity_sha256"] != reference["identity_sha256"]:
            raise ValueError("E3c-4b plan device differs")
        check_current(plan.snapshot, reference)
        reviewed_candidate(plan.image, root)
    elif not isinstance(plan, tx.RecoveryPlan):
        raise ValueError("saved E3c-4b recovery plan required")
    return plan
