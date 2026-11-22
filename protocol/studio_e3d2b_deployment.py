"""Frozen catalog App with the user-accepted E3c-5b as exact rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_e3c5b_deployment as previous
from protocol import storage_continuous_mmap_e3c3b_baseline as paths
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3d-2b", "E3c-5b"
SOURCE = Path("tmp/studio-stable-ladder-20261108/catalog/on/xiaozhi.bin")
PROJECT = SOURCE.parent.parent / "p"
BUILD_REPORT = Path("artifacts/studio-stable-ladder-20261108/catalog-build.json")
BUILD_REPORT_SHA = "2fa2d86b490b3df354e45e8e6a7ab1ade9bd8d7e28381cd99019b8690b41bc39"
PATCH = BUILD_REPORT.with_name("catalog.patch")
PATCH_SHA = "1e472cbc35f94beb0fd39997e70f72d2add416420955ca57b85cc7003f6e4132"
OFFLINE_REPORT = Path("artifacts/studio-catalog-e3d2b-20261108/native-validation-2.json")
OFFLINE_REPORT_SHA = "f778327266aaa646e60ac702e10c55719dc773d41d5299086bc6e0986451e6c7"
APP_SIZE = 4_517_936
APP_SHA = "b7f7139d688c46f10e5662387cc74c87a7614d289b3364872a48301c93d59532"
APP_ELF_SHA = "87d3b429846407fa26ad7b38dbe5c1a54fea0d1cbd5f30bf5bafa01429a4cc7a"
ORIGINAL_SIZE, ORIGINAL_SHA, ORIGINAL_ELF_SHA = previous.APP_SIZE, previous.APP_SHA, previous.APP_ELF_SHA
IDENTITY_SHA = previous.IDENTITY_SHA
LIVE_REFERENCE = Path("tmp/e3c-live-20261108-e3c5b-live1")
LIVE_PLAN_ID = "d4ce46794049877f94683fd8a82411b213034268b21d5e5e0f88f9001e95235c"
LIVE_PLAN_SHA = "82a9c8e15018d00c7dce29d6319dfd930b16167b0c92b33a901b5da649107387"
LIVE_INSTALL_SHA = "2ee278de0a01eb343c09395fccc7a51de9fa55aaec2e4ab27b53b347e2a400f6"
ACCEPTANCE = Path("artifacts/studio-neutral-e3c5b-20261108/acceptance-20261108.json")
ACCEPTANCE_SHA = "dbeb1782e70674d19821049167149aae0ea9f2b5d69f473338012b869d6ab952"
REFERENCE_SNAPSHOT_SHA = "e424100c24812a01c09df0989941f0cae6cdb083adcdf70d8866be20d0f5c30a"
ERASE_BYTES = 4_521_984
ORIGINAL_UNION_SHA = "b1281ad9d1b4c9df6a0e9b97fb1959285e2711dcfc43ac3414ae56388fcf19b0"
WRITE_PAYLOAD_SHA = "a461a4d431296b35a04150c267caba3ffde700fc56a40c27d3560edde35d321f"


def _read(root, name):
    return paths.repo_bytes(root, name)


def _fixed_json(root, name, digest):
    raw = _read(root, name)
    if tx.sha(raw) != digest:
        raise ValueError("pinned E3d-2b evidence changed: " + str(name))
    return json.loads(raw)


def _neutral_build(root):
    # Historical firmware is checked against its frozen build inputs. Current
    # host code is checked separately by this release's native report.
    previous._display_build(root)
    report = _fixed_json(root, previous.BUILD_REPORT, previous.BUILD_REPORT_SHA)
    previous._source_manifest(root, previous.PROJECT, report["source_sha256"], "E3c-5b")
    previous._artifacts(root, report, "E3c-5b")
    if (report.get("stage") != "neutral" or report.get("result") != "off_on_builds_verified"
            or report["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=ORIGINAL_SIZE, sha256=ORIGINAL_SHA)
            or report["builds"][1]["app_elf_sha256"] != ORIGINAL_ELF_SHA):
        raise ValueError("accepted E3c-5b frozen build differs")
    previous.reviewed_candidate(_read(root, previous.SOURCE), root)


def reviewed_candidate(raw=None, root=ROOT):
    raw = _read(root, SOURCE) if raw is None else raw
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError("reviewed E3d-2b App changed")
    facts = image_facts(raw)
    start = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != tx.FLASH or raw[start+144:start+176].hex() != APP_ELF_SHA:
        raise ValueError("E3d-2b capacity or ELF identity differs")
    return raw


def load_reference(root=ROOT):
    _fixed_json(root, LIVE_REFERENCE / "plan.json", LIVE_PLAN_SHA)
    install = _fixed_json(root, LIVE_REFERENCE / "install-result.json", LIVE_INSTALL_SHA)
    acceptance = _fixed_json(root, ACCEPTANCE, ACCEPTANCE_SHA)
    folder = paths.safe_path(root / LIVE_REFERENCE, root)
    plan = tx.load(folder)
    state = tx.Journal(folder, plan).load()
    if (plan.id != LIVE_PLAN_ID or plan.metadata["identity_sha256"] != IDENTITY_SHA
            or len(plan.image) != ORIGINAL_SIZE or tx.sha(plan.image) != ORIGINAL_SHA
            or state is None or state.get("phase") != "installed"
            or install.get("result") != "candidate_installed"
            or install.get("readback", {}).get("app_sha256") != tx.sha(plan.candidate)
            or not all(install.get("readback", {}).get(key) is True
                       for key in ("app_byte_equal", "static_outside_app_equal", "nvs_equal"))
            or install.get("boot", {}).get("normal_boot") is not True
            or install.get("boot", {}).get("firmware_matches_candidate") is not True
            or install.get("boot", {}).get("hello", {}).get("firmware", {}).get("app_elf_sha256") != ORIGINAL_ELF_SHA
            or acceptance.get("result") != "accepted" or acceptance.get("app") != ROLLBACK_TARGET
            or acceptance.get("scope") != "user_reported_current_normal_operation"
            or acceptance.get("app_sha256") != ORIGINAL_SHA
            or acceptance.get("app_elf_sha256") != ORIGINAL_ELF_SHA
            or acceptance.get("run") != LIVE_REFERENCE.as_posix() or acceptance.get("plan_id") != plan.id):
        raise ValueError("accepted E3c-5b installation and user record required")
    for item in acceptance["evidence"]:
        if tx.sha(_read(root, item["path"])) != item["sha256"]:
            raise ValueError("E3c-5b acceptance evidence changed")
    end = tx.START + len(plan.rollback)
    snapshot = plan.snapshot[:tx.START] + plan.candidate + plan.snapshot[end:]
    union = snapshot[tx.START:tx.START + ERASE_BYTES]
    if tx.sha(snapshot) != REFERENCE_SNAPSHOT_SHA or tx.sha(union) != ORIGINAL_UNION_SHA:
        raise ValueError("accepted E3c-5b reconstructed snapshot differs")
    return dict(snapshot=snapshot, app_union=union, original_bytes=ORIGINAL_SIZE,
                identity_sha256=IDENTITY_SHA,
                report=dict(path=ACCEPTANCE.as_posix(), sha256=ACCEPTANCE_SHA),
                transaction=dict(path=LIVE_REFERENCE.as_posix(), plan_id=plan.id))


def check_current(raw, reference):
    expected = reference["snapshot"]
    if len(raw) != tx.FLASH or len(expected) != tx.FLASH:
        raise ValueError("complete Flash reference required")
    # Runtime NVS is refreshed by live preparation; all static bytes include the
    # next two erase sectors introduced by the larger catalog App.
    if raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]:
        raise ValueError("current Flash differs from accepted E3c-5b")


def check_build_evidence(root=ROOT):
    _neutral_build(root)
    report = _fixed_json(root, BUILD_REPORT, BUILD_REPORT_SHA)
    if (report.get("schema") != "deskbot.stable_candidate_ladder/v1"
            or report.get("stage") != "catalog" or report.get("baseline") != "E3c-3b"
            or report.get("result") != "off_on_builds_verified"
            or report.get("reference_app") != dict(bytes=ORIGINAL_SIZE, sha256=ORIGINAL_SHA)
            or report.get("device_accessed") is not False or report.get("device_written") is not False
            or report.get("disabled_comparison", {}).get("executable_and_data_equal") is not True
            or report.get("usb") != dict(reads_per_iteration=1, yield_ms=10, stack_bytes=6144, priority=1)
            or report.get("new_tasks") != 0 or report.get("catalog_snapshot_max_psram_bytes") != 69632
            or [row.get("enabled") for row in report.get("builds", [])] != [False, True]):
        raise ValueError("E3d-2b isolated catalog build required")
    previous._source_manifest(root, PROJECT, report["source_sha256"], "E3d-2b")
    previous._artifacts(root, report, "E3d-2b")
    for row in report["builds"]:
        folder = Path(row["build_directory"])
        if (row["app_headroom_bytes"] != tx.PARTITION - row["artifacts"]["xiaozhi.bin"]["bytes"]
                or row["app_headroom_bytes"] < 1048576
                or not 0 < row["internal_static_data_bytes"] < 80 * 1024
                or not row["protected_equal"] or not all(row["protected_equal"].values())
                or not row["compiler_stack_usage"]
                or any(item["bytes"] > 2048 or item["allocation"] not in ("static", "dynamic,bounded")
                       for item in row["compiler_stack_usage"].values())):
            raise ValueError("E3d-2b memory or protected-output budget differs")
        config = json.loads(_read(root, folder / "config/sdkconfig.json"))
        if (config.get("DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE") is not True
                or config.get("DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE") is not True
                or config.get("DESKBOT_STUDIO_CATALOG_ENABLE", False) is not row["enabled"]
                or row["config_delta"] != (["DESKBOT_STUDIO_CATALOG_ENABLE"] if row["enabled"] else [])):
            raise ValueError("E3d-2b feature configuration differs")
    tests = _fixed_json(root, OFFLINE_REPORT, OFFLINE_REPORT_SHA)
    if (tests.get("result") != "passed" or tests.get("tests") != 20 or tests.get("skipped") != 0
            or tests.get("build_report_sha256") != BUILD_REPORT_SHA
            or tests.get("failures") != 0 or tests.get("errors") != 0 or tests.get("source_unchanged") is not True
            or tests.get("hardware_accessed") is not False or tests.get("device_written") is not False
            or tests.get("candidate_source") != PROJECT.as_posix()):
        raise ValueError("candidate production catalog and expression native tests required")
    for name, digest in tests["source_sha256"].items():
        if tx.sha(_read(root, name)) != digest:
            raise ValueError("E3d-2b tested source changed: " + name)
    for name, digest in tests["compiled_source_sha256"].items():
        if tx.sha(_read(root, PROJECT / name)) != digest:
            raise ValueError("E3d-2b staged source changed: " + name)
    if tx.sha(_read(root, tests["log"])) != tests["log_sha256"] or tx.sha(_read(root, PATCH)) != PATCH_SHA:
        raise ValueError("E3d-2b test log or patch changed")
    image = reviewed_candidate(root=root)
    if (report["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA)
            or report["builds"][1]["app_elf_sha256"] != APP_ELF_SHA
            or tx.sha(_read(root, SOURCE.with_suffix(".elf"))) != APP_ELF_SHA):
        raise ValueError("E3d-2b candidate differs from build")
    return dict(candidate=dict(variant="actual_catalog", bytes=APP_SIZE, sha256=APP_SHA,
                               app_elf_sha256=APP_ELF_SHA), image=image,
                build_report_sha256=BUILD_REPORT_SHA, offline_report_sha256=OFFLINE_REPORT_SHA,
                path_audit_sha256=PATCH_SHA)


def prepare_plan(snapshot, identity, image, root=ROOT):
    reference = load_reference(root)
    if identity != reference["identity_sha256"]:
        raise ValueError("E3d-2b plan targets another device")
    check_current(snapshot, reference)
    plan = tx.prepare(snapshot, reviewed_candidate(image, root), identity, ORIGINAL_SIZE, edition="E3c")
    return check_plan(plan, reference=reference, root=root)


def check_plan(plan, *, candidate=True, reference=None, root=ROOT):
    plan.validate()
    meta = plan.metadata
    # Recovery uses only this transaction's durable bytes and fixed release IDs.
    if (meta["edition"] != "E3c" or meta["identity_sha256"] != IDENTITY_SHA
            or meta["original_bytes"] != ORIGINAL_SIZE or meta["candidate_bytes"] != APP_SIZE
            or meta["candidate_sha256"] != APP_SHA or meta["erase_bytes"] != ERASE_BYTES
            or meta["rollback_sha256"] != ORIGINAL_UNION_SHA or meta["write_payload_sha256"] != WRITE_PAYLOAD_SHA
            or tx.sha(plan.rollback[:ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("E3d-2b plan or E3c-5b rollback differs")
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError("complete E3d-2b plan required")
        reference = reference or load_reference(root)
        check_current(plan.snapshot, reference)
        reviewed_candidate(plan.image, root)
    elif not isinstance(plan, tx.RecoveryPlan):
        raise ValueError("saved E3d-2b recovery plan required")
    return plan
