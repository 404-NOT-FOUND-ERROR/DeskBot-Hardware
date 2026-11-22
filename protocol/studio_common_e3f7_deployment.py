"""Pinned eight-expression App with the accepted E3f-5/E1.45 as rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_shy_e3f5_deployment as baseline
from protocol import storage_continuous_mmap_e3c3b_baseline as paths
from protocol.studio_e3c5b_deployment import _source_manifest
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3f-7", "E3f-5"
SOURCE = Path("tmp/studio-common-e3f7/on/xiaozhi.bin")
PROJECT = SOURCE.parent.parent / "p"
BUILD_REPORT = Path("artifacts/studio-common-e3f7/firmware-build-report.json")
OFFLINE_REPORT = BUILD_REPORT.with_name("offline-validation-1.json")
PEER_REPORT = BUILD_REPORT.with_name("peer-review.json")
BUILD_REPORT_SHA = "39f1897b3564a3e9aee1981ade60658ab9be029e59544486859f7b7cd127941d"
OFFLINE_REPORT_SHA = "66d8eb14dcc49fbca5675073b83458b01701e61f3274cf191ccbaf1623426f00"
PEER_REPORT_SHA = "c89541df954b46517a414e7f1ee64addc19e882a7056758f57ae22d22eac7b51"
APP_SIZE = 4521872
APP_SHA = "6104665ae0574435b53cfffc1ab2ac605bf916566d4005f3e0d397e40c7f0149"
APP_ELF_SHA = "e81bba5514893da25165667a82ab76d7941b1cf7620ac239350d9742df8b837e"
ORIGINAL_SIZE, ORIGINAL_SHA = baseline.APP_SIZE, baseline.APP_SHA
IDENTITY_SHA = baseline.IDENTITY_SHA
ERASE_BYTES = 4521984
ORIGINAL_UNION_SHA = "24300f8a64f309cfb1cc5ae03a686dd08585df504e6faabae735b7e39219a056"
REFERENCE = Path("tmp/e3c-live-20261110-e3f5-live1")
REFERENCE_PLAN_ID = "cb837f6c3d731d8aabc193cfa53ed9517cad67cabab00898b839eab6a52c75cd"
REFERENCE_PLAN_SHA = "5fb1924e657b6506123b8d13327d63189b00802617aa47615acfa22d6d44fd22"
REFERENCE_INSTALL_SHA = "9858ae8833f71b649168278aa9d3ce0b0e217f0e2dff8276426a770a38259e74"
REFERENCE_SNAPSHOT_SHA = "fb2083e1523f94939cd5fe9d3288036e80eda486f40aac7cf6d9ef8dd9352b83"
STATIC_BASELINE_SHA = "a9c52604e0262f60aef00d07d1e57c491a970a01fb3c19a64cd6d9a4fbe57fed"
ACCEPTANCE = Path("artifacts/studio-shy-e3f5/manual-acceptance.json")
ACCEPTANCE_SHA = "ac4289e7fe67207aeab8988746d347b5fbf04247035b2805f8233db471ea633a"
SOURCE_DELTA = sorted(["main/Kconfig.projbuild", *("main/boards/esp_vocat/" + name for name in
    ("studio_display_readiness.h", "studio_expression_adapter.cc", "studio_expression_control.h"))])


def _read(root, name):
    return paths.repo_bytes(root, name)


def _fixed_json(root, name, digest):
    raw = _read(root, name)
    if tx.sha(raw) != digest:
        raise ValueError("common_install_evidence_changed: " + str(name))
    return json.loads(raw)


def validate_baseline(raw):
    # Settings may evolve in NVS. All other bytes, including the App tail and
    # resources, must match the accepted E3f-5 installation.
    if (type(raw) is not bytes or len(raw) != tx.FLASH
            or tx.sha(raw[:0x9000] + raw[0xD000:]) != STATIC_BASELINE_SHA
            or tx.sha(raw[tx.START:tx.START + ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("accepted_E3f5_E145_snapshot_required")


def load_reference(root=ROOT):
    _fixed_json(root, REFERENCE / "plan.json", REFERENCE_PLAN_SHA)
    receipt = _fixed_json(root, REFERENCE / "install-result.json", REFERENCE_INSTALL_SHA)
    accepted = _fixed_json(root, ACCEPTANCE, ACCEPTANCE_SHA)
    folder = paths.safe_path(root / REFERENCE, root)
    plan = tx.load(folder)
    state = tx.Journal(folder, plan).load()
    if (plan.id != REFERENCE_PLAN_ID or plan.metadata["identity_sha256"] != IDENTITY_SHA
            or tx.sha(plan.image) != ORIGINAL_SHA or len(plan.image) != ORIGINAL_SIZE
            or state is None or state["phase"] != "installed"
            or receipt.get("result") != "candidate_installed"
            or receipt.get("readback", {}).get("app_sha256") != ORIGINAL_UNION_SHA
            or not all(receipt.get("readback", {}).get(k) is True
                       for k in ("app_byte_equal", "nvs_equal", "static_outside_app_equal"))
            or receipt.get("boot", {}).get("firmware_matches_candidate") is not True
            or accepted.get("result") != "passed" or accepted.get("edition") != ROLLBACK_TARGET
            or accepted.get("resources") != "E1.45" or accepted.get("app_sha256") != ORIGINAL_SHA
            or accepted.get("app_elf_sha256") != baseline.APP_ELF_SHA
            or accepted.get("installation") != REFERENCE.as_posix()
            or accepted.get("plan_id") != plan.id):
        raise ValueError("accepted_E3f5_installation_required")
    end = tx.START + len(plan.rollback)
    raw = plan.snapshot[:tx.START] + plan.candidate + plan.snapshot[end:]
    validate_baseline(raw)
    if tx.sha(raw) != REFERENCE_SNAPSHOT_SHA:
        raise ValueError("E3f5_reference_snapshot_changed")
    return dict(snapshot=raw, identity_sha256=IDENTITY_SHA,
        app_union=raw[tx.START:tx.START + ERASE_BYTES],
        report=dict(run=REFERENCE.as_posix(), plan_id=plan.id, acceptance_sha256=ACCEPTANCE_SHA,
                    reconstructed_sha256=REFERENCE_SNAPSHOT_SHA))


def check_current(raw, reference):
    validate_baseline(raw)
    validate_baseline(reference["snapshot"])
    if raw[:0x9000] != reference["snapshot"][:0x9000] or raw[0xD000:] != reference["snapshot"][0xD000:]:
        raise ValueError("current_E3f5_static_bytes_changed")


def reviewed_candidate(raw=None, root=ROOT):
    image = _read(root, SOURCE) if raw is None else raw
    if len(image) != APP_SIZE or tx.sha(image) != APP_SHA:
        raise ValueError("reviewed_common_App_changed")
    facts = image_facts(image)
    start = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != tx.FLASH or image[start + 144:start + 176].hex() != APP_ELF_SHA:
        raise ValueError("common_App_capacity_or_ELF_changed")
    return image


def check_build_evidence(root=ROOT):
    build = _fixed_json(root, BUILD_REPORT, BUILD_REPORT_SHA)
    if (build.get("schema") != "deskbot.studio_common_e3f7_build/v1"
            or build.get("baseline") != ROLLBACK_TARGET or build.get("edition") != EDITION
            or build.get("baseline_report_sha256") != baseline.BUILD_REPORT_SHA
            or build.get("result") != "off_on_builds_verified" or build.get("source_delta") != SOURCE_DELTA
            or build.get("disabled_comparison", {}).get("executable_and_data_equal") is not True
            or (build.get("new_tasks"), build.get("usb_reads_per_iteration"), build.get("usb_yield_ms"),
                build.get("usb_stack_bytes"), build.get("usb_priority")) != (0, 1, 10, 6144, 1)
            or [row.get("enabled") for row in build.get("builds", [])] != [False, True]
            or [row.get("config_delta") for row in build["builds"]] != [[], ["DESKBOT_STUDIO_COMMON_REQUEST_ENABLE"]]):
        raise ValueError("paired_common_expression_build_required")
    _source_manifest(root, PROJECT, build["source_sha256"], EDITION)
    for row in build["steps"]:
        if tx.sha(_read(root, Path(row["log"]))) != row["sha256"]:
            raise ValueError("common_build_log_changed")
    for label, row in zip(("off", "on"), build["builds"]):
        for name, record in row["artifacts"].items():
            raw = _read(root, SOURCE.parent.parent / label / name)
            if dict(bytes=len(raw), sha256=tx.sha(raw)) != record:
                raise ValueError("common_build_artifact_changed: " + name)
        if (not all(row["protected_equal"].values()) or row["app_headroom_bytes"] < 1048576
                or row["internal_static_data_bytes"] != 65168
                or any(item["bytes"] > 2048 for item in row["compiler_stack_usage"].values())):
            raise ValueError("common_build_budget_changed")
    tests = _fixed_json(root, OFFLINE_REPORT, OFFLINE_REPORT_SHA)
    if (tests.get("result") != "passed" or tests.get("tests_run") != 385
            or tests.get("source_unchanged") is not True or tests.get("node_tests") != 19
            or tests.get("node_exit") != 0 or tests.get("browser_checks") != 40
            or any(tests.get(k) != 0 for k in ("errors", "failures", "skipped"))):
        raise ValueError("common_offline_validation_required")
    for name, digest in tests["source_sha256"].items():
        if tx.sha(_read(root, Path(name))) != digest:
            raise ValueError("common_tested_source_changed: " + name)
    for name, digest in ((tests["log"], tests["log_sha256"]),
                         (tests["browser_validation"], tests["browser_validation_sha256"])):
        if tx.sha(_read(root, Path(name))) != digest:
            raise ValueError("common_validation_log_changed")
    peer = _fixed_json(root, PEER_REPORT, PEER_REPORT_SHA)
    if (peer.get("result") != "approved" or peer.get("edition") != EDITION
            or peer.get("evidence", {}).get("build_report", {}).get("sha256") != BUILD_REPORT_SHA
            or peer.get("evidence", {}).get("offline_validation", {}).get("sha256") != OFFLINE_REPORT_SHA
            or peer.get("candidate", {}).get("sha256") != APP_SHA
            or peer.get("candidate", {}).get("app_elf_sha256") != APP_ELF_SHA):
        raise ValueError("common_candidate_peer_review_required")
    image = reviewed_candidate(root=root)
    if build["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA):
        raise ValueError("common_candidate_build_mismatch")
    return dict(image=image, candidate=dict(variant="common-on-shy", bytes=APP_SIZE,
                sha256=APP_SHA, app_elf_sha256=APP_ELF_SHA), build_report_sha256=BUILD_REPORT_SHA,
                offline_report_sha256=OFFLINE_REPORT_SHA, path_audit_sha256=PEER_REPORT_SHA)


def prepare_plan(snapshot, identity, image, root=ROOT):
    check_current(snapshot, load_reference(root))
    if identity != IDENTITY_SHA:
        raise ValueError("common_plan_device_mismatch")
    return check_plan(tx.prepare(snapshot, reviewed_candidate(image, root), identity,
                                ORIGINAL_SIZE, edition="E3c"), root=root)


def check_plan(plan, *, candidate=True, root=ROOT):
    plan.validate()
    m = plan.metadata
    validate_baseline(plan.snapshot)
    if (m["edition"] != "E3c" or m["identity_sha256"] != IDENTITY_SHA
            or m["original_bytes"] != ORIGINAL_SIZE or m["candidate_bytes"] != APP_SIZE
            or m["candidate_sha256"] != APP_SHA or m["erase_bytes"] != ERASE_BYTES
            or tx.sha(plan.rollback) != ORIGINAL_UNION_SHA):
        raise ValueError("common_plan_or_original_changed")
    # The recovery path only reads same-run originals. Candidate/build/history
    # files can be removed without affecting recovery.
    if candidate:
        reviewed_candidate(plan.image, root)
        check_current(plan.snapshot, load_reference(root))
    return plan
