#!/usr/bin/env python3
"""Prepare exact E1.21 candidate and E1.18 rollback assets partition images.

This is an offline staging gate only. It pads audited compressed packages with
the erased-flash value to the exact assets partition size and verifies their
full-partition hashes against previously accepted deployment records. It never
opens a serial port or writes a device.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E121_REPORT = ROOT / "artifacts/expression-e1-21-package-player-20261026/report.json"
E122_REPORT = ROOT / "artifacts/expression-e1-22-candidate-preview-20261026/report.json"
SOURCE_DIR = ROOT / "tmp/expression-e1-21-package-player-20261026-run2"
CANDIDATE_PACKAGE = SOURCE_DIR / "candidate-e1-20-do-not-flash.bin"
ROLLBACK_PACKAGE = SOURCE_DIR / "rollback-e1-18-exact.bin"
OUTPUT_DIR = ROOT / "tmp/expression-e1-23-deployment-preflight-20261026"
PUBLIC_REPORT = ROOT / "artifacts/expression-e1-23-deployment-preflight-20261026/report.json"

PARTITION_OFFSET = 0x5EC000
PARTITION_BYTES = 0x5DC000
PARTITION_END = PARTITION_OFFSET + PARTITION_BYTES
ERASED_BYTE = 0xFF

EXPECTED = {
    "candidate_package": {
        "bytes": 5_538_349,
        "sha256": "5e342db35da13531836339a98c25d6e74ac6fdff446ad4e515fcf39f28b7e81f",
    },
    "candidate_partition": {
        "bytes": PARTITION_BYTES,
        "sha256": "6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205",
    },
    "rollback_package": {
        "bytes": 5_538_351,
        "sha256": "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1",
    },
    "rollback_partition": {
        "bytes": PARTITION_BYTES,
        "sha256": "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6",
    },
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def project_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def immutable_write(path: Path, raw: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"refusing to overwrite different evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def load_report(path: Path, edition: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("edition") != edition or value.get("audit_pass") is not True:
        raise ValueError(f"{edition} report is missing or did not pass")
    if value.get("deployment_eligible") is not False:
        raise ValueError(f"{edition} must remain explicitly non-deployable before authorization")
    safety = value.get("safety", {})
    for key in ("hardware_accessed", "serial_accessed", "device_written", "flash_authorized"):
        if safety.get(key) is not False:
            raise ValueError(f"{edition} report has an unsafe {key} value")
    return value


def full_partition(package_path: Path, expected: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    if not package_path.is_file():
        raise FileNotFoundError(f"retained package is missing: {package_path}")
    package = package_path.read_bytes()
    package_sha = sha(package)
    if len(package) != expected["bytes"] or package_sha != expected["sha256"]:
        raise ValueError(f"package size/hash mismatch: {package_path.name}")
    if len(package) > PARTITION_BYTES:
        raise ValueError(f"package exceeds the assets partition: {package_path.name}")
    image = package + bytes([ERASED_BYTE]) * (PARTITION_BYTES - len(package))
    return image, {"package_bytes": len(package), "package_sha256": package_sha,
                   "partition_bytes": len(image), "partition_sha256": sha(image)}


def prepare() -> dict[str, Any]:
    e121 = load_report(E121_REPORT, "E1.21")
    e122 = load_report(E122_REPORT, "E1.22")
    candidate_info = e121.get("candidate_package", {})
    rollback_info = e121.get("current_e118_package", {})
    if candidate_info.get("bytes") != EXPECTED["candidate_package"]["bytes"] or candidate_info.get("sha256") != EXPECTED["candidate_package"]["sha256"]:
        raise ValueError("E1.21 candidate package anchor changed")
    if rollback_info.get("bytes") != EXPECTED["rollback_package"]["bytes"] or rollback_info.get("sha256") != EXPECTED["rollback_package"]["sha256"]:
        raise ValueError("E1.21 E1.18 rollback package anchor changed")
    mapping = e121.get("mapping", {})
    if mapping.get("semantic") != "surprised" or mapping.get("before") != "shocked.eaf" or mapping.get("after") != "Happy.eaf":
        raise ValueError("E1.21 mapping is not the expected one-semantic change")
    comparison = e122.get("comparison", {})
    if comparison.get("semantic") != "surprised" or comparison.get("before_resource") != "shocked.eaf" or comparison.get("candidate_resource") != "Happy.eaf":
        raise ValueError("E1.22 visual comparison does not match the candidate package")

    candidate, candidate_digest = full_partition(CANDIDATE_PACKAGE, EXPECTED["candidate_package"])
    rollback, rollback_digest = full_partition(ROLLBACK_PACKAGE, EXPECTED["rollback_package"])
    if candidate_digest["partition_sha256"] != EXPECTED["candidate_partition"]["sha256"]:
        raise ValueError("padded E1.13 candidate does not match the accepted full-partition hash")
    if rollback_digest["partition_sha256"] != EXPECTED["rollback_partition"]["sha256"]:
        raise ValueError("padded E1.18 rollback does not match the deployed full-partition hash")

    candidate_path = OUTPUT_DIR / "candidate-assets-6mb-do-not-flash.bin"
    rollback_path = OUTPUT_DIR / "rollback-e1-18-assets-6mb.bin"
    immutable_write(candidate_path, candidate)
    immutable_write(rollback_path, rollback)
    report = {
        "schema": "deskbot.expression_e1_23_deployment_preflight/v1",
        "edition": "E1.23",
        "date": "2026-10-26",
        "result": "offline_deployment_artifacts_prepared",
        "audit_pass": True,
        "deployment_eligible": False,
        "flash_authorized": False,
        "hardware_accessed": False,
        "serial_accessed": False,
        "device_written": False,
        "mapping_review": "E1.22 page is available; no separate semantic acceptance is recorded by this report",
        "mapping": {"semantic": "surprised", "before": "shocked.eaf", "after": "Happy.eaf",
                    "action_id": "look_around", "layout_id": "eye_anim", "fps": 20, "loop": True},
        "partition": {"name": "assets", "offset": PARTITION_OFFSET, "end_exclusive": PARTITION_END,
                      "length_bytes": PARTITION_BYTES, "padding_byte": "0xFF"},
        "candidate": {**candidate_digest, "path": project_relative(candidate_path),
                      "matches_accepted_e1_13_full_partition": True},
        "rollback": {**rollback_digest, "path": project_relative(rollback_path),
                     "matches_deployed_e1_18_full_partition": True},
        "anchors": {
            "e1_21_report_sha256": file_sha(E121_REPORT),
            "e1_22_report_sha256": file_sha(E122_REPORT),
            "e1_13_full_partition_sha256": EXPECTED["candidate_partition"]["sha256"],
            "e1_18_full_partition_sha256": EXPECTED["rollback_partition"]["sha256"],
        },
        "pending_gates": [
            "user explicitly accepts the E1.22 expression choice and authorizes assets-only deployment",
            "identify the connected ESP32-S3 by read-only chip/MAC on the confirmed body port",
            "read and compare the current App, partition table, and full assets partition before writing",
            "write only the 0x5DC000-byte assets partition at 0x5EC000; do not erase flash or write NVS/C61",
            "read back the full assets partition and verify App/partition table remain unchanged",
            "boot-log check and user regression: display, menu, dialogue, touch, sleep/wake, DOA, expression",
        ],
        "safety": {"firmware_built": False, "firmware_changed": False, "c61_changed": False,
                   "nvs_changed": False, "device_written": False, "flash_authorized": False},
    }
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    immutable_write(PUBLIC_REPORT, raw)
    return report


def main() -> int:
    try:
        report = prepare()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"E1.23 preflight failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"edition": report["edition"], "result": report["result"],
                      "candidate_partition_sha256": report["candidate"]["partition_sha256"],
                      "rollback_partition_sha256": report["rollback"]["partition_sha256"],
                      "deployment_eligible": report["deployment_eligible"],
                      "flash_authorized": report["flash_authorized"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
