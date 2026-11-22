#!/usr/bin/env python3
"""Offline audit of a prepared E1.43b E3c-3a device snapshot and rollback plan."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol import storage_continuous_mmap_e3c3a_baseline as baseline
from protocol.storage_continuous_mmap_experiment import (
    APP_START, FLASH_BYTES, load_plan, sha, static_check,
)
from protocol.storage_upper_experiment_esptool import (
    CAPTURE_SCRIPT, CAPTURE_SCRIPT_SHA256, _MAC, _NORMAL_BOOT_MARKERS,
    _PANIC_MARKERS, parse_identity,
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load local module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD_AUDIT = _load_module(
    "expression_e143b_e3c3a_deployment_build_audit",
    ROOT / "scripts/audit-expression-e1-43b-e3c3a-build.py",
)
RUN_LABEL = re.compile(r"e143b-e3c3a-[0-9]{8}-[a-z0-9][a-z0-9-]{0,23}")
CHUNK_NAME = re.compile(r"^[0-9]{3}-full-readback-([0-9a-f]{8})-00100000\.bin$")


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("required evidence file is missing or unsafe: " + path.name)
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("evidence must be a JSON object: " + path.name)
    return value


def _require_local_path(path: Path, parent: Path) -> Path:
    if path.is_symlink():
        raise ValueError("evidence path must not be a symlink")
    resolved = path.resolve(strict=True)
    if resolved.parent != parent.resolve(strict=True) or not resolved.is_file():
        raise ValueError("evidence path must be a direct child of its run folder")
    return resolved


def _check_snapshot_chunks(folder: Path, snapshot: bytes) -> list[dict]:
    captures = folder / "captures"
    if captures.is_symlink() or not captures.is_dir():
        raise ValueError("Flash capture directory is missing or unsafe")
    by_offset = {}
    for path in captures.iterdir():
        match = CHUNK_NAME.fullmatch(path.name)
        if match is None:
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("Flash capture chunk is not a regular file")
        offset = int(match.group(1), 16)
        if offset in by_offset:
            raise ValueError("duplicate Flash capture offset")
        by_offset[offset] = {"path": path, "sequence": int(path.name[:3])}
    expected_offsets = list(range(0, FLASH_BYTES, 0x100000))
    if sorted(by_offset) != expected_offsets:
        raise ValueError("32 ordered one-MiB Flash capture chunks are required")
    rows = []
    for offset in expected_offsets:
        capture = by_offset[offset]
        raw = capture["path"].read_bytes()
        expected = snapshot[offset:offset + 0x100000]
        if len(raw) != 0x100000 or raw != expected:
            raise ValueError("saved Flash capture differs from the complete snapshot")
        rows.append({"offset": offset, "bytes": len(raw),
                     "capture_file": capture["path"].name,
                     "capture_sequence": capture["sequence"], "sha256": sha(raw)})
    return rows


def _check_read_transfer_logs(folder: Path, expected_identity: str) -> list[dict]:
    logs = folder / "logs"
    if logs.is_symlink() or not logs.is_dir():
        raise ValueError("esptool log directory is missing or unsafe")
    paths = list(logs.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("esptool log directory must contain only regular files")

    flash_by_sequence = {}
    security_by_sequence = {}
    read_by_offset = {}
    boot_capture_logs = []
    for path in paths:
        identity_match = re.fullmatch(r"([0-9]{3})-flash-id\.log", path.name)
        security_match = re.fullmatch(r"([0-9]{3})-security-info\.log", path.name)
        read_match = re.fullmatch(r"([0-9]{3})-full-readback\.log", path.name)
        if identity_match:
            flash_by_sequence[identity_match.group(1)] = path
        elif security_match:
            security_by_sequence[security_match.group(1)] = path
        elif read_match:
            raw = path.read_text(encoding="utf-8", errors="strict")
            transfer = re.findall(r"Read ([0-9]+) bytes at 0x([0-9a-fA-F]+) in ", raw)
            macs = {value.lower() for value in _MAC.findall(raw)}
            if ("esptool.py v4.12.0" not in raw or raw.count("Serial port COM4") != 1
                    or len(macs) != 1 or len(transfer) != 1
                    or int(transfer[0][0]) != 0x100000):
                raise ValueError("Flash read log does not identify one complete COM4 transfer")
            offset = int(transfer[0][1], 16)
            if offset in read_by_offset:
                raise ValueError("duplicate COM4 Flash read log offset")
            read_by_offset[offset] = {
                "path": path, "sequence": int(read_match.group(1)),
                "mac": next(iter(macs)), "log_sha256": sha(path.read_bytes()),
            }
        elif re.fullmatch(r"[0-9]{3}-normal-boot-capture\.log", path.name):
            boot_capture_logs.append(path)
        elif re.search(r"write|erase", path.name, re.IGNORECASE):
            raise ValueError("write or erase command log found in read-only preparation")

    flash_logs = [flash_by_sequence[key] for key in sorted(flash_by_sequence)]
    security_logs = [security_by_sequence[key] for key in sorted(security_by_sequence)]
    if not flash_logs or len(flash_logs) != len(security_logs):
        raise ValueError("paired Flash identity logs are required")
    identities = {}
    for index, (flash_path, security_path) in enumerate(zip(flash_logs, security_logs)):
        identity = parse_identity(
            flash_path.read_text(encoding="utf-8", errors="strict"),
            security_path.read_text(encoding="utf-8", errors="strict"),
        )
        if identity.digest != expected_identity:
            raise ValueError("COM4 identity log differs from the migrated device")
        identities[str(index)] = identity
    reference = next(iter(identities.values()))
    if any(identity.digest != reference.digest for identity in identities.values()):
        raise ValueError("COM4 identity changed between read commands")

    expected_offsets = list(range(0, FLASH_BYTES, 0x100000))
    if sorted(read_by_offset) != expected_offsets:
        raise ValueError("32 COM4 read logs must cover the complete Flash in order")
    transfers = []
    for offset in expected_offsets:
        row = read_by_offset[offset]
        if row["mac"] != reference.mac:
            raise ValueError("Flash block was read from a different COM4 device")
        transfers.append({"log": row["path"].name, "log_sequence": row["sequence"],
                          "offset": offset, "bytes": 0x100000,
                          "log_sha256": row["log_sha256"]})
    if len(boot_capture_logs) != 1:
        raise ValueError("one original-App serial capture command log is required")

    allowed_logs = (set(flash_logs) | set(security_logs)
                    | {row["path"] for row in read_by_offset.values()} | set(boot_capture_logs))
    if set(paths) != allowed_logs:
        raise ValueError("unexpected command log found in the read-only run")
    payloads = folder / "payloads"
    if payloads.exists() and (payloads.is_symlink() or not payloads.is_dir() or any(payloads.iterdir())):
        raise ValueError("Flash payload found in the read-only run")
    return transfers


def _bind_read_transfers_to_captures(transfers: list[dict], captures: list[dict]) -> list[dict]:
    captures_by_offset = {row["offset"]: row for row in captures}
    if len(captures_by_offset) != len(captures):
        raise ValueError("duplicate Flash capture offset")
    if {row["offset"] for row in transfers} != set(captures_by_offset):
        raise ValueError("COM4 read logs and saved Flash captures cover different ranges")
    bound = []
    for transfer in transfers:
        capture = captures_by_offset[transfer["offset"]]
        if transfer["log_sequence"] != capture["capture_sequence"] + 1:
            raise ValueError("COM4 read log does not follow its matching capture file")
        bound.append({**transfer, "capture_file": capture["capture_file"],
                      "capture_sha256": capture["sha256"]})
    return bound


def _repo_file(relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("repository evidence path escapes the project")
    candidate = ROOT
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("repository evidence path contains a symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(ROOT.resolve(strict=True)):
        raise ValueError("repository evidence file is missing or outside the project")
    return resolved


def audit_preparation(folder: Path, build_report: Path) -> dict:
    folder = Path(folder)
    if folder.is_symlink():
        raise ValueError("run folder must not be a symlink")
    folder = folder.resolve(strict=True)
    if folder.parent != (ROOT / "tmp").resolve(strict=True):
        raise ValueError("run folder must be a direct child of repository tmp")
    run_label = folder.name
    if RUN_LABEL.fullmatch(run_label) is None:
        raise ValueError("invalid E1.43b E3c-3a run label")

    receipt = _read_json(folder / "preflight.json")
    observation = _read_json(folder / "device-observation.json")
    if (receipt.get("schema") != "deskbot.continuous_mmap.live_preflight.e3c3a.v1"
            or receipt.get("edition") != "E1.43b-2"
            or receipt.get("result") != "snapshot_validated_rollback_prepared"
            or receipt.get("run_label") != run_label
            or receipt.get("device_written") is not False
            or receipt.get("flash_written") is not False
            or receipt.get("nvs_written") is not False
            or receipt.get("assets_written") is not False
            or receipt.get("bootloader_written") is not False
            or receipt.get("partition_table_written") is not False
            or receipt.get("c61_accessed") is not False
            or receipt.get("normal_boot_observed") is not True):
        raise ValueError("live preparation receipt differs")
    if (observation.get("schema") != "deskbot.continuous_mmap.device_observation.e3c3a.v1"
            or observation.get("run_label") != run_label
            or observation.get("target") != "body_COM4"
            or observation.get("identity_sha256") != receipt.get("device", {}).get("identity_sha256")
            or observation.get("snapshot_bytes") != FLASH_BYTES
            or observation.get("snapshot_sha256") != receipt.get("snapshot", {}).get("sha256")
            or observation.get("baseline_validated") is not True
            or observation.get("normal_boot_observed") is not True
            or observation.get("device_written") is not False):
        raise ValueError("device observation differs from the preparation receipt")

    snapshot_path = _require_local_path(folder / "snapshot-32m.bin", folder)
    snapshot = snapshot_path.read_bytes()
    if (len(snapshot) != FLASH_BYTES or len(snapshot) != receipt["snapshot"].get("bytes")
            or sha(snapshot) != receipt["snapshot"].get("sha256")):
        raise ValueError("saved 32 MiB snapshot differs")
    if sha(snapshot) != observation.get("snapshot_sha256"):
        raise ValueError("observation snapshot digest differs")
    chunk_rows = _check_snapshot_chunks(folder, snapshot)
    transfer_rows = _check_read_transfer_logs(
        folder, receipt.get("device", {}).get("identity_sha256", ""))
    transfer_rows = _bind_read_transfers_to_captures(transfer_rows, chunk_rows)

    capture_entries = list((folder / "captures").iterdir())
    if any(path.is_symlink() or not path.is_file() for path in capture_entries):
        raise ValueError("capture directory must contain only regular files")
    allowed_captures = {row["capture_file"] for row in chunk_rows}

    build = BUILD_AUDIT.audit_report(build_report)
    build_raw = Path(build_report)
    if not build_raw.is_absolute():
        build_raw = ROOT / build_raw
    build_digest = sha(build_raw.read_bytes())
    if (build.get("result") != "passed"
            or build.get("report") != receipt.get("build", {}).get("report")
            or build_digest != receipt.get("build", {}).get("report_sha256")
            or build.get("report_sha256") != build_digest):
        raise ValueError("independent E1.43b build audit differs")
    build_raw = Path(build_report)
    if not build_raw.is_absolute():
        build_raw = ROOT / build_raw
    build_document = json.loads(build_raw.read_bytes())
    candidate_facts = receipt["build"].get("candidate_app", {})
    candidate_relative = Path(candidate_facts.get("path", ""))
    expected_candidate = Path(build_document["rounds"]["on"]["evidence_directory"]) / "xiaozhi.bin"
    if candidate_relative != expected_candidate:
        raise ValueError("candidate App path does not match the audited build")
    candidate_path = _repo_file(candidate_relative)
    candidate = candidate_path.read_bytes()
    if (len(candidate) != candidate_facts.get("bytes")
            or sha(candidate) != candidate_facts.get("sha256")):
        raise ValueError("audited candidate App differs")

    identity = receipt["device"].get("identity_sha256")
    baseline_result = baseline.validate_e3c3a_snapshot(snapshot, identity)
    baseline_receipt = receipt.get("baseline", {})
    original_app = baseline_result["original_app"]
    if (baseline_receipt.get("layout") != baseline_result["layout"]
            or baseline_receipt.get("app_bytes") != original_app["bytes"]
            or baseline_receipt.get("app_sha256") != original_app["sha256"]
            or baseline_receipt.get("nvs_sha256") != baseline_result["nvs"]["sha256"]):
        raise ValueError("baseline App or NVS receipt differs from the saved snapshot")
    plan = load_plan(folder, execution=True)
    if plan.plan_id != receipt.get("plan_id"):
        raise ValueError("persisted plan id differs from receipt")
    if plan.metadata["snapshot_sha256"] != sha(snapshot):
        raise ValueError("persisted plan is not bound to this snapshot")
    if plan.rollback != snapshot[APP_START:APP_START + len(plan.rollback)]:
        raise ValueError("App rollback bytes are not from the same-run snapshot")
    padded = candidate + b"\xff" * (len(plan.candidate) - len(candidate))
    if plan.candidate != padded:
        raise ValueError("persisted candidate payload differs from audited build")
    expected_erase = ((max(original_app["bytes"], len(candidate)) + 4095) // 4096) * 4096
    if len(plan.rollback) != expected_erase or plan.metadata["erase_bytes"] != expected_erase:
        raise ValueError("App rollback span does not cover current and candidate App images")
    static_check(plan, snapshot)
    if (plan.metadata["identity_sha256"] != identity
            or plan.metadata["nvs_sha256"] != baseline_result["nvs"]["sha256"]
            or receipt.get("rollback", {}).get("sha256") != sha(plan.rollback)
            or receipt.get("rollback", {}).get("bytes") != len(plan.rollback)
            or receipt.get("candidate_write") != {
                "offset": APP_START, "payload_bytes": len(candidate),
                "erase_bytes": plan.metadata["erase_bytes"],
                "end_exclusive": APP_START + plan.metadata["erase_bytes"],
            }):
        raise ValueError("App plan range or rollback receipt differs")

    captures = folder / "captures"
    boot_logs = [path for path in captures.iterdir()
                 if re.fullmatch(r"[0-9]{3}-normal-boot\.log", path.name)]
    if len(boot_logs) != 1 or boot_logs[0].is_symlink():
        raise ValueError("one original-App boot log is required")
    allowed_captures.add(boot_logs[0].name)
    if {path.name for path in capture_entries} != allowed_captures:
        raise ValueError("capture directory contains unexpected evidence")
    boot_log = boot_logs[0].read_text(encoding="utf-8", errors="strict")
    if (not all(marker in boot_log for marker in _NORMAL_BOOT_MARKERS)
            or any(marker in boot_log for marker in _PANIC_MARKERS)):
        raise ValueError("original App normal boot markers are incomplete")
    capture_logs = list((folder / "logs").glob("[0-9][0-9][0-9]-normal-boot-capture.log"))
    if len(capture_logs) != 1 or sha(CAPTURE_SCRIPT.read_bytes()) != CAPTURE_SCRIPT_SHA256:
        raise ValueError("fixed original-App capture script evidence is missing or changed")
    command_output = capture_logs[0].read_text(encoding="utf-8", errors="strict")
    capture_match = re.search(r"Capture complete: (.+) \(lines=([0-9]+)\)", command_output)
    if (capture_match is None
            or Path(capture_match.group(1)).resolve() != boot_logs[0].resolve()
            or int(capture_match.group(2)) <= 0):
        raise ValueError("original-App capture log does not bind the saved boot log")
    if any((folder / name).exists() for name in ("recovery.json", "execute-result.json")):
        raise ValueError("preparation stage must not include an execution journal or result")

    return {
        "schema": "deskbot.continuous_mmap.live_preflight_audit.e3c3a.v3",
        "result": "passed",
        "run_label": run_label,
        "plan_id": plan.plan_id,
        "identity_sha256": identity,
        "snapshot_sha256": sha(snapshot),
        "snapshot_bytes": len(snapshot),
        "snapshot_chunks": chunk_rows,
        "com4_read_transfers": transfer_rows,
        "identity_log_sha256": {path.name: sha(path.read_bytes()) for path in
                                 list((folder / "logs").glob("[0-9][0-9][0-9]-flash-id.log"))
                                 + list((folder / "logs").glob("[0-9][0-9][0-9]-security-info.log"))},
        "candidate_app_sha256": sha(candidate),
        "rollback_app_sha256": sha(plan.rollback),
        "normal_boot_markers_verified": list(_NORMAL_BOOT_MARKERS),
        "build_audit_result": build["result"],
        "baseline_layout": baseline_result["layout"],
        "write_logs": 0,
        "candidate_app_file_present": True,
        "write_command_payload_files": 0,
        "device_written": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    if output.is_symlink() or output.exists():
        parser.error("audit output must be a new file")
    if output.resolve().parent != (ROOT / "artifacts/expression-continuous-mmap-e1-43b2-e3c3a-20261107").resolve():
        parser.error("audit output must be in the E1.43b archive")
    result = audit_preparation(ROOT / "tmp" / args.run_label, args.build_report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": result["result"], "run_label": result["run_label"],
                      "plan_id": result["plan_id"], "device_written": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
