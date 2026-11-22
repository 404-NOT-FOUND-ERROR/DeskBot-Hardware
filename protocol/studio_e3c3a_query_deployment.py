"""Pinned E3c-3a state-query App with the accepted query-off App as rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol.storage_capacity import image_facts
from protocol import studio_e3c3a_deployment as query_off

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("tmp/e3c3a-usb-path-20261106/on/xiaozhi.bin")
LIVE_QUERY_OFF = Path("tmp/e3c-live-20261106-e3c3a-menu-off-203142")
LIVE_RESULT = Path("artifacts/ui-menu-regression-20261106/e3c3a-live-result.json")
APP_SIZE = 4489232
APP_SHA = "e34d29e96bfb68eadda974b1f0a48350551a9b4d669cbf52e58db54b205d6dc3"
APP_ELF_SHA = "cfe8c4b4700e0b0a51c0c7a881988af11d0d7084cfa05931a0af5f97c9618ca5"
QUERY_OFF_APP_SIZE = 4488224
QUERY_OFF_APP_SHA = "a67aaec69b359039db52e1987ed6d9f2dc726243f2871d2b9718b6095c0d8375"
QUERY_OFF_APP_UNION_SHA = "e70b908d262cb2a7b66209e9a51c78d72d67905898a797bbb39942793685cfc0"
EDITION = "E3c-3a"
ROLLBACK_TARGET = "E3c-3a-query-off"


def reviewed_candidate(raw=None, root=ROOT):
    if raw is None:
        raw = (root / SOURCE).read_bytes()
    facts = image_facts(raw)
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError("reviewed E3c-3a query-enabled App changed")
    if facts["configured_flash_bytes"] != tx.FLASH:
        raise ValueError("query-enabled Flash capacity differs")
    start = facts["segments"][0]["file_offset"]
    if raw[start + 144:start + 176].hex() != APP_ELF_SHA:
        raise ValueError("query-enabled ELF identity differs")
    return raw


def _query_off_transaction(root):
    folder = root / LIVE_QUERY_OFF
    if folder.is_symlink() or folder.resolve().parent != (root / "tmp").resolve():
        raise ValueError("accepted query-off transaction path differs")
    plan = tx.load(folder)
    state = tx.Journal(folder, plan).load()
    result_path = folder / "install-result.json"
    install = json.loads(result_path.read_bytes())
    query_off.check_plan(plan)
    if (state is None or state.get("phase") != "installed"
            or install.get("result") != "candidate_installed"
            or tx.sha(plan.image) != QUERY_OFF_APP_SHA
            or len(plan.image) != QUERY_OFF_APP_SIZE
            or install.get("readback", {}).get("app_sha256") != tx.sha(plan.candidate)
            or not all(install.get("readback", {}).get(key) is True
                       for key in ("app_byte_equal", "static_outside_app_equal", "nvs_equal"))
            or install.get("boot", {}).get("firmware_matches_candidate") is not True
            or install.get("boot", {}).get("hello", {}).get("firmware", {}).get("app_elf_sha256")
            != query_off.APP_ELF_SHA):
        raise ValueError("accepted query-off App transaction required")
    receipt_path = root / LIVE_RESULT
    receipt = json.loads(receipt_path.read_bytes())
    tests = receipt.get("user_acceptance", {}).get("tests", {})
    if (receipt.get("result") != "query_off_menu_and_interaction_passed"
            or receipt.get("device_written") is not True
            or not all(tests.get(key) is True for key in
                       ("idle_60s", "menu_open_return_5x", "conversation_during_after_menu",
                        "head_touch_three", "sleep_wake"))):
        raise ValueError("query-off live menu acceptance required")
    end = tx.START + len(plan.rollback)
    snapshot = plan.snapshot[:tx.START] + plan.candidate + plan.snapshot[end:]
    if tx.sha(plan.candidate) != QUERY_OFF_APP_UNION_SHA:
        raise ValueError("query-off rollback App union differs")
    return {
        "identity_sha256": plan.metadata["identity_sha256"],
        "snapshot": snapshot,
        "app_union": plan.candidate,
        "original_bytes": len(plan.image),
        "report": {"path": LIVE_RESULT.as_posix(),
                   "sha256": tx.sha(receipt_path.read_bytes())},
        "transaction": {"path": LIVE_QUERY_OFF.as_posix(),
                        "install_result_sha256": tx.sha(result_path.read_bytes())},
    }


def load_reference(root=ROOT):
    return _query_off_transaction(root)


def check_current(raw, reference):
    expected = reference["snapshot"]
    app_union = reference["app_union"]
    if len(raw) != tx.FLASH or len(expected) != tx.FLASH:
        raise ValueError("complete current Flash reference required")
    end = tx.START + len(app_union)
    if (raw[:0x9000] != expected[:0x9000]
            or raw[0xD000:tx.START] != expected[0xD000:tx.START]
            or raw[tx.START:end] != app_union
            or raw[end:] != expected[end:]):
        raise ValueError("COM4 Flash differs from the accepted E3c-3a query-off reference")


def check_build_evidence(root=ROOT):
    evidence = query_off.check_build_evidence(root)
    raw = reviewed_candidate((root / SOURCE).read_bytes(), root)
    report = json.loads((root / query_off.BUILD_REPORT).read_bytes())
    row = next((item for item in report["builds"] if item.get("query_enabled") is True), None)
    artifact = None if row is None else row.get("artifacts", {}).get("xiaozhi.bin")
    if artifact != {"bytes": len(raw), "sha256": tx.sha(raw)} or row.get("app_elf_sha256") != APP_ELF_SHA:
        raise ValueError("query-enabled image differs from the isolated build report")
    candidate = {"variant": "query_enabled", "bytes": len(raw),
                 "sha256": tx.sha(raw), "app_elf_sha256": APP_ELF_SHA}
    return {**evidence, "candidate": candidate, "image": raw}


def prepare_plan(snapshot, identity, image, root=ROOT):
    reference = load_reference(root)
    if identity != reference["identity_sha256"]:
        raise ValueError("query-off and query-on plans target different bodies")
    check_current(snapshot, reference)
    plan = tx.prepare(snapshot, reviewed_candidate(image, root), identity,
                      reference["original_bytes"], edition="E3c")
    check_plan(plan, reference=reference)
    return plan


def check_plan(plan, *, candidate=True, reference=None, root=ROOT):
    plan.validate()
    metadata = plan.metadata
    if (metadata["edition"] != "E3c" or metadata["original_bytes"] != QUERY_OFF_APP_SIZE
            or metadata["candidate_bytes"] != APP_SIZE
            or metadata["candidate_sha256"] != APP_SHA
            or tx.sha(plan.rollback[:QUERY_OFF_APP_SIZE]) != QUERY_OFF_APP_SHA
            or tx.sha(plan.rollback[:4489216]) != QUERY_OFF_APP_UNION_SHA):
        raise ValueError("query-enabled E3c-3a plan identity differs")
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError("complete query-enabled candidate plan required")
        reference = reference or load_reference(root)
        if metadata["identity_sha256"] != reference["identity_sha256"]:
            raise ValueError("query-enabled plan body differs")
        check_current(plan.snapshot, reference)
        reviewed_candidate(plan.image, root)
    elif not isinstance(plan, tx.RecoveryPlan):
        raise ValueError("query-enabled recovery plan required")
    return plan
