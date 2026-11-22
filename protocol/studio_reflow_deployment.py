"""Install the reviewed first-frame fix over the installed E3f-4c/E1.45 pair."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_asset_layout_deployment as baseline
from protocol import studio_resource_installation as resources
from protocol import storage_continuous_mmap_e3c3b_baseline as paths
from protocol.studio_e3c5b_deployment import _source_manifest
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3f-4e", "E3f-4c"
SOURCE = Path("tmp/studio-reflow-e3f4e/on/xiaozhi.bin")
PROJECT = SOURCE.parent.parent / "p"
BUILD_REPORT = Path("artifacts/studio-reflow-e3f4e/firmware-build-report.json")
OFFLINE_REPORT = BUILD_REPORT.with_name("offline-validation.json")
PEER_REPORT = BUILD_REPORT.with_name("peer-review.json")
BUILD_REPORT_SHA = "31dcdc05e154c6cb2a85241f5d9178b7d3c9a0f0db3bb2d59f72b07c24ef2918"
OFFLINE_REPORT_SHA = "63091f135c74648de6ceaafdb688a4a856ea68e301822b7ba43ed8950bceb9a9"
PEER_REPORT_SHA = "37c261a6aaed91a3c4f4d9e3388b05f7ed2b3eb69f5064fde331ccaa7c1cbf1e"
APP_SIZE = 4521760
APP_SHA = "efa32283b6c0ad85a2e60e25a2a2ac32a5f2d30debc7edaf45ad096e3d80da88"
APP_ELF_SHA = "f063f7cf33d6a3841290786307687ce5f79e8a8f109001bd8e9cb12bda2aead6"
ORIGINAL_SIZE, ORIGINAL_SHA = baseline.APP_SIZE, baseline.APP_SHA
IDENTITY_SHA = baseline.IDENTITY_SHA
ERASE_BYTES = 4521984
ORIGINAL_UNION_SHA = "d1da2ab2c35c6334485ecf67979b8dae0e99239d4e73d76e31f076b1d0b95ffb"
REFERENCE = Path("tmp/e145-assets-20261110-e3f4d-live1")
REFERENCE_PLAN_ID = "2bfb92d38a0ac97379a6085490f2946579e506d3c49349093813cd6eb595d381"
REFERENCE_INSTALL_SHA = "89462c808b7c9fd4d59be0a28f153dc56e6ce0a86207a0c0d4fa3aef537e2a10"
REFERENCE_SNAPSHOT_SHA = "adff8850d0e57b1b377cd95d94ba71f8259aa82acca8d4d01239ad2821ea99bb"


def _read(root, name):
    return paths.repo_bytes(root, name)


def _fixed_json(root, name, digest):
    raw = _read(root, name)
    if tx.sha(raw) != digest:
        raise ValueError("reflow_evidence_changed: " + str(name))
    return json.loads(raw)


def validate_baseline(raw):
    if (type(raw) is not bytes or len(raw) != tx.FLASH
            or tx.sha(raw[tx.START:tx.START + ORIGINAL_SIZE]) != ORIGINAL_SHA
            or tx.sha(raw[0x8000:0x8C00]) != resources.TABLE_SHA
            or raw[0x8C00:0x9000] != b"\xff" * 1024
            or tx.sha(raw[resources.START:resources.START + resources.SIZE]) != resources.CANDIDATE_SHA):
        raise ValueError("installed_E3f4c_E145_snapshot_required")


def load_reference(root=ROOT):
    folder = paths.safe_path(root / REFERENCE, root)
    plan = resources.load(folder)
    receipt = _fixed_json(root, REFERENCE / "install-result.json", REFERENCE_INSTALL_SHA)
    state = resources.Journal(folder, plan).load()
    if (plan.id != REFERENCE_PLAN_ID or resources.plan_baseline(plan) != ROLLBACK_TARGET
            or plan.metadata["identity_sha256"] != IDENTITY_SHA
            or plan.metadata["mode"] != "live" or state is None or state["phase"] != "installed"
            or receipt.get("result") != "candidate_resources_installed"
            or receipt.get("readback_sha256") != REFERENCE_SNAPSHOT_SHA
            or state.get("readback_sha256") != REFERENCE_SNAPSHOT_SHA):
        raise ValueError("installed_E3f4d_resource_evidence_required")
    # The resource snapshot predates installation: overlay all 8 MiB, including
    # padding, to retain the actual E1.45 package in this App-only transaction.
    raw = (plan.snapshot[:resources.START] + plan.image
           + plan.snapshot[resources.START + resources.SIZE:])
    validate_baseline(raw)
    if tx.sha(raw) != REFERENCE_SNAPSHOT_SHA:
        raise ValueError("installed_E3f4d_snapshot_changed")
    return dict(snapshot=raw, identity_sha256=IDENTITY_SHA,
                app_union=raw[tx.START:tx.START + ERASE_BYTES],
                report=dict(run=REFERENCE.as_posix(), plan_id=plan.id,
                            install_sha256=REFERENCE_INSTALL_SHA,
                            reconstructed_sha256=REFERENCE_SNAPSHOT_SHA))


def check_current(raw, reference):
    validate_baseline(raw)
    expected = reference["snapshot"]
    # Only live NVS may differ from the retained reference. Fresh preparation
    # captures it; immediately before writing the full same-run bytes are checked.
    if raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]:
        raise ValueError("current_E3f4c_E145_static_bytes_changed")


def reviewed_candidate(raw=None, root=ROOT):
    image = _read(root, SOURCE) if raw is None else raw
    if len(image) != APP_SIZE or tx.sha(image) != APP_SHA:
        raise ValueError("reviewed_reflow_App_changed")
    facts = image_facts(image)
    offset = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != tx.FLASH or image[offset + 144:offset + 176].hex() != APP_ELF_SHA:
        raise ValueError("reflow_App_capacity_or_ELF_changed")
    return image


def check_build_evidence(root=ROOT):
    build = _fixed_json(root, BUILD_REPORT, BUILD_REPORT_SHA)
    if (build.get("schema") != "deskbot.studio_reflow_build/v1"
            or build.get("baseline") != ROLLBACK_TARGET
            or build.get("result") != "off_on_builds_verified"
            or build.get("disabled_comparison", {}).get("executable_and_data_equal") is not True
            or build.get("source_delta") != ["main/Kconfig.projbuild", "main/boards/esp_vocat/studio_asset_layout.cc"]
            or (build.get("new_tasks"), build.get("usb_reads_per_iteration"), build.get("usb_yield_ms"),
                build.get("usb_stack_bytes"), build.get("usb_priority")) != (0, 1, 10, 6144, 1)
            or [row.get("enabled") for row in build.get("builds", [])] != [False, True]):
        raise ValueError("paired_reflow_build_required")
    _source_manifest(root, PROJECT, build["source_sha256"], EDITION)
    for row in build["steps"]:
        if tx.sha(_read(root, Path(row["log"]))) != row["sha256"]:
            raise ValueError("reflow_build_log_changed")
    for label, row in zip(("off", "on"), build["builds"]):
        for name, record in row["artifacts"].items():
            data = _read(root, SOURCE.parent.parent / label / name)
            if dict(bytes=len(data), sha256=tx.sha(data)) != record:
                raise ValueError("reflow_build_artifact_changed: " + name)
        if (not all(row["protected_equal"].values()) or row["app_headroom_bytes"] < 1048576
                or row["internal_static_data_bytes"] != 65168
                or any(item["bytes"] > 2048 for item in row["compiler_stack_usage"].values())):
            raise ValueError("reflow_build_budget_changed")
    tests = _fixed_json(root, OFFLINE_REPORT, OFFLINE_REPORT_SHA)
    if (tests.get("result") != "passed" or tests.get("mode") != "candidate"
            or tests.get("tests_run") != 80 or tests.get("source_unchanged") is not True
            or any(tests.get(k) != 0 for k in ("errors", "failures", "skipped"))):
        raise ValueError("reflow_offline_validation_required")
    for name, digest in tests["source_sha256"].items():
        if tx.sha(_read(root, Path(name))) != digest:
            raise ValueError("reflow_tested_source_changed: " + name)
    if tx.sha(_read(root, Path(tests["log"]))) != tests["log_sha256"]:
        raise ValueError("reflow_test_log_changed")
    peer = _fixed_json(root, PEER_REPORT, PEER_REPORT_SHA)
    if (peer.get("result") != "approved" or peer.get("edition") != EDITION
            or peer.get("build_report_sha256") != BUILD_REPORT_SHA
            or peer.get("offline_report_sha256") != OFFLINE_REPORT_SHA):
        raise ValueError("reflow_peer_review_required")
    image = reviewed_candidate(root=root)
    if build["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA):
        raise ValueError("reflow_candidate_build_mismatch")
    return dict(image=image, candidate=dict(variant="asset-reflow", bytes=APP_SIZE,
                sha256=APP_SHA, app_elf_sha256=APP_ELF_SHA),
                build_report_sha256=BUILD_REPORT_SHA,
                offline_report_sha256=OFFLINE_REPORT_SHA, path_audit_sha256=PEER_REPORT_SHA)


def prepare_plan(snapshot, identity, image, root=ROOT):
    check_current(snapshot, load_reference(root))
    if identity != IDENTITY_SHA:
        raise ValueError("reflow_plan_device_mismatch")
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
        raise ValueError("reflow_plan_or_original_changed")
    # Recovery needs the durable originals and fixed identities, independent of
    # candidate files, build outputs or historical resource transactions.
    if candidate:
        reviewed_candidate(plan.image, root)
        check_current(plan.snapshot, load_reference(root))
    return plan
