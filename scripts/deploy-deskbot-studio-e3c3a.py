#!/usr/bin/env python3
"""Prepare offline or install/recover the reviewed E3c-3a menu-test App."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol import studio_e3c3a_deployment as reviewed
from protocol import studio_e3c3a_query_deployment as query_enabled
query_off = reviewed

spec = importlib.util.spec_from_file_location("e3c3a_generic_deploy", ROOT / "scripts/deploy-deskbot-studio-e3c.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry.reviewed = reviewed
entry.ROLLBACK_TARGET = reviewed.ROLLBACK_TARGET


class MenuTestDevice(entry.Device):
    def verify_original(self):
        # Live preparation saves the current prefix, including NVS.
        self.verify_image(self.deployment_plan.rollback, allow_runtime_nvs=False)
        return True


def folder_for(label):
    if re.fullmatch(r"e3c-live-[0-9]{8}-e3c3a-[a-z0-9-]{1,24}", label) is None:
        raise ValueError("run label must be e3c-live-YYYYMMDD-e3c3a-<name>")
    folder = ROOT / "tmp" / label
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("run must stay inside repository tmp")
    return folder


def prepare_offline(folder):
    evidence = reviewed.check_build_evidence()
    reference = reviewed.load_reference()
    plan = reviewed.prepare_plan(reference["snapshot"], reference["identity_sha256"], evidence["image"])
    if folder.exists():
        raise FileExistsError("run directory already exists; use a new run label")
    folder.mkdir()
    tx.persist(folder, plan)
    tx.write_new(folder / "preflight.json", tx.canonical({
        "schema": "deskbot.usb_path_install_preflight/v1",
        "edition": reviewed.EDITION,
        "result": "offline_plan_ready",
        "plan_id": plan.id,
        "target": "body_COM4",
        "candidate": evidence["candidate"],
        "rollback_target": reviewed.ROLLBACK_TARGET,
        "rollback_source": reference["report"],
        "rollback_sha256": tx.sha(plan.rollback),
        "write": {"offset": tx.START, "erase_bytes": len(plan.rollback),
                  "end_exclusive": tx.START + len(plan.rollback)},
        "fresh_device_check_required_before_install": True,
        "checksums": {"build_report": evidence["build_report_sha256"],
                      "offline_tests": evidence["offline_report_sha256"],
                      "path_audit": evidence["path_audit_sha256"]},
        "hardware_accessed": False,
        "device_written": False,
        "hardware_acceptance": "pending",
    }))
    return {"phase": "prepared_offline", "edition": reviewed.EDITION,
            "plan_id": plan.id, "erase_bytes": len(plan.rollback), "hardware_accessed": False}


def validate_saved(folder, phase):
    plan = reviewed.check_plan((tx.load_recovery if phase == "recover" else tx.load)(folder),
                              candidate=phase != "recover")
    if phase == "recover":
        return plan
    preflight = json.loads((folder / "preflight.json").read_bytes())
    evidence = reviewed.check_build_evidence()
    live = preflight.get("result") == "device_preflight_ready"
    if phase in {"install", "restart"} and not live:
        raise ValueError("live preflight required before E3c-3a installation")
    expected_result = "device_preflight_ready" if live else "offline_plan_ready"
    expected_hardware = live
    expected_fresh_check = not live
    expected_write = {"offset": tx.START, "erase_bytes": len(plan.rollback),
                      "end_exclusive": tx.START + len(plan.rollback)}
    if (preflight.get("edition") != reviewed.EDITION
            or preflight.get("schema") != "deskbot.usb_path_install_preflight/v1"
            or preflight.get("result") != expected_result or preflight.get("plan_id") != plan.id
            or preflight.get("device_written") is not False
            or preflight.get("hardware_accessed") is not expected_hardware
            or preflight.get("fresh_device_check_required_before_install") is not expected_fresh_check
            or preflight.get("target") != "body_COM4"
            or preflight.get("candidate") != evidence["candidate"]
            or preflight.get("rollback_target") != reviewed.ROLLBACK_TARGET
            or preflight.get("rollback_sha256") != tx.sha(plan.rollback)
            or preflight.get("write") != expected_write
            or preflight.get("checksums") != {"build_report": evidence["build_report_sha256"],
                                               "offline_tests": evidence["offline_report_sha256"],
                                               "path_audit": evidence["path_audit_sha256"]}):
        raise ValueError("E3c-3a preflight receipt differs")
    return plan


def prepare_live(folder, source, *, backup_verification="fresh_prefix_and_full_device_md5"):
    template = validate_saved(source, "check")
    evidence = reviewed.check_build_evidence()
    if folder.exists():
        raise FileExistsError("run directory already exists; use a new run label")
    folder.mkdir()
    device = MenuTestDevice(folder, template.metadata["identity_sha256"])
    confirmed = False
    try:
        device.identity()
        confirmed = True
        reference = {"snapshot": template.snapshot, "app_union": template.rollback,
                     "identity_sha256": template.metadata["identity_sha256"]}
        fresh = device.read_reference(reference)
        reviewed.check_current(fresh, reference)
        plan = reviewed.prepare_plan(fresh, reference["identity_sha256"], template.image)
        tx.persist(folder, plan)
        tx.write_new(folder / "preflight.json", tx.canonical({
            "schema": "deskbot.usb_path_install_preflight/v1",
            "edition": reviewed.EDITION,
            "result": "device_preflight_ready",
            "plan_id": plan.id,
            "source_plan_id": template.id,
            "target": "body_COM4",
            "candidate": evidence["candidate"],
            "rollback_target": reviewed.ROLLBACK_TARGET,
            "rollback_sha256": tx.sha(plan.rollback),
            "write": {"offset": tx.START, "erase_bytes": len(plan.rollback),
                      "end_exclusive": tx.START + len(plan.rollback)},
            "backup_verification": backup_verification,
            "fresh_device_check_required_before_install": False,
            "checksums": {"build_report": evidence["build_report_sha256"],
                          "offline_tests": evidence["offline_report_sha256"],
                          "path_audit": evidence["path_audit_sha256"]},
            "hardware_accessed": True,
            "device_written": False,
            "hardware_acceptance": "pending",
        }))
        return {"phase": "prepared_live", "edition": reviewed.EDITION, "plan_id": plan.id,
                "erase_bytes": len(plan.rollback), "hardware_accessed": True}
    except BaseException:
        if confirmed:
            device.boot(False)
        raise


def run(phase, folder, *, source=None):
    if phase == "prepare":
        return prepare_offline(folder)
    if phase == "prepare-live":
        if source is None:
            raise ValueError("offline source plan required for live preflight")
        return prepare_live(folder, source)
    if source is not None:
        raise ValueError("source plan only applies to live preflight")
    validate_saved(folder, phase)
    if phase == "check":
        plan = tx.load(folder)
        return {"phase": "checked_offline", "edition": reviewed.EDITION,
                "plan_id": plan.id, "erase_bytes": len(plan.rollback), "hardware_accessed": False}
    original_device = entry.Device
    try:
        entry.Device = MenuTestDevice
        return entry.run(phase, folder)
    finally:
        entry.Device = original_device


def select_reviewed(query_on):
    global reviewed
    reviewed = query_enabled if query_on else query_off
    entry.reviewed = reviewed
    entry.ROLLBACK_TARGET = reviewed.ROLLBACK_TARGET
    return reviewed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "check", "prepare-live", "install", "recover", "restart"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--from-run", help="offline plan used by prepare-live")
    parser.add_argument("--query-enabled", action="store_true",
                        help="select the separately pinned state-query build")
    args = parser.parse_args(argv)
    if (args.phase == "prepare-live") != (args.from_run is not None):
        parser.error("prepare-live requires --from-run; other phases use only --run")
    select_reviewed(args.query_enabled)
    result = run(args.phase, folder_for(args.run),
                 source=folder_for(args.from_run) if args.from_run is not None else None)
    print(json.dumps({key: value for key, value in result.items()
                      if key in {"phase", "result", "edition", "plan_id", "erase_bytes", "hardware_accessed"}},
                     sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
