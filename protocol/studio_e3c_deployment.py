"""Pinned E3c image and the installed E3b rollback reference."""
from pathlib import Path
import json

from protocol import studio_app_deployment as tx
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
# The reviewed build hash is deliberately independent of the deployment plan.
APP_SHA = "cbc298e816cef97372fd845a5388a0d72f4fc3a70afb983ada5154cbb79f9873"
APP_SIZE = 4510432
SOURCE = Path("tmp/e3c7-usb-secondary-20261105/on/xiaozhi.bin")
ORIGINAL_SHA = "8dc14006255ca30ffce584e81df7aeec2e3fb5dd2721da9136c144fdf312c69e"
ORIGINAL_SIZE = 4483424
PREVIOUS_RUN = "e3b-live-20261104-install2"
REPORT = Path("artifacts/deskbot-studio-e3b-20261104/live-deployment.json")
REPORT_SHA = "1081e0d9aa6e4c1378213cee186097e15f80deb196b5a0254896849e5ce99405"


def reviewed_candidate(raw):
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError("reviewed E3c candidate changed")
    facts = image_facts(raw)
    if facts["configured_flash_bytes"] != tx.FLASH:
        raise ValueError("E3c image capacity differs")
    return raw


def load_reference(root=ROOT):
    """Revalidate the installed E3b transaction, without accessing hardware."""
    report_raw = (root / REPORT).read_bytes()
    if tx.sha(report_raw) != REPORT_SHA:
        raise ValueError("sealed E3b live report differs")
    report = json.loads(report_raw)
    folder = root / "tmp" / PREVIOUS_RUN
    previous = tx.load(folder)
    state = tx.Journal(folder, previous).load()
    if (report["run_label"] != PREVIOUS_RUN or report["plan_id"] != previous.id
            or previous.metadata["edition"] != "E3b" or state is None or state["phase"] != "installed"
            or len(previous.image) != ORIGINAL_SIZE or tx.sha(previous.image) != ORIGINAL_SHA
            or report["verification"]["app_sha256"] != tx.sha(previous.candidate)
            or not all(report["verification"][key] for key in
                       ("app_byte_equal", "static_outside_app_equal", "nvs_equal"))):
        raise ValueError("installed E3b reference differs")
    for name, digest in report["evidence_sha256"].items():
        if Path(name).name != name or tx.sha((folder / name).read_bytes()) != digest:
            raise ValueError("saved E3b deployment evidence differs")
    end = tx.START + len(previous.rollback)
    reference = previous.snapshot[:tx.START] + previous.candidate + previous.snapshot[end:]
    return {"identity_sha256": previous.metadata["identity_sha256"], "snapshot": reference,
            "report": {"path": REPORT.as_posix(), "sha256": REPORT_SHA},
            "original_bytes": ORIGINAL_SIZE, "original_sha256": ORIGINAL_SHA}


def check_current(raw, reference):
    """Every byte outside NVS must match the E3b reference, including App tails."""
    expected = reference["snapshot"]
    if (len(raw) != tx.FLASH or len(expected) != tx.FLASH
            or raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]):
        raise ValueError("current Flash differs from installed E3b reference")


def check_plan(plan, *, candidate=True):
    plan.validate()
    if (plan.metadata["edition"] != "E3c" or plan.metadata["original_bytes"] != ORIGINAL_SIZE
            or tx.sha(plan.snapshot[tx.START:tx.START + ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError("E3c plan must retain the installed E3b App")
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError("complete E3c candidate plan required")
        reviewed_candidate(plan.image)
    return plan
