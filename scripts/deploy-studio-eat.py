#!/usr/bin/env python3
"""Prepare/install/recover the eating App through the existing COM4 transaction."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_eat_deployment as reviewed
from protocol.storage_upper_migration_esptool import ASSET_ERRORS

spec = importlib.util.spec_from_file_location("eat_transaction", ROOT / "scripts/deploy-deskbot-studio-e3c3a.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.reviewed = reviewed
base.entry.reviewed = reviewed
base.entry.ROLLBACK_TARGET = reviewed.ROLLBACK_TARGET


class EatingDevice(base.MenuTestDevice):
    def read_reference(self, reference):
        # Refresh runtime settings, then verify every byte of the retained image
        # by a complete device digest before saving this run's full snapshot.
        return super().read_reference(reference)

    def boot(self, candidate):
        image = (self.deployment_plan.image if candidate else
                 self.deployment_plan.snapshot[0x10000:0x10000 + reviewed.ORIGINAL_SIZE]
                 if self.deployment_plan is not None else reviewed.baseline.reviewed_candidate())
        log = self._serial("eat-boot" if candidate else "restored-boot", 45)
        if (log.count("Calling app_main()") != 1 or "USB input ready: /dev/secondary" not in log
                or any(marker in log for marker in ASSET_ERRORS + ("USB input unavailable",
                    "USB input configuration unavailable", "USB input owner changed"))):
            raise ValueError("eating_App_boot_incomplete")
        return base.entry.parent.inspect_boot(log, image, self._identity.mac, identity_required=True)


base.MenuTestDevice = EatingDevice
_prepare_live = base.prepare_live


def prepare_live(folder, source):
    return _prepare_live(folder, source)


base.prepare_live = prepare_live


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "check", "prepare-live", "install", "recover", "restart"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--from-run")
    args = parser.parse_args(argv)
    if (args.phase == "prepare-live") != (args.from_run is not None):
        parser.error("prepare-live requires --from-run")
    folder = base.entry.folder_for(args.run)
    source = base.entry.folder_for(args.from_run) if args.from_run else None
    result = base.run(args.phase, folder, source=source)
    print(json.dumps({k: v for k, v in result.items()
        if k in {"phase", "result", "edition", "plan_id", "erase_bytes", "hardware_accessed"}}), flush=True)


if __name__ == "__main__":
    main()
