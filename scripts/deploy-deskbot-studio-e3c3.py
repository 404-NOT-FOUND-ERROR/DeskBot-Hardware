#!/usr/bin/env python3
"""Prepare, install or recover the reviewed E3c-3 COM4 App."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_e3c3_deployment as reviewed

spec = importlib.util.spec_from_file_location(
    "e3c3_deploy", ROOT / "scripts/deploy-deskbot-studio-e3c.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry.reviewed = reviewed
entry.ROLLBACK_TARGET = "E3c-2"


class MenuComparisonDevice(entry.Device):
    """Check the retained E3c-3 image with its historical USB task exit."""

    def boot(self, candidate):
        if not candidate:
            return super().boot(False)
        image = reviewed.reviewed_candidate(self.deployment_plan.image)
        log = self._serial("e3c-boot", 45)
        unavailable = re.findall(
            r"(?:^|\t)W \([0-9]+\) DeskBotSession: USB input unavailable\r?$",
            log, flags=re.MULTILINE)
        if (len(unavailable) != 1 or any(marker in log for marker in
                ("USB input ready", "USB input configuration unavailable", "USB input owner changed"))):
            raise ValueError("retained E3c-3 historical USB task exit required")
        result = entry.parent.inspect_boot(log, image, self._identity.mac, identity_required=True)
        return dict(result, validation_scope="historical_menu_comparison",
                    usb_input="historical_path_unavailable", usb_query_validation="pending")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "install", "restart", "recover"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--reuse-installed-reference", action="store_true",
                        help="refresh the prefix and verify the retained E3c-2 backup against COM4")
    parser.add_argument("--menu-comparison", action="store_true",
                        help="use the pinned historical E3c-3 image for menu comparison; USB queries remain pending")
    args = parser.parse_args(argv)
    if args.phase in {"install", "restart"} and not args.menu_comparison:
        parser.error("install and restart require --menu-comparison for this historical image")
    default_device = entry.Device
    try:
        if args.menu_comparison:
            entry.Device = MenuComparisonDevice
        result = entry.run(args.phase, entry.folder_for(args.run),
                           reuse_reference=args.reuse_installed_reference)
    finally:
        entry.Device = default_device
    print(json.dumps({key: value for key, value in result.items()
                      if key in {"phase", "result", "plan_id", "erase_bytes", "hardware_acceptance"}},
                     sort_keys=True), flush=True)
    return result


if __name__ == "__main__":
    main()
