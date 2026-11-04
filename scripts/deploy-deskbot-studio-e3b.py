#!/usr/bin/env python3
"""Prepare, install or recover the reviewed E3b COM4 App."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol.device_link import decode_hello
from protocol.storage_capacity import image_facts
from protocol.storage_continuous_mmap_baseline import load_migrated_baseline, accepted_snapshot
from protocol.storage_upper_experiment_esptool import _NORMAL_BOOT_MARKERS, _PANIC_MARKERS

spec = importlib.util.spec_from_file_location("reviewed_app_transport", ROOT / "scripts/run-expression-continuous-mmap-upper-e1-43b.py")
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)
APP_SHA = "8dc14006255ca30ffce584e81df7aeec2e3fb5dd2721da9136c144fdf312c69e"
APP_SIZE = 4483424
SOURCE = ROOT / "tmp/e3b-identity-20261104/on/xiaozhi.bin"


def image_identity(raw):
    start = image_facts(raw)["segments"][0]["file_offset"]
    if struct.unpack_from("<I", raw, start)[0] != 0xABCD5432:
        raise ValueError("App descriptor differs")
    return {"version": raw[start + 16:start + 48].split(b"\0")[0].decode("ascii"),
            "app_elf_sha256": raw[start + 144:start + 176].hex()}


def inspect_boot(log, image, mac, *, identity_required):
    if not all(s in log for s in _NORMAL_BOOT_MARKERS) or any(s in log for s in _PANIC_MARKERS):
        raise ValueError("boot initialization failed")
    if not identity_required:
        return {"normal_boot": True}
    expected = image_identity(image)
    device_id = "device-" + hashlib.sha256(b"deskbot-link-v1\0" + bytes.fromhex(mac.replace(":", ""))).digest()[:16].hex()
    messages = []
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    for line in log.splitlines():
        line = ansi.sub("", line.split("\t", 1)[-1])
        match = re.fullmatch(r"I \([0-9]+\) DeskBotLink: (\{.*\})", line)
        if match:
            hello = decode_hello(match[1].encode())
            if (hello["device_id"] != device_id or any(hello["firmware"][key] != value for key, value in expected.items())
                    or hello["hardware"]["flash_bytes"] != 0x2000000
                    or hello["hardware"]["psram_bytes"] != 0x1000000):
                raise ValueError("runtime identity differs from built App/device")
            if messages and (hello["boot_id"] != messages[-1]["boot_id"]
                             or hello["sequence"] != messages[-1]["sequence"] + 1
                             or not 9000 <= hello["uptime_ms"] - messages[-1]["uptime_ms"] <= 12000):
                raise ValueError("hello order or interval differs")
            messages.append(hello)
    if len(messages) < 3:
        raise ValueError("three periodic identity messages required")
    return {"normal_boot": True, "hello_count": len(messages), "hello": messages[-1],
            "sequence_start": messages[0]["sequence"], "periodic_identity_verified": True,
            "firmware_matches_candidate": True, "hardware_matches_target": True}


class Device(transport.AppDevice):
    def __init__(self, folder, identity, plan=None):
        super().__init__(folder)
        self.expected_identity, self.deployment_plan = identity, plan

    def identity(self):
        actual = super().identity()
        if actual != self.expected_identity:
            raise ValueError("COM4 is not the accepted body")
        return actual

    def read_app(self, size):
        if self.deployment_plan is None or size != len(self.deployment_plan.rollback):
            raise ValueError("exact App erase union required")
        self.identity()
        return self._capture("app-union", tx.START, size)

    def write_app(self, raw):
        saved = tx.load_recovery(self.run_dir)
        state = tx.Journal(self.run_dir, saved).load()
        if self.deployment_plan is None or saved.id != self.deployment_plan.id or state is None:
            raise ValueError("persisted deployment plan required")
        allowed = [saved.rollback] if state["phase"] in {"restoring", "needs_recovery"} else []
        if state["phase"] == "restore_required":
            allowed.append(saved.rollback)
            if isinstance(self.deployment_plan, tx.Plan):
                allowed.append(tx.load(self.run_dir).candidate)
        if not isinstance(raw, bytes) or raw not in allowed:
            raise ValueError("exact planned App bytes and durable write intent required")
        self.identity()
        path = self._new_path("payloads", "app-union.bin")
        tx.write_new(path, raw)
        self._invoke("write-studio-app", ("write_flash", "--verify", "--flash_mode", "keep",
                     "--flash_size", "keep", "--flash_freq", "keep", hex(tx.START), str(path)))

    def verify_original(self):
        plan = tx.load(self.run_dir)
        if self.deployment_plan is None or plan.id != self.deployment_plan.id:
            raise ValueError("saved rollback required")
        self.identity()
        output = self._invoke("verify-original-app", ("verify_flash", "--flash_mode", "keep",
                  "--flash_size", "keep", "--flash_freq", "keep", hex(tx.START),
                  str(self.run_dir / "rollback-app-union.bin")))
        return output.count("-- verify OK (digest matched)") == 1

    def boot(self, candidate):
        log = self._serial("e3b-boot" if candidate else "restored-boot", 45)
        return inspect_boot(log, self.deployment_plan.image if candidate else b"", self._identity.mac,
                            identity_required=candidate)

    def verify_image(self, payload, *, allow_runtime_nvs):
        plan = tx.load_recovery(self.run_dir)
        allowed = [plan.rollback]
        if isinstance(self.deployment_plan, tx.Plan) and payload != plan.rollback:
            allowed.append(tx.load(self.run_dir).candidate)
        if self.deployment_plan is None or plan.id != self.deployment_plan.id or payload not in allowed:
            raise ValueError("saved App payload required for verification")
        self.identity()
        app = self._capture("app-readback", tx.START, len(payload))
        if app != payload:
            raise ValueError("App erase-union readback differs")
        end = tx.START + len(payload)
        ranges = [(0, plan.snapshot[:tx.START]), (end, plan.snapshot[end:])]
        if allow_runtime_nvs:
            ranges = [(0, plan.snapshot[:0x9000]), (0xD000, plan.snapshot[0xD000:tx.START]), ranges[1]]
        args = ["verify_flash", "--flash_mode", "keep", "--flash_size", "keep", "--flash_freq", "keep"]
        facts = []
        for offset, raw in ranges:
            path = self._new_path("payloads", f"verify-{offset:08x}.bin")
            tx.write_new(path, raw)
            args += [hex(offset), str(path)]
            facts.append({"offset": offset, "bytes": len(raw), "reference_sha256": tx.sha(raw)})
        output = self._invoke("verify-outside-app", args)
        if output.count("-- verify OK (digest matched)") != len(ranges):
            raise ValueError("complete outside-App device digest verification missing")
        prefix = self._capture("prefix-readback", 0, tx.START)
        nvs_equal = prefix[0x9000:0xD000] == plan.snapshot[0x9000:0xD000]
        if not allow_runtime_nvs and not nvs_equal:
            raise ValueError("NVS differs before first boot")
        return {"method": "app_byte_readback_and_outside_app_device_md5",
                "app_bytes": len(app), "app_sha256": tx.sha(app),
                "app_byte_equal": True, "static_outside_app_equal": True, "nvs_equal": nvs_equal,
                "verified_ranges": facts}


def recover_saved(folder, plan):
    """Reserve a separate result before device access; keep earlier attempts."""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "install", "recover", "restart"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--from-run", help="reuse a restored full backup after fresh prefix/device-digest checks")
    args = parser.parse_args()
    if re.fullmatch(r"e3b-live-[0-9]{8}-[a-z0-9-]{1,32}", args.run) is None:
        raise ValueError("run label must begin e3b-live-YYYYMMDD-")
    folder = ROOT / "tmp" / args.run
    if folder.is_symlink() or folder.resolve().parent != (ROOT / "tmp").resolve():
        raise ValueError("run must stay inside repository tmp")
    if args.phase == "prepare":
        baseline = load_migrated_baseline()
        raw_app = SOURCE.read_bytes()
        if len(raw_app) != APP_SIZE or tx.sha(raw_app) != APP_SHA:
            raise ValueError("reviewed candidate changed")
        folder.mkdir()
        device = Device(folder, baseline["identity_sha256"])
        confirmed = False
        try:
            device.identity()
            confirmed = True
            if args.from_run:
                if re.fullmatch(r"e3b-live-[0-9]{8}-[a-z0-9-]{1,32}", args.from_run) is None:
                    raise ValueError("invalid source backup label")
                previous_folder = ROOT / "tmp" / args.from_run
                previous = tx.load(previous_folder)
                previous_state = tx.Journal(previous_folder, previous).load()
                if (previous_state is None or previous_state["phase"] != "restored"
                        or previous.metadata["identity_sha256"] != baseline["identity_sha256"]):
                    raise ValueError("restored same-body backup required")
                prefix = device._capture("current-prefix", 0, tx.START)
                raw = prefix + previous.snapshot[tx.START:]
                verification = device._new_path("payloads", "current-flash-reference.bin")
                tx.write_new(verification, raw)
                output = device._invoke("verify-reused-backup", ("verify_flash", "--flash_mode", "keep",
                        "--flash_size", "keep", "--flash_freq", "keep", "0x0", str(verification)))
                if output.count("-- verify OK (digest matched)") != 1:
                    raise ValueError("same-body full Flash digest verification missing")
                backup_source = {"method": "prior_complete_readback_fresh_prefix_and_full_device_md5", "run": args.from_run}
            else:
                print("COM4 identity matched; reading 32 MiB backup.", flush=True)
                raw = device.read_full()
                backup_source = {"method": "same_run_complete_byte_readback"}
            accepted_snapshot(raw, baseline)
            plan = tx.prepare(raw, raw_app, baseline["identity_sha256"], baseline["original_app"]["bytes"])
            tx.persist(folder, plan)
            tx.write_new(folder / "preflight.json", tx.canonical({"result": "passed", "plan_id": plan.id,
                         "baseline": baseline["report"], "flash_written": False,
                         "backup_source": backup_source,
                         "erase_start": tx.START, "erase_end": tx.START + len(plan.rollback),
                         "candidate_firmware": image_identity(raw_app)}))
            print(json.dumps({"phase": "prepared", "plan_id": plan.id, "erase_bytes": len(plan.rollback)}), flush=True)
        except BaseException:
            if confirmed:
                device.boot(False)
            raise
        return
    plan = (tx.load_recovery if args.phase == "recover" else tx.load)(folder)
    if args.phase == "recover":
        result = recover_saved(folder, plan)
        print(json.dumps({"result": result["result"], "hardware_acceptance": "pending"}), flush=True)
        return
    if args.phase != "recover" and (tx.sha(plan.image) != APP_SHA or len(plan.image) != APP_SIZE):
        raise ValueError("saved candidate is not E3b")
    device = Device(folder, plan.metadata["identity_sha256"], plan)
    journal = tx.Journal(folder, plan)
    if args.phase == "restart":
        if journal.load()["phase"] != "installed":
            raise ValueError("installed candidate required")
        try:
            previous = json.loads((folder / "install-result.json").read_bytes())["boot"]["hello"]
            boot = device.boot(True)
            if (boot["hello"]["boot_id"] == previous["boot_id"]
                    or boot["hello"]["device_id"] != previous["device_id"]):
                raise ValueError("restart identity did not change as expected")
            result = {"result": "restart_verified", "boot": boot,
                      "device_id_stable": True, "boot_id_changed": True}
        except BaseException:
            tx.recover(plan, device, journal)
            raise
        name = "restart-result.json"
    else:
        print("Starting App-only " + args.phase + "; logs saved locally.", flush=True)
        try:
            result = (tx.install if args.phase == "install" else tx.recover)(plan, device, journal)
        except BaseException:
            state = journal.load()
            if args.phase == "install" and (state is None or state["phase"] == "prepared"):
                device.boot(False)
            raise
        name = args.phase + "-result.json"
    tx.write_new(folder / name, tx.canonical(result))
    print(json.dumps({"result": result["result"], "hardware_acceptance": result.get("hardware_acceptance", "pending")}), flush=True)


if __name__ == "__main__":
    main()
