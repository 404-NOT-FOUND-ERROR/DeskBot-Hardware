#!/usr/bin/env python3
"""Audit retained E1.43a-6 bytes and logs; no device I/O."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_upper_pattern_runtime import extract_records

RUN = ROOT / "tmp/e143a6-live2-20261102"
PUBLIC = ROOT / "artifacts/expression-upper-live-e1-43a6-20261103/report.json"
PLAN_ID = "3ca2a150e65e9fe9ff66387ed68cf59d1c096ef3ca0e611986658c8fd6a80671"
REFERENCE = ROOT / "tmp/e140-capacity-boot-20261030/current-full-32MiB.bin"
REFERENCE_SHA = "7597eb8e56ae08180f472085ba392bd0d48443f7ef79a281f46403a1fe2ee58e"
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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def join_chunks(chunks: list[tuple[int, bytes]], start: int, size: int) -> bytes:
    cursor = start
    for address, raw in sorted(chunks):
        require(address == cursor and len(raw) > 0, "missing or overlapping capture chunk")
        cursor += len(raw)
    require(cursor == start + size, "capture range differs from its reviewed geometry")
    return b"".join(raw for _, raw in sorted(chunks))


def snapshot(first: int, last: int) -> dict[str, bytes]:
    groups: dict[str, list[tuple[int, bytes]]] = {}
    for path in (RUN / "captures").iterdir():
        match = SNAPSHOT.fullmatch(path.name)
        if match is None or not first <= int(match[1]) <= last:
            continue
        raw = path.read_bytes()
        require(len(raw) == int(match[4], 16), "snapshot file is truncated")
        groups.setdefault(match[2], []).append((int(match[3], 16), raw))
    require(set(groups) == set(GEOMETRY), "snapshot regions differ from the reviewed set")
    return {name: join_chunks(groups[name], *span) for name, span in GEOMETRY.items()}


def readback(first: int, last: int, start: int, size: int) -> bytes:
    chunks = []
    for path in (RUN / "captures").iterdir():
        match = READBACK.fullmatch(path.name)
        if match is None or not first <= int(match[1]) <= last:
            continue
        raw = path.read_bytes()
        require(len(raw) == int(match[3], 16), "readback file is truncated")
        chunks.append((int(match[2], 16), raw))
    return join_chunks(chunks, start, size)


def make_report() -> dict:
    result = load(RUN / "result.json")
    journal = load(RUN / "recovery.json")
    plan = load(RUN / "plan/manifest.json")
    require(all(value.get("plan_id") == PLAN_ID for value in (result, journal, plan)), "plan ID changed")
    require(result.get("result") == "probe_failed_but_restored" and result.get("probe_passed") is False
            and result.get("restored") is True and result.get("journal_finalized") is True
            and result.get("error") is None, "retained experiment result differs")
    require(journal.get("phase") == "complete" and journal.get("sequence") == 11
            and result["journal"] == journal, "recovery journal is incomplete or inconsistent")
    files = {}
    for key, meta in plan["files"].items():
        raw = (RUN / "plan" / meta["file"]).read_bytes()
        require(len(raw) == meta["bytes"] and sha(raw) == meta["sha256"], "plan bytes differ: " + key)
        files[key] = raw

    before, after = snapshot(153, 231), snapshot(297, 375)
    require(sum(len(raw) for raw in before.values()) == 32 * 1024 * 1024, "snapshot is not 32 MiB")
    ranges = {}
    for name, (start, size) in GEOMETRY.items():
        require(before[name] == after[name], "restored bytes differ: " + name)
        ranges[name] = {"offset": start, "bytes": size, "sha256": sha(before[name]), "byte_equal": True}
    for name, digest in plan["expected_static_protected_sha256"].items():
        require(sha(before[name]) == sha(after[name]) == digest
                and journal["static_protected_sha256"][name] == digest, "static protection differs: " + name)
    require(before["app"] == files["rollback_app"] and before["page"] == files["rollback_page"],
            "fresh execute snapshot differs from rollback files")
    require(readback(241, 241, *GEOMETRY["page"]) == files["pattern"], "pattern write/readback differs")
    candidate = files["candidate_app"]
    padded_candidate = candidate + b"\xff" * (GEOMETRY["app"][1] - len(candidate))
    require(readback(251, 259, *GEOMETRY["app"]) == padded_candidate, "diagnostic App readback differs")
    require(readback(275, 275, *GEOMETRY["page"]) == files["rollback_page"], "page restore readback differs")
    require(readback(285, 293, *GEOMETRY["app"]) == files["rollback_app"], "App restore readback differs")

    runtime = result["runtime_validation"]
    probe_path = RUN / "captures/263-upper-probe.log"
    records = extract_records(probe_path.read_text(encoding="utf-8"))
    require(records == runtime["raw_records"], "runtime report differs from raw CPU records")
    failed = sorted(name for name, okay in runtime["checks"].items() if okay is not True)
    require(failed == ["firmware_check_passed", "immutable_lower_prefix_matches_reference"],
            "unexpected additional probe failure")
    current_prefix = before["bootloader_reserved"] + before["partition_table"]
    reference = REFERENCE.read_bytes()
    require(len(reference) == 32 * 1024 * 1024 and sha(reference) == REFERENCE_SHA,
            "historical reference differs from its anchor")
    old_prefix = reference[:0x9000]
    require(before["partition_table"] == reference[0x8000:0x9000],
            "partition table differs from the historical reference")
    differences = [i for i, pair in enumerate(zip(current_prefix, old_prefix)) if pair[0] != pair[1]]
    require(differences and max(differences) < 0x8000, "reference difference is outside bootloader region")
    require(records["summary"]["low_protected_read_sha256"] == sha(current_prefix),
            "CPU protected prefix differs from the same-run snapshot")
    require(runtime["reference"]["low_protected_sha256"] == sha(old_prefix), "reported historical reference differs")
    require(sha(old_prefix).encode("ascii") in candidate,
            "retained diagnostic binary does not contain the historical prefix constant")

    boot_path = RUN / "captures/379-normal-boot.log"
    boot = boot_path.read_text(encoding="utf-8")
    markers = ("Calling app_main()", "LCD panel create success", "Touch sensor initialized successfully",
               "Found 16MB PSRAM device", "detected chip: mxic (opi)", "flash io: opi_str")
    require(all(token in boot for token in markers), "restored normal boot is incomplete")
    require(not any(token in boot for token in ("Guru Meditation", "assert failed", "abort() was called")),
            "panic/assert observed after restore")
    return {
        "schema": "deskbot.upper_pattern.live_audit.e1_43a6/v1", "date": "2026-11-03",
        "result": "probe_failed_but_restored", "plan_id": PLAN_ID,
        "probe_passed": False, "restore_byte_audit_passed": True, "journal_phase": "complete",
        "journal_sequence": 11, "audited_snapshot_bytes": 32 * 1024 * 1024,
        "pattern_and_diagnostic_app_readbacks_match": True,
        "restored_ranges": ranges,
        "failed_runtime_checks": failed,
        "diagnosis": {
            "cause": "historical_bootloader_reference_used_for_current_protected_prefix",
            "prefix_offset": 0, "prefix_bytes": 0x9000,
            "same_run_sha256": sha(current_prefix), "historical_sha256": sha(old_prefix),
            "cpu_matches_same_run_snapshot": True, "partition_table_matches_historical": True,
            "changed_bytes": len(differences), "first_difference": min(differences),
            "last_difference": max(differences), "differences_within_bootloader_only": True,
            "firmware_has_historical_prefix_constant": True,
        },
        "startup": {"required_markers_found": len(markers), "panic_or_assert_observed": False,
                    "physical_regression": "pending"},
        "evidence_sha256": {name: sha(path.read_bytes()) for name, path in {
            "result": RUN / "result.json", "recovery": RUN / "recovery.json",
            "plan": RUN / "plan/manifest.json", "probe_log": probe_path, "restored_boot_log": boot_path,
        }.items()},
        "next_step": "bind the next probe to its fresh snapshot, then run offline tests and isolated builds",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = make_report()
    rendered = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    for path in (RUN / "post-run-audit.json", PUBLIC):
        if args.check:
            require(path.read_bytes() == rendered, "retained audit differs: " + path.name)
        elif path.exists():
            require(path.read_bytes() == rendered, "existing audit differs: " + path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(rendered)
    print(json.dumps({"result": report["result"], "restore_byte_audit_passed": True,
                      "snapshot_bytes": report["audited_snapshot_bytes"],
                      "cpu_prefix_matches_same_run": True, "physical_regression": "pending"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
