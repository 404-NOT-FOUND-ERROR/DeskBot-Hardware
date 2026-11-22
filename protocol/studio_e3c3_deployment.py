"""Pinned E3c-3 query candidate and the verified installed E3c-2 reference."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
APP_SHA = "1e659ae53fc944b7c73925e6ace152759ef657e0b99acde12d1ef9a6c59df1bd"
APP_SIZE = 4489152
SOURCE = Path("tmp/e3c3-state-20261105/on/xiaozhi.bin")
ORIGINAL_SIZE = 4488144
ORIGINAL_SHA = "5e29d548f439d64f4df48cf82850e458aedab9bca92df8a0a43f0a65811fdce4"
PREVIOUS_RUN = "e3c-live-20261106-menu3"
PREVIOUS_PLAN = "11632f001de2bcd1259c4952ed918632985f91387ff8340ba78bd2bc5c84b538"


def reviewed_candidate(raw):
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError("reviewed E3c-3 candidate changed")
    if image_facts(raw)["configured_flash_bytes"] != tx.FLASH:
        raise ValueError("E3c-3 image capacity differs")
    return raw


def load_reference(root=ROOT):
    folder = root / "tmp" / PREVIOUS_RUN
    if folder.is_symlink() or folder.resolve().parent != (root / "tmp").resolve():
        raise ValueError("saved E3c-2 transaction path differs")
    plan = tx.load(folder)
    state = tx.Journal(folder, plan).load()
    install = json.loads((folder / "install-result.json").read_bytes())
    if (plan.id != PREVIOUS_PLAN or state is None or state["phase"] != "installed"
            or plan.metadata["edition"] != "E3c" or tx.sha(plan.image) != ORIGINAL_SHA
            or len(plan.image) != ORIGINAL_SIZE or install.get("result") != "candidate_installed"
            or install.get("readback", {}).get("app_sha256") != tx.sha(plan.candidate)
            or not all(install.get("readback", {}).get(key) for key in
                       ("app_byte_equal", "static_outside_app_equal", "nvs_equal"))):
        raise ValueError("verified installed E3c-2 transaction required")
    end = tx.START + len(plan.rollback)
    snapshot = plan.snapshot[:tx.START] + plan.candidate + plan.snapshot[end:]
    return {"identity_sha256": plan.metadata["identity_sha256"], "snapshot": snapshot,
            "app_union": plan.candidate,
            "report": {"path": (Path("tmp") / PREVIOUS_RUN / "install-result.json").as_posix(),
                       "sha256": tx.sha((folder / "install-result.json").read_bytes())},
            "original_bytes": ORIGINAL_SIZE, "original_sha256": ORIGINAL_SHA}


def check_current(raw, reference):
    expected = reference["snapshot"]
    app_union = reference["app_union"]
    if len(raw) != tx.FLASH or len(expected) != tx.FLASH:
        raise ValueError("complete current Flash readback required")
    end = tx.START + len(app_union)
    if (raw[:0x9000] != expected[:0x9000]
            or raw[0xD000:tx.START] != expected[0xD000:tx.START]
            or raw[tx.START:end] != app_union
            or raw[end:] != expected[end:]):
        raise ValueError("COM4 Flash differs from the verified E3c-2 reference")


def check_plan(plan, *, candidate=True):
    plan.validate()
    if (plan.metadata["edition"] != "E3c" or plan.metadata["original_bytes"] != ORIGINAL_SIZE
            or tx.sha(plan.snapshot[tx.START:tx.START + ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("E3c-3 plan must retain the installed E3c-2 App")
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError("complete E3c-3 candidate plan required")
        reviewed_candidate(plan.image)
    return plan
