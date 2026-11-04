#!/usr/bin/env python3
"""Independently audit retained A7 captures; no device I/O."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_upper_pattern import build_pattern, manifest_for
from protocol.storage_upper_pattern_runtime import extract_records, validate_capture
from protocol.storage_upper_snapshot import load_snapshot
from protocol.storage_upper_experiment_esptool import load_persisted_plan, parse_identity

RUN = ROOT / "tmp/e143a7-live-20261103"
PUBLIC = ROOT / "artifacts/expression-upper-snapshot-e1-43a7-20261103/live-report.json"
PLAN_ID = "e8b4e59ed36201756f11656ec53add1f861a1fdec8613aef6e40c9677412c941"
CANDIDATE_SHA = "35a5d080e4b783b581db818b1d2c16bb7ae788384f55274fa8412f4531a2dc3b"
GEOMETRY = {
    "bootloader_reserved": (0, 0x8000), "partition_table": (0x8000, 0x1000),
    "nvs": (0x9000, 0x4000), "otadata": (0xD000, 0x2000),
    "phy_init": (0xF000, 0x1000), "app": (0x10000, 0x448000),
    "gap_after_app": (0x458000, 0x194000), "assets": (0x5EC000, 0x5DC000),
    "model": (0xBC8000, 0xC8000), "gap_after_model": (0xC90000, 0x370000),
    "page": (0x1000000, 0x10000), "upper_outside_test_page": (0x1010000, 0xFF0000),
}
SNAPSHOT = re.compile(r"([0-9]+)-snapshot-(.+)-([0-9a-f]{8})-([0-9a-f]{8})\.bin")
READBACK = re.compile(r"([0-9]+)-readback-([0-9a-f]{8})-([0-9a-f]{8})\.bin")
PANIC = ("Guru Meditation", "assert failed", "abort() was called")
BOOT_MARKERS = ("Calling app_main()", "LCD panel create success",
                "Touch sensor initialized successfully", "Found 16MB PSRAM device",
                "detected chip: mxic (opi)", "flash io: opi_str")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(path: Path) -> dict:
    require(path.is_file() and not path.is_symlink(), "evidence missing or linked: " + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def join_chunks(chunks: list[tuple[int, bytes]], start: int, size: int) -> bytes:
    cursor = start
    for address, raw in sorted(chunks):
        require(address == cursor and len(raw) > 0, "missing or overlapping capture chunk")
        cursor += len(raw)
    require(cursor == start + size, "capture range differs from reviewed geometry")
    return b"".join(raw for _, raw in sorted(chunks))


def captures() -> tuple[list[dict], list[dict]]:
    rows, retries = [], []
    for path in (RUN / "captures").iterdir():
        match = SNAPSHOT.fullmatch(path.name) or READBACK.fullmatch(path.name)
        if match is None:
            continue
        require(path.is_file() and not path.is_symlink(), "unsafe capture: " + path.name)
        is_snapshot = SNAPSHOT.fullmatch(path.name) is not None
        sequence = int(match[1])
        label = "snapshot-" + match[2] if is_snapshot else "readback"
        offset = int(match[3 if is_snapshot else 2], 16)
        size = int(match[4 if is_snapshot else 3], 16)
        command = RUN / "logs" / f"{sequence + 1:03d}-{label}.log"
        require(command.is_file() and not command.is_symlink(), "missing capture command log")
        log = command.read_text(encoding="utf-8")
        require(log.count("Serial port COM4") == 1, "capture targets another port")
        raw = path.read_bytes()
        okay = (len(raw) == size and re.search(
            rf"Read {size} bytes at 0x{offset:08x} in [0-9.]+ seconds", log) is not None)
        if not okay:
            require("A fatal error occurred: Corrupt data, expected " in log,
                    "incomplete capture without retained read-transfer error: " + path.name)
            retries.append({"file": path.name, "offset": offset, "expected_bytes": size,
                            "retained_bytes": len(raw), "command_sha256": sha(log.encode("utf-8"))})
            continue
        rows.append({"sequence": sequence, "region": match[2] if is_snapshot else None,
                     "offset": offset, "bytes": raw, "file": path.name,
                     "command_sha256": sha(log.encode("utf-8"))})
    rows.sort(key=lambda row: row["sequence"])
    require(len({row["sequence"] for row in rows}) == len(rows), "duplicate capture sequence")
    return rows, retries


def snapshots(rows: list[dict]) -> list[dict]:
    batches = []
    for row in (r for r in rows if r["region"] is not None):
        if row["region"] == "app" and row["offset"] == GEOMETRY["app"][0]:
            batches.append({"first": row["sequence"], "rows": []})
        require(bool(batches), "snapshot starts outside App")
        batches[-1]["rows"].append(row)
    require(len(batches) == 3, "expected preflight, write-time and restored snapshots")
    for batch in batches:
        grouped = {}
        for row in batch["rows"]:
            grouped.setdefault(row["region"], []).append((row["offset"], row["bytes"]))
        require(set(grouped) == set(GEOMETRY), "snapshot region set differs")
        batch["regions"] = {name: join_chunks(grouped[name], *span) for name, span in GEOMETRY.items()}
        batch["last"] = batch["rows"][-1]["sequence"]
        require(sum(len(raw) for raw in batch["regions"].values()) == 32 * 1024 * 1024,
                "snapshot does not cover 32 MiB")
    return batches


def audit_restored_ranges(before: dict, after: dict, nvs_observation: dict) -> dict:
    require(set(before) == set(after) == set(GEOMETRY), "restoration region set differs")
    ranges = {}
    for name, (start, size) in GEOMETRY.items():
        a, b = before[name], after[name]
        require(isinstance(a, bytes) and isinstance(b, bytes) and len(a) == len(b) == size,
                "restoration region length differs: " + name)
        if name != "nvs":
            require(a == b, "restored bytes differ from write-time snapshot: " + name)
        ranges[name] = {"offset": start, "bytes": size, "sha256": sha(a),
                        "after_sha256": sha(b), "byte_equal": a == b}
    expected = {"before_sha256": sha(before["nvs"]), "latest_sha256": sha(after["nvs"]),
                "changed": before["nvs"] != after["nvs"]}
    require(nvs_observation == expected and type(nvs_observation.get("changed")) is bool,
            "NVS observation differs from the retained before/after captures")
    return ranges


def make_report() -> dict:
    result, journal, preflight = (load(RUN / name) for name in ("result.json", "recovery.json", "preflight.json"))
    plan = load_persisted_plan(RUN)
    require(plan.plan_id == PLAN_ID and sha(plan.candidate_app) == CANDIDATE_SHA, "plan or candidate changed")
    require(all(value.get("plan_id") == PLAN_ID for value in (result, journal, preflight)), "plan binding differs")
    require(preflight.get("result") == "fresh_snapshot_and_original_boot_passed"
            and preflight.get("normal_boot_observed") is True
            and preflight["safety"]["flash_written"] is False, "read-only preflight did not pass")
    require(result.get("result") == "probe_passed_and_restored" and result.get("probe_passed") is True
            and result.get("restored") is True and result.get("journal_finalized") is True
            and result.get("error") is None, "experiment did not pass and restore")
    require(journal.get("schema") == "deskbot.upper_pattern.experiment_journal.v4"
            and journal.get("phase") == "complete" and journal.get("sequence") == 11
            and journal.get("probe_passed") is True and result["journal"] == journal,
            "recovery journal incomplete or inconsistent")
    require(result["snapshot_manifest_sha256"] == journal["snapshot_manifest_sha256"], "journal binding differs")
    reference = load_snapshot(RUN, identity_sha256=plan.expected_identity_sha256, plan_id=PLAN_ID,
                              expected_manifest_sha256=journal["snapshot_manifest_sha256"])

    rows, retries = captures()
    pre, before, after = snapshots(rows)
    ranges = audit_restored_ranges(before["regions"], after["regions"], journal["nvs_observation"])
    for name, digest in plan.expected_static_protected_sha256.items():
        require(all(sha(batch["regions"][name]) == digest for batch in (pre, before, after))
                and journal["static_protected_sha256"][name] == digest, "static protection differs: " + name)
    for batch in (pre, before, after):
        require(batch["regions"]["app"] == plan.rollback_app and batch["regions"]["page"] == plan.rollback_page,
                "snapshot differs from rollback bytes")
    low = b"".join(before["regions"][name] for name in (
        "bootloader_reserved", "partition_table", "nvs", "otadata", "phy_init"))
    require(reference.low_page == low and reference.boundary_lower == before["regions"]["gap_after_model"][-16:],
            "saved runtime reference differs from same-run Flash captures")
    require(preflight["snapshot"]["app_erase_sha256"] == sha(pre["regions"]["app"])
            and preflight["snapshot"]["upper_page_sha256"] == sha(pre["regions"]["page"])
            and all(preflight["snapshot"]["protected_sha256"][name] == sha(pre["regions"][name])
                    for name in preflight["snapshot"]["protected_sha256"]), "preflight report differs from captures")
    require(journal["nvs_observation"]["before_sha256"] == sha(before["regions"]["nvs"])
            and journal["nvs_observation"]["latest_sha256"] == sha(after["regions"]["nvs"]), "NVS observation differs")

    writes = sorted((RUN / "payloads").glob("*.bin"), key=lambda p: int(p.name.split("-", 1)[0]))
    expected_writes = [(GEOMETRY["page"][0], plan.pattern), (GEOMETRY["app"][0], plan.candidate_app),
                       (GEOMETRY["page"][0], plan.rollback_page), (GEOMETRY["app"][0], plan.rollback_app)]
    require(len(writes) == 4, "expected exactly four write payloads")
    readbacks = [row for row in rows if row["region"] is None]
    write_facts = []
    for index, (path, (offset, raw)) in enumerate(zip(writes, expected_writes)):
        require(path.is_file() and not path.is_symlink() and path.read_bytes() == raw, "write payload differs")
        seq = int(path.name.split("-", 1)[0])
        end = int(writes[index + 1].name.split("-", 1)[0]) if index < 3 else after["first"]
        chunks = [row for row in readbacks if seq < row["sequence"] < end]
        padded_size = (len(raw) + 0xFFF) & ~0xFFF
        actual = join_chunks([(row["offset"], row["bytes"]) for row in chunks], offset, padded_size)
        require(actual == raw + b"\xff" * (padded_size - len(raw)), "write readback differs")
        label = "write-page" if offset == GEOMETRY["page"][0] else "write-app"
        log_path = RUN / "logs" / f"{seq + 1:03d}-{label}.log"
        log = log_path.read_text(encoding="utf-8")
        require(log.count("Serial port COM4") == 1 and "Hash of data verified." in log
                and "A fatal error occurred" not in log, "write command did not verify")
        write_facts.append({"payload": path.name, "offset": offset, "bytes": len(raw),
                            "sha256": sha(raw), "readback_matches": True, "command_sha256": sha(log.encode("utf-8"))})
    require(len(readbacks) == sum(1 if offset == GEOMETRY["page"][0] else 5 for offset, _ in expected_writes),
            "unexpected extra readback captures")

    probe_paths = list((RUN / "captures").glob("*-upper-probe.log"))
    require(len(probe_paths) == 1, "expected one CPU capture")
    probe_path = probe_paths[0]
    probe_log = probe_path.read_text(encoding="utf-8")
    records = extract_records(probe_log)
    checked = validate_capture(records, manifest_for(build_pattern()), sha(low),
                               (reference.boundary_lower + plan.pattern[:16]).hex(), sha(low[:0x9000]))
    require(checked["probe_passed"] is True and all(checked["checks"].values()), "CPU checks failed")
    runtime = result["runtime_validation"]
    require(records == runtime["raw_records"] and checked["checks"] == runtime["checks"]
            and runtime["result"] == "runtime_probe_passed"
            and runtime["binding"]["identity_sha256"] == plan.expected_identity_sha256
            and runtime["binding"]["plan_id"] == PLAN_ID
            and runtime["binding"]["run_label"] == RUN.name
            and runtime["binding"]["source_log_sha256"] == sha(probe_log.encode("utf-8"))
            and runtime["binding"]["snapshot_manifest_sha256"] == reference.manifest_sha256,
            "runtime result differs from raw CPU capture or same-run binding")

    boot_paths = sorted((RUN / "captures").glob("*-normal-boot.log"), key=lambda p: int(p.name.split("-", 1)[0]))
    require(len(boot_paths) == 2, "expected preflight and restored boot captures")
    for path in boot_paths:
        log = path.read_text(encoding="utf-8")
        require(all(marker in log for marker in BOOT_MARKERS) and not any(marker in log for marker in PANIC),
                "normal boot incomplete or panic observed")
    require(not any(marker in probe_log for marker in PANIC), "probe panicked")
    identity_count = 0
    for path in (RUN / "logs").glob("*-flash-id.log"):
        number = int(path.name.split("-", 1)[0])
        security = RUN / "logs" / f"{number + 1:03d}-security-info.log"
        identity = parse_identity(path.read_text(encoding="utf-8"), security.read_text(encoding="utf-8"))
        require(identity.digest == plan.expected_identity_sha256, "device identity changed")
        identity_count += 1
    require(identity_count > 0, "no fresh device identities")
    before_image = b"".join(before["regions"][name] for name in sorted(GEOMETRY, key=lambda n: GEOMETRY[n][0]))
    after_image = b"".join(after["regions"][name] for name in sorted(GEOMETRY, key=lambda n: GEOMETRY[n][0]))
    require(len(before_image) == len(after_image) == 32 * 1024 * 1024, "full Flash capture size differs")
    summary = records["summary"]
    return {
        "schema": "deskbot.upper_pattern.live_audit.e1_43a7/v2", "date": "2026-11-03",
        "result": "probe_passed_and_restored", "run_label": RUN.name, "plan_id": PLAN_ID,
        "probe_passed": True, "restore_byte_audit_passed": True, "journal_phase": "complete",
        "journal_sequence": 11, "snapshot_manifest_sha256": reference.manifest_sha256,
        "audited_snapshot_bytes": len(before_image), "before_full_sha256": sha(before_image),
        "after_full_sha256": sha(after_image), "full_image_byte_equal": before_image == after_image,
        "restore_scope": "exact_App_and_test_page_and_all_static_ranges",
        "static_ranges_byte_equal": all(row["byte_equal"] for name, row in ranges.items() if name != "nvs"),
        "nvs_observation": journal["nvs_observation"],
        "snapshot_sequence_ranges": [[batch["first"], batch["last"]] for batch in (pre, before, after)],
        "restored_ranges": ranges, "writes": write_facts, "read_transfer_retries": retries,
        "fresh_identity_pairs_checked": identity_count, "runtime_checks": checked["checks"],
        "low_page_matches_write_snapshot": summary["low_alias_read_sha256"] == sha(low),
        "runtime_summary": summary,
        "startup": {"required_markers_found": len(BOOT_MARKERS), "panic_or_assert_observed": False,
                    "physical_regression": "pending"},
        "evidence_sha256": {name: sha(path.read_bytes()) for name, path in {
            "result": RUN / "result.json", "recovery": RUN / "recovery.json", "preflight": RUN / "preflight.json",
            "plan": RUN / "plan/manifest.json", "probe_log": probe_path,
            "restored_boot_log": boot_paths[-1], "auditor": Path(__file__),
        }.items()},
        "next_step": "physical regression, then continuous 8 MiB mmap",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = make_report()
    rendered = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    for path in (RUN / "post-run-audit.json", PUBLIC):
        if args.check or path.exists():
            require(path.read_bytes() == rendered, "retained audit differs: " + path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(rendered)
    print(json.dumps({"result": report["result"], "restore_byte_audit_passed": True,
                      "snapshot_bytes": report["audited_snapshot_bytes"], "physical_regression": "pending"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
