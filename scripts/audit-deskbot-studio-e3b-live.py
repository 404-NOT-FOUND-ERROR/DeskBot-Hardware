#!/usr/bin/env python3
"""Publish identity deployment facts from the private same-run evidence."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol.device_link import validate_hello

PUBLIC = ROOT / "artifacts/deskbot-studio-e3b-20261104/live-deployment.json"


def audit(folder):
    plan = tx.load(folder)
    state = tx.Journal(folder, plan).load()
    install = json.loads((folder / "install-result.json").read_bytes())
    restart = json.loads((folder / "restart-result.json").read_bytes())
    observation = json.loads((folder / "reconnect-evidence.json").read_bytes())
    if (state is None or state["phase"] != "installed" or install["result"] != "candidate_installed"
            or restart["result"] != "restart_verified" or not restart["device_id_stable"]
            or not restart["boot_id_changed"]):
        raise ValueError("installation and restart evidence required")
    first, second = install["boot"], restart["boot"]
    for boot in (first, second):
        validate_hello(boot["hello"])
        if not boot["normal_boot"] or not boot["periodic_identity_verified"] or boot["hello_count"] < 3:
            raise ValueError("periodic boot identity not verified")
    if first["hello"]["device_id"] != second["hello"]["device_id"] or first["hello"]["boot_id"] == second["hello"]["boot_id"]:
        raise ValueError("restart identity comparison differs")
    off = observation["disconnected"]
    if (off["connection"]["status"] not in {"disabled", "disconnected"}
            or off["identity"]["status"] != "disconnected" or off["identity"]["hello"] is not None):
        raise ValueError("disconnect must clear identity")
    hellos = []
    for key in ("first", "reconnected", "later"):
        value = observation[key]
        identity = value["identity"]
        if (value["schema"] != "deskbot.body_observation/v2" or value["connection"]["status"] != "connected"
                or identity["status"] != "confirmed" or identity["age_ms"] > 30000
                or value["controls_available"] is not False):
            raise ValueError("fresh live identity required: " + key)
        hello = validate_hello(identity["hello"])
        if any(hello[k] != second["hello"][k] for k in ("device_id", "boot_id", "firmware", "hardware")):
            raise ValueError("passive reconnect changed device or boot identity")
        hellos.append(hello)
    if not hellos[0]["sequence"] < hellos[1]["sequence"] < hellos[2]["sequence"]:
        raise ValueError("new identity messages required across reconnect")
    readback = install["readback"]
    if (readback["method"] != "app_byte_readback_and_outside_app_device_md5"
            or readback["app_sha256"] != tx.sha(plan.candidate) or not readback["app_byte_equal"]
            or not readback["static_outside_app_equal"] or not readback["nvs_equal"]):
        raise ValueError("App readback and protected Flash verification required")
    return {"schema": "deskbot.studio_identity_live/v1", "edition": "E3b", "date": "2026-11-04",
            "result": "app_readback_boot_identity_reconnect_passed_interaction_pending",
            "run_label": folder.name, "plan_id": plan.id, "device_written": True,
            "write_scope": {"target": "body_COM4", "offset": tx.START, "erase_bytes": len(plan.rollback)},
            "candidate": {"bytes": len(plan.image), "sha256": tx.sha(plan.image)},
            "backup": {"flash_bytes": len(plan.snapshot), "sha256": tx.sha(plan.snapshot),
                       "rollback_bytes": len(plan.rollback), "rollback_sha256": tx.sha(plan.rollback),
                       "retained_locally": True},
            "verification": readback,
            "initial_boot": {k: v for k, v in first.items() if k != "hello"},
            "restart": {"device_id_stable": True, "boot_id_changed": True, "hello_count": second["hello_count"]},
            "device_announcement": hellos[-1],
            "reconnect": {"identity_cleared_on_disconnect": True, "same_device": True, "same_boot": True,
                          "sequence_samples": [h["sequence"] for h in hellos], "periodic_updates": True},
            "hardware_acceptance": "pending", "base_tests": "after_repair",
            "evidence_sha256": {name: tx.sha((folder / name).read_bytes()) for name in
                                ("preflight.json", "install-result.json", "restart-result.json", "reconnect-evidence.json")}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if re.fullmatch(r"e3b-live-[0-9]{8}-[a-z0-9-]{1,32}", args.run) is None:
        raise ValueError("invalid run label")
    folder = ROOT / "tmp" / args.run
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("run directory differs")
    raw = (json.dumps(audit(folder), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if PUBLIC.read_bytes() != raw:
            raise ValueError("live report differs")
    else:
        tx.write_new(PUBLIC, raw)
    print("E3b App readback, protected Flash, boot and reconnect audit passed")
