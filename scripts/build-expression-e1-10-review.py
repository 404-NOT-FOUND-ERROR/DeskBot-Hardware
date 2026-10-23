#!/usr/bin/env python3
"""E1.10 offline semantic alternatives and lossless large-asset audit.

Only writes an isolated, immutable project artifact directory. It never builds
firmware, opens a serial port, edits official assets, or approves deployment.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocol.eaf_compression import transcode_legacy_eaf
from protocol.eaf_preview import compose_display_frame, decode_eaf, write_rgba_atlas

OUTPUT = ROOT / "artifacts/expression-e1-10-review-20261022"
TEMPLATE = ROOT / "scripts/expression-e1-10-review.template.html"
PARTITION_BYTES = 6_144_000
INPUTS = {
    "official_package": ("tmp/remote-esp-vocat-official-build/b-events-v1/expression_assets.bin",
                         "56b39e7fdd14b9dd0de9b22d3aedc9b04383376dbdef70925675f639d763cd5b"),
    "matrix": ("artifacts/expression-binding-matrix-e1-9-20261022/matrix.json",
               "f2867059513910d91d21a534b5953869dd5312eadcfb961494fc5ae6d29d90c1"),
    "bindings": ("artifacts/expression-binding-e1-4a-20261021/bindings.json",
                 "98d57c67e52e240cb7ef0ed8c99cfc088f834fbf662424cd48c2c03c50bd4921"),
    "release_audit": ("artifacts/expression-node-workbench-e1-4d-20261021/release-audit.json",
                      "89b99c644e5bfec536ea331280489f79d9a10f15071bf4795b2f87d717ae08af"),
    "shy_original": ("tmp/expression-e1-2-20261020/shy_legacy.eaf",
                     "4dd57777598dc1517fcf770fd859894567fbaa434373ef604e4d080e9331a5b1"),
    "shy_four_bit": ("tmp/expression-e1-3b-20261020/candidate/shy-quantized-4bit-15color-host-only-do-not-flash.eaf",
                     "7144c594e066c2411e43a492c7ff8f7c4befb0ac282f05250a0656f1b637c9fd"),
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PACKAGE = load_module("e110_package", ROOT / "scripts/audit-expression-package-candidate.py")
AUDIT = PACKAGE.AUDIT


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def safe_output(path: Path) -> Path:
    artifacts = (ROOT / "artifacts").resolve()
    resolved = path.resolve()
    if (path.is_symlink() or resolved.parent != artifacts
            or not resolved.name.startswith("expression-e1-10-")):
        raise ValueError("output must be a direct expression-e1-10-* child of project artifacts")
    return resolved


def immutable(path: Path, raw: bytes, output: Path) -> None:
    path.resolve().relative_to(output)
    if path.is_symlink():
        raise ValueError(f"refusing symlink output: {path}")
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"refusing to overwrite different evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def read_pinned(inputs: dict = INPUTS) -> dict[str, bytes]:
    result = {}
    for name, (relative, expected) in inputs.items():
        raw = (ROOT / relative).read_bytes()
        if sha(raw) != expected:
            raise ValueError(f"pinned input drift: {name}")
        result[name] = raw
    return result


def frame_payloads(raw: bytes) -> list[bytes]:
    AUDIT.parse_eaf_bytes(raw)
    return list(PACKAGE._eaf_frame_payloads(raw))


def pack_frames(frames: list[bytes], magic: bytes = b"\x89EAF", *, dedup: bool = True) -> bytes:
    if not frames or len(frames) > 4096:
        raise ValueError("invalid frame count")
    table, storage = bytearray(), bytearray()
    locations: dict[bytes, tuple[int, int]] = {}
    for frame in frames:
        location = locations.get(frame) if dedup else None
        if location is None:
            location = len(frame), len(storage)
            locations[frame] = location
            storage.extend(frame)
        table.extend(struct.pack("<II", *location))
    payload = table + storage
    return magic + struct.pack("<III", len(frames), sum(payload) & 0xFFFFFFFF, len(payload)) + payload


def transcode_with_metadata(raw: bytes, *, bit_depth: int = 8) -> bytes:
    """Preserve _C records and sequence; reuse the existing image-only codec."""
    result = []
    for frame in frame_payloads(raw):
        if frame[2:4] == b"_C":
            result.append(frame)
        elif frame[2:4] == b"_S":
            single = pack_frames([frame], raw[:4])
            encoded = transcode_legacy_eaf(single, encoding="best", bit_depth=bit_depth)
            result.extend(frame_payloads(encoded))
        else:
            raise ValueError("unsupported frame kind")
    encoded = pack_frames(result, raw[:4])
    AUDIT.parse_eaf_bytes(encoded)
    return encoded


def metadata_signature(raw: bytes) -> list[dict]:
    return [{"stored_index": i, "sha256": sha(frame)}
            for i, frame in enumerate(frame_payloads(raw)) if frame[2:4] == b"_C"]


def package_summary(raw: bytes, baseline: bytes) -> dict:
    parsed = AUDIT.parse_package_bytes(raw, modern=False)
    return {"bytes": len(raw), "sha256": sha(raw), "delta_bytes": len(raw) - len(baseline),
            "remaining_bytes": PARTITION_BYTES - len(raw), "fits_partition": len(raw) <= PARTITION_BYTES,
            "member_count": parsed["header"]["member_count"], "round_trip_valid": True,
            "deployment_eligible": False, "firmware_built": False}


def replace_semantic(members: dict[str, bytes], semantic: str, resource: str,
                     *, new_resource: bytes | None = None) -> tuple[bytes, dict]:
    """Change one file reference, never the loop/FPS or any other index field."""
    original = json.loads(members["index.json"])
    changed = copy.deepcopy(original)
    entries = [item for item in changed["emoji_collection"] if item["name"] == semantic]
    if len(entries) != 1 or not resource.endswith(".eaf") or Path(resource).name != resource:
        raise ValueError("invalid semantic or resource identity")
    candidate = dict(members)
    if resource in candidate and new_resource is not None:
        raise ValueError("candidate must not overwrite an official resource")
    if new_resource is not None:
        candidate[resource] = new_resource
    if resource not in candidate:
        raise ValueError("resource is absent")
    before = PACKAGE._index_semantics(original)
    if before[semantic] == resource:
        raise ValueError("semantic replacement is a no-op")
    entries[0]["file"] = resource
    candidate["index.json"] = (json.dumps(changed, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    after = PACKAGE._index_semantics(changed)
    if [name for name in before if before[name] != after[name]] != [semantic]:
        raise ValueError("replacement changed more than one semantic")
    for old, new in zip(original["emoji_collection"], changed["emoji_collection"]):
        if {k: v for k, v in old.items() if k != "file"} != {k: v for k, v in new.items() if k != "file"}:
            raise ValueError("replacement changed timing or other metadata")
    packed = PACKAGE.build_legacy_package(candidate)
    if AUDIT.parse_package_bytes(packed, modern=False)["members"] != candidate:
        raise ValueError("package round-trip failed")
    # Exact rollback uses the original serialized index, not a reserialization.
    restored = {name: data for name, data in candidate.items() if name in members}
    restored["index.json"] = members["index.json"]
    rollback = PACKAGE.build_legacy_package(restored)
    if rollback != PACKAGE.build_legacy_package(members):
        raise ValueError("exact rollback failed")
    return packed, {"changed_semantics": [semantic], "other_semantics_unchanged": True,
                    "loop_and_fps_unchanged": True, "official_resources_unchanged": True,
                    "resources_removed": [], "rollback_exact": True, "rollback_sha256": sha(rollback)}


def compare_frames(reference, candidate) -> dict:
    dimensions_equal = (reference.width, reference.height) == (candidate.width, candidate.height)
    equal = dimensions_equal and reference.frames == candidate.frames
    return {"image_frame_count": len(reference.frames), "candidate_image_frame_count": len(candidate.frames),
            "dimensions_equal": dimensions_equal, "all_decoded_rgba_frames_identical": equal,
            "reference_frame_hashes": [sha(frame) for frame in reference.frames],
            "candidate_frame_hashes": [sha(frame) for frame in candidate.frames],
            "comparison_basis": "host decoder RGB565-expanded RGBA; not physical LCD or runtime timing"}


def build(output: Path = OUTPUT) -> dict:
    output = safe_output(output)
    pinned = read_pinned()
    bindings = json.loads(pinned["bindings"])
    matrix = json.loads(pinned["matrix"])
    release = json.loads(pinned["release_audit"])
    actions = bindings["emotion_action_map"]
    if (bindings.get("source", {}).get("official_commit") != "46d427255756fe2a148777ea3f256b615b52e160"
            or matrix.get("source", {}).get("official_commit") != bindings["source"]["official_commit"]
            or release.get("deployment_eligible") is not False):
        raise ValueError("baseline source or release audit drift")
    if actions != release["mapping_comparison"]["official_emotion_action_map"]:
        raise ValueError("action mapping drift")
    official = pinned["official_package"]
    members = AUDIT.parse_package_bytes(official, modern=False)["members"]
    if PACKAGE.build_legacy_package(members) != official:
        raise ValueError("official package reproduction failed")
    if matrix["capacity"]["official_package_bytes"] != len(official):
        raise ValueError("E1.9 capacity drift")

    output.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}

    def put(name: str, raw: bytes):
        immutable(output / name, raw, output)
        files[name] = {"bytes": len(raw), "sha256": sha(raw)}

    def decode(raw: bytes, label: str, fps: int = 20):
        name = f"eaf/{label}.eaf"
        put(name, raw)
        return decode_eaf(output / name, fps=fps)

    semantic_plans = []
    preview = {}
    for label, raw, fps, layout in (
        ("happy", members["Happy.eaf"], 20, "eye"),
        ("shocked", members["shocked.eaf"], 20, "eye"),
        ("shy-original", pinned["shy_original"], 25, "full_screen"),
        ("shy-four-bit", pinned["shy_four_bit"], 25, "full_screen"),
    ):
        print(f"preview {label}", flush=True)
        animation = decode(raw, label, fps)
        atlas_path = output / "preview" / f"{label}.png"
        # Generate to a project-local scratch path; immutable publication follows.
        scratch = output / f".atlas-{label}.png"
        if scratch.is_symlink():
            raise ValueError("refusing symlink scratch")
        composed = (tuple(compose_display_frame(frame, animation.width, animation.height, object_kind="eye")
                          for frame in animation.frames) if layout == "eye" else animation.frames)
        atlas = write_rgba_atlas(scratch, composed, 360, 360)
        atlas_raw = scratch.read_bytes()
        put(f"preview/{label}.png", atlas_raw)
        scratch.unlink()
        preview[label] = {**atlas, "fps": fps, "layout": layout,
                          "data_url": "data:image/png;base64," + base64.b64encode(atlas_raw).decode("ascii")}
        del animation, composed

    shy_original = decode_eaf(output / "eaf/shy-original.eaf", fps=25)
    shy_four = decode_eaf(output / "eaf/shy-four-bit.eaf", fps=25)
    shy_equality = compare_frames(shy_original, shy_four)
    if not shy_equality["all_decoded_rgba_frames_identical"]:
        raise ValueError("anchored shy compression changed decoded frames")
    del shy_original, shy_four

    for identity, semantic, resource, raw, added, reason in (
        ("surprised-reuse-shocked", "surprised", "shocked.eaf", None, 0,
         "复用官方惊讶视觉作为对照；会让 surprised 与 shocked 共用画面，不是新的独立表情。"),
        ("embarrassed-shy-original", "embarrassed", "shy_candidate.eaf", pinned["shy_original"], len(pinned["shy_original"]),
         "全屏样稿不适合官方 eye_anim 镜像通道；仍需布局、FPS 和播放策略设计。"),
        ("embarrassed-shy-four-bit", "embarrassed", "shy_candidate.eaf", pinned["shy_four_bit"], len(pinned["shy_four_bit"]),
         "4-bit 主机画面等价，但容量通过不等于布局、物理遮罩或实机播放通过。"),
    ):
        packed, validation = replace_semantic(members, semantic, resource, new_resource=raw)
        name = f"packages/{identity}-offline-do-not-flash.bin"
        put(name, packed)
        semantic_plans.append({"id": identity, "semantic": semantic, "before": "Happy.eaf", "after": resource,
                               "added_resource_bytes": added, "shared_resource_bytes_freed": 0,
                               "index_delta_bytes": len(packed) - len(official) - added - (46 if raw is not None else 0),
                               "package_table_and_separator_delta_bytes": 46 if raw is not None else 0,
                               "action_before": actions.get(semantic), "action_after": actions.get(semantic),
                               "action_map_unchanged": True, "action_completion_observed": False,
                               "package": package_summary(packed, official) | {"path": name},
                               "validation": validation, "note_zh": reason,
                               "index_fps_policy": "keep official 20 FPS for byte-budget comparison; not approved candidate timing",
                               "requires_runtime_layout_change": raw is not None,
                               "visual_review_approved": False, "deployment_eligible": False})

    compression = []
    best_resources = {}
    for resource in ("insert.eaf", "eat.eaf"):
        print(f"compress {resource}", flush=True)
        source = members[resource]
        reference = decode(source, resource[:-4] + "-original")
        item = {"resource": resource, "source_bytes": len(source), "source_sha256": sha(source),
                "protected": True, "event_removed": False, "variants": []}
        for mode in ("dedup", "best-8bit", "best-4bit-lossless"):
            print(f"  {mode}", flush=True)
            try:
                encoded = (PACKAGE.deduplicate_eaf_frames(source) if mode == "dedup" else
                           transcode_with_metadata(source, bit_depth=4 if mode == "best-4bit-lossless" else 8))
            except ValueError as error:
                if mode != "best-4bit-lossless" or "quantization" not in str(error):
                    raise
                item["variants"].append({"id": mode, "status": "rejected_no_quantization_allowed",
                                         "reason": str(error), "deployment_eligible": False})
                continue
            decoded = decode(encoded, resource[:-4] + "-" + mode)
            equality = compare_frames(reference, decoded)
            metadata_equal = metadata_signature(source) == metadata_signature(encoded)
            if not equality["all_decoded_rgba_frames_identical"] or not metadata_equal:
                raise ValueError(f"lossless verification failed: {resource}/{mode}")
            changed_members = members | {resource: encoded}
            packed = PACKAGE.build_legacy_package(changed_members)
            if AUDIT.parse_package_bytes(packed, modern=False)["members"] != changed_members:
                raise ValueError("compressed package round-trip failed")
            package_path = f"packages/{resource[:-4]}-{mode}-offline-do-not-flash.bin"
            put(package_path, packed)
            item["variants"].append({"id": mode, "status": "host_lossless_verified",
                                     "bytes": len(encoded), "sha256": sha(encoded),
                                     "bytes_saved": len(source) - len(encoded),
                                     "eaf_info": AUDIT.parse_eaf_bytes(encoded),
                                     "frame_comparison": equality, "metadata_sequence_identical": metadata_equal,
                                     "index_bytes_unchanged": True, "official_other_members_unchanged": True,
                                     "package": package_summary(packed, official) | {"path": package_path},
                                     "device_playback_verified": False, "deployment_eligible": False})
            if len(encoded) < len(best_resources.get(resource, source)):
                best_resources[resource] = encoded
            del decoded
        compression.append(item)
        del reference

    combined_members = members | best_resources
    combined_package = PACKAGE.build_legacy_package(combined_members)
    put("packages/combined-lossless-offline-do-not-flash.bin", combined_package)
    combined = package_summary(combined_package, official)
    combined.update({"changed_resources": sorted(best_resources), "index_bytes_unchanged": True,
                     "bytes_saved": len(official) - len(combined_package),
                     "experiment_only": True, "not_combined_with_semantic_candidate": True})
    if AUDIT.parse_package_bytes(combined_package, modern=False)["members"] != combined_members:
        raise ValueError("combined package round-trip failed")

    # Re-read locked inputs before publishing the report.
    if read_pinned() != pinned:
        raise ValueError("inputs changed during generation")
    report = {
        "schema": "deskbot.expression_e1_10_offline_review/v1", "edition": "E1.10",
        "result": "offline_review_complete_no_deployment", "date": "2026-10-22",
        "source_commit": "46d427255756fe2a148777ea3f256b615b52e160",
        "safety": {"hardware_accessed": False, "serial_ports_accessed": [], "firmware_changed": False,
                   "firmware_built": False, "official_bindings_changed": False, "resources_deleted": False,
                   "nvs_changed": False, "partition_changed": False, "device_package_changed": False},
        "deployment_eligible": False,
        "inputs": {key: {"path": path, "sha256": digest} for key, (path, digest) in INPUTS.items()},
        "baseline": package_summary(official, official) | {"partition_bytes": PARTITION_BYTES},
        "semantic_plans": semantic_plans, "shy_comparison": shy_equality,
        "large_asset_compression": compression, "combined_compression": combined,
        "semantic_index_changes": {"surprised": {"before": "Happy.eaf", "candidate": "shocked.eaf",
                                                   "package_delta_bytes": next(item["package"]["delta_bytes"] for item in semantic_plans if item["id"] == "surprised-reuse-shocked"),
                                                   "eaf_bytes_released": 0, "recommendation": "not_recommended_redundant_visual"},
                                    "embarrassed": {"before": "Happy.eaf", "candidate": "shy_legacy.eaf",
                                                    "shared_happy_still_used": True, "recommendation": "needs_compact_distinct_asset"}},
        "official_emotion_action_map": actions,
        "visual_review": {"user_approved": False, "physical_lcd_mask_verified": False,
                          "c_renderer_verified_for_new_large_variants": False,
                          "preview_is_visual_only": True, "runtime_frame_timing_verified": False},
        "next_gates": ["用户只选一个语义方案，不同时叠加资源压缩", "冻结候选和发布锁、验证 C 播放器与物理圆屏裁切",
                       "隔离构建后核对实际构建包和回退身份", "明确授权后单分区实验；底座修复后完成整机回归"],
    }
    page_data = {"report": report, "preview": preview}
    embedded = json.dumps(page_data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__E110_DATA__", embedded)
    put("index.html", html.encode("utf-8"))
    put("README.txt", ("E1.10 离线语义候选与无损压缩评估。打开 index.html。\n"
                        "packages/*.bin 全部为主机实验包，不是固件、不能直接刷写。\n"
                        "原版/候选/压缩实验彼此隔离；官方文件、设备和 NVS 未改。\n").encode("utf-8"))
    report["files"] = files
    report["tooling_sha256"] = {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path.read_bytes()) for path in (
        Path(__file__), TEMPLATE, ROOT / "protocol/eaf_preview.py", ROOT / "protocol/eaf_compression.py",
        ROOT / "scripts/audit-expression-package-candidate.py", ROOT / "scripts/audit-expression-resources.py")}
    immutable(output / "report.json", json_bytes(report), output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    try:
        report = build(args.output)
    except (ValueError, OSError, KeyError, TypeError, struct.error) as error:
        print(json.dumps({"result": "rejected", "error": str(error), "deployment_eligible": False}, ensure_ascii=False))
        return 2
    print(json.dumps({"result": report["result"], "deployment_eligible": False,
                      "semantic_packages": [{"id": x["id"], **x["package"]} for x in report["semantic_plans"]],
                      "combined_compression": report["combined_compression"],
                      "report": str((args.output / "report.json").resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
