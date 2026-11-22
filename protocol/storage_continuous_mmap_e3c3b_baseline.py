"""Pinned E3c-3b App, probe and snapshot checks; no device access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat

from protocol import storage_continuous_mmap_baseline as migrated
from protocol import storage_continuous_mmap_e3c3a_baseline as previous
from protocol import storage_upper_migration_transaction as migration
from protocol.storage_continuous_mmap_experiment import APP_BYTES, APP_START, FLASH_BYTES, NVS_END, NVS_START
from protocol.storage_upper_migration_preflight import IMAGE_SHA256, LIVE_MODEL_SHA256, UPPER_BYTES, UPPER_START

ROOT = Path(__file__).resolve().parents[1]
BUILD_REPORT = Path("artifacts/display-dma-repair-e3c3b-20261107/firmware-build-report.json")
BUILD_REPORT_SHA256 = "1caccb773de444a2184d12ccbdb4fabe2aab301250a3d517ae043f6b55d8eed1"
ACCEPTANCE = BUILD_REPORT.parent / "manual-verification.json"
ACCEPTANCE_SHA256 = "4088f73c45411bcb627c3963f7801d1e901b2e0c53302c53bfba98461bbddd9c"
BUILD_DIRECTORY = Path("tmp/e3c3b-display-dma-20261107/on")
APP_BYTES_EXPECTED = 4_489_232
APP_SHA256_EXPECTED = "180aa8cbcc98e3d5b95329175ff2e3f2ee1d35f806b03cbe0778aa2fc2d0f42a"
APP_ELF_SHA256_EXPECTED = "e0941ae3c68528763764f918a62b508fcb990008936d7dd1c69228f283345098"
ARCHIVE = Path("artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107")
PROBE_LABEL = "e143b-e3c3b-20261107-a"
PROBE_REPORT = ARCHIVE / (PROBE_LABEL + "-build-report.json")
PROBE_REPORT_SHA256 = "410013067f97b2f3d7c4ce2a12876da72ff5b089499e8977bea558ecbfa3a297"
PROBE_AUDIT = ARCHIVE / (PROBE_LABEL + "-audit.json")
PROBE_AUDIT_SHA256 = "3e2b8611cb9f1ed89d37aabbb756f8ac352c022aa8a5d4fdedd1f9216f1dd554"
PROBE_APP_BYTES = 4_494_096
PROBE_APP_SHA256 = "66b887e276fe25353c3cef9456f7bbfbf8a1ce6b4df9ac239ad82cf7b4fa76b2"
PROBE_FLAG = "DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT"
QUERY_FLAGS = ("DESKBOT_EXPRESSION_OBSERVER_ENABLE", "DESKBOT_STUDIO_IDENTITY_ENABLE",
               "DESKBOT_STUDIO_STATE_QUERY_ENABLE", "DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE")
PROTECTED = {"bootloader/bootloader.bin", "partition_table/partition-table.bin",
             "srmodels/srmodels.bin", "ota_data_initial.bin"}
MINIMUM_APP_HEADROOM = 1024 * 1024


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def safe_path(path: Path, parent: Path, *, exists: bool = True) -> Path:
    """Reject redirected ancestors, including Windows directory junctions."""
    parent = Path(parent).absolute()
    target = Path(path).absolute()
    if ".." in target.parts or not target.is_relative_to(parent):
        raise ValueError("evidence path escapes its directory")
    for item in (target, *target.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if item == target and exists:
                raise ValueError("required evidence is missing: " + str(item))
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("evidence path contains a reparse point")
    resolved = target.resolve(strict=exists)
    if not resolved.is_relative_to(parent.resolve(strict=True)):
        raise ValueError("evidence path escapes its directory")
    return resolved


def repo_bytes(root: Path, relative: Path | str) -> bytes:
    relative = Path(relative)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("repository evidence must use a relative child path")
    path = safe_path(Path(root) / relative, Path(root))
    if not path.is_file():
        raise ValueError("repository evidence is not a regular file")
    return path.read_bytes()


def _fixed_json(root: Path, path: Path, digest: str) -> dict:
    raw = repo_bytes(root, path)
    if sha256(raw) != digest:
        raise ValueError("pinned report digest differs: " + path.as_posix())
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("pinned report must be a JSON object")
    return value


def _record(root: Path, relative: Path, expected: dict) -> bytes:
    raw = repo_bytes(root, relative)
    if expected != {"bytes": len(raw), "sha256": sha256(raw)}:
        raise ValueError("build artifact differs: " + relative.as_posix())
    return raw


def load_e3c3b_build_reference(root: Path = ROOT) -> dict:
    report = _fixed_json(root, BUILD_REPORT, BUILD_REPORT_SHA256)
    acceptance = _fixed_json(root, ACCEPTANCE, ACCEPTANCE_SHA256)
    row = report.get("build", {})
    target = report.get("target", {})
    if (report.get("schema") != "deskbot.display_dma_repair_build/v1"
            or report.get("edition") != "E3c-3b"
            or report.get("result") != "query_enabled_build_verified"
            or target.get("chip") != "esp32s3" or target.get("flash_bytes") != FLASH_BYTES
            or target.get("psram_bytes") != 16 * 1024 * 1024
            or target.get("layout") != "upper-assets-8m"
            or row.get("build_directory") != BUILD_DIRECTORY.as_posix()
            or row.get("query_enabled") is not True
            or row.get("config_equal_reference") is not True
            or set(row.get("protected_equal", {})) != PROTECTED
            or not all(value is True for value in row["protected_equal"].values())):
        raise ValueError("accepted E3c-3b build identity differs")
    if (acceptance.get("app") != "E3c-3b" or acceptance.get("result") != "passed"
            or acceptance.get("app_sha256") != APP_SHA256_EXPECTED
            or acceptance.get("phase") != "online_menu_regression"):
        raise ValueError("E3c-3b hardware acceptance differs")
    artifacts = row.get("artifacts", {})
    if set(artifacts) != previous.BUILD_ARTIFACTS:
        raise ValueError("E3c-3b artifact set differs")
    raw = {name: _record(root, BUILD_DIRECTORY / name, item) for name, item in artifacts.items()}
    app = {"bytes": APP_BYTES_EXPECTED, "sha256": APP_SHA256_EXPECTED}
    elf = repo_bytes(root, BUILD_DIRECTORY / "xiaozhi.elf")
    if (artifacts["xiaozhi.bin"] != app or row.get("app_elf_sha256") != APP_ELF_SHA256_EXPECTED
            or sha256(elf) != APP_ELF_SHA256_EXPECTED
            or raw["xiaozhi.bin"][176:208].hex() != APP_ELF_SHA256_EXPECTED
            or row.get("app_headroom_bytes") != APP_BYTES - APP_BYTES_EXPECTED
            or artifacts["partition_table/partition-table.bin"]["sha256"] != target.get("partition_table_sha256")):
        raise ValueError("accepted E3c-3b App or ELF differs")
    config = json.loads(raw["config/sdkconfig.json"])
    if not all(config.get(key) is True for key in QUERY_FLAGS) or config.get(PROBE_FLAG) is not False:
        raise ValueError("accepted E3c-3b query/probe configuration differs")
    return {"layout": "upper-assets-8m", "report": {"path": BUILD_REPORT.as_posix(), "sha256": BUILD_REPORT_SHA256},
            "acceptance": {"path": ACCEPTANCE.as_posix(), "sha256": ACCEPTANCE_SHA256},
            "build_directory": BUILD_DIRECTORY.as_posix(),
            "app": {"offset": APP_START, **app, "elf_sha256": APP_ELF_SHA256_EXPECTED},
            "artifacts": artifacts, "config": config}


def load_e3c3b_probe_inputs(root: Path = ROOT) -> dict:
    """Recheck pinned completed build/audit and every archived output."""
    accepted = load_e3c3b_build_reference(root)
    report = _fixed_json(root, PROBE_REPORT, PROBE_REPORT_SHA256)
    audit = _fixed_json(root, PROBE_AUDIT, PROBE_AUDIT_SHA256)
    report_binding = {"path": PROBE_REPORT.as_posix(), "sha256": PROBE_REPORT_SHA256}
    if (report.get("schema") != "deskbot.continuous_mmap.build.e1_43b_e3c3b/v1"
            or report.get("result") != "e3c3b_probe_off_on_builds_verified"
            or report.get("baseline") != "E3c-3b" or report.get("run_label") != PROBE_LABEL
            or report.get("reference_report") != accepted["report"]
            or audit.get("schema") != "deskbot.continuous_mmap.build_audit.e1_43b_e3c3b/v1"
            or audit.get("result") != "passed" or audit.get("build_report") != report_binding
            or not all(audit.get(key) is True for key in
                       ("probe_off_matches_e3c3b", "probe_on_isolated", "protected_outputs_match"))):
        raise ValueError("E3c-3b probe build/audit binding differs")
    for key in ("device_accessed", "device_written", "runtime_verified", "physical_interaction_accepted"):
        if report.get(key) is not False:
            raise ValueError("offline probe build has an unexpected device result")
    base = Path("tmp/e143b-e3c3b-20261107") / PROBE_LABEL
    candidate_path = base / "on/evidence/xiaozhi.bin"
    rounds = report.get("rounds", {})
    if set(rounds) != {"off", "on"}:
        raise ValueError("both probe build configurations are required")
    for label, enabled in (("off", False), ("on", True)):
        row = rounds[label]
        if (row.get("build_directory") != (base / label / "build").as_posix()
                or row.get("evidence_directory") != (base / label / "evidence").as_posix()
                or row.get("probe_enabled") is not enabled):
            raise ValueError("probe build paths differ")
        if set(row.get("artifacts", {})) != previous.BUILD_ARTIFACTS | {"xiaozhi.elf"}:
            raise ValueError("probe artifact set differs")
        for name, item in row["artifacts"].items():
            built = _record(root, base / label / "build" / name, item)
            archived = _record(root, base / label / "evidence" / name, item)
            if built != archived:
                raise ValueError("archived probe output differs")
            if name in PROTECTED and item != accepted["artifacts"][name]:
                raise ValueError("protected probe output differs")
        config = json.loads(repo_bytes(root, base / label / "evidence/config/sdkconfig.json"))
        expected = {**accepted["config"], PROBE_FLAG: enabled}
        if config != expected:
            raise ValueError("probe configuration has extra changes")
        app_record = row["artifacts"]["xiaozhi.bin"]
        expected_app = {"bytes": PROBE_APP_BYTES, "sha256": PROBE_APP_SHA256} if enabled else {
            "bytes": APP_BYTES_EXPECTED, "sha256": APP_SHA256_EXPECTED}
        app = repo_bytes(root, base / label / "evidence/xiaozhi.bin")
        elf_digest = row["artifacts"]["xiaozhi.elf"]["sha256"]
        if (app_record != expected_app or app[176:208].hex() != elf_digest
                or row.get("embedded_elf_sha256") != elf_digest):
            raise ValueError("probe App or ELF differs")
    return {"report": report_binding, "audit": {"path": PROBE_AUDIT.as_posix(), "sha256": PROBE_AUDIT_SHA256},
            "candidate_path": candidate_path.as_posix(), "candidate": repo_bytes(root, candidate_path),
            "candidate_sha256": PROBE_APP_SHA256, "accepted": accepted}


def _validate_snapshot(snapshot: bytes, identity_sha256: str, migration_reference: dict,
                       build_reference: dict) -> dict:
    if not isinstance(snapshot, bytes) or len(snapshot) != FLASH_BYTES:
        raise ValueError("complete 32 MiB device snapshot required")
    if not _valid_sha(identity_sha256) or identity_sha256 != migration_reference.get("identity_sha256"):
        raise ValueError("device identity differs from migrated body")
    # Reuse only historical static-range geometry; its old App digest is skipped.
    protected = previous._validate_migration_reference(migration_reference)
    expected_app = {"offset": APP_START, "bytes": APP_BYTES_EXPECTED,
                    "sha256": APP_SHA256_EXPECTED, "elf_sha256": APP_ELF_SHA256_EXPECTED}
    if build_reference.get("layout") != "upper-assets-8m" or build_reference.get("app") != expected_app:
        raise ValueError("accepted E3c-3b App reference differs")
    if sha256(snapshot[APP_START:APP_START + APP_BYTES_EXPECTED]) != APP_SHA256_EXPECTED:
        raise ValueError("current App BIN differs from E3c-3b")
    fixed = {"partition_table": migration.TABLE_SECTOR_SHA256,
             "upper_assets": IMAGE_SHA256, "model": LIVE_MODEL_SHA256}
    for name, row in protected.items():
        if name == "app":
            continue
        raw = snapshot[row["offset"]:row["offset"] + row["bytes"]]
        if sha256(raw) != row["sha256"] or (name in fixed and row["sha256"] != fixed[name]):
            raise ValueError("migrated static baseline differs: " + name)
    return {"schema": "deskbot.continuous_mmap.e3c3b_baseline.v1", "layout": "upper-assets-8m",
            "identity_sha256": identity_sha256, "migration_reference": migration_reference.get("report"),
            "migration_plan_id": migration_reference.get("migration_plan_id"),
            "build_reference": build_reference.get("report"), "original_app": expected_app,
            "protected_ranges": [dict(row) for name, row in protected.items() if name != "app"],
            "historical_app_range_skipped": True,
            "nvs": {"offset": NVS_START, "bytes": NVS_END - NVS_START,
                    "sha256": sha256(snapshot[NVS_START:NVS_END])}}


def validate_e3c3b_snapshot(snapshot: bytes, identity_sha256: str) -> dict:
    """Use sealed references; callers cannot replace the accepted baseline."""
    return _validate_snapshot(snapshot, identity_sha256, migrated.load_migrated_baseline(),
                              load_e3c3b_build_reference())
