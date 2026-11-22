"""Pinned neutral-expression candidate with the accepted E3c-4b rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_e3c4b_deployment as previous
from protocol import storage_continuous_mmap_e3c3b_baseline as paths
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
EDITION, ROLLBACK_TARGET = "E3c-5b", "E3c-4b"
SOURCE = Path("tmp/studio-stable-ladder-20261108/neutral/on/xiaozhi.bin")
PROJECT = SOURCE.parent.parent / "p"
BUILD_REPORT = Path("artifacts/studio-stable-ladder-20261108/neutral-build.json")
DISPLAY_BUILD_REPORT = BUILD_REPORT.with_name("display-build.json")
DISPLAY_BUILD_SHA = "37284b49239e7670cde6ef8bbde9fdee3fd1f4bb2aa605b2cc68ced99fbfe99d"
OFFLINE_REPORT = Path("artifacts/studio-neutral-e3c5b-20261108/native-validation.json")
PATCH = BUILD_REPORT.with_name("neutral.patch")
APP_SIZE = 4_510_480
APP_SHA = "998d9997b49fbdecbfe87b87e74194f8c55fcaf2e73e1ab2aad7c71e6b502b8f"
APP_ELF_SHA = "6488208fc428bf124762aa34c20959bf746f5a55948d12113a2c277a2658feb6"
BUILD_REPORT_SHA = "b96afbfbffb1f0252bf581553b8440be36b7d48f815ccbeff6c92041308b5766"
OFFLINE_REPORT_SHA = "8017a80b53a5aa4b69cc7e824c69279e8a315e24ad2ba828eb85e429b58ff4ca"
PATCH_SHA = "a47a3b2e15bf2f5b197c67bf97e3091e7d729e894cb2097cb0bfde2c2dfba31e"
LIVE_REFERENCE = Path("tmp/e3c-live-20261108-e3c4b-live1")
LIVE_PLAN_ID = "da9aa84e95debbbb34b5786a6b71a43e85505d0fe998907ec7a392851e83a79e"
LIVE_PLAN_SHA = "21173e51c6fba572ab2ae8e7474a1ba8b539240bcb81e08c8a7e05beda1510d4"
LIVE_INSTALL_SHA = "6dee083db5bb2d5e03edda03e533ed90823f4933c44f976fdd19d4907dd61751"
ACCEPTANCE = BUILD_REPORT.with_name("manual-verification.json")
ACCEPTANCE_SHA = "278ee87a1bcc9dccae251e398352961bcde8145a47d2ae335f7b7401b2a00b7a"
ORIGINAL_SIZE = 4_490_272
ORIGINAL_SHA = "cb4d8f943109f4625dbb3f810e21b5ce78272aef557a681b2f939e51696f5afb"
ORIGINAL_ELF_SHA = "32d15b1cf2108b51e9f14a4574edee0596c2ae822e6d536cf247387f6d39df7b"
IDENTITY_SHA = "1232890adbc14b7a1b677968e67e0eab8df37784762209cc69987d1c98e938e2"
REFERENCE_SNAPSHOT_SHA = "826b87deb5b5629dce52897d21e8598543ac51c9812c91fd272512f23fbc5219"
ERASE_BYTES = 4_513_792
ORIGINAL_UNION_SHA = "5c927351adc87336d1f7edf20c05c4adf472fb928516f79b3f13d95f8b5364f2"
WRITE_PAYLOAD_SHA = "bc1905c267fc0c5ee1924a96bc33848cc3a05eae0706b90c9e81d29937271425"


def _read(root, name):
    return paths.repo_bytes(root, name)


def _fixed_json(root, name, digest):
    raw = _read(root, name)
    if tx.sha(raw) != digest:
        raise ValueError("pinned E3c-5b evidence changed: " + str(name))
    return json.loads(raw)


def _source_manifest(root, project, manifest, label):
    for name, digest in manifest.items():
        raw = _read(root, project / name)
        if name == "dependencies.lock":
            # Only this recorded build-local path is normalized. Other source
            # and dependency-lock bytes retain their exact recorded digest.
            touch = root / project / "components/espressif__touch_sensor_fsm"
            matches = [value for value in {str(touch).encode(), touch.as_posix().encode()}
                       if raw.count(value) == 1]
            if len(matches) != 1:
                raise ValueError(label + " local dependency path differs")
            raw = raw.replace(matches[0], b"components/espressif__touch_sensor_fsm")
        if tx.sha(raw) != digest:
            raise ValueError(label + " build source changed: " + name)


def _artifacts(root, report, label):
    for row in report["builds"]:
        for name, item in row["artifacts"].items():
            raw = _read(root, Path(row["build_directory"]) / name)
            if item != dict(bytes=len(raw), sha256=tx.sha(raw)):
                raise ValueError(label + " build artifact changed: " + name)
    for step in report["steps"]:
        if tx.sha(_read(root, step["log"])) != step["sha256"]:
            raise ValueError(label + " build log changed")


def _display_build(root):
    # Validate the accepted firmware from its frozen source/build evidence.
    # Current host tests are bound separately by this release's native report.
    report = _fixed_json(root, DISPLAY_BUILD_REPORT, DISPLAY_BUILD_SHA)
    if (report.get("schema") != "deskbot.stable_candidate_ladder/v1"
            or report.get("stage") != "display" or report.get("baseline") != "E3c-3b"
            or report.get("result") != "off_on_builds_verified"
            or report.get("device_accessed") is not False or report.get("device_written") is not False
            or report.get("disabled_comparison", {}).get("executable_and_data_equal") is not True
            or report.get("usb") != dict(reads_per_iteration=1, yield_ms=10, stack_bytes=6144, priority=1)
            or report.get("new_tasks") != 0
            or [row.get("enabled") for row in report.get("builds", [])] != [False, True]):
        raise ValueError("accepted E3c-4b static build required")
    _source_manifest(root, previous.PROJECT, report["source_sha256"], "E3c-4b")
    _artifacts(root, report, "E3c-4b")
    for row in report["builds"]:
        config = json.loads(_read(root, Path(row["build_directory"]) / "config/sdkconfig.json"))
        if (row["app_headroom_bytes"] < 1048576
                or not row["protected_equal"] or not all(row["protected_equal"].values())
                or config.get("DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE", False) is not row["enabled"]
                or config.get("DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE", False) is not False
                or config.get("DESKBOT_STUDIO_CATALOG_ENABLE", False) is not False
                or row["config_delta"] != (["DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE"] if row["enabled"] else [])):
            raise ValueError("E3c-4b display-only configuration differs")
    candidate = previous.reviewed_candidate(_read(root, previous.SOURCE), root)
    if (len(candidate) != ORIGINAL_SIZE or tx.sha(candidate) != ORIGINAL_SHA
            or report["builds"][1]["artifacts"]["xiaozhi.bin"] != dict(bytes=ORIGINAL_SIZE, sha256=ORIGINAL_SHA)
            or report["builds"][1]["app_elf_sha256"] != ORIGINAL_ELF_SHA
            or tx.sha(_read(root, previous.SOURCE.with_suffix(".elf"))) != ORIGINAL_ELF_SHA):
        raise ValueError("accepted E3c-4b build identity differs")


def reviewed_candidate(raw=None, root=ROOT):
    raw = _read(root, SOURCE) if raw is None else raw
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError("reviewed E3c-5b App changed")
    facts = image_facts(raw)
    start = facts["segments"][0]["file_offset"]
    if facts["configured_flash_bytes"] != tx.FLASH or raw[start+144:start+176].hex() != APP_ELF_SHA:
        raise ValueError("E3c-5b capacity or ELF identity differs")
    return raw


def load_reference(root=ROOT):
    _fixed_json(root, LIVE_REFERENCE / "plan.json", LIVE_PLAN_SHA)
    install = _fixed_json(root, LIVE_REFERENCE / "install-result.json", LIVE_INSTALL_SHA)
    receipt = _fixed_json(root, ACCEPTANCE, ACCEPTANCE_SHA)
    folder = paths.safe_path(root / LIVE_REFERENCE, root)
    plan = previous.check_plan(tx.load(folder), root=root)
    state = tx.Journal(folder, plan).load()
    tests = receipt.get("tests", {})
    if (plan.id != LIVE_PLAN_ID or plan.metadata["identity_sha256"] != IDENTITY_SHA
            or len(plan.image) != ORIGINAL_SIZE or tx.sha(plan.image) != ORIGINAL_SHA
            or state is None or state.get("phase") != "installed"
            or install.get("result") != "candidate_installed"
            or install.get("readback", {}).get("app_sha256") != tx.sha(plan.candidate)
            or not all(install.get("readback", {}).get(key) is True
                       for key in ("app_byte_equal", "static_outside_app_equal", "nvs_equal"))
            or install.get("boot", {}).get("firmware_matches_candidate") is not True
            or install.get("boot", {}).get("hello", {}).get("firmware", {}).get("app_elf_sha256") != ORIGINAL_ELF_SHA
            or receipt.get("result") != "passed" or receipt.get("app") != ROLLBACK_TARGET
            or receipt.get("app_sha256") != ORIGINAL_SHA or receipt.get("app_elf_sha256") != ORIGINAL_ELF_SHA
            or receipt.get("run") != LIVE_REFERENCE.as_posix() or receipt.get("plan_id") != plan.id
            or not all(tests.get(key) == "passed" for key in
                       ("menu_open_return", "conversation_menu_switch", "head_touch", "sleep_wake"))):
        raise ValueError("accepted E3c-4b installation and interaction record required")
    for item in receipt["evidence"]:
        if tx.sha(_read(root, item["path"])) != item["sha256"]:
            raise ValueError("E3c-4b acceptance evidence changed")
    # candidate includes the previous erase-sector tail. Keep that tail and the
    # following bytes when the next, larger App union is derived.
    end = tx.START + len(plan.rollback)
    snapshot = plan.snapshot[:tx.START] + plan.candidate + plan.snapshot[end:]
    union = snapshot[tx.START:tx.START + ERASE_BYTES]
    if tx.sha(snapshot) != REFERENCE_SNAPSHOT_SHA or tx.sha(union) != ORIGINAL_UNION_SHA:
        raise ValueError("accepted E3c-4b reconstructed snapshot differs")
    return dict(snapshot=snapshot, app_union=union, original_bytes=ORIGINAL_SIZE,
                identity_sha256=IDENTITY_SHA,
                report=dict(path=ACCEPTANCE.as_posix(), sha256=ACCEPTANCE_SHA),
                transaction=dict(path=LIVE_REFERENCE.as_posix(), plan_id=plan.id))


def check_current(raw, reference):
    expected = reference["snapshot"]
    if len(raw) != tx.FLASH or len(expected) != tx.FLASH:
        raise ValueError("complete Flash reference required")
    # Live preparation refreshes NVS. Every static byte, including the enlarged
    # erase union's extra 20 KiB, remains tied to the accepted installation.
    if raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]:
        raise ValueError("current Flash differs from accepted E3c-4b")


def check_build_evidence(root=ROOT):
    _display_build(root)
    report = _fixed_json(root, BUILD_REPORT, BUILD_REPORT_SHA)
    if (report.get("schema") != "deskbot.stable_candidate_ladder/v1"
            or report.get("stage") != "neutral" or report.get("baseline") != "E3c-3b"
            or report.get("result") != "off_on_builds_verified"
            or report.get("reference_app") != dict(bytes=ORIGINAL_SIZE, sha256=ORIGINAL_SHA)
            or report.get("device_accessed") is not False or report.get("device_written") is not False
            or report.get("disabled_comparison", {}).get("executable_and_data_equal") is not True
            or report.get("usb") != dict(reads_per_iteration=1, yield_ms=10, stack_bytes=6144, priority=1)
            or report.get("new_tasks") != 0
            or [row.get("enabled") for row in report.get("builds", [])] != [False, True]):
        raise ValueError("E3c-5b isolated neutral build required")
    _source_manifest(root, PROJECT, report["source_sha256"], "E3c-5b")
    _artifacts(root, report, "E3c-5b")
    for row in report["builds"]:
        folder = Path(row["build_directory"])
        if (row["app_headroom_bytes"] != tx.PARTITION - row["artifacts"]["xiaozhi.bin"]["bytes"]
                or row["app_headroom_bytes"] < 1048576
                or not 0 < row["internal_static_data_bytes"] < 80 * 1024
                or not row["protected_equal"] or not all(row["protected_equal"].values())
                or not row["compiler_stack_usage"]
                or any(item["bytes"] > 2048 or item["allocation"] not in ("static", "dynamic,bounded")
                       for item in row["compiler_stack_usage"].values())):
            raise ValueError("E3c-5b memory or protected-output budget differs")
        config = json.loads(_read(root, folder / "config/sdkconfig.json"))
        if (config.get("DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE") is not True
                or config.get("DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE", False) is not row["enabled"]
                or config.get("DESKBOT_STUDIO_CATALOG_ENABLE", False) is not False
                or row["config_delta"] != (["DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE"] if row["enabled"] else [])):
            raise ValueError("E3c-5b feature configuration differs")
    tests = _fixed_json(root, OFFLINE_REPORT, OFFLINE_REPORT_SHA)
    if (tests.get("result") != "passed" or tests.get("tests") != 7
            or tests.get("skipped") != 0 or tests.get("build_report_sha256") != BUILD_REPORT_SHA
            or tests.get("failures") != 0 or tests.get("errors") != 0 or tests.get("source_unchanged") is not True
            or tests.get("hardware_accessed") is not False or tests.get("device_written") is not False
            or tests.get("candidate_source") != PROJECT.as_posix()):
        raise ValueError("candidate production neutral native tests required")
    for name, digest in tests["source_sha256"].items():
        if tx.sha(_read(root, name)) != digest:
            raise ValueError("E3c-5b tested source changed: " + name)
    for name, digest in tests["compiled_source_sha256"].items():
        if tx.sha(_read(root, PROJECT / name)) != digest:
            raise ValueError("E3c-5b staged source changed: " + name)
    if tx.sha(_read(root, tests["log"])) != tests["log_sha256"] or tx.sha(_read(root, PATCH)) != PATCH_SHA:
        raise ValueError("E3c-5b test log or patch changed")
    image = reviewed_candidate(root=root)
    row = report["builds"][1]
    if (row["artifacts"]["xiaozhi.bin"] != dict(bytes=APP_SIZE, sha256=APP_SHA)
            or row["app_elf_sha256"] != APP_ELF_SHA
            or tx.sha(_read(root, SOURCE.with_suffix(".elf"))) != APP_ELF_SHA):
        raise ValueError("E3c-5b candidate differs from build")
    return dict(candidate=dict(variant="neutral_expression", bytes=APP_SIZE, sha256=APP_SHA,
                               app_elf_sha256=APP_ELF_SHA), image=image,
                build_report_sha256=BUILD_REPORT_SHA, offline_report_sha256=OFFLINE_REPORT_SHA,
                path_audit_sha256=PATCH_SHA)


def prepare_plan(snapshot, identity, image, root=ROOT):
    reference = load_reference(root)
    if identity != reference["identity_sha256"]:
        raise ValueError("E3c-5b plan targets another device")
    check_current(snapshot, reference)
    plan = tx.prepare(snapshot, reviewed_candidate(image, root), identity, ORIGINAL_SIZE, edition="E3c")
    return check_plan(plan, reference=reference, root=root)


def check_plan(plan, *, candidate=True, reference=None, root=ROOT):
    plan.validate()
    m = plan.metadata
    # Recovery uses only the persisted same-run backup and these fixed IDs; it
    # remains available after candidate, build and earlier-run files are removed.
    if (m["edition"] != "E3c" or m["identity_sha256"] != IDENTITY_SHA
            or m["original_bytes"] != ORIGINAL_SIZE or m["candidate_bytes"] != APP_SIZE
            or m["candidate_sha256"] != APP_SHA or m["erase_bytes"] != ERASE_BYTES
            or m["rollback_sha256"] != ORIGINAL_UNION_SHA or m["write_payload_sha256"] != WRITE_PAYLOAD_SHA
            or tx.sha(plan.rollback[:ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("E3c-5b plan or E3c-4b rollback differs")
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError("complete E3c-5b plan required")
        reference = reference or load_reference(root)
        check_current(plan.snapshot, reference)
        reviewed_candidate(plan.image, root)
    elif not isinstance(plan, tx.RecoveryPlan):
        raise ValueError("saved E3c-5b recovery plan required")
    return plan
