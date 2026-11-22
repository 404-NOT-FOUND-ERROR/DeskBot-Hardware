#!/usr/bin/env python3
"""Automatically check upper resources, then restore the accepted catalog App."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import resource_read_specialty as task
from protocol import studio_app_deployment as tx

spec = importlib.util.spec_from_file_location("resource_specialty_transport", ROOT / "scripts/deploy-deskbot-studio-e3c.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class Device(base.Device):
    probe_log = None

    def read_reference(self, inputs):
        self.identity()
        prefix = self._capture("current-prefix", 0, tx.START)
        raw = prefix + inputs["snapshot"][tx.START:]
        task.check_current(raw, inputs["snapshot"])
        path = self._new_path("payloads", "current-reference.bin")
        tx.write_new(path, raw)
        out = self._invoke("verify-current-reference", ("verify_flash", "--flash_mode", "keep",
             "--flash_size", "keep", "--flash_freq", "keep", "0x0", str(path)))
        if out.count("-- verify OK (digest matched)") != 1:
            raise ValueError("complete current device digest verification required")
        self.identity()
        return raw

    def boot(self, candidate):
        seconds = 450 if candidate else 45
        log = self._serial("mapping-automatic" if candidate else "catalog-restored", seconds)
        if candidate:
            self.probe_log = log
            image = self.deployment_plan.image
        else:
            image = (self.deployment_plan.rollback[:task.catalog.APP_SIZE] if self.deployment_plan
                     else task.catalog.reviewed_candidate())
        return base.parent.inspect_boot(log, image, self._identity.mac, identity_required=True)


def folder_for(label, *, phase):
    if re.fullmatch(r"resource-read-[0-9]{8}-[a-z0-9-]{1,20}", label) is None:
        raise ValueError("run label must be resource-read-YYYYMMDD-<name>")
    folder = task.probe.safe_path(ROOT / "tmp" / label, ROOT / "tmp", exists=phase != "prepare")
    if folder.parent != (ROOT / "tmp").resolve():
        raise ValueError("resource-test run must be a direct child of tmp")
    if phase == "prepare" and folder.exists():
        raise FileExistsError("retain prior evidence and select a new run label")
    if phase != "prepare" and not folder.is_dir():
        raise ValueError("resource-test run directory is missing")
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "check", "execute", "recover"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--confirm-plan")
    args = parser.parse_args()
    folder = folder_for(args.run, phase=args.phase)
    if args.phase == "prepare":
        result = task.prepare(folder, Device)
    elif args.phase == "check":
        p = task.check(folder)
        result = dict(result="ready", plan_id=p.id)
    elif args.phase == "execute":
        if args.confirm_plan is None:
            parser.error("execute requires --confirm-plan")
        result = task.execute(folder, args.confirm_plan, Device)
    else:
        result = task.recover(folder, Device)
    print(json.dumps({k:v for k,v in result.items() if k in
          {"result", "plan_id", "restored", "erase_bytes", "interaction"}}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
