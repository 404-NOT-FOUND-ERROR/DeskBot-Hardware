#!/usr/bin/env python3
"""Create and audit the offline E1.35b storage migration bundle.

The bundle contains complete, padded images for accepted build artifacts in
the old and candidate layouts.  It never reads a serial port, changes formal
firmware sources, or writes a device.  Live NVS and the old unallocated tail
remain explicit E1.35c prerequisites.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "tmp/e135b-migration-20261029"
PUBLIC = ROOT / "artifacts/expression-storage-migration-e1-35b-20261029"
E135A = ROOT / "artifacts/expression-storage-preflight-e1-35a-20261029/report.json"
BUILD_REPORT = ROOT / "artifacts/expression-storage-preflight-e1-35a-20261029/build-report.json"
PACKAGE = ROOT / "tmp/expression-e1-21-package-player-20261026-run2/rollback-e1-18-exact.bin"
MODEL = ROOT / "tmp/e135a-storage/outputs/on-old/srmodels/srmodels.bin"
OLD_BUILD = ROOT / "tmp/e135a-storage/outputs/on-old"
NEW_BUILD = ROOT / "tmp/e135a-storage/outputs/on-8m"

E118_PACKAGE_SHA = "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1"
E118_ASSETS_IMAGE_SHA = "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6"
E118_PACKAGE_BYTES = 5_538_351
OLD_ASSETS_BYTES = 6000 * 1024
NEW_ASSETS_BYTES = 8 * 1024 * 1024
MODEL_BYTES = 800 * 1024
APP_BYTES = 6000 * 1024

sys.path.insert(0, str(ROOT))
from protocol.storage_migration import (  # noqa: E402
    EMBEDDED_SPEECH_MODEL_SHA256,
    NEW_TABLE_SHA256,
    NEW_MODEL_START,
    OLD_TABLE_SHA256,
    OLD_ASSETS_START,
    OLD_MODEL_START,
    OLD_TAIL_OVERWRITTEN_END,
    OLD_TAIL_OVERWRITTEN_START,
    padded_partition,
    sha256_bytes,
    json_canonical,
    sha256_file,
    safe_relative_path,
    validate_manifest,
    validate_member_records,
    validate_model_identity,
    validate_schema,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def immutable_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"refusing to overwrite changed migration evidence: {path}")
        return
    path.write_bytes(raw)


def stage_image(relative: str, payload: bytes, capacity: int, *, check: bool = False) -> Path:
    path = WORK / relative
    image = padded_partition(payload, capacity)
    if check:
        if not path.is_file() or path.read_bytes() != image:
            raise ValueError(f"stale or missing staged image: {path}")
    else:
        immutable_write(path, image)
    return path


def segment(path: Path, offset: int, capacity: int, payload: bytes,
            source_kind: str = "accepted_build_artifact_padded_0xff",
            write_action: str = "reference_only_do_not_write") -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix(), "offset": offset,
            "capacity_bytes": capacity, "bytes": path.stat().st_size,
            "sha256": sha256_file(path), "payload_bytes": len(payload),
            "payload_sha256": sha256_bytes(payload), "source_kind": source_kind,
            "write_action": write_action}


def verify_source_inputs() -> dict[str, Any]:
    if not PACKAGE.is_file() or PACKAGE.stat().st_size != E118_PACKAGE_BYTES or sha256_file(PACKAGE) != E118_PACKAGE_SHA:
        raise ValueError("accepted E1.18 package is missing or changed")
    if not MODEL.is_file():
        raise ValueError("accepted standalone model source is missing or changed")
    validate_model_identity(MODEL.read_bytes())
    e135a = read_json(E135A)
    build = read_json(BUILD_REPORT)
    if e135a.get("edition") != "E1.35a" or e135a.get("audit_pass") is not True:
        raise ValueError("E1.35a audit report is not a passing offline input")
    if e135a.get("deployment_eligible") is not False or e135a.get("flash_authorized") is not False:
        raise ValueError("E1.35a must remain offline-only")
    if (build.get("build_pass") is not True or build.get("formal_inputs_match_accepted_e126") is not True
            or build.get("formal_inputs_unchanged") is not True):
        raise ValueError("four isolated E1.35a builds are required")
    if (not build.get("safety") or any(value is not False for value in build["safety"].values())
            or any(value is not False for value in e135a["safety"].values())):
        raise ValueError("build evidence crossed the device boundary")
    if (build.get("source_manifest_sha256") != sha256_file(ROOT / "firmware/body/SOURCE_MANIFEST.sha256")
            or build.get("source_manifest_sha256") != e135a.get("source_manifest_sha256")):
        raise ValueError("E1.35a formal source manifest changed")
    if len(build.get("builds", [])) != 4:
        raise ValueError("all four isolated old/candidate builds are required")
    expected_apps = {
        True: "f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2",
        False: e135a["build_summary"][0]["app"]["sha256"],
    }
    unchanged_artifacts = (
        "xiaozhi.bin", "xiaozhi.elf", "bootloader/bootloader.bin", "expression_assets.bin",
        "srmodels/srmodels.bin", "ota_data_initial.bin", "sdkconfig",
    )
    grouped: dict[bool, dict[str, dict[str, str]]] = {False: {}, True: {}}
    seen = set()
    for row in build["builds"]:
        if (not isinstance(row, dict) or type(row.get("observer_enabled")) is not bool
                or row.get("layout") not in {"old", "8m"}):
            raise ValueError("invalid E1.35a build identity")
        key = (row.get("observer_enabled"), row.get("layout"))
        if key in seen:
            raise ValueError("duplicate E1.35a build identity")
        seen.add(key)
        safe_relative_path(ROOT, row.get("archive", ""))
        artifacts = row.get("artifacts", {})
        if not isinstance(artifacts, dict):
            raise ValueError("E1.35a artifact inventory is missing")
        for name in (*unchanged_artifacts, "partition_table/partition-table.bin"):
            info = artifacts.get(name, {})
            path = safe_relative_path(ROOT, f"{row['archive']}/{name}")
            if (not path.is_file() or not isinstance(info, dict)
                    or info.get("bytes") != path.stat().st_size
                    or info.get("sha256") != sha256_file(path)):
                raise ValueError("E1.35a build artifact is missing or changed: " + name)
            if name == "xiaozhi.bin" and info["sha256"] != expected_apps[row["observer_enabled"]]:
                raise ValueError("E1.35a App identity changed")
            if name == "srmodels/srmodels.bin" and info["sha256"] != sha256_file(MODEL):
                raise ValueError("standalone model differs across E1.35a builds")
            if name == "partition_table/partition-table.bin":
                expected_table = OLD_TABLE_SHA256 if row["layout"] == "old" else NEW_TABLE_SHA256
                if info["sha256"] != expected_table:
                    raise ValueError("E1.35a partition table identity changed")
            else:
                grouped[row["observer_enabled"]].setdefault(row["layout"], {})[name] = info["sha256"]
    if seen != {(False, "old"), (False, "8m"), (True, "old"), (True, "8m")}:
        raise ValueError("E1.35a build matrix is incomplete")
    if any(by_layout["old"] != by_layout["8m"] for by_layout in grouped.values()):
        raise ValueError("E1.35a non-layout artifacts differ")
    parser_path = ROOT / "scripts/audit-expression-resources.py"
    spec = importlib.util.spec_from_file_location("e135b_resource_parser", parser_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("resource package parser is unavailable")
    parser = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = parser
    spec.loader.exec_module(parser)
    members = parser.parse_package_bytes(PACKAGE.read_bytes(), modern=False)["members"]
    index = json.loads(members["index.json"])
    if index.get("srmodels") != "srmodels.bin" or sha256_bytes(members["srmodels.bin"]) != EMBEDDED_SPEECH_MODEL_SHA256:
        raise ValueError("embedded E1.18 speech model identity changed")
    selected = [entry for entry in index.get("emoji_collection", []) if entry.get("name") == "surprised"]
    if len(selected) != 1 or selected[0].get("file") != "shocked.eaf":
        raise ValueError("accepted E1.18 semantic selection changed")
    member_records = [{"name": name, "bytes": len(raw), "sha256": sha256_bytes(raw)}
                      for name, raw in sorted(members.items())]
    validate_member_records(member_records, members)
    return {"e135a_report_sha256": sha256_file(E135A),
            "e135a_build_report_sha256": sha256_file(BUILD_REPORT),
            "e135a_source_manifest_sha256": build["source_manifest_sha256"],
            "accepted_package_sha256": E118_PACKAGE_SHA,
            "model_payload_sha256": sha256_file(MODEL),
            "model_payload_bytes": MODEL.stat().st_size,
            "asset_member_count": len(member_records),
            "asset_member_manifest_sha256": sha256_bytes(json_canonical(member_records)),
            "asset_members": member_records,
            "embedded_speech_model_sha256": sha256_bytes(members["srmodels.bin"])}


def make_manifest(*, check: bool = False) -> dict[str, Any]:
    inputs = verify_source_inputs()
    old_table = OLD_BUILD / "partition_table/partition-table.bin"
    new_table = NEW_BUILD / "partition_table/partition-table.bin"
    if sha256_file(old_table) != "f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c":
        raise ValueError("old partition table identity changed")
    if sha256_file(new_table) != "0a55d50b8b2fb845c205c475e1cd19aad6801b81320a5ebb4f9b3f31269945fa":
        raise ValueError("candidate partition table identity changed")
    old_sources = {
        "bootloader": OLD_BUILD / "bootloader/bootloader.bin",
        "partition_table": old_table,
        "app": OLD_BUILD / "xiaozhi.bin",
        "otadata": OLD_BUILD / "ota_data_initial.bin",
    }
    new_sources = {
        "bootloader": NEW_BUILD / "bootloader/bootloader.bin",
        "partition_table": new_table,
        "app": NEW_BUILD / "xiaozhi.bin",
        "otadata": NEW_BUILD / "ota_data_initial.bin",
    }
    for source in (*old_sources.values(), *new_sources.values()):
        if not source.is_file():
            raise FileNotFoundError(source)
    layouts = {}
    for layout_name, sources, assets_bytes, model_start in (
            ("old", old_sources, OLD_ASSETS_BYTES, OLD_MODEL_START),
            ("candidate", new_sources, NEW_ASSETS_BYTES, NEW_MODEL_START)):
        capacities = {"bootloader": (0x0, 0x8000), "partition_table": (0x8000, 0x1000),
                      "app": (0x10000, APP_BYTES), "otadata": (0xD000, 0x2000),
                      "assets": (OLD_ASSETS_START, assets_bytes), "model": (model_start, MODEL_BYTES)}
        payloads = {name: path.read_bytes() for name, path in sources.items()}
        payloads.update(assets=PACKAGE.read_bytes(), model=MODEL.read_bytes())
        rows = {}
        for name, payload in payloads.items():
            offset, capacity = capacities[name]
            basename = "partition-table" if name == "partition_table" else name
            image = stage_image(f"{layout_name}/{basename}.bin", payload, capacity, check=check)
            source_kind = ("accepted_e118_package_padded_0xff" if name == "assets"
                           else "e135a_model_build_reference_padded_0xff" if name == "model"
                           else "accepted_build_artifact_padded_0xff")
            action = ("rollback_reference_only" if layout_name == "old" else {
                "model": "write_first", "assets": "write_after_model", "partition_table": "commit_last",
            }.get(name, "reference_only_do_not_write"))
            rows[name] = segment(image, offset, capacity, payload, source_kind, action)
        layouts[layout_name] = rows
    old_segments, candidate_segments = layouts["old"], layouts["candidate"]
    old_assets = ROOT / old_segments["assets"]["path"]
    new_assets = ROOT / candidate_segments["assets"]["path"]
    old_model = ROOT / old_segments["model"]["path"]
    new_model = ROOT / candidate_segments["model"]["path"]
    if sha256_file(old_assets) != E118_ASSETS_IMAGE_SHA:
        raise ValueError("old E1.18 assets image does not match the accepted full-partition hash")
    manifest: dict[str, Any] = {
        "schema": "deskbot.expression.storage_migration.v1",
        "edition": "E1.35b",
        "date": "2026-10-29",
        "result": "offline_build_bundle_ready_live_rollback_pending",
        "deployment_eligible": False,
        "flash_authorized": False,
        "safety": {"hardware_accessed": False, "serial_accessed": False,
                    "device_written": False, "nvs_written": False},
        "source_inputs": inputs,
        "accepted_assets": {"package_bytes": E118_PACKAGE_BYTES, "package_sha256": E118_PACKAGE_SHA,
                            "old_full_partition_bytes": OLD_ASSETS_BYTES,
                            "old_full_partition_sha256": sha256_file(old_assets),
                            "candidate_full_partition_bytes": NEW_ASSETS_BYTES,
                            "candidate_full_partition_sha256": sha256_file(new_assets),
                            "padding_byte": "0xFF"},
        "model": {"payload_bytes": MODEL.stat().st_size, "payload_sha256": sha256_file(MODEL),
                  "candidate_payload_sha256": sha256_file(MODEL),
                  "old_full_partition_sha256": sha256_file(old_model),
                  "candidate_full_partition_sha256": sha256_file(new_model),
                  "partition_bytes": MODEL_BYTES},
        "layouts": {
            "old": {"id": "old-6m", "label": "accepted E1.26 + E1.18 layout",
                    "partition_table_payload_sha256": OLD_TABLE_SHA256, "segments": old_segments},
            "candidate": {"id": "candidate-8m", "label": "E1.35a 8 MiB assets candidate",
                          "partition_table_payload_sha256": NEW_TABLE_SHA256, "segments": candidate_segments},
        },
        "untouched_partitions": [
            {"name": "bootloader", "offset": 0x0, "bytes": 0x8000, "action": "preserve_and_back_up_live"},
            {"name": "app", "offset": 0x10000, "bytes": APP_BYTES, "action": "preserve_and_back_up_live"},
            {"name": "nvs", "offset": 0x9000, "bytes": 0x4000, "action": "preserve_and_back_up_live"},
            {"name": "phy_init", "offset": 0xF000, "bytes": 0x1000, "action": "preserve_and_back_up_live"},
            {"name": "otadata", "offset": 0xD000, "bytes": 0x2000, "action": "preserve_and_back_up_live"},
        ],
        "live_backup_requirements": {
            "complete_device_backup": False,
            "reason": "offline artifacts are not a readback of the connected device",
            "required_before_write": [
                {"id": "bootloader", "offset": 0x0, "bytes": 0x8000, "why": "retain untouched boot chain"},
                {"id": "current_partition_table", "offset": 0x8000, "bytes": 0x1000, "why": "compare actual layout"},
                {"id": "otadata", "offset": 0xD000, "bytes": 0x2000, "why": "retain OTA selection state"},
                {"id": "phy_init", "offset": 0xF000, "bytes": 0x1000, "why": "retain untouched PHY data"},
                {"id": "current_app", "offset": 0x10000, "bytes": APP_BYTES, "why": "retain current App rollback"},
                {"id": "current_assets", "offset": OLD_ASSETS_START, "bytes": OLD_ASSETS_BYTES, "why": "verify accepted E1.18"},
                {"id": "current_model", "offset": OLD_MODEL_START, "bytes": MODEL_BYTES, "why": "verify source model before overwrite"},
                {"id": "nvs", "offset": 0x9000, "bytes": 0x4000, "why": "preserve user/network state"},
                {"id": "old_tail_extension", "offset": OLD_TAIL_OVERWRITTEN_START,
                 "bytes": OLD_TAIL_OVERWRITTEN_END - OLD_TAIL_OVERWRITTEN_START,
                 "why": "new assets and relocated model overwrite previously unallocated/unknown bytes"},
            ],
        },
        "rollback": {
            "offline_build_reference_complete": True,
            "device_complete": False,
            "model_source_kind": "E1.35a build artifact, not device readback",
            "known_build_artifacts": ["bootloader", "partition_table", "app", "otadata", "assets", "model_reference"],
            "missing_live_items": ["bootloader", "current_partition_table", "otadata", "phy_init", "current_app", "current_assets", "current_model", "nvs", "old_tail_extension", "current_device_identity"],
            "method": "live readbacks are mandatory; restore the original storage/tail bytes in download mode, verify them, then restore the old table last; restore protected ranges only from live backups if changed; never use App-only rollback",
        },
        "write_order": ["new_model", "new_assets", "partition_table_last"],
        "write_plan": [
            {"id": "new_model", "segment": "model", "offset": NEW_MODEL_START, "bytes": MODEL_BYTES,
             "sha256": candidate_segments["model"]["sha256"]},
            {"id": "new_assets", "segment": "assets", "offset": OLD_ASSETS_START, "bytes": NEW_ASSETS_BYTES,
             "sha256": candidate_segments["assets"]["sha256"]},
            {"id": "partition_table_last", "segment": "partition_table", "offset": 0x8000, "bytes": 0x1000,
             "sha256": candidate_segments["partition_table"]["sha256"]},
        ],
        "flash_maps": {"layout_id": "candidate-8m", "candidate_layout_id": "candidate-8m", "legacy_map_allowed_for_candidate": False,
                       "model_offset": NEW_MODEL_START, "partition_table_sha256": NEW_TABLE_SHA256},
        "power_loss_warning": "This order is a recovery discipline, not atomic OTA. Keep USB download mode and the live backup until all writes and readbacks pass.",
        "negative_checks": [
            "reject a partition table with an unexpected hash or layout",
            "reject a candidate image whose model payload hash differs from the accepted source",
            "reject a manifest without the old model image",
            "reject a manifest without the old tail extension and NVS live-backup requirements",
            "reject old-layout flash maps for candidate layout writes",
            "reject any deployment or flash authorization in this offline stage",
        ],
    }
    validate_manifest(manifest, ROOT, require_files=True)
    validate_schema(manifest)
    return manifest


def render(manifest: dict[str, Any]) -> str:
    from html import escape

    old = manifest["layouts"]["old"]["segments"]
    candidate = manifest["layouts"]["candidate"]["segments"]
    actions = {"reference_only_do_not_write": "保护；只作参考", "commit_last": "最后提交",
               "write_after_model": "模型之后写入", "write_first": "先写入；需核对现场原模型"}
    rows = []
    for name in ("bootloader", "partition_table", "app", "otadata", "assets", "model"):
        before, after = old[name], candidate[name]
        rows.append(f"<tr><td>{name}</td><td>{before['bytes']:,} B<br><code>{before['sha256'][:16]}...</code></td>"
                    f"<td>{after['bytes']:,} B<br><code>{after['sha256'][:16]}...</code></td>"
                    f"<td>{actions[after['write_action']]}</td></tr>")
    backups = "".join(f"<tr><td>{escape(row['id'])}</td><td><code>0x{row['offset']:06X}</code></td>"
                      f"<td><code>0x{row['offset']+row['bytes']:06X}</code></td><td>{row['bytes']:,}</td></tr>"
                      for row in sorted(manifest['live_backup_requirements']['required_before_write'], key=lambda r: r['offset']))
    members = "".join(f"<tr><td>{escape(row['name'])}</td><td>{row['bytes']:,}</td><td><code>{row['sha256']}</code></td></tr>"
                      for row in manifest['source_inputs']['asset_members'])
    def bar(assets_bytes):
        remaining = 16 * 1024 * 1024 - OLD_ASSETS_START - assets_bytes - MODEL_BYTES
        return "".join(f'<div class="{name}" style="width:{size/(16*1024*1024-OLD_ASSETS_START)*100:.6f}%" title="{name}: {size:,} B"></div>'
                       for name, size in (("assets", assets_bytes), ("model", MODEL_BYTES), ("tail", remaining)))
    negatives = ["拒绝分区表哈希、布局版本或地址不符", "拒绝旧模型缺失、模型负载或完整镜像被更改",
                 "拒绝资源成员或 0xFF 填充被更改", "拒绝漏掉 NVS、旧模型或受影响尾部的备份要求",
                 "拒绝旧布局写入图用于新布局", "拒绝把 App、NVS、OTA 数据加入候选写入序列",
                 "拒绝离线阶段伪称已授权、已部署或已有完整设备回退"]
    replacements = {
        "@@MEMBER_COUNT@@": str(manifest['source_inputs']['asset_member_count']),
        "@@OLD_BAR@@": bar(OLD_ASSETS_BYTES), "@@NEW_BAR@@": bar(NEW_ASSETS_BYTES),
        "@@SEGMENT_ROWS@@": "".join(rows), "@@BACKUP_ROWS@@": backups, "@@MEMBER_ROWS@@": members,
        "@@NEGATIVE_ROWS@@": "".join(f"<li>{escape(value)}</li>" for value in negatives),
        "@@DATA@@": json.dumps({"edition": "E1.35b", "manifest": manifest}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"),
    }
    result = (ROOT / "scripts/expression-e1-35b-migration.template.html").read_text(encoding="utf-8")
    for marker, value in replacements.items():
        result = result.replace(marker, value)
    if "@@" in result:
        raise ValueError("unresolved E1.35b template marker")
    return result


def write_or_check(path: Path, raw: bytes, check: bool) -> None:
    if check:
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"stale or missing E1.35b artifact: {path}")
    else:
        # Only regenerated metadata uses this path. Binary images stay immutable.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the existing offline bundle without changing it")
    args = parser.parse_args()
    manifest = make_manifest(check=args.check)
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest_path = WORK / "manifest.json"
    write_or_check(manifest_path, manifest_raw, args.check)
    public_report = dict(manifest)
    public_report["manifest_sha256"] = sha256_bytes(manifest_raw)
    report_raw = (json.dumps(public_report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    html_raw = render(manifest).encode("utf-8")
    readme_raw = ("DeskBot E1.35b offline storage migration bundle / 原资源迁移包\n\n"
                  "Known build artifacts are staged for review only. No serial access, device readback or flash occurred.\n"
                  "Live NVS, current partitions and the old tail extension are mandatory E1.35c backups.\n").encode("utf-8")
    write_or_check(PUBLIC / "report.json", report_raw, args.check)
    write_or_check(PUBLIC / "index.html", html_raw, args.check)
    write_or_check(PUBLIC / "README.txt", readme_raw, args.check)
    validate_manifest(manifest, ROOT, require_files=True)
    print(json.dumps({"edition": "E1.35b", "result": "checked" if args.check else "built",
                      "manifest": str(manifest_path), "deployment_eligible": False,
                      "flash_authorized": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"E1.35b blocked: {exc}", file=sys.stderr)
        raise SystemExit(2)
