#!/usr/bin/env python3
"""Deploy the reviewed E3c App on COM4 with an exact E3b rollback."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol import studio_e3c_deployment as reviewed
ROLLBACK_TARGET = 'E3b-revision-2'

spec = importlib.util.spec_from_file_location("studio_e3c_deploy_parent", ROOT / "scripts/deploy-deskbot-studio-e3b.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class Device(parent.Device):
    def read_reference(self, reference):
        """Refresh the prefix and verify every byte of the retained E3b backup."""
        self.identity()
        prefix = self._capture("current-prefix", 0, tx.START)
        raw = prefix + reference["snapshot"][tx.START:]
        reviewed.check_current(raw, reference)
        path = self._new_path("payloads", "current-flash-reference.bin")
        tx.write_new(path, raw)
        output = self._invoke("verify-retained-backup", ("verify_flash", "--flash_mode", "keep",
            "--flash_size", "keep", "--flash_freq", "keep", "0x0", str(path)))
        if output.count("-- verify OK (digest matched)") != 1:
            raise ValueError("complete retained-backup device digest verification required")
        self.identity()
        return raw

    def verify_original(self):
        # Check the App and all static Flash again immediately before writing.
        self.verify_image(self.deployment_plan.rollback, allow_runtime_nvs=True)
        return True

    def boot(self, candidate):
        log = self._serial("e3c-boot" if candidate else "restored-boot", 45)
        if self.deployment_plan is None:
            return parent.inspect_boot(log, b"", self._identity.mac, identity_required=False)
        if candidate and ("USB input ready: /dev/secondary" not in log
                          or any(marker in log for marker in ("USB input unavailable",
                                  "USB input configuration unavailable", "USB input owner changed"))):
            raise ValueError("E3c secondary USB input initialization failed")
        image = (self.deployment_plan.image if candidate else
                 self.deployment_plan.snapshot[tx.START:tx.START + self.deployment_plan.metadata["original_bytes"]])
        return parent.inspect_boot(log, image, self._identity.mac, identity_required=True)


def folder_for(label):
    if re.fullmatch(r"e3c-live-[0-9]{8}-[a-z0-9-]{1,32}", label) is None:
        raise ValueError("run label must begin e3c-live-YYYYMMDD-")
    folder = ROOT / "tmp" / label
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("run must stay inside repository tmp")
    return folder


def prepare(folder, *, reuse_reference=False):
    image = reviewed.reviewed_candidate((ROOT / reviewed.SOURCE).read_bytes())
    reference = reviewed.load_reference()
    folder.mkdir()
    device = Device(folder, reference["identity_sha256"])
    confirmed = False
    try:
        device.identity()
        confirmed = True
        if reuse_reference:
            print(f"COM4 identity matched; refreshing the prefix and verifying the retained 32 MiB {ROLLBACK_TARGET} backup.", flush=True)
            raw = device.read_reference(reference)
            backup_source = {"method": "installed_reference_fresh_prefix_and_full_device_md5",
                             "run": reviewed.PREVIOUS_RUN}
        else:
            print(f"COM4 identity matched; reading a fresh 32 MiB {ROLLBACK_TARGET} backup.", flush=True)
            raw = device.read_full()
            backup_source = {"method": "same_run_complete_byte_readback"}
            reviewed.check_current(raw, reference)
        plan = tx.prepare(raw, image, reference["identity_sha256"], reviewed.ORIGINAL_SIZE, edition="E3c")
        reviewed.check_plan(plan)
        tx.persist(folder, plan)
        tx.write_new(folder / "preflight.json", tx.canonical({"result": "passed", "plan_id": plan.id,
            "baseline": reference["report"], "rollback_target": ROLLBACK_TARGET, "flash_written": False,
            "backup_source": backup_source,
            "erase_start": tx.START, "erase_end": tx.START + len(plan.rollback),
            "candidate_firmware": parent.image_identity(image)}))
        return {"phase": "prepared", "plan_id": plan.id, "erase_bytes": len(plan.rollback)}
    except BaseException:
        if confirmed:
            device.boot(False)
        raise


def recover_saved(folder, plan):
    numbers = [int(match[1]) for path in folder.iterdir()
               if (match := re.fullmatch(r"recover-result-([0-9]+)\.json", path.name))]
    path = folder / f"recover-result-{max(numbers, default=0) + 1:03d}.json"
    with path.open("xb") as stream:
        device = Device(folder, plan.metadata["identity_sha256"], plan)
        result = tx.recover(plan, device, tx.Journal(folder, plan))
        stream.write(tx.canonical(result))
        stream.flush()
        os.fsync(stream.fileno())
    return result


def run(phase, folder, *, reuse_reference=False):
    if reuse_reference and phase != "prepare":
        raise ValueError("retained-reference preparation only applies to prepare")
    if phase == "prepare":
        return prepare(folder, reuse_reference=reuse_reference)
    plan = reviewed.check_plan((tx.load_recovery if phase == "recover" else tx.load)(folder),
                              candidate=phase != "recover")
    if phase == "recover":
        return recover_saved(folder, plan)
    device = Device(folder, plan.metadata["identity_sha256"], plan)
    journal = tx.Journal(folder, plan)
    if phase == "restart":
        state = journal.load()
        if state is None or state["phase"] != "installed":
            raise ValueError("installed E3c required")
        try:
            previous = json.loads((folder / "install-result.json").read_bytes())["boot"]["hello"]
            boot = device.boot(True)
            if (boot["hello"]["boot_id"] == previous["boot_id"]
                    or boot["hello"]["device_id"] != previous["device_id"]):
                raise ValueError("restart identity differs")
            result = {"result": "restart_verified", "boot": boot,
                      "device_id_stable": True, "boot_id_changed": True}
        except BaseException:
            tx.recover(plan, device, journal)
            raise
    else:
        try:
            result = tx.install(plan, device, journal)
        except BaseException:
            state = journal.load()
            if state is None or state["phase"] == "prepared":
                device.boot(False)
            raise
    tx.write_new(folder / (phase + "-result.json"), tx.canonical(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "install", "recover", "restart"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--reuse-installed-reference", action="store_true",
        help="refresh current prefix and verify the complete retained E3b backup against COM4")
    args = parser.parse_args()
    result = run(args.phase, folder_for(args.run), reuse_reference=args.reuse_installed_reference)
    print(json.dumps({k: v for k, v in result.items() if k in
                      {"phase", "result", "plan_id", "erase_bytes", "hardware_acceptance"}}), flush=True)


if __name__ == "__main__":
    main()
