#!/usr/bin/env python3
"""Create/check the E1.35c read-only device-backup report.

The raw 32 MiB readback stays in the local project tmp directory.  This report
publishes only hashes, partition facts, and the migration stop conditions; it
never writes flash or changes firmware.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tmp/e135b-migration-20261029/manifest.json"
PUBLIC = ROOT / "artifacts/expression-storage-live-e1-35c-20261029/report.json"
DEFAULT_CAPTURE = ROOT.parent.parent / "tmp/e135c-live-20261029"

sys.path.insert(0, str(ROOT))
from protocol.storage_live_backup import (  # noqa: E402
    audit_backup,
    require_independent_verify,
    verify_capture_files,
)
from protocol.storage_migration import validate_manifest, validate_schema  # noqa: E402


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_page(report: dict[str, Any]) -> str:
    template = (ROOT / "scripts/expression-e1-35c-live.template.html").read_text(encoding="utf-8")
    labels = {
        "old_partition_payload_matches": ("分区表", "与旧布局一致"),
        "accepted_app_payload_matches": ("E1.26 App", "与已验收有效镜像一致"),
        "accepted_e118_assets_matches": ("E1.18 资源", "完整原分区与已验收镜像一致"),
        "model_payload_matches_build_reference": ("模型有效内容", "与构建参考一致"),
        "model_full_partition_matches_build_reference": ("模型整分区", "有历史尾部字节，保留现场原始内容"),
        "bootloader_matches_build_reference": ("bootloader", "与构建参考不同，已完整备份且未写入"),
    }
    checks = "".join(f'<tr><td>{title}</td><td class="{"ok" if report["checks"][key] else "warn"}">{"一致" if report["checks"][key] else "差异已保存"}</td><td>{description}</td></tr>'
                     for key, (title, description) in labels.items())
    ranges = "".join(f'<tr><td>{html.escape(row["id"])}</td><td><code>0x{row["offset"]:X}</code></td><td><code>0x{row["end_exclusive"]:X}</code></td><td>{row["bytes"]:,}</td><td>{"是" if row["all_erased"] else "否"}</td></tr>'
                     for row in report["required_ranges"])
    data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return (template.replace("@@CHECKS@@", checks).replace("@@RANGES@@", ranges)
            .replace("@@FULL_SHA@@", report["whole_device_sha256"]).replace("@@DATA@@", data))


def immutable_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"refusing to overwrite changed evidence: {path}")
        return
    path.write_bytes(raw)


def load_manifest() -> dict[str, Any]:
    report = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = {key: value for key, value in report.items() if key != "manifest_sha256"}
    validate_schema(manifest)
    validate_manifest(manifest, ROOT, require_files=False)
    return manifest


def make_report(capture: Path) -> dict[str, Any]:
    index = json.loads((capture / "capture-index.json").read_text(encoding="utf-8"))
    raw = verify_capture_files(capture, index)
    full = index["full_image"]
    verify_log_path = capture / "verify-full-keep.log"
    require_independent_verify(
        verify_log_path.read_text(encoding="utf-8"),
        index["mac"],
        index["physical_flash_bytes"],
        str((capture / full["path"]).resolve()),
    )
    manifest = load_manifest()
    facts = audit_backup(raw, manifest, ROOT)
    boot_log = capture / "boot-after-backup.log"
    boot = boot_log.read_text(encoding="utf-8")
    security = (capture / "security-info.log").read_text(encoding="utf-8")
    flash = (capture / "flash-id.log").read_text(encoding="utf-8")
    if ("Found 16MB PSRAM device" not in boot
            or "Detected size(32768k) larger than the size in the binary image header(16384k)" not in boot
            or "Detected flash size: 32MB" not in flash
            or f"MAC: {index['mac']}" not in flash
            or "Secure Boot: Disabled" not in security or "Flash Encryption: Disabled" not in security):
        raise ValueError("physical capacity, image configuration or security evidence is incomplete")
    report: dict[str, Any] = {
        "schema": "deskbot.expression.storage_live_backup.v1",
        "edition": "E1.35c",
        "date": "2026-10-29",
        "result": "device_backup_complete_candidate_rebuild_required",
        "deployment_eligible": False,
        "flash_authorized": False,
        "safety": {
            "hardware_accessed": True,
            "serial_accessed": True,
            "device_written": False,
            "nvs_written": False,
        },
        "device": {
            "port": index["port"],
            "chip": index["chip"],
            "identity_confirmed": True,
            "physical_flash_bytes": index["physical_flash_bytes"],
            "configured_flash_bytes": facts["configured_flash_bytes"],
            "psram_bytes": 16 * 1024 * 1024,
            "secure_boot": False,
            "flash_encryption": False,
        },
        "capture": {
            "local_directory": "tmp/e135c-live-20261029",
            "full_image": {"bytes": full["bytes"], "sha256": full["sha256"]},
            "chunk_count": len(index["chunks"]),
            "chunk_bytes": index["chunks"][0]["bytes"],
            "transport_digest_verified": all(row.get("transport_digest_verified") is True for row in index["chunks"]),
            "independent_verify_log": "tmp/e135c-live-20261029/verify-full-keep.log",
            "independent_verify_passed": True,
            "boot_log": {"path": "tmp/e135c-live-20261029/boot-after-backup.log", "sha256": sha_file(boot_log)} if boot_log.is_file() else None,
        },
        "source_manifest_sha256": sha_file(MANIFEST),
        "checks": facts["checks"],
        "baseline_matches": facts["baseline_matches"],
        "required_ranges": facts["required_ranges"],
        "whole_device_sha256": facts["whole_device_sha256"],
        "additional_unconfigured_bytes": facts["additional_unconfigured_bytes"],
        "additional_unconfigured_all_erased": facts["additional_unconfigured_all_erased"],
        "startup": {
            "application_started": "Calling app_main()" in boot,
            "lcd_initialized": "LCD panel create success" in boot,
            "touch_initialized": "Touch sensor initialized successfully" in boot,
            "doa_initialized": "DOA tracker initialized" in boot,
            "audio_initialized": "Audio codec started" in boot,
            "wifi_connected": "STATE: idle" in boot,
            "icon_lookup_errors_present": "Not found:\"icon_" in boot,
            "panic_seen": any(s in boot for s in ("Guru Meditation", "assert failed", "abort() was called")),
        },
        "blockers": [
            "physical Flash is 32 MiB while the current image header is configured for 16 MiB; the storage plan needs revision",
            "the live model partition tail contains device bytes and is not the E1.35a all-0xFF build reference",
            "the live bootloader bytes differ from the E1.35a build reference",
            "candidate 8 MiB mmap and runtime model loading have not been verified",
        ],
        "next_gate": "rebuild_device_first_32MiB_candidate_then_request_separate_migration_authorization",
        "runtime_mmap_verified": False,
        "physical_interaction_accepted": False,
        "device_written": False,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output", type=Path, default=PUBLIC)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        target = args.output.resolve()
        if not any(target.is_relative_to(ROOT / scope) for scope in ("artifacts", "tmp")):
            raise ValueError("report output must stay inside repository artifacts/ or tmp/")
        report = make_report(args.capture_dir)
        raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if args.check:
            if not args.output.is_file() or args.output.read_bytes() != raw:
                raise ValueError("E1.35c report is missing or stale")
            page = args.output.with_name("index.html")
            if not page.is_file() or page.read_text(encoding="utf-8") != render_page(report):
                raise ValueError("E1.35c review page is missing or stale")
        else:
            immutable_write(args.output, raw)
            args.output.with_name("index.html").write_text(render_page(report), encoding="utf-8")
        print(json.dumps({"edition": "E1.35c", "result": report["result"],
                          "physical_flash_bytes": report["device"]["physical_flash_bytes"],
                          "configured_flash_bytes": report["device"]["configured_flash_bytes"],
                          "baseline_matches": report["baseline_matches"],
                          "deployment_eligible": False, "flash_authorized": False}, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"E1.35c live audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
