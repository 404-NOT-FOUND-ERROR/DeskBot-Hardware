"""E1.43c offline preflight for the selected upper-assets-8m layout."""
from __future__ import annotations

import json
from typing import Any

from protocol.storage_migration import (
    E118_OLD_ASSETS_SHA256, EMBEDDED_SPEECH_MODEL_SHA256,
    sha256_bytes, validate_member_inventory, validate_member_records,
)
from protocol.storage_n32r16 import (
    ASSETS_BYTES, MIB, PACKAGE_BYTES, PACKAGE_SHA256, TABLE_SHA256,
    legacy_rows, proposals, validate_proposal,
)

UPPER_START = 0x1000000
UPPER_BYTES = 8 * MIB
IMAGE_SHA256 = "8e9dae01b99060c313a76f2dfc7c2b854b80c4584d76f607a74dd2903d39fce4"
INVENTORY_SHA256 = "986200a5d74cf3957c5a3187d7dae89590164100bd71dbcd64087a6d71227e78"
LIVE_MODEL_SHA256 = "37a572cda25d8381b733f8ce48c1532488c87cf3359e1c78b13b1262f58eed39"
LEGACY_TABLE_SECTOR_SHA256 = "5a0251d24335255b36e5993d5a2a260abb638c29ef02a480094ae26490da477c"
PENDING = [
    "E1.43b_continuous_mapping_cpu_validation",
    "E1.43b_interaction_during_mapping",
    "E1.43b_original_app_restore_and_regression",
    "upper_migration_runner_and_recovery_fault_tests",
    "same_execution_device_identity_and_full_backup",
]
FUTURE_SEQUENCE = [
    "fresh_full_backup_and_identity", "upper_assets_write_and_readback",
    "partition_table_sector_commit_last", "boot_mount_and_interaction_regression",
]
RECOVERY_SEQUENCE = [
    "enter_usb_download_mode", "restore_original_table_sector_and_verify",
    "restore_upper_destination_bytes_and_verify", "audit_untouched_static_regions",
    "boot_original_app_and_regress",
]


def upper_plan() -> dict:
    return next(p for p in proposals() if p["id"] == "upper-assets-8m")


def validate_upper_layout(plan: dict, table: bytes) -> None:
    if not isinstance(plan, dict) or plan.get("id") != "upper-assets-8m":
        raise ValueError("the selected route is upper-assets-8m")
    rows = validate_proposal(plan)
    if rows["assets"]["start"] != UPPER_START or rows["assets"]["size_bytes"] != UPPER_BYTES:
        raise ValueError("upper resource geometry changed")
    if len(table) != 3072 or sha256_bytes(table) != TABLE_SHA256["upper-assets-8m"]:
        raise ValueError("retained E1.36 upper partition table changed")


def validate_members(records: list[dict], members: dict[str, bytes]) -> dict:
    if (not isinstance(records, list) or len(records) != 29
            or validate_member_inventory(records) != INVENTORY_SHA256):
        raise ValueError("accepted E1.18 member inventory changed")
    validate_member_records(records, members)
    index = json.loads(members["index.json"])
    if (index.get("srmodels") != "srmodels.bin"
            or sha256_bytes(members["srmodels.bin"]) != EMBEDDED_SPEECH_MODEL_SHA256):
        raise ValueError("embedded speech model changed")
    surprised = [r for r in index.get("emoji_collection", []) if r.get("name") == "surprised"]
    if len(surprised) != 1 or surprised[0].get("file") != "shocked.eaf":
        raise ValueError("accepted surprised binding changed")
    return {"members": 29, "inventory_sha256": INVENTORY_SHA256,
            "index_sha256": sha256_bytes(members["index.json"]),
            "embedded_model_sha256": EMBEDDED_SPEECH_MODEL_SHA256,
            "surprised": "shocked.eaf"}


def validate_resource_images(payload: bytes, old: bytes, upper: bytes) -> None:
    if len(payload) != PACKAGE_BYTES or sha256_bytes(payload) != PACKAGE_SHA256:
        raise ValueError("accepted E1.18 payload changed")
    for name, raw, size, digest in (
        ("legacy", old, ASSETS_BYTES, E118_OLD_ASSETS_SHA256),
        ("upper", upper, UPPER_BYTES, IMAGE_SHA256),
    ):
        if len(raw) != size or raw[:PACKAGE_BYTES] != payload:
            raise ValueError(name + " resource image size or payload changed")
        if raw[PACKAGE_BYTES:] != b"\xff" * (size - PACKAGE_BYTES):
            raise ValueError(name + " resource image padding changed")
        if sha256_bytes(raw) != digest:
            raise ValueError(name + " full resource image changed")


def validate_deferred_mapping(readiness: dict) -> None:
    if (readiness.get("schema") != "deskbot.continuous_mmap.readiness.v1"
            or readiness.get("stage") != "E1.43b"
            or readiness.get("result") != "offline_build_and_deployment_tooling_ready_live_deferred"
            or readiness.get("tests", {}).get("total") != 97):
        raise ValueError("E1.43b offline readiness is incomplete")
    for field in ("serial_accessed", "device_written", "cpu_verified", "physical_interaction_accepted"):
        if readiness.get(field) is not False:
            raise ValueError("deferred mapping evidence cannot claim a live result: " + field)
    if readiness.get("device_baseline") != {
        "app": "E1.42", "bootloader": "E1.40", "assets": "E1.18", "layout": "legacy"
    }:
        raise ValueError("E1.43b baseline changed")


def validate_accepted_baseline(live: dict, acceptance: dict) -> None:
    if (live.get("result") != "probe_passed_and_restored"
            or live.get("restore_byte_audit_passed") is not True
            or live.get("static_ranges_byte_equal") is not True
            or live.get("audited_snapshot_bytes") != 32 * MIB):
        raise ValueError("A7 static restoration evidence is incomplete")
    expected = {
        "assets": (0x5EC000, ASSETS_BYTES, E118_OLD_ASSETS_SHA256),
        "model": (0xBC8000, 800 * 1024, LIVE_MODEL_SHA256),
        "partition_table": (0x8000, 0x1000, LEGACY_TABLE_SECTOR_SHA256),
    }
    for name, (offset, size, digest) in expected.items():
        row = live.get("restored_ranges", {}).get(name, {})
        if (row.get("offset") != offset or row.get("bytes") != size
                or row.get("sha256") != digest or row.get("after_sha256") != digest
                or row.get("byte_equal") is not True):
            raise ValueError("accepted static region changed: " + name)
    if (acceptance.get("stage") != "E1.43a-7"
            or acceptance.get("result") != "restored_baseline_interaction_accepted"
            or acceptance.get("source") != "user_feedback"
            or acceptance.get("baseline") != {"app": "E1.42", "bootloader": "E1.40",
                 "assets": "E1.18", "partition_layout": "unchanged_legacy"}
            or set(acceptance.get("accepted_checks", [])) != {
                "screen_display", "menu_open_and_return", "head_touch", "online_conversation",
                "menu_during_conversation", "menu_after_conversation"}):
        raise ValueError("A7 restored-baseline interaction acceptance is incomplete")


def future_geometry() -> list[dict]:
    """Describe future affected sectors; this is not an executable write plan."""
    return [
        {"id": "upper_assets", "offset": UPPER_START, "bytes": UPPER_BYTES,
         "end_exclusive": UPPER_START + UPPER_BYTES, "commit": "write_and_readback_first"},
        {"id": "partition_table", "offset": 0x8000, "bytes": 0x1000,
         "end_exclusive": 0x9000, "commit": "commit_last_after_assets_readback"},
    ]


def protected_geometry() -> list[dict]:
    return [{"name": "bootloader_reserved", "offset": 0, "bytes": 0x8000}] + [
        {"name": r["name"], "offset": r["start"], "bytes": r["size_bytes"]}
        for r in legacy_rows()
    ]


def validate_preflight_report(report: dict[str, Any]) -> None:
    if (report.get("schema") != "deskbot.upper_assets.preflight.v1"
            or report.get("stage") != "E1.43c"
            or report.get("result") != "offline_preflight_passed_live_pending"):
        raise ValueError("invalid upper-assets preflight identity")
    for field in ("serial_accessed", "device_written", "firmware_changed", "table_generated",
                  "flash_bundle_generated", "deployment_eligible", "continuous_mapping_accepted"):
        if report.get(field) is not False:
            raise ValueError("offline preflight crossed its stage: " + field)
    if report.get("layout") != upper_plan() or report.get("future_affected_ranges") != future_geometry():
        raise ValueError("upper layout or commit order changed")
    if report.get("protected_ranges") != protected_geometry():
        raise ValueError("protected geometry changed")
    if report.get("pending_before_deployment") != PENDING:
        raise ValueError("pending live prerequisites changed")
    if (report.get("future_sequence") != FUTURE_SEQUENCE
            or report.get("future_recovery") != RECOVERY_SEQUENCE
            or report.get("recovery_design_status") != "specified_not_implemented_or_fault_tested"
            or report.get("historical_inplace_plan_reused") is not False):
        raise ValueError("future migration or recovery procedure changed")
    if report.get("resources") != {
        "members": 29, "inventory_sha256": INVENTORY_SHA256,
        "index_sha256": "ba853dbe8388cb9dbfd992431d9f972302035de5fe5f574f984542b5cf76fe69",
        "embedded_model_sha256": EMBEDDED_SPEECH_MODEL_SHA256, "surprised": "shocked.eaf",
    }:
        raise ValueError("accepted resource identity changed")
    if report.get("budget") != {
        "partition_bytes": UPPER_BYTES, "package_bytes": PACKAGE_BYTES,
        "free_bytes": UPPER_BYTES - PACKAGE_BYTES, "publish_soft_limit_bytes": 7 * MIB,
        "additional_publish_budget_bytes": 7 * MIB - PACKAGE_BYTES, "reserve_bytes": MIB,
    }:
        raise ValueError("resource budget changed")
    if report.get("standalone_model") != {
        "offset": 0xBC8000, "bytes": 800 * 1024, "write": False,
        "accepted_live_sha256": LIVE_MODEL_SHA256,
        "source": "accepted_A7_readback_reference_fresh_backup_still_required",
    }:
        raise ValueError("standalone model must remain in place")
