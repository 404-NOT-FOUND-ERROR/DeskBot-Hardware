#!/usr/bin/env python3
"""Independently audit the single-source USB VFS correction and its builds."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_capacity import image_facts
from protocol.studio_resource_package import json_bytes, load_target

PUBLIC = ROOT / "artifacts/deskbot-studio-e3c7-20261105"
SOURCE = "main/boards/esp_vocat/studio_usb_diagnostic.cc"


def record(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def audit():
    previous_path = ROOT / "artifacts/deskbot-studio-e3c5-20261105/firmware-build-report.json"
    before = json.loads(previous_path.read_bytes())
    after = json.loads((PUBLIC / "firmware-build-report.json").read_bytes())
    old, new = before["source_sha256"], after["source_sha256"]
    if (after["edition"] != "E3c-7" or after["prior_report"] != record(previous_path)
            or after["target"] != load_target()
            or {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)} != {SOURCE}):
        raise ValueError("single USB task source correction required")
    for name, digest in new.items():
        if record(ROOT / "firmware/body" / name)["sha256"] != digest:
            raise ValueError("current source differs: " + name)
    old_path = ROOT / "tmp/e3c5-expression-20261105/p" / SOURCE
    if record(old_path)["sha256"] != old[SOURCE]:
        raise ValueError("retained pre-correction source differs")
    old_source = old_path.read_text(encoding="utf-8")
    expected = old_source.replace("#include <driver/usb_serial_jtag.h>", "#include <cerrno>\n#include <driver/usb_serial_jtag.h>")
    expected = expected.replace("Endpoint endpoint;\n", '''Endpoint endpoint;
#if CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG
constexpr char kUsbDevice[] = "/dev/secondary";
#elif CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
constexpr char kUsbDevice[] = "/dev/usbserjtag";
#else
#error "Studio USB input requires a USB Serial/JTAG console"
#endif
''')
    expected = expected.replace('open("/dev/usbserjtag", O_RDONLY)', 'open(kUsbDevice, O_RDONLY)')
    expected = expected.replace('"USB input unavailable");', '"USB input unavailable (%d)", errno);')
    expected = expected.replace('    bool connected = false;', '    ESP_LOGI("DeskBotSession", "USB input ready: %s", kUsbDevice);\n    bool connected = false;')
    if (ROOT / "firmware/body" / SOURCE).read_text(encoding="utf-8") != expected:
        raise ValueError("unexpected USB task code changes")
    if [row["enabled"] for row in after["builds"]] != [False, True]:
        raise ValueError("off/on builds required")
    builds = []
    for row in after["builds"]:
        prior = next(item for item in before["builds"] if item["enabled"] == row["enabled"])
        folder, original = ROOT / row["build_directory"], ROOT / prior["build_directory"]
        for name, item in row["artifacts"].items():
            if record(folder / name) != item:
                raise ValueError("retained output differs: " + name)
        for name in ("bootloader/bootloader.bin", "partition_table/partition-table.bin", "srmodels/srmodels.bin", "ota_data_initial.bin"):
            if record(folder / name) != record(original / name):
                raise ValueError("protected output changed: " + name)
        if json.loads((folder / "config/sdkconfig.json").read_bytes()) != json.loads((original / "config/sdkconfig.json").read_bytes()):
            raise ValueError("configuration differs from prior build")
        if row["usb_input_vfs"] != "/dev/secondary":
            raise ValueError("unexpected transport configuration")
        if (row["printf_portability"]["status"] != "pass" or not row["console_topology_unchanged"]
                or not all(row["protected_matches"].values()) or row["app_headroom_bytes"] < 1048576):
            raise ValueError("build checks differ")
        if image_facts((folder / "xiaozhi.bin").read_bytes())["configured_flash_bytes"] != 33554432:
            raise ValueError("App capacity differs")
        builds.append({"enabled": row["enabled"], "app": row["artifacts"]["xiaozhi.bin"],
                       "headroom_bytes": row["app_headroom_bytes"]})
    return {"schema": "deskbot.studio_usb_transport_audit/v1", "edition": "E3c-7", "result": "passed",
            "source_delta": [SOURCE], "usb_input_vfs": "/dev/secondary", "configurations_unchanged": True,
            "protected_outputs_equal": True, "original_application_ui_player_unchanged": True,
            "new_tasks": 0, "builds": builds, "hardware_accessed": False,
            "screen_playback_confirmed": False, "hardware_acceptance": "pending"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = PUBLIC / "firmware-audit.json"
    raw = json_bytes(audit())
    if args.check:
        if path.read_bytes() != raw:
            raise ValueError("saved audit differs")
    else:
        path.write_bytes(raw)
    print("E3c-7 USB VFS, exact source scope, configurations and protected output audit passed")
