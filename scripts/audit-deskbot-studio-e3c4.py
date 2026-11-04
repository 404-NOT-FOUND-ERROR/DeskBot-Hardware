#!/usr/bin/env python3
"""Verify display-query scope and compare disabled bytes against frozen E3c-3."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_capacity import image_facts
from protocol.studio_resource_package import json_bytes, load_target

PUBLIC = ROOT / "artifacts/deskbot-studio-e3c4-20261105"
EXPECTED = {"main/Kconfig.projbuild", "main/boards/esp_vocat/studio_usb_session.h",
            "main/boards/esp_vocat/studio_usb_diagnostic.cc", "main/boards/esp_vocat/studio_display_readiness.h",
            "main/boards/esp_vocat/studio_runtime_state.h", "main/boards/esp_vocat/studio_runtime_state.cc",
            "main/display/emote_display.cc"}


def record(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def audit():
    before = json.loads((ROOT / "artifacts/deskbot-studio-e3c3-20261105/firmware-build-report.json").read_bytes())
    after = json.loads((PUBLIC / "firmware-build-report.json").read_bytes())
    old, new = before["source_sha256"], after["source_sha256"]
    changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    if set(changed) != EXPECTED or after["target"] != load_target():
        raise ValueError("source scope or target differs")
    for name, digest in new.items():
        if record(ROOT / "firmware/body" / name)["sha256"] != digest:
            raise ValueError("current firmware source changed: " + name)
    prior_on = next(row for row in before["builds"] if row["enabled"])
    for name, item in prior_on["artifacts"].items():
        if record(ROOT / "tmp/e3c3-state-20261105/on" / name) != item:
            raise ValueError("frozen E3c-3 artifact changed: " + name)
    reference = (ROOT / "tmp/e3c3-state-20261105/on/xiaozhi.bin").read_bytes()
    disabled = (ROOT / "tmp/e3c4-display-20261105/off/xiaozhi.bin").read_bytes()
    ref, off = image_facts(reference), image_facts(disabled)
    if len(reference) != len(disabled) or [(s["load_address"], s["bytes"]) for s in ref["segments"]] != [
            (s["load_address"], s["bytes"]) for s in off["segments"]]:
        raise ValueError("disabled size or segment layout changed")
    first = off["segments"][0]["file_offset"]
    if struct.unpack_from("<I", disabled, first)[0] != 0xABCD5432:
        raise ValueError("App description magic")
    elf_hash = first + 144
    allowed = set(range(elf_hash, elf_hash + 32)) | {off["checksum_offset"]} | set(range(len(disabled) - 32, len(disabled)))
    differences = [i for i, (a, b) in enumerate(zip(reference, disabled)) if a != b]
    if not set(differences) <= allowed:
        raise ValueError("disabled executable or data changed")
    if [row["enabled"] for row in after["builds"]] != [False, True]:
        raise ValueError("both configurations required")
    for row in after["builds"]:
        if (row["printf_portability"]["status"] != "pass" or row["display_query_reader_present"] != row["enabled"] or row["display_query_schema_present"] != row["enabled"]
                or not all(row["protected_matches"].values()) or not row["bootloader_configuration_equal"]
                or row["app_headroom_bytes"] < 1048576 or row["expression_controls"] is not False):
            raise ValueError("build checks differ")
        if row["display_store_bytes"] != (8 if row["enabled"] else 0):
            raise ValueError("display store bounds")
        if not (0 < row["snapshot_store_bytes"] <= 80):
            raise ValueError("snapshot store bounds")
        for name, item in row["artifacts"].items():
            if record(ROOT / row["build_directory"] / name) != item:
                raise ValueError("saved output changed: " + name)
    return {"schema": "deskbot.studio_display_query_audit/v1", "edition": "E3c-4", "result": "passed",
            "source_delta": changed, "source_files_before": len(old), "source_files_after": len(new),
            "disabled_comparison": {"reference": "E3c-3 query-on", "bytes": len(disabled),
                                    "differing_bytes": len(differences), "elf_hash_range": [elf_hash, elf_hash + 32],
                                    "only_elf_hash_and_image_checksums_changed": True, "executable_and_other_data_equal": True},
            "protected_artifacts_equal": True, "printf_portability_passed": True,
            "new_tasks": 0, "sleep_state": "unknown", "snapshot_transport_integrated": True, "display_query_integrated": True, "preview_available": False,
            "device_written": False, "hardware_accessed": False, "hardware_acceptance": "pending"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = json_bytes(audit())
    path = PUBLIC / "firmware-audit.json"
    if args.check:
        if path.read_bytes() != raw:
            raise ValueError("saved audit differs")
    else:
        path.write_bytes(raw)
    print("E3c-4 firmware audit passed; disabled executable and data equal E3c-3")
