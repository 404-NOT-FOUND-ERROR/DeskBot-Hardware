#!/usr/bin/env python3
"""Verify E3c-1 source scope, outputs and disabled executable against E3b."""
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

PUBLIC = ROOT / "artifacts/deskbot-studio-e3c-20261104"
EXPECTED = {"main/Kconfig.projbuild", "main/boards/esp_vocat/studio_device_link.cc",
            "main/boards/esp_vocat/studio_request_framer.h", "main/boards/esp_vocat/studio_usb_session.h",
            "main/boards/esp_vocat/studio_usb_diagnostic.h", "main/boards/esp_vocat/studio_usb_diagnostic.cc"}


def record(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def audit():
    before = json.loads((ROOT / "artifacts/deskbot-studio-e3b-20261104/firmware-build-report.json").read_bytes())
    after = json.loads((PUBLIC / "firmware-build-report.json").read_bytes())
    old, new = before["source_sha256"], after["source_sha256"]
    changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    if set(changed) != EXPECTED or after["target"] != load_target():
        raise ValueError("source scope or target changed")
    for name, digest in new.items():
        if record(ROOT / "firmware/body" / name)["sha256"] != digest:
            raise ValueError("current firmware source changed: " + name)
    prior_on = next(row for row in before["builds"] if row["enabled"])
    for name, item in prior_on["artifacts"].items():
        if record(ROOT / "tmp/e3b-identity-20261104/on" / name) != item:
            raise ValueError("frozen E3b artifact changed: " + name)
    reference = (ROOT / "tmp/e3b-identity-20261104/on/xiaozhi.bin").read_bytes()
    disabled = (ROOT / "tmp/e3c-usb-20261104/off/xiaozhi.bin").read_bytes()
    ref_facts, off_facts = image_facts(reference), image_facts(disabled)
    if len(reference) != len(disabled) or [(s["load_address"], s["bytes"]) for s in ref_facts["segments"]] != [
            (s["load_address"], s["bytes"]) for s in off_facts["segments"]]:
        raise ValueError("disabled size or segment layout changed")
    first = off_facts["segments"][0]["file_offset"]
    if struct.unpack_from("<I", disabled, first)[0] != 0xABCD5432:
        raise ValueError("App description magic")
    elf_hash = first + 144
    allowed = set(range(elf_hash, elf_hash + 32)) | {off_facts["checksum_offset"]} | set(range(len(disabled) - 32, len(disabled)))
    differences = [i for i, (a, b) in enumerate(zip(reference, disabled)) if a != b]
    if not set(differences) <= allowed:
        raise ValueError("disabled executable or data changed")
    if [row["enabled"] for row in after["builds"]] != [False, True]:
        raise ValueError("both build configurations required")
    for row in after["builds"]:
        if (row["printf_portability"]["status"] != "pass" or not row["identity_symbols_present"]
                or row["session_symbols_present"] != row["enabled"] or not all(row["protected_matches"].values())
                or row["app_headroom_bytes"] < 1048576 or row["expression_controls"] is not False):
            raise ValueError("build checks differ")
        for name, item in row["artifacts"].items():
            if record(ROOT / row["build_directory"] / name) != item:
                raise ValueError("saved output changed: " + name)
    return {"schema": "deskbot.studio_session_audit/v1", "edition": "E3c-1", "result": "passed",
            "source_delta": changed, "source_files_before": len(old), "source_files_after": len(new),
            "disabled_comparison": {"reference": "E3b revision 2 identity-on", "bytes": len(disabled),
                                    "differing_bytes": len(differences), "elf_hash_range": [elf_hash, elf_hash + 32],
                                    "only_elf_hash_and_image_checksums_changed": True, "executable_and_other_data_equal": True},
            "protected_artifacts_equal": True, "printf_portability_passed": True,
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
    print("E3c-1 firmware audit passed: six source changes; disabled executable and data equal E3b")
