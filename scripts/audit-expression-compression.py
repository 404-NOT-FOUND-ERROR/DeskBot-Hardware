#!/usr/bin/env python3
"""Audit lossless EAF encodings against the current package and player anchors."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "tmp/expression-e1-3b-20261020"
OFFICIAL_PACKAGE = ROOT / "tmp/remote-esp-vocat-official-build/b-events-v1/expression_assets.bin"
OFFICIAL_SOURCE = ROOT / "tmp/remote-esp-vocat/examples/xiaozhi-esp32"
SHY_EAF = ROOT / "tmp/expression-e1-2-20261020/shy_legacy.eaf"
SHY_REPORT = ROOT / "tmp/expression-e1-2-20261020/candidate-report.json"
O2F2 = ROOT / "tmp/o2f2-o2d-readonly-20261018"
O2F2_APP = O2F2 / "build-mapped/xiaozhi.bin"
O2F2_BACKUP = ROOT / "backups/o2f2-accepted-20261018/body-app-0x10000.bin"
O2F2_MAP = O2F2 / "build-mapped/xiaozhi.map"
O2F2_PACKAGE = O2F2 / "build-mapped/expression_assets.bin"
O2F2_PARTITION_TABLE = O2F2 / "build-mapped/partition_table/partition-table.bin"
PLAYER_ROOT = O2F2 / "source/managed_components/espressif2022__esp_emote_gfx"
PLAYER_DECODER = PLAYER_ROOT / "src/lib/eaf/gfx_eaf_dec.c"
PLAYER_ANIM = PLAYER_ROOT / "src/widget/gfx_anim.c"
AUDIT_MODULE_PATH = ROOT / "scripts/audit-expression-resources.py"
PACKAGE_MODULE_PATH = ROOT / "scripts/audit-expression-package-candidate.py"

EXPECTED_OFFICIAL_SHA256 = "56b39e7fdd14b9dd0de9b22d3aedc9b04383376dbdef70925675f639d763cd5b"
EXPECTED_SHY_SHA256 = "4dd57777598dc1517fcf770fd859894567fbaa434373ef604e4d080e9331a5b1"
EXPECTED_SHY_RGBA_SHA256 = "97d5286ff3ef5afb77b15feaaebc05c61002ffb70467fae0818bab17344d34ea"
EXPECTED_APP_SHA256 = "4be89c2533909d06e9a8ef43a2aaa67ce9ec1e5a70a762f2827bf4388ac880a5"
EXPECTED_DECODER_SHA256 = "61931bc0f6f8c1fe28e7efd491a0b15ca659267c285f9a913aeff23dd94f6c96"
EXPECTED_ANIM_SHA256 = "95aa86482b7dec9616c5db5d14598004ae5769ed2e56a1ba22978030906d7c0f"
EXPECTED_PARTITION_TABLE_SHA256 = "f71d23804957dc85d36554b326a3ace9510ac07a2859234e07dd2b0081a2b46c"
EXPECTED_SOURCE_COMMIT = "46d427255756fe2a148777ea3f256b615b52e160"
PARTITION_BYTES = 6000 * 1024


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load audit helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AUDIT = _load_module(AUDIT_MODULE_PATH, "deskbot_expression_resource_audit_e13b")
PACKAGE_AUDIT = _load_module(PACKAGE_MODULE_PATH, "deskbot_expression_package_audit_e13b")
from protocol.eaf_compression import transcode_legacy_eaf
from protocol.eaf_encode import encode_rgba_frames
from protocol.eaf_preview import DecodedAnimation, decode_eaf


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _write_immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        if not path.is_file() or sha256_file(path) != sha256_bytes(raw):
            raise ValueError(f"refusing to overwrite different evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _decode_bytes(raw: bytes, scratch: Path, label: str) -> DecodedAnimation:
    path = scratch / f"{label}.eaf"
    path.write_bytes(raw)
    return decode_eaf(path, fps=25)


def _verify_player_anchor() -> dict[str, Any]:
    decoder_raw = PLAYER_DECODER.read_bytes()
    anim_raw = PLAYER_ANIM.read_bytes()
    app_raw = O2F2_APP.read_bytes()
    backup_raw = O2F2_BACKUP.read_bytes()
    map_text = O2F2_MAP.read_text(encoding="utf-8", errors="replace")
    if sha256_file(O2F2_APP) != EXPECTED_APP_SHA256 or sha256_file(O2F2_BACKUP) != EXPECTED_APP_SHA256:
        raise ValueError("O2f2 App or accepted backup differs from the current baseline anchor")
    if app_raw != backup_raw:
        raise ValueError("O2f2 App and accepted backup are not byte-identical")
    if sha256_file(O2F2_PACKAGE) != EXPECTED_OFFICIAL_SHA256:
        raise ValueError("O2f2 built expression package differs from the official package anchor")
    if sha256_file(O2F2_PARTITION_TABLE) != EXPECTED_PARTITION_TABLE_SHA256:
        raise ValueError("O2f2 built partition table differs from the current baseline anchor")
    if sha256_bytes(decoder_raw) != EXPECTED_DECODER_SHA256 or sha256_bytes(anim_raw) != EXPECTED_ANIM_SHA256:
        raise ValueError("O2f2 EAF player source differs from the audited 2.1.0 source anchor")
    if "eaf_decode_block" not in map_text or "gfx_anim_create" not in map_text:
        raise ValueError("O2f2 link map does not confirm the audited EAF player symbols")

    decoder = decoder_raw.decode("utf-8", errors="replace")
    anim = anim_raw.decode("utf-8", errors="replace")
    required = (
        "eaf_register_decoder(EAF_ENCODING_RLE, eaf_decode_rle)",
        "eaf_register_decoder(EAF_ENCODING_HUFFMAN, eaf_decode_huffman_rle)",
        "eaf_register_decoder(EAF_ENCODING_HUFFMAN_DIRECT, eaf_decode_huffman)",
        "decoded_data[decoded_pos++] = current_node->symbol",
        "out_size = width * block_height",
        "memset(output_buffer, single_symbol, decoded_size)",
        "4-bit depth not supported",
    )
    missing = [token for token in required if token not in decoder]
    if missing:
        raise ValueError(f"anchored EAF decoder no longer contains expected behavior: {missing}")
    four_bit_allocation = "pixel_buffer_size = frame_width * (block_height + (block_height % 2)) / 2"
    if four_bit_allocation not in anim:
        raise ValueError("anchored animation renderer no longer matches the audited 4-bit allocation")

    return {
        "app": {"path": str(O2F2_APP.relative_to(ROOT)).replace("\\", "/"),
                "size_bytes": len(app_raw), "sha256": sha256_bytes(app_raw),
                "matches_accepted_backup": True,
                "expression_package_matches_official_anchor": True,
                "partition_table_sha256": EXPECTED_PARTITION_TABLE_SHA256,
                "link_map_sha256": sha256_file(O2F2_MAP),
                "link_map_contains_eaf_decode_block_and_gfx_anim_create": True},
        "component": {"path": str(PLAYER_ROOT.relative_to(ROOT)).replace("\\", "/"),
                      "version": "2.1.0 (O2f2 dependency lock/source audit)",
                      "decoder_sha256": sha256_bytes(decoder_raw),
                      "animation_renderer_sha256": sha256_bytes(anim_raw),
                      "registered_encodings": [0, 1, 3]},
        "encoding_8bit": {
            "status": "registered_and_host_round_trip_checked_not_device_playback_verified",
            "notes": [
                "encoding 0 routes to RLE; 1 routes to Huffman then RLE; 3 routes to direct Huffman",
                "O2f2 map contains eaf_decode_block and gfx_anim_create",
                "the firmware Huffman decoder writes decoded symbols before its final size check; only generated, bounded, host-round-trip-verified bytes are considered here",
            ],
        },
        "bit_depth_4": {
            "status": "not_device_eligible_without_player_bounds_validation",
            "decoder_advertised_capacity_bytes": "width * block_height",
            "gfx_anim_allocation_bytes": "width * (block_height + (block_height % 2)) / 2",
            "additional_risk": "single-symbol empty Huffman stream fills the advertised capacity; decoder writes have no per-symbol capacity guard",
            "frame_decode_api": "eaf_frame_decode explicitly warns that 4-bit depth is unsupported",
            "required_before_any_device_candidate": "bound and test the exact gfx_anim decode path with a C harness or fix the isolated player buffer contract, then rebuild and regression-test separately",
        },
    }


def _candidate_index(official_index: dict[str, Any]) -> dict[str, Any]:
    candidate = json.loads(json.dumps(official_index))
    candidate["emoji_collection"].append({
        "name": "shy_candidate",
        "file": "shy_candidate.eaf",
        "eaf": {"loop": True, "fps": 25},
    })
    return candidate


def _package_variant(official_members: dict[str, bytes], official_index_map: dict[str, str],
                    variant_eaf: bytes) -> dict[str, Any]:
    original_index = json.loads(official_members["index.json"])
    candidate_index = _candidate_index(original_index)
    members = dict(official_members)
    members["index.json"] = (json.dumps(candidate_index, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    members["shy_candidate.eaf"] = variant_eaf
    packed = PACKAGE_AUDIT.build_legacy_package(members)
    parsed = AUDIT.parse_package_bytes(packed, modern=False)
    if parsed["members"] != members:
        raise ValueError("candidate package round trip changed one or more members")
    for name, raw in official_members.items():
        if name != "index.json" and parsed["members"].get(name) != raw:
            raise ValueError(f"compression candidate changed official member: {name}")
    if PACKAGE_AUDIT._index_semantics(candidate_index) != official_index_map | {"shy_candidate": "shy_candidate.eaf"}:
        raise ValueError("compression candidate changed an official semantic binding")
    return {
        "sha256": sha256_bytes(packed),
        "size_bytes": len(packed),
        "remaining_partition_bytes": PARTITION_BYTES - len(packed),
        "overflow_bytes": max(0, len(packed) - PARTITION_BYTES),
        "fits_partition": len(packed) <= PARTITION_BYTES,
        "member_count": parsed["header"]["member_count"],
        "legacy_package_checksum_valid": parsed["header"]["checksum_valid"],
        "official_non_index_members_byte_identical": True,
        "official_semantics_unchanged": True,
        "package_round_trip_valid": True,
    }


def _quality_metrics(reference: tuple[bytes, ...], candidate: tuple[bytes, ...]) -> dict[str, Any]:
    if len(reference) != len(candidate):
        raise ValueError("quality comparison frame count differs")
    visible_pixels = nonblack_pixels = alpha_mismatch = channel_count = 0
    total_error = visible_error = nonblack_error = 0
    max_channel_error = 0
    for expected, actual in zip(reference, candidate):
        if len(expected) != len(actual) or len(expected) % 4:
            raise ValueError("quality comparison frame dimensions differ")
        for offset in range(0, len(expected), 4):
            expected_alpha, actual_alpha = expected[offset + 3], actual[offset + 3]
            if bool(expected_alpha) != bool(actual_alpha):
                alpha_mismatch += 1
            is_visible = expected_alpha > 0
            if is_visible:
                visible_pixels += 1
                if expected[offset] or expected[offset + 1] or expected[offset + 2]:
                    nonblack_pixels += 1
            for channel in range(3):
                difference = abs(expected[offset + channel] - actual[offset + channel])
                total_error += difference
                if is_visible:
                    visible_error += difference
                if (is_visible and (expected[offset] or expected[offset + 1] or expected[offset + 2])):
                    nonblack_error += difference
                max_channel_error = max(max_channel_error, difference)
                channel_count += 1
    return {
        "mean_rgb_channel_absolute_error_all_pixels": round(total_error / max(1, channel_count), 6),
        "mean_rgb_channel_absolute_error_visible_pixels": round(visible_error / max(1, visible_pixels * 3), 6),
        "mean_rgb_channel_absolute_error_nonblack_foreground": round(nonblack_error / max(1, nonblack_pixels * 3), 6),
        "max_rgb_channel_absolute_error": max_channel_error,
        "alpha_presence_mismatch_pixels": alpha_mismatch,
        "visible_pixels_in_reference": visible_pixels,
        "nonblack_foreground_pixels_in_reference": nonblack_pixels,
        "comparison_basis": "device-RGB565-expanded host frames; transparency mismatch counted separately",
        "e1_2_mean_error_limit": 12,
        "e1_2_limit_all_pixels_met": total_error / max(1, channel_count) <= 12,
        "note": "the E1.2 limit is a broad mean-only threshold, not a user-approved quantization acceptance or a perceptual metric",
    }


def audit_compression(output: Path = OUTPUT, *, write_outputs: bool = True) -> dict[str, Any]:
    output = output.resolve()
    output.relative_to((ROOT / "tmp").resolve())

    official_raw = OFFICIAL_PACKAGE.read_bytes()
    if sha256_bytes(official_raw) != EXPECTED_OFFICIAL_SHA256:
        raise ValueError("official package differs from the E1.3 immutable anchor")
    official = AUDIT.parse_package_bytes(official_raw, modern=False)
    if PACKAGE_AUDIT.build_legacy_package(official["members"]) != official_raw:
        raise ValueError("local packer does not reproduce the official package byte-for-byte")
    if AUDIT.git_commit(OFFICIAL_SOURCE) != EXPECTED_SOURCE_COMMIT:
        raise ValueError("official source commit differs from the E1.3 source anchor")
    official_index = json.loads(official["members"]["index.json"])
    official_semantics = PACKAGE_AUDIT._index_semantics(official_index)
    emote_map = {item["emote"]: item["src"] for item in AUDIT.read_json(
        OFFICIAL_SOURCE / "main/boards/esp_vocat/assets/360_360/emote.json")}
    if official_semantics != emote_map:
        raise ValueError("official package index differs from the anchored source semantics")
    if "shy_candidate" in official_semantics or "shy_candidate.eaf" in official["members"]:
        raise ValueError("candidate identity already exists in the official package")

    source_eaf = SHY_EAF.read_bytes()
    if sha256_bytes(source_eaf) != EXPECTED_SHY_SHA256:
        raise ValueError("E1.2 visually accepted candidate changed since its immutable anchor")
    source_report = json.loads(SHY_REPORT.read_text(encoding="utf-8"))
    if source_report.get("eaf", {}).get("sha256") != EXPECTED_SHY_SHA256:
        raise ValueError("E1.2 candidate report does not match the immutable EAF anchor")
    player = _verify_player_anchor()

    baseline_info = AUDIT.parse_eaf_bytes(source_eaf, fps=25)
    expected_profile = (188, [{"width": 360, "height": 360}], [0])
    actual_profile = (baseline_info["image_frame_count"], baseline_info["dimensions"], baseline_info["encodings"])
    if actual_profile != expected_profile:
        raise ValueError(f"E1.2 profile changed: expected {expected_profile}, found {actual_profile}")

    baseline_package_bytes = len(official_raw)
    official_remaining = PARTITION_BYTES - baseline_package_bytes
    variants: list[dict[str, Any]] = []
    variant_bytes: dict[str, bytes] = {}
    quantized_summary: dict[str, Any] | None = None
    quantized_bytes: bytes | None = None
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".e1-3b-scratch-", dir=output) as scratch_name:
        scratch = Path(scratch_name)
        baseline_decoded = _decode_bytes(source_eaf, scratch, "baseline")
        baseline_hashes = [sha256_bytes(frame) for frame in baseline_decoded.frames]
        for name, encoding in (("encoding_0", 0), ("encoding_1", 1), ("encoding_3", 3), ("best", "best")):
            candidate_eaf = transcode_legacy_eaf(source_eaf, encoding=encoding, bit_depth=8, deduplicate=True)
            parsed_eaf = AUDIT.parse_eaf_bytes(candidate_eaf, fps=25)
            decoded = _decode_bytes(candidate_eaf, scratch, name)
            decoded_hashes = [sha256_bytes(frame) for frame in decoded.frames]
            frames_match = (
                decoded.width == baseline_decoded.width
                and decoded.height == baseline_decoded.height
                and decoded.bit_depth == baseline_decoded.bit_depth == 8
                and decoded.frame_rate == baseline_decoded.frame_rate == 25
                and decoded_hashes == baseline_hashes
                and len(decoded.frames) == len(baseline_decoded.frames) == 188
            )
            if not frames_match:
                raise ValueError(f"{name} changed decoded pixels, dimensions, bit depth, timing, or frame order")
            package = _package_variant(official["members"], official_semantics, candidate_eaf)
            variant_bytes[name] = candidate_eaf
            variants.append({
                "id": name,
                "requested_encoding": encoding,
                "actual_encodings": parsed_eaf["encodings"],
                "eaf_sha256": sha256_bytes(candidate_eaf),
                "eaf_size_bytes": len(candidate_eaf),
                "unique_stored_frame_payloads": len(set(PACKAGE_AUDIT._eaf_frame_payloads(candidate_eaf))),
                "frame_count": parsed_eaf["image_frame_count"],
                "decoded_frame_sha256_sequence_matches_source": True,
                "decoded_dimensions_and_fps_match_source": True,
                "full_package": package,
            })

        rgba_raw = (ROOT / "tmp/expression-e1-2-20261020/shy.rgba").read_bytes()
        if sha256_bytes(rgba_raw) != EXPECTED_SHY_RGBA_SHA256:
            raise ValueError("E1.2 original RGBA frames differ from their immutable source anchor")
        frame_bytes = baseline_decoded.width * baseline_decoded.height * 4
        if len(rgba_raw) != frame_bytes * len(baseline_decoded.frames):
            raise ValueError("E1.2 source RGBA byte length does not match the accepted 188-frame profile")
        rgba_frames = tuple(rgba_raw[offset:offset + frame_bytes]
                            for offset in range(0, len(rgba_raw), frame_bytes))
        if len(rgba_frames) != 188:
            raise ValueError("E1.2 source RGBA does not contain exactly 188 frames")
        quantized_8 = encode_rgba_frames(rgba_frames, baseline_decoded.width, baseline_decoded.height,
                                         block_height=32, palette_colors=15)
        quantized_4 = transcode_legacy_eaf(quantized_8, encoding="best", bit_depth=4, deduplicate=True)
        quantized_decoded = _decode_bytes(quantized_4, scratch, "quantized-4bit-15color")
        quality = _quality_metrics(baseline_decoded.frames, quantized_decoded.frames)
        if (quantized_decoded.width, quantized_decoded.height, quantized_decoded.frame_rate,
                len(quantized_decoded.frames), quantized_decoded.bit_depth) != (
                baseline_decoded.width, baseline_decoded.height, baseline_decoded.frame_rate, 188, 4):
            raise ValueError("15-color 4-bit host decode changed candidate geometry or frame sequence")
        quantized_package = _package_variant(official["members"], official_semantics, quantized_4)
        quantized_bytes = quantized_4
        quantized_matches_source = [sha256_bytes(frame) for frame in quantized_decoded.frames] == baseline_hashes
        quantized_summary = {
            "id": "reduced_palette_4bit_15color_best",
            "source_conversion": "re-quantize E1.2 RGBA to transparency plus at most 15 RGB555 colors, then pack indices 4bpp",
            "actual_encodings": AUDIT.parse_eaf_bytes(quantized_4, fps=25)["encodings"],
            "eaf_sha256": sha256_bytes(quantized_4),
            "eaf_size_bytes": len(quantized_4),
            "full_package": quantized_package,
            "quality_vs_visually_accepted_e1_2": quality,
            "decoded_frame_sha256_sequence_matches_e1_2": quantized_matches_source,
            "visual_acceptance": ("host frames exactly match visually accepted E1.2; device rendering unverified"
                                  if quantized_matches_source else "new palette needs separate visual acceptance"),
            "device_eligible": False,
            "reason": "O2f2 player 4-bit decode write capacity is not proven safe; this reduced-palette candidate is host-only even if its full package fits",
        }

    all_size_candidates = variants + ([quantized_summary] if quantized_summary is not None else [])
    smallest = min(all_size_candidates, key=lambda item: (item["full_package"]["size_bytes"], item["eaf_size_bytes"], item["id"]))
    smallest_lossless = min(variants, key=lambda item: (item["full_package"]["size_bytes"], item["eaf_size_bytes"], item["id"]))
    eligible = next((item for item in variants if item["full_package"]["fits_partition"]), None)
    result = "capacity_candidate_found_playback_unverified" if eligible else "lossless_8bit_capacity_rejected_4bit_host_only"
    best_output = output / "candidate/shy-smallest-lossless-8bit-device-playback-unverified-do-not-flash.eaf"
    quantized_output = output / "candidate/shy-quantized-4bit-15color-host-only-do-not-flash.eaf"
    report_path = output / "compression-audit-v1.json"
    report = {
        "schema": "deskbot.expression_compression_audit/v1",
        "result": result,
        "hardware_accessed": False,
        "firmware_changed": False,
        "device_partition_read": False,
        "device_package_eligible": False,
        "lossless_8bit_visual_status": "identical decoded frame SHA-256 sequence to the E1.2 visually accepted source",
        "baseline": {
            "official_package_sha256": sha256_bytes(official_raw),
            "official_package_size_bytes": baseline_package_bytes,
            "partition_bytes": PARTITION_BYTES,
            "official_remaining_bytes": official_remaining,
            "official_semantic_count": len(official_semantics),
            "official_member_count": official["header"]["member_count"],
            "source_commit": EXPECTED_SOURCE_COMMIT,
        },
        "candidate_source": {
            "eaf_sha256": sha256_bytes(source_eaf),
            "eaf_size_bytes": len(source_eaf),
            "frame_count": baseline_info["image_frame_count"],
            "dimensions": baseline_info["dimensions"],
            "fps": 25,
            "source_rgba_sha256": EXPECTED_SHY_RGBA_SHA256,
        },
        "player_anchor": player,
        "lossless_8bit_variants": variants,
        "reduced_palette_4bit_host_only_candidate": quantized_summary,
        "smallest_variant": {
            "id": smallest["id"],
            "eaf_sha256": smallest["eaf_sha256"],
            "eaf_size_bytes": smallest["eaf_size_bytes"],
            "full_package_sha256": smallest["full_package"]["sha256"],
            "full_package_size_bytes": smallest["full_package"]["size_bytes"],
            "remaining_partition_bytes": smallest["full_package"]["remaining_partition_bytes"],
            "overflow_bytes": smallest["full_package"]["overflow_bytes"],
            "saved_against_lossless_encoding_0_package_bytes": variants[0]["full_package"]["size_bytes"] - smallest["full_package"]["size_bytes"],
            "saved_against_original_candidate_eaf_bytes": len(source_eaf) - smallest["eaf_size_bytes"],
            "device_eligible": False,
            "preserved_eaf": (str(best_output.relative_to(ROOT)).replace("\\", "/")
                              if smallest["id"] in {item["id"] for item in variants}
                              else str(quantized_output.relative_to(ROOT)).replace("\\", "/")),
        },
        "smallest_lossless_8bit_variant": {
            "id": smallest_lossless["id"],
            "eaf_sha256": smallest_lossless["eaf_sha256"],
            "eaf_size_bytes": smallest_lossless["eaf_size_bytes"],
            "full_package_sha256": smallest_lossless["full_package"]["sha256"],
            "full_package_size_bytes": smallest_lossless["full_package"]["size_bytes"],
            "remaining_partition_bytes": smallest_lossless["full_package"]["remaining_partition_bytes"],
            "overflow_bytes": smallest_lossless["full_package"]["overflow_bytes"],
            "preserved_eaf": str(best_output.relative_to(ROOT)).replace("\\", "/"),
        },
        "four_bit_research": {
            "host_decoded": True,
            "device_tested": False,
            "reason": "15-color 4-bit candidate has measured host quality and capacity; the current O2f2 renderer/player has no proven bounded 4-bit decode contract",
            "device_eligible": False,
            "next_required_gate": "build a host C harness for the exact gfx_anim/eaf_decode_block path and verify candidate write sizes and canaries before considering any device candidate",
        },
        "validation": {
            "official_package_reproduced_byte_for_byte": True,
            "o2f2_app_matches_accepted_backup": True,
            "o2f2_map_confirms_player_symbols": True,
            "all_variants_host_decoded": True,
            "all_variant_frame_sha_sequences_match": True,
            "all_full_packages_round_trip_checked": True,
            "all_official_non_index_members_unchanged": True,
            "all_official_semantics_unchanged": True,
            "all_lossless_8bit_variants_fit_partition": all(item["full_package"]["fits_partition"] for item in variants),
            "reduced_palette_4bit_host_candidate_full_package_measured": quantized_summary is not None,
            "device_playback_verified": False,
        },
        "next_gate": (
            "no device-eligible E2 package: 8-bit lossless variants exceed capacity and the 4-bit package is host-only; build a bounded player C harness before any player/device change, otherwise discuss a separately approved resource strategy"
            if eligible is None else
            "capacity is only a host gate; perform an isolated firmware resource build and source/layout review before requesting a separately confirmed E2 flash"
        ),
    }
    if write_outputs:
        _write_immutable(best_output, variant_bytes[smallest_lossless["id"]])
        if quantized_bytes is not None:
            _write_immutable(quantized_output, quantized_bytes)
        _write_immutable(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    try:
        report = audit_compression(args.output)
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        print(json.dumps({"result": "rejected", "hardware_accessed": False, "error": str(error)},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({
        "result": report["result"],
        "device_package_eligible": report["device_package_eligible"],
        "official_remaining_bytes": report["baseline"]["official_remaining_bytes"],
        "variants": [{"id": item["id"], "encoding": item["actual_encodings"],
                      "eaf_bytes": item["eaf_size_bytes"],
                      "package_bytes": item["full_package"]["size_bytes"],
                      "overflow_bytes": item["full_package"]["overflow_bytes"],
                      "fits": item["full_package"]["fits_partition"]} for item in report["lossless_8bit_variants"]],
        "smallest_variant": report["smallest_variant"],
        "smallest_lossless_8bit_variant": report["smallest_lossless_8bit_variant"],
        "reduced_palette_4bit_host_only_candidate": report["reduced_palette_4bit_host_only_candidate"],
        "report": str((args.output / "compression-audit-v1.json").resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
