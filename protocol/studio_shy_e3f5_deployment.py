"""Pinned shy App increment with the accepted E3f-4e/E1.45 as rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_reflow_deployment as baseline
from protocol import studio_resource_installation as resources
from protocol import storage_continuous_mmap_e3c3b_baseline as paths
from protocol.studio_e3c5b_deployment import _source_manifest
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3f-5", "E3f-4e"
SOURCE = Path("tmp/studio-shy-e3f5/on/xiaozhi.bin")
PROJECT = SOURCE.parent.parent / "p"
BUILD_REPORT = Path("artifacts/studio-shy-e3f5/firmware-build-report.json")
OFFLINE_REPORT = BUILD_REPORT.with_name("offline-validation-4.json")
PEER_REPORT = BUILD_REPORT.with_name("peer-review.json")
BUILD_REPORT_SHA = "7700254116731e00e7170493a2a9dc90949a6fe8cf479660eb242fdedf2347cb"
OFFLINE_REPORT_SHA = "ee3005b6ffc5ea9577e98a235671d121f89d55f9e2e5c8a30b58ac677398304e"
PEER_REPORT_SHA = "cace9de048ab63775c61dc809d289fee7692b3f12b56a3b3c58337efaba8d9ae"
APP_SIZE = 4521760
APP_SHA = "592793c3db80b6d4d5257b7f2fde5308f98db69ee94a8e5badaa8bcab9c251bb"
APP_ELF_SHA = "c8e4587184a97d0cc2b0a07167670066e29a651698a72cb9f3899437cafa90aa"
ORIGINAL_SIZE, ORIGINAL_SHA = baseline.APP_SIZE, baseline.APP_SHA
IDENTITY_SHA = baseline.IDENTITY_SHA
ERASE_BYTES = 4521984
ORIGINAL_UNION_SHA = "e71bcbaf1315f10e33f459d150249d31289526e78b206e109f60f97f5e60ecfe"
REFERENCE = Path("tmp/e3c-live-20261110-e3f4e-live1")
REFERENCE_PLAN_ID = "01a3670df3ebd1c04aade209ea59cd81ac015827716eb8e5bc27625aa2071be6"
REFERENCE_PLAN_SHA = "813f6572d25eed95e21d87b0336d47c1fbb7a27137a708307e902d64c83bac96"
REFERENCE_INSTALL_SHA = "905f54b26a39906171d62dd442110ff1d5018ff487e7f92eb1524eba37cb87c5"
REFERENCE_SNAPSHOT_SHA = "3f37a61e0691fea7cf6d9b46bed78a6176a1150149163eb2c247fb07a24b6684"
STATIC_BASELINE_SHA = "d78f55fd2764a65336f6d4e42736f8ebfb48b809fea6043a29ccbd30b999706f"
ACCEPTANCE = Path("artifacts/studio-reflow-e3f4e/manual-acceptance.json")
ACCEPTANCE_SHA = "efd2ded92ad0bf18a59b6a6eab2ea749097a1364293727c629a25ee3b5cd8d78"


def _read(root, name):
    return paths.repo_bytes(root, name)


def _fixed_json(root, name, digest):
    raw = _read(root, name)
    if tx.sha(raw) != digest:
        raise ValueError("shy_install_evidence_changed: " + str(name))
    return json.loads(raw)


def validate_baseline(raw):
    # NVS is the only runtime region. The immutable digest also covers the App
    # tail, all resources and every byte outside the installation span.
    if (type(raw) is not bytes or len(raw) != tx.FLASH
            or tx.sha(raw[:0x9000] + raw[0xD000:]) != STATIC_BASELINE_SHA
            or tx.sha(raw[tx.START:tx.START + ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("accepted_E3f4e_E145_snapshot_required")


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
            or accepted.get("installation") != REFERENCE.as_posix()):
        raise ValueError("accepted_E3f4e_installation_required")
    end = tx.START + len(plan.rollback)
    raw = plan.snapshot[:tx.START] + plan.candidate + plan.snapshot[end:]
    validate_baseline(raw)
    if tx.sha(raw) != REFERENCE_SNAPSHOT_SHA:
        raise ValueError("E3f4e_reference_snapshot_changed")
    return dict(snapshot=raw, identity_sha256=IDENTITY_SHA,
        app_union=raw[tx.START:tx.START + ERASE_BYTES],
        report=dict(run=REFERENCE.as_posix(), plan_id=plan.id, acceptance_sha256=ACCEPTANCE_SHA,
                    reconstructed_sha256=REFERENCE_SNAPSHOT_SHA))


def check_current(raw, reference):
    validate_baseline(raw)
    validate_baseline(reference["snapshot"])
    if raw[:0x9000] != reference["snapshot"][:0x9000] or raw[0xD000:] != reference["snapshot"][0xD000:]:
        raise ValueError("current_E3f4e_static_bytes_changed")


def reviewed_candidate(raw=None, root=ROOT):
    image = _read(root, SOURCE) if raw is None else raw
    if len(image) != APP_SIZE or tx.sha(image) != APP_SHA:
        raise ValueError("reviewed_shy_App_changed")
    facts = image_facts(image)
    start = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != tx.FLASH or image[start + 144:start + 176].hex() != APP_ELF_SHA:
        raise ValueError("shy_App_capacity_or_ELF_changed")
    return image


def check_build_evidence(root=ROOT):
    build = _fixed_json(root, BUILD_REPORT, BUILD_REPORT_SHA)
    if (build.get("schema") != "deskbot.studio_shy_e3f5_build/v1"
            or build.get("baseline") != ROLLBACK_TARGET or build.get("edition") != EDITION
            or build.get("result") != "off_on_builds_verified" or build.get("source_delta") != []
            or build.get("disabled_comparison", {}).get("byte_identical") is not True
            or (build.get("new_tasks"), build.get("usb_reads_per_iteration"), build.get("usb_yield_ms"),
                build.get("usb_stack_bytes"), build.get("usb_priority")) != (0, 1, 10, 6144, 1)
            or [row.get("enabled") for row in build.get("builds", [])] != [False, True]
            or [row.get("config_delta") for row in build["builds"]] != [[], ["DESKBOT_STUDIO_SHY_REQUEST_ENABLE"]]):
        raise ValueError("paired_shy_config_only_build_required")
    _source_manifest(root, PROJECT, build["source_sha256"], EDITION)
    for row in build["steps"]:
        if tx.sha(_read(root, Path(row["log"]))) != row["sha256"]:
            raise ValueError("shy_build_log_changed")
    for label, row in zip(("off", "on"), build["builds"]):
        for name, record in row["artifacts"].items():
            data = _read(root, SOURCE.parent.parent / label / name)
            if dict(bytes=len(data), sha256=tx.sha(data)) != record:
                raise ValueError("shy_build_artifact_changed: " + name)
        if (not all(row["protected_equal"].values()) or row["app_headroom_bytes"] < 1048576
                or row["internal_static_data_bytes"] != 65168
                or any(item["bytes"] > 2048 for item in row["compiler_stack_usage"].values())):
            raise ValueError("shy_build_budget_changed")
    if build["builds"][0]["artifacts"]["xiaozhi.bin"] != dict(bytes=ORIGINAL_SIZE, sha256=ORIGINAL_SHA):
        raise ValueError("shy_disabled_baseline_changed")
    tests = _fixed_json(root, OFFLINE_REPORT, OFFLINE_REPORT_SHA)
    if (tests.get("result") != "passed" or tests.get("tests_run") != 211
            or tests.get("source_unchanged") is not True
            or any(tests.get(k) != 0 for k in ("errors", "failures", "skipped"))):
        raise ValueError("shy_offline_validation_required")
    for name, digest in tests["source_sha256"].items():
        if tx.sha(_read(root, Path(name))) != digest:
            raise ValueError("shy_tested_source_changed: " + name)
    if tx.sha(_read(root, Path(tests["log"]))) != tests["log_sha256"]:
        raise ValueError("shy_test_log_changed")
    peer = _fixed_json(root, PEER_REPORT, PEER_REPORT_SHA)
    if (peer.get("result") != "approved" or peer.get("edition") != EDITION
            or peer.get("build_report_sha256") != BUILD_REPORT_SHA
            or peer.get("evidence", {}).get("candidate", {}).get("sha256") != APP_SHA):
        raise ValueError("shy_candidate_peer_review_required")
    image = reviewed_candidate(root=root)
    if build["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA):
        raise ValueError("shy_candidate_build_mismatch")
    return dict(image=image, candidate=dict(variant="shy-on-reflow", bytes=APP_SIZE,
                sha256=APP_SHA, app_elf_sha256=APP_ELF_SHA), build_report_sha256=BUILD_REPORT_SHA,
                offline_report_sha256=OFFLINE_REPORT_SHA, path_audit_sha256=PEER_REPORT_SHA)


def prepare_plan(snapshot, identity, image, root=ROOT):
    check_current(snapshot, load_reference(root))
    if identity != IDENTITY_SHA:
        raise ValueError("shy_plan_device_mismatch")
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
        raise ValueError("shy_plan_or_original_changed")
    # Recovery validates saved original bytes; it does not reopen candidate,
    # build, acceptance, or historical reference files.
    if candidate:
        reviewed_candidate(plan.image, root)
        check_current(plan.snapshot, load_reference(root))
    return plan
