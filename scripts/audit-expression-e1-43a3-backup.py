#!/usr/bin/env python3
"""Audit retained E1.43a-3 reads and rollback files; never opens a device."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_addressing import bounded_image
from protocol.storage_capacity import image_facts
from protocol.storage_live_backup import require_independent_verify, verify_capture_files
from protocol.storage_upper_pattern import (
    PAGE_BYTES, PAGE_START, PHYSICAL_FLASH_BYTES, PSRAM_BYTES, REFERENCE_SHA256,
    SECTOR_BYTES, build_pattern, parse_legacy_table, validate_manifest, validate_target_context,
)

WORK = ROOT / "tmp/e143a-single-page-20261102"
RETAINED_SOURCE = ROOT / "tmp/e143a-probe-20261102/p"
PUBLIC = ROOT / "artifacts/expression-upper-backup-e1-43a3-20261102/report.json"
APP_START, APP_BYTES = 0x10000, 0x5DC000
APP_SHA = "14bc81889621bc8cd9f9c34077ade062231a964fb0e9efcdbbccdfdf296e2f1d"
BOOT_SHA = "1b21ce721cae5f456511e4611d0a2fb61b9809108f39eb488d4eea314c0d0485"
ASSETS_SHA = "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6"
MODEL_SHA = "37a572cda25d8381b733f8ce48c1532488c87cf3359e1c78b13b1262f58eed39"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_identity(flash: str, security: str, mac: str) -> None:
    require(isinstance(mac, str) and re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac) is not None,
            "invalid private capture identity")
    for label, log in (("Flash", flash), ("security", security)):
        require(re.findall(r"MAC: ([0-9a-f:]{17})", log) == [mac], label + " identity is absent or ambiguous")
        require("esptool.py v4.12.0" in log and "Serial port COM4" in log
                and "Chip is ESP32-S3 (QFN56) (revision v0.2)" in log,
                label + " tool, port or chip differs")
    for token in ("Embedded PSRAM 16MB", "Manufacturer: c2", "Device: 8039",
                  "Detected flash size: 32MB", "Flash type set in eFuse: octal (8 data lines)",
                  "Flash voltage set by eFuse to 3.3V"):
        require(flash.count(token) == 1, "Flash identity field differs: " + token)
    for token in ("Secure Boot: Disabled", "Flash Encryption: Disabled"):
        require(security.count(token) == 1, "security field differs: " + token)
    require("Secure Boot: Enabled" not in security and "Flash Encryption: Enabled" not in security,
            "conflicting security state")


def rollback_span(old_bytes: int, new_bytes: int) -> int:
    require(all(type(value) is int and 0 < value <= APP_BYTES for value in (old_bytes, new_bytes)),
            "invalid App payload size")
    return (max(old_bytes, new_bytes) + SECTOR_BYTES - 1) // SECTOR_BYTES * SECTOR_BYTES


def verify_slice(path: Path, raw: bytes, start: int, size: int) -> dict:
    require(all(type(value) is int for value in (start, size))
            and start >= 0 and size > 0 and start % SECTOR_BYTES == size % SECTOR_BYTES == 0
            and start + size <= len(raw), "invalid rollback geometry")
    require(not path.is_symlink(), "rollback must be a regular retained file")
    data = path.read_bytes()
    require(len(data) == size and data == raw[start:start + size], "rollback differs from fresh capture: " + path.name)
    return {"offset": start, "bytes": size, "end_exclusive": start + size, "sha256": sha(data),
            "verified_against_fresh_capture": True}


def verified_file(path: Path, metadata: dict) -> bytes:
    data = path.read_bytes()
    require(type(metadata.get("bytes")) is int and len(data) == metadata["bytes"]
            and sha(data) == metadata.get("sha256"), "retained file changed: " + path.name)
    return data


def verify_retained_sources(build: dict) -> None:
    """Bind the historical build to its archived source, not today's candidate."""
    inputs = build["source_sha256"]
    require(isinstance(inputs, dict) and bool(inputs), "historical source inventory is missing")
    manifest = "".join(digest + "  " + name + "\n" for name, digest in inputs.items()).encode("utf-8")
    require(sha(manifest) == build["source_manifest_sha256"], "historical source manifest changed")
    source = RETAINED_SOURCE.resolve()
    require(RETAINED_SOURCE.is_dir() and not RETAINED_SOURCE.is_symlink(), "retained source directory is missing or unsafe")
    for name, digest in inputs.items():
        path = RETAINED_SOURCE / name
        require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                and path.resolve().is_relative_to(source) and not path.is_symlink() and path.is_file(),
                "retained source is missing or unsafe: " + name)
        raw = path.read_bytes()
        if name == "dependencies.lock":
            # Staging rebases exactly one local component path. Restore that
            # field in memory, then require the original byte-level digest.
            matches = re.findall(rb"(?m)^      path: ([^\r\n]+)(?=\r?$)", raw)
            require(len(matches) == 1
                    and Path(matches[0].decode("utf-8")).resolve()
                    == source / "components/espressif__touch_sensor_fsm",
                    "retained lock has an unexpected local component path")
            raw = re.sub(rb"(?m)^      path: [^\r\n]+(?=\r?$)",
                         b"      path: components/espressif__touch_sensor_fsm", raw)
        require(sha(raw) == digest, "historical build source changed: " + name)


def make_report() -> dict:
    capture = WORK / "capture"
    index_path = capture / "capture-index.json"
    index = load_json(index_path)
    require(index.get("port") == "COM4", "capture is not the intended body port")
    previous = ROOT / "tmp/e1423-serial-probe-20261102"
    previous_audit = load_json(previous / "prewrite-audit.json")
    require(index.get("mac") == previous_audit["serial"].lower(),
            "fresh capture is not the previously confirmed body")
    raw = verify_capture_files(capture, index)
    require(len(raw) == PHYSICAL_FLASH_BYTES, "fresh capture is not 32 MiB")
    flash_path, security_path = WORK / "flash-id.log", WORK / "security-info.log"
    validate_identity(flash_path.read_text(encoding="utf-8"), security_path.read_text(encoding="utf-8"), index["mac"])
    verify_path = WORK / "verify-full-keep-absolute.log"
    require_independent_verify(verify_path.read_text(encoding="utf-8"), index["mac"], len(raw),
                               str((capture / index["full_image"]["path"]).resolve()))
    rows = parse_legacy_table(raw[0x8000:0x9000])
    boot, app = bounded_image(raw, 0, 0x8000), bounded_image(raw, APP_START, APP_BYTES)
    context = {"port": "COM4", "chip": "esp32s3", "physical_flash_bytes": PHYSICAL_FLASH_BYTES,
               "psram_bytes": PSRAM_BYTES, "bootloader_header_bytes": image_facts(boot)["configured_flash_bytes"],
               "driver_flash_bytes": PHYSICAL_FLASH_BYTES}
    # The intended runtime capacity comes from E1.42.3; this audit does not execute a CPU probe.
    validate_target_context(context, rows)
    require(image_facts(app)["configured_flash_bytes"] == PHYSICAL_FLASH_BYTES,
            "current App capacity header differs")
    require(sha(app) == APP_SHA and sha(boot) == BOOT_SHA, "accepted E1.42 App or E1.40 bootloader differs")

    baseline_path = ROOT / "artifacts/expression-capacity-read-mmap-e1-42-20261031/build-report.json"
    baseline = load_json(baseline_path)
    require(baseline["artifacts"]["xiaozhi.bin"]["sha256"] == APP_SHA, "baseline report identity changed")
    reference = verified_file(ROOT / baseline["reference_readback"]["path"], baseline["reference_readback"])
    require(sha(reference) == REFERENCE_SHA256, "reviewed historical reference changed")
    previous_raw = verified_file(previous / "prewrite-low-flash.bin",
                                 {"bytes": previous_audit["bytes"], "sha256": previous_audit["prewrite_sha256"]})
    require(raw[:0x9000] == previous_raw[:0x9000],
            "immutable lower prefix differs from the restored E1.42 snapshot")
    for label, start, end, digest in (("assets", 0x5EC000, 0xBC8000, ASSETS_SHA),
                                      ("model", 0xBC8000, 0xC90000, MODEL_SHA)):
        require(sha(raw[start:end]) == digest and raw[start:end] == reference[start:end],
                "accepted live " + label + " region differs")
    require(raw[PAGE_START:] == b"\xff" * (PHYSICAL_FLASH_BYTES - PAGE_START),
            "upper half contains unreviewed data")

    build_path = ROOT / "artifacts/expression-upper-pattern-probe-e1-43a2-20261102/final-build-report.json"
    build = load_json(build_path)
    require(build.get("result") == "isolated_default_off_and_diagnostic_builds_passed"
            and build.get("run_label") == "final", "final diagnostic build gate missing")
    verify_retained_sources(build)
    for round_info in build["rounds"].values():
        for relative, metadata in round_info["artifacts"].items():
            verified_file(ROOT / round_info["evidence_directory"] / relative, metadata)
    candidate_path = ROOT / build["rounds"]["on"]["evidence_directory"] / "xiaozhi.bin"
    candidate = candidate_path.read_bytes()
    require(image_facts(candidate)["configured_flash_bytes"] == PHYSICAL_FLASH_BYTES,
            "diagnostic candidate has wrong capacity")
    pattern = (ROOT / "tmp/e143a-upper-pattern-20261102/initial/upper-pattern-64k.bin").read_bytes()
    validate_manifest(load_json(ROOT / "artifacts/expression-upper-pattern-e1-43a-20261102/pattern-manifest.json"), pattern)
    require(pattern == build_pattern(), "test pattern changed")

    span = rollback_span(len(app), len(candidate))
    rollback = []
    for name, start, size in (("bootloader_reserved", 0, 0x8000),
                              ("app_original_sectors", APP_START, span),
                              ("upper_original_page", PAGE_START, PAGE_BYTES)):
        path = WORK / "rollback" / (name + ".bin")
        row = {"id": name, "private_file": path.relative_to(ROOT).as_posix(), **verify_slice(path, raw, start, size)}
        rollback.append(row)
    protected = []
    for name, start, end in (("bootloader_reserved", 0, 0x8000), ("partition_table", 0x8000, 0x9000),
                             ("nvs", 0x9000, 0xD000), ("otadata", 0xD000, 0xF000),
                             ("phy_init", 0xF000, APP_START), ("assets", 0x5EC000, 0xBC8000),
                             ("model", 0xBC8000, 0xC90000), ("upper_half", PAGE_START, PHYSICAL_FLASH_BYTES)):
        part = raw[start:end]
        protected.append({"id": name, "offset": start, "bytes": len(part), "end_exclusive": end,
                          "sha256": sha(part), "all_ff": part == b"\xff" * len(part)})

    boot_path = WORK / "normal-boot.log"
    boot_log = boot_path.read_text(encoding="utf-8")
    tokens = {"application_started": "Calling app_main()", "lcd_initialized": "LCD panel create success",
              "touch_initialized": "Touch sensor initialized successfully", "psram_16m": "Found 16MB PSRAM device",
              "opi_driver": "detected chip: mxic (opi)", "opi_str": "flash io: opi_str"}
    startup = {key: value in boot_log for key, value in tokens.items()}
    startup["no_panic_in_capture"] = not any(value in boot_log for value in ("Guru Meditation", "assert failed", "abort() was called"))
    require(all(startup.values()), "normal boot observations failed")
    startup.update(observed_seconds=45, wifi_connected="STATE: idle" in boot_log,
                   physical_interaction_retested=False, missing_icon_warnings_observed='Not found:"icon_' in boot_log)
    return {
        "schema": "deskbot.upper_pattern.fresh_backup.v1", "edition": "E1.43a-3-preflight", "date": "2026-11-02",
        "result": "fresh_backup_and_exact_rollback_passed_single_page_test_pending",
        "device": {"port": "COM4", "chip": "esp32s3", "revision": "v0.2", "private_identity_consistent": True,
                   "physical_flash_bytes": PHYSICAL_FLASH_BYTES, "psram_bytes": PSRAM_BYTES,
                   "secure_boot": False, "flash_encryption": False},
        "capture": {"bytes": len(raw), "sha256": sha(raw), "chunk_count": len(index["chunks"]),
                    "read_attempt_count": len(index["attempts"]), "all_chunks_validated": True,
                    "independent_unmodified_image_verify_passed": True,
                    "capture_completed_utc": index["completed_utc"],
                    "raw_files_private": True},
        "evidence_sha256": {"capture_index": sha(index_path.read_bytes()), "flash_id": sha(flash_path.read_bytes()),
                            "security_info": sha(security_path.read_bytes()), "independent_verify": sha(verify_path.read_bytes()),
                            "normal_boot": sha(boot_path.read_bytes()), "build_report": sha(build_path.read_bytes())},
        "current_images": {"app": {"bytes": len(app), "sha256": sha(app)},
                           "bootloader": {"bytes": len(boot), "sha256": sha(boot)}},
        "partitions": rows, "protected_ranges": protected,
        "candidate": {"app_sha256": sha(candidate), "app_payload_bytes": len(candidate),
                      "app_erase_offset": APP_START, "app_erase_bytes": span, "app_erase_end_exclusive": APP_START + span,
                      "pattern_offset": PAGE_START, "pattern_bytes": PAGE_BYTES, "pattern_sha256": sha(pattern),
                      "source_files_verified": len(build["source_sha256"]),
                      "archived_build_files_verified": sum(len(r["artifacts"]) for r in build["rounds"].values())},
        "rollback": {"source_capture_sha256": sha(raw), "exact_files_verified": True, "ranges": rollback},
        "startup": startup,
        "safety": {"serial_accessed": True, "device_reset": True, "device_written": False,
                   "nvs_targeted": False, "c61_accessed": False, "partition_table_changed": False,
                   "deployment_script_generated": False, "flash_authorized": False, "deployment_eligible": False,
                   "known_pattern_cpu_verified": False, "hardware_accepted": False},
        "procedure_observation": "The initial verify used 32MB and updated an in-memory image digest. It was excluded from the gate; keep verification passed. No Flash or backup-file write resulted.",
        "next_gate": "bounded pattern/App experiment with an audited automatic exact restore; refresh identity and protected bytes immediately before any write",
        "claim_limits": "This is a read-only backup and rollback-file audit. It does not authorize a write, prove CPU access to known upper-half data, continuous 8 MiB mmap, or post-experiment physical regression. Normal firmware may update settings after boot; pre-write bytes must be refreshed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the retained report without writing it")
    args = parser.parse_args()
    report = make_report()
    data = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.check:
        require(PUBLIC.read_bytes() == data, "retained fresh-backup report is stale")
    else:
        require(not PUBLIC.exists() or PUBLIC.read_bytes() == data, "refuse to overwrite changed audit evidence")
        PUBLIC.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC.write_bytes(data)
    print(json.dumps({"result": "checked" if args.check else report["result"],
                      "chunks": report["capture"]["chunk_count"], "rollback_bytes": [r["bytes"] for r in report["rollback"]["ranges"]],
                      "device_written": False, "flash_authorized": False}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print("E1.43a-3 backup audit failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
