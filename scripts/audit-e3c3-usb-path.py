#!/usr/bin/env python3
"""Audit the retained E3c-3 USB path using local evidence only."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HISTORY = Path("tmp/e3c3-state-20261105")
PUBLIC = Path("artifacts/ui-menu-regression-20261106")
OUTPUT = PUBLIC / "e3c3-usb-path-audit.json"
USB_SOURCE = "main/boards/esp_vocat/studio_usb_diagnostic.cc"
EXPECTED_SOURCE = "71747d5efb4d69899b9866a241eacb6cc1147225f736e0b9242873525a69a231"
EXPECTED_APP = "1e659ae53fc944b7c73925e6ace152759ef657e0b99acde12d1ef9a6c59df1bd"
EXPECTED_ELF = "bf6b4a802abc44fcfb3ccc0a320972761d157e83a3432a584ffef7d50938930e"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def record(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def line_number(text, needle):
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


def audit(idf):
    report_path = Path("artifacts/deskbot-studio-e3c3-20261105/firmware-build-report.json")
    report = json.loads((ROOT / report_path).read_bytes())
    require(report["edition"] == "E3c-3"
            and report["result"] == "isolated_off_on_builds_verified", "historical build report")
    manifest = report["source_sha256"]
    require(len(manifest) == 1072 and manifest[USB_SOURCE] == EXPECTED_SOURCE, "historical source pin")
    source_root = (ROOT / HISTORY / "p").resolve()
    for name, digest in manifest.items():
        path = (source_root / name).resolve()
        require(path.is_relative_to(source_root), "source path escapes snapshot")
        raw = path.read_bytes()
        if name == "dependencies.lock":
            staged_path = str(source_root / "components/espressif__touch_sensor_fsm").encode("utf-8")
            require(raw.count(staged_path) == 1, "staged local touch component path")
            raw = raw.replace(staged_path, b"components/espressif__touch_sensor_fsm")
        require(hashlib.sha256(raw).hexdigest() == digest, "retained source changed: " + name)

    verified_artifacts = 0
    for build in report["builds"]:
        for name, expected in build["artifacts"].items():
            require(record(ROOT / build["build_directory"] / name) == expected,
                    "retained artifact changed: " + name)
            verified_artifacts += 1
    enabled = next(build for build in report["builds"] if build["enabled"])
    require(enabled["build_directory"] == (HISTORY / "on").as_posix(), "enabled build location")
    app_path = HISTORY / "on/xiaozhi.bin"
    app = (ROOT / app_path).read_bytes()
    require(len(app) == 4489152 and hashlib.sha256(app).hexdigest() == EXPECTED_APP, "App pin")
    require(app[32:36] == bytes.fromhex("3254cdab") and app[176:208].hex() == EXPECTED_ELF,
            "App description and ELF pin")
    config_path = HISTORY / "on/config/sdkconfig.json"
    config = json.loads((ROOT / config_path).read_bytes())
    require(config.get("ESP_CONSOLE_UART_DEFAULT") is True, "UART0 primary console")
    require(config.get("ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG") is True, "USB secondary console")
    require(not config.get("ESP_CONSOLE_USB_SERIAL_JTAG"), "USB primary console disabled")

    source = (source_root / USB_SOURCE).read_text(encoding="utf-8")
    require('open("/dev/usbserjtag", O_RDONLY)' in source, "historical open path")
    require('ESP_LOGW("DeskBotSession", "USB input unavailable");' in source,
            "historical failure log")
    require("errno" not in source and '"USB input ready' not in source, "historical diagnostics")
    map_path = HISTORY / "on/xiaozhi.map"
    link_map = (ROOT / map_path).read_text(encoding="utf-8")
    init = re.search(r" \.text\.__esp_system_init_fn_init_vfs_usj_sec\s+"
                     r"(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+[^\n]*usb_serial_jtag_vfs.c.obj", link_map)
    require(init is not None and int(init[1], 16) > 0, "linked secondary VFS initializer")

    vfs_rel = Path("components/esp_driver_usb_serial_jtag/src/usb_serial_jtag_vfs.c")
    console_rel = Path("components/esp_vfs_console/vfs_console.c")
    vfs = (idf / vfs_rel).read_text(encoding="utf-8")
    console = (idf / console_rel).read_text(encoding="utf-8")
    secondary = re.search(r"#if CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG\s+"
                          r"ESP_SYSTEM_INIT_FN\(init_vfs_usj_sec,.*?\n#endif", vfs, re.S)
    primary = re.search(r"#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG\s+"
                        r"ESP_SYSTEM_INIT_FN\(init_vfs_usj,.*?\n#endif", vfs, re.S)
    require(secondary is not None and 'esp_vfs_register_fs("/dev/secondary", &s_vfs_jtag' in secondary[0],
            "IDF secondary path registration")
    require(primary is not None and "usb_serial_jtag_vfs_register();" in primary[0]
            and 'esp_vfs_register_fs("/dev/usbserjtag", &s_vfs_jtag' in vfs, "IDF primary path")
    require(".read = &usb_serial_jtag_read," in vfs, "secondary VFS read operation")
    require(re.search(r"ssize_t console_read\([^}]+return read\(vfs_console.fd_primary, dst, size\);",
                      console) is not None, "stdin reads primary console")

    boot_path = Path("tmp/e3c-live-20261106-statequery2/captures/056-e3c-boot.log")
    boot = (ROOT / boot_path).read_text(encoding="utf-8")
    require(EXPECTED_ELF in boot and "DeskBotSession: USB input unavailable" in boot, "boot evidence")
    for unexpected in ("DeskBotSession: USB input ready", "DeskBotSession: USB input configuration unavailable",
                       "DeskBotSession: USB input owner changed"):
        require(unexpected not in boot, "unexpected USB startup evidence")
    receipt_path = PUBLIC / "e3c3-live-result.json"
    receipt = json.loads((ROOT / receipt_path).read_bytes())
    require(receipt["candidate"]["menu_test"] == "not_run", "E3c-3 menu test status")
    require(receipt["post_restore_hardware_acceptance"]["edition"] == "E3c-2"
            and receipt["post_restore_hardware_acceptance"]["result"] == "passed", "E3c-2 acceptance")

    return {
        "schema": "deskbot.usb_path_audit/v1", "date": "2026-11-06",
        "result": "historical_path_mismatch_confirmed", "edition": "E3c-3",
        "scope": {"hardware_accessed": False, "device_written": False, "firmware_modified": False},
        "retained_material": {"source_files_verified": len(manifest),
                              "source_files_byte_equal": len(manifest) - 1,
                              "dependency_lock_difference": "local_touch_component_path_only",
                              "artifacts_verified": verified_artifacts,
                              "app": record(ROOT / app_path), "app_elf_sha256": EXPECTED_ELF},
        "console": {"primary": "UART0", "secondary": "USB_SERIAL_JTAG",
                    "historical_open_path": "/dev/usbserjtag", "registered_input_path": "/dev/secondary",
                    "secondary_has_direct_read": True, "stdin_reads_primary_only": True,
                    "secondary_initializer_address": init[1]},
        "boot": {"usb_input": "unavailable", "errno_observed": None,
                 "log_line": line_number(boot, "DeskBotSession: USB input unavailable")},
        "acceptance": {"E3c-2": "idle_one_minute_tap_wake_left_swipe_return_passed",
                       "E3c-3": "menu_not_tested", "persistent_usb_receiver": "not_validated",
                       "ui_freeze_cause": "under_comparison"},
        "next_candidate": {"base": "retained_E3c-3", "change": "VFS_path_selection_and_startup_diagnostics",
                           "keep": ["console_configuration", "driver_ownership", "original_read_loop",
                                    "task_stack_and_priority", "application_and_display_owners"]},
        "evidence": {path.as_posix(): record(ROOT / path) for path in
                     (report_path, HISTORY / "p" / USB_SOURCE, config_path, map_path, boot_path, receipt_path)},
        "idf_evidence": {path.as_posix(): record(idf / path) for path in (vfs_rel, console_rel)},
        "idf_registration_lines": {"secondary": line_number(vfs, 'esp_vfs_register_fs("/dev/secondary"'),
                                   "primary": line_number(vfs, 'esp_vfs_register_fs("/dev/usbserjtag"')},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idf-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="compare with saved audit without writing")
    args = parser.parse_args()
    raw = (json.dumps(audit(args.idf_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if args.check:
        require((ROOT / OUTPUT).read_bytes() == raw, "saved USB path audit differs")
    else:
        (ROOT / OUTPUT).write_bytes(raw)
    print("E3c-3 retained sources and outputs verified; USB path mismatch confirmed; no hardware access")


if __name__ == "__main__":
    main()
