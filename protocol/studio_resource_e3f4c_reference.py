"""Reconstruct the installed, accepted E3f-4c image from frozen evidence."""
import json
from pathlib import Path

from protocol import studio_app_deployment as app
from protocol import studio_resource_installation as resources

ROOT = Path(__file__).resolve().parents[1]
RUN = Path("tmp/e3c-live-20261110-e3f4c-live1")
PLAN_ID = "79567205ed8b94e4e289e251d1b744157c2e92d1b1508d2f608212697a6cafc6"
INSTALL_SHA = "9f4980fe9c6678da9388796f8557a54d137f3354d48aefb7df272d24adabc22e"
IDENTITY_SHA = "1232890adbc14b7a1b677968e67e0eab8df37784762209cc69987d1c98e938e2"
APP_ELF_SHA = "e621cd55020aac48c0887f95c05c035ccfe912b043a454d87f66d1b4227d5392"
UNION_SHA = "d1da2ab2c35c6334485ecf67979b8dae0e99239d4e73d76e31f076b1d0b95ffb"
ACCEPTANCE = Path("artifacts/studio-layout-e3f4c-device/manual-acceptance.json")


def load_reference(root=ROOT):
    folder = resources._safe(root / RUN, ".")
    plan = app.load(folder)
    receipt_raw = resources._safe(folder, "install-result.json").read_bytes()
    receipt = json.loads(receipt_raw)
    acceptance = json.loads(resources._safe(root, str(ACCEPTANCE)).read_bytes())
    if (plan.id != PLAN_ID or resources.sha(receipt_raw) != INSTALL_SHA
            or plan.metadata["identity_sha256"] != IDENTITY_SHA
            or plan.metadata["candidate_sha256"] != resources.E3F4C_APP_SHA
            or len(plan.image) != resources.E3F4C_APP_BYTES
            or resources.sha(plan.candidate) != UNION_SHA
            or receipt.get("result") != "candidate_installed"
            or not all(receipt["readback"].get(key) is True
                       for key in ("app_byte_equal", "nvs_equal", "static_outside_app_equal"))
            or receipt["boot"]["hello"]["firmware"]["app_elf_sha256"] != APP_ELF_SHA
            or (acceptance.get("app"), acceptance.get("resources"), acceptance.get("result"))
               != ("E3f-4c", "E1.18", "accepted_by_user")):
        raise ValueError("accepted_E3f4c_install_evidence_changed")
    # The saved snapshot precedes App installation. Overlay the complete App
    # erase union; using the snapshot alone would silently retain E3f-4a.
    raw = plan.snapshot[:app.START] + plan.candidate + plan.snapshot[app.START + len(plan.candidate):]
    resources.validate_snapshot(raw, baseline="E3f-4c")
    return dict(snapshot=raw, identity_sha256=IDENTITY_SHA,
                report=dict(run=RUN.as_posix(), plan_id=plan.id, install_sha256=INSTALL_SHA,
                            reconstructed_sha256=resources.sha(raw)))
