#!/usr/bin/env python3
"""Offline E1.34 encoding/crop/timing audit; no device or deployable packages.

The 13-entry grid is decoder-checked exploration. The retained full-face and
single-eye candidates also receive complete official C player/layout checks.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent.parent
sys.path.insert(0, str(ROOT))
from protocol.eaf_preprocess import preprocess_eaf  # noqa: E402
from protocol.eaf_preview import decode_eaf, write_rgba_atlas  # noqa: E402

OUTPUT = ROOT / "tmp/expression-e1-34-preprocess-20261029-final"
PUBLIC = ROOT / "artifacts/expression-e1-34-preprocess-20261029"
E133_REPORT = ROOT / "artifacts/expression-e1-33-import-20261029/report.json"
E133_NATIVE = ROOT / "tmp/expression-e1-33-import-20261029-final/native"
SOURCE_SHA256 = "467db1c5c912b4a7d0c2c280f3eaf158fb3c9e0fe633e2c7e47ad4b848b78293"
SOURCE_BYTES, SOURCE_FRAMES, SOURCE_FPS = 717_402, 188, 25
SOURCE_LOOP = [50, 150]
CROP = (10, 90, 160, 200)
PARTITION_BYTES, BASE_BYTES, APPEND_OVERHEAD = 6_144_000, 5_538_351, -147
ROLLBACK_SHA256 = "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


E133 = load_module(ROOT / "scripts/audit-expression-e1-33-import.py", "deskbot_e133_for_e134")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def immutable(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError(f"refusing to overwrite different evidence: {path}")
    if not path.exists():
        path.write_bytes(raw)


def safe_output(path):
    resolved = path.resolve()
    if resolved.parent != (ROOT / "tmp").resolve() or not resolved.name.startswith("expression-e1-34-"):
        raise ValueError("E1.34 output must be a direct stage directory under checkout tmp")
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
           for p in (path, *path.parents) if p.exists()):
        raise ValueError("E1.34 output cannot traverse links or junctions")
    return resolved


def sequence_sha(frames):
    return sha(b"".join(hashlib.sha256(frame).digest() for frame in frames))


def compare_frames(actual, expected):
    lengths_equal = len(actual) == len(expected) and all(len(a) == len(b) for a, b in zip(actual, expected))
    different = [i for i, (a, b) in enumerate(zip(actual, expected)) if a != b]
    return {"frame_count_equal": len(actual) == len(expected), "frame_sizes_equal": lengths_equal,
            "exact_sequence": lengths_equal and not different, "mismatch_frames": len(different),
            "first_mismatch_frame": different[0] if different else None,
            "rgba_sequence_sha256": sequence_sha(actual), "reference_sequence_sha256": sequence_sha(expected)}


def baseline_context():
    resources = load_module(E133.RESOURCE_SCRIPT, "deskbot_e134_resources")
    packer = load_module(E133.PACKAGE_SCRIPT, "deskbot_e134_packer")
    partition = E133.ROLLBACK.read_bytes()
    if len(partition) != PARTITION_BYTES or sha(partition) != ROLLBACK_SHA256:
        raise ValueError("E1.18 rollback partition hash/size mismatch")
    package = partition[:12 + struct.unpack_from("<I", partition, 8)[0]]
    if len(package) != BASE_BYTES or partition[BASE_BYTES:] != b"\xff" * (PARTITION_BYTES - BASE_BYTES):
        raise ValueError("E1.18 package length/padding mismatch")
    members = resources.parse_package_bytes(package, modern=False)["members"]
    if packer.build_legacy_package(members) != package:
        raise ValueError("E1.18 package cannot be reproduced byte-for-byte")
    index = json.loads(members["index.json"])
    if "shy_candidate.eaf" in members or any(e["name"] == "shy_candidate" for e in index["emoji_collection"]):
        raise ValueError("candidate identity is already present in E1.18")
    base = {"partition_bytes": PARTITION_BYTES, "package_bytes": len(package),
            "remaining_bytes": PARTITION_BYTES - len(package), "package_sha256": sha(package),
            "rollback_partition_sha256": sha(partition), "append_overhead_bytes": APPEND_OVERHEAD,
            "member_count": len(members),
            "member_sha256": {name: sha(raw) for name, raw in sorted(members.items())}}
    return resources, packer, members, index, base


def projection(raw, context):
    resources, packer, members, index, _ = context
    candidate_index = copy.deepcopy(index)
    candidate_index["emoji_collection"].append({"name": "shy_candidate", "file": "shy_candidate.eaf",
                                                "eaf": {"loop": True, "fps": SOURCE_FPS}})
    added = dict(members)
    added["index.json"] = (json.dumps(candidate_index, ensure_ascii=False, indent=4) + "\n").encode()
    added["shy_candidate.eaf"] = raw
    # Build and parse in RAM only; no flashable resource package is emitted.
    package = packer.build_legacy_package(added)
    parsed = resources.parse_package_bytes(package, modern=False)["members"]
    preserved = all(parsed[name] == value for name, value in members.items() if name != "index.json")
    existing_preserved = json.loads(parsed["index.json"])["emoji_collection"][:-1] == index["emoji_collection"]
    if not preserved or not existing_preserved or set(parsed) != set(members) | {"shy_candidate.eaf"}:
        raise ValueError("projection changed an existing member/semantic")
    if len(package) != BASE_BYTES + APPEND_OVERHEAD + len(raw):
        raise ValueError("append capacity overhead changed")
    return {"package_bytes": len(package), "package_sha256": sha(package),
            "remaining_bytes": PARTITION_BYTES - len(package), "fits_partition": len(package) <= PARTITION_BYTES,
            "existing_non_index_members_preserved": preserved, "existing_semantics_preserved": existing_preserved,
            "package_round_trip_pass": parsed == added, "package_emitted": False}


def player_context():
    player = load_module(E133.PLAYER_SCRIPT, "deskbot_e134_player")
    cached = json.loads((E133_NATIVE / "toolchain.json").read_text(encoding="utf-8"))
    anchors = {"player_source": player.RENDER.source_anchor_report(),
               "compression_source": player.RENDER.COMPRESSION._verify_player_anchor(),
               "harness_sha256": sha(player.HARNESS.read_bytes()),
               "renderer_harness_sha256": sha((PROJECT_ROOT / "tests/eaf_renderer_host/gfx_renderer_harness.c").read_bytes())}
    if cached["anchors"] != anchors:
        raise ValueError("retained official player source/harness anchors changed")
    dll = E133_NATIVE / "player-audit-e1-11.dll"
    if sha(dll.read_bytes()) != cached["toolchain"]["dll_sha256"]:
        raise ValueError("retained official player DLL hash changed")
    return player, dll, cached


def default_timer(player, raw, dll):
    native = player.PlayerNative(dll)
    opened = native.open(raw, 1, SOURCE_FPS, False, 0, 0, 0, 0xffff)
    trace = []
    try:
        for _ in range(SOURCE_FRAMES + 2):
            obs = native.tick(40)
            if not player.clean(obs):
                raise ValueError("default timer guard failure")
            trace.append({"current_frame": int(obs.current_frame), "one_done": int(obs.one_done),
                          "all_done": int(obs.all_done), "is_playing": int(obs.is_playing)})
    finally:
        closed = native.close()
        if not player.clean(closed) or closed.live_allocations:
            raise ValueError("default timer release/guard failure")
    # No trailing _C metadata in this research source. The unchanged product
    # clamps end to count-1, then treats that end as exclusive.
    expected = list(range(1, SOURCE_FRAMES)) + [SOURCE_FRAMES - 1] * 3
    if ([row["current_frame"] for row in trace] != expected or trace[-1]["one_done"] != 187
            or trace[-1]["all_done"] != 1 or trace[-1]["is_playing"] != 0):
        raise ValueError("default timer trace differs from the anchored player")
    return {"requested_end": 0xffff, "clamped_end": int(opened.end_frame), "end_exclusive": True,
            "prepared_image_range": [0, 186], "one_frame_done_count": 187, "all_frame_done_count": 1,
            "guards_and_release_clean": True, "last_image_index_not_scheduled": 187,
            "all_188_images_scheduled": False, "trace": trace}


def run_native(row, raw, output, player_info):
    _, dll, cached = player_info
    stage = output / "native" / row["id"]
    stage.mkdir(parents=True, exist_ok=True)
    immutable(stage / "candidate.eaf", raw)
    immutable(stage / "player.dll", dll.read_bytes())
    command = [sys.executable, str(Path(__file__).resolve()), "--render-worker", "--output", str(output),
               "--candidate-id", row["id"], "--candidate-sha", sha(raw), "--dll-sha", cached["toolchain"]["dll_sha256"]]
    child = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)
    immutable(stage / "worker.log", (child.stdout + child.stderr).encode())
    if child.returncode:
        raise RuntimeError(f"isolated C player failed ({row['id']}): " + child.stderr[-1600:])
    report = json.loads((stage / "result.json").read_text(encoding="utf-8"))
    if report["result"] != "pass" or report["candidate_sha256"] != row["eaf_sha256"]:
        raise ValueError("native worker result did not match candidate")
    return report


def worker(args):
    output = safe_output(args.output)
    if not re.fullmatch(r"[a-z0-9-]{1,80}", args.candidate_id or ""):
        raise ValueError("invalid native candidate ID")
    stage = output / "native" / args.candidate_id
    raw, dll = (stage / "candidate.eaf").read_bytes(), stage / "player.dll"
    if sha(raw) != args.candidate_sha or sha(dll.read_bytes()) != args.dll_sha:
        raise ValueError("native worker candidate/DLL hash mismatch")
    decoded = decode_eaf(stage / "candidate.eaf", fps=SOURCE_FPS)
    if (len(decoded.frames) != 188 or decoded.bit_depth != 8
            or (decoded.width, decoded.height) not in ((360, 360), (160, 200))):
        raise ValueError("native candidate geometry/frame count mismatch")
    player = load_module(E133.PLAYER_SCRIPT, "deskbot_e134_worker_player")
    renderer = E133.native_render(player, raw, decoded, dll, stage)
    timer = default_timer(player, raw, dll)
    passed = renderer["all_reference_frames_equal"] and renderer["all_guards_clean"] and renderer["explicit_range"]["renderer_accepts_range"]
    immutable(stage / "result.json", json_bytes({"result": "pass" if passed else "fail",
              "candidate_sha256": sha(raw), "renderer": renderer, "default_product_timer": timer}))
    return 0 if passed else 2


def timing_research():
    return [{"fps": fps, "frame_count": 188, "eaf_bytes": SOURCE_BYTES, "saved_bytes_by_fps_only": 0,
             "full_timeline_seconds": round(188 / fps, 6), "source_segment_frame_range": SOURCE_LOOP,
             "source_segment_seconds": [round(value / fps, 6) for value in SOURCE_LOOP],
             "preserves_original_timing": fps == 25, "frame_resampling_performed": False}
            for fps in (15, 20, 25, 30)]


def audit(output):
    output = safe_output(output)
    output.mkdir(parents=True, exist_ok=True)
    raw = E133.EAF.read_bytes()
    if sha(raw) != SOURCE_SHA256 or len(raw) != SOURCE_BYTES:
        raise ValueError("E1.33 candidate source hash/size drift")
    prior = json.loads(E133_REPORT.read_text(encoding="utf-8"))
    E133.validate_report(prior)
    source = decode_eaf(E133.EAF, fps=25)
    if (source.width, source.height, len(source.frames), source.bit_depth) != (360, 360, 188, 8):
        raise ValueError("source geometry changed")
    context, player_info = baseline_context(), player_context()
    variants, candidate_bytes = [], {}
    specs = [(enc, h, True) for enc in (0, 1, 3, "best") for h in (16, 32, 64)] + [("best", 32, False)]
    for enc, h, dedup in specs:
        label = f"lossless-{enc}-{h}" + ("-no-dedup" if not dedup else "")
        print("Preparing " + label, flush=True)
        processed = preprocess_eaf(raw, encoding=enc, block_height=h, deduplicate=dedup)
        path = output / "candidates" / (label + ".eaf")
        immutable(path, processed)
        decoded = decode_eaf(path, fps=25)
        pixels = compare_frames(decoded.frames, source.frames)
        if not pixels["exact_sequence"]:
            raise ValueError("lossless candidate changed source pixels: " + label)
        row = {"id": label, "encoding": enc, "block_height": h, "deduplicate_payloads": dedup,
               "crop": None, "eaf_bytes": len(processed), "eaf_sha256": sha(processed),
               "dimensions": [decoded.width, decoded.height], "frame_count": len(decoded.frames), "bit_depth": 8,
               "fps": 25, "pixel_comparison": pixels, "capacity": projection(processed, context),
               "native_checked": False, "deployment_eligible": False}
        variants.append(row)
        candidate_bytes[label] = processed
        print(json.dumps({"id": label, "bytes": len(processed), "exact_pixels": True, "fits": row["capacity"]["fits_partition"]}), flush=True)
    best = min(variants, key=lambda row: (row["eaf_bytes"], row["id"]))
    print("C full-frame/layout/timer audit: " + best["id"], flush=True)
    best["official_player"] = run_native(best, candidate_bytes[best["id"]], output, player_info)
    best["native_checked"] = True
    crop_raw = preprocess_eaf(raw, crop=CROP, block_height=32)
    crop_path = output / "candidates/research-single-eye.eaf"
    immutable(crop_path, crop_raw)
    decoded_crop = decode_eaf(crop_path, fps=25)
    x, y, w, h = CROP
    expected = tuple(b"".join(frame[(row * 360 + x) * 4:(row * 360 + x + w) * 4]
                              for row in range(y, y + h)) for frame in source.frames)
    crop = {"id": "research-single-eye", "crop": list(CROP), "encoding": "best", "block_height": 32,
            "eaf_bytes": len(crop_raw), "eaf_sha256": sha(crop_raw), "dimensions": [w, h], "frame_count": 188,
            "fps": 25, "bit_depth": 8, "pixel_comparison": compare_frames(decoded_crop.frames, expected),
            "lossless_to_full_source": False, "layout_id": "eye_anim", "visual_accepted": False,
            "capacity": projection(crop_raw, context), "native_checked": True, "deployment_eligible": False}
    if not crop["pixel_comparison"]["exact_sequence"]:
        raise ValueError("single-eye crop changed retained pixels")
    print("C full-frame/layout/timer audit: research-single-eye", flush=True)
    crop["official_player"] = run_native(crop, crop_raw, output, player_info)
    full_hash = {r["frame"]: r["actual_sha256"] for r in best["official_player"]["renderer"]["checks"] if r["layout"] == "full_screen"}
    eye_hash = {r["frame"]: r["actual_sha256"] for r in crop["official_player"]["renderer"]["checks"] if r["layout"] == "eye_anim"}
    crop["full_screen_reference_difference_frames"] = sum(full_hash[i] != eye_hash[i] for i in range(188))
    # Delete only files this run created for the discarded grid, after all
    # selected C audits pass. No source, rollback or historical build is touched.
    removed = []
    for row in variants:
        if row["id"] != best["id"]:
            path = output / "candidates" / (row["id"] + ".eaf")
            if not path.resolve().is_relative_to(output) or path.is_symlink():
                raise ValueError("discarded output resolves outside its stage")
            if sha(path.read_bytes()) != row["eaf_sha256"]:
                raise ValueError("discarded output changed unexpectedly")
            path.unlink()
            removed.append(path.name)
    atlases = {}
    for key, anim in (("full", source), ("eye", decoded_crop)):
        path = output / ("preview-" + key + ".png")
        meta = write_rgba_atlas(path, anim.frames, anim.width, anim.height, columns=8)
        atlases[key] = {**meta, "path": path.name, "sha256": sha(path.read_bytes()), "bytes": path.stat().st_size}
    cached = player_info[2]
    report = {"schema": "deskbot.expression_e1_34_preprocess/v1", "edition": "E1.34", "date": "2026-10-29",
              "result": "offline_preprocess_complete_deployment_blocked", "audit_pass": True,
              "hardware_accessed": False, "firmware_changed": False, "nvs_changed": False, "c61_changed": False,
              "deployment_eligible": False, "flash_authorized": False, "flashable_package_emitted": False,
              "source": {"sha256": SOURCE_SHA256, "bytes": SOURCE_BYTES, "dimensions": [360, 360],
                         "frame_count": 188, "fps": 25, "source_loop": SOURCE_LOOP,
                         "rgba_sequence_sha256": sequence_sha(source.frames), "source_e1_33_report_sha256": sha(E133_REPORT.read_bytes())},
              "baseline": context[4], "lossless_variants": variants, "smallest_lossless_id": best["id"],
              "crop_research": crop, "timing_research": timing_research(), "preview_atlases": atlases,
              "official_player_anchors": cached["anchors"],
              "toolchain": {key: cached["toolchain"][key] for key in ("dll_sha256", "dll_size_bytes", "compiler_sha256", "compiler_version", "optimization")},
              "cleanup": {"discarded_grid_eafs": sorted(removed), "retained_candidates": [best["id"], crop["id"]]},
              "next_gate": "E1.35: visual choice, terminal-metadata timing adaptation, one-semantic mapping audit; no automatic flash"}
    validate_report(report)
    immutable(output / "report.json", json_bytes(report))
    return report


def validate_report(report):
    if (report.get("schema") != "deskbot.expression_e1_34_preprocess/v1" or report.get("edition") != "E1.34"
            or report.get("result") != "offline_preprocess_complete_deployment_blocked" or report.get("audit_pass") is not True):
        raise ValueError("E1.34 report identity mismatch")
    for flag in ("hardware_accessed", "firmware_changed", "nvs_changed", "c61_changed", "deployment_eligible", "flash_authorized", "flashable_package_emitted"):
        if report.get(flag) is not False:
            raise ValueError("E1.34 offline boundary mismatch: " + flag)
    src = report["source"]
    if (src["sha256"], src["bytes"], src["dimensions"], src["frame_count"], src["fps"], src["source_loop"]) != (SOURCE_SHA256, SOURCE_BYTES, [360, 360], 188, 25, SOURCE_LOOP):
        raise ValueError("source anchor mismatch")
    base = report["baseline"]
    if (base["partition_bytes"], base["package_bytes"], base["remaining_bytes"], base["rollback_partition_sha256"], base["append_overhead_bytes"]) != (PARTITION_BYTES, BASE_BYTES, PARTITION_BYTES - BASE_BYTES, ROLLBACK_SHA256, APPEND_OVERHEAD):
        raise ValueError("E1.18 capacity/rollback mismatch")
    if src["source_e1_33_report_sha256"] != sha(E133_REPORT.read_bytes()):
        raise ValueError("E1.33 source report hash mismatch")
    rows = report["lossless_variants"]
    expected_ids = {f"lossless-{enc}-{h}" for enc in (0, 1, 3, "best") for h in (16, 32, 64)} | {"lossless-best-32-no-dedup"}
    if len(rows) != 13 or {r["id"] for r in rows} != expected_ids:
        raise ValueError("lossless search grid mismatch")
    for row in rows + [report["crop_research"]]:
        pixels, capacity = row["pixel_comparison"], row["capacity"]
        if (pixels["exact_sequence"] is not True or pixels["frame_count_equal"] is not True or pixels["frame_sizes_equal"] is not True
                or pixels["mismatch_frames"] != 0 or pixels["rgba_sequence_sha256"] != pixels["reference_sequence_sha256"]):
            raise ValueError("candidate pixel sequence mismatch")
        if row["frame_count"] != 188 or row["fps"] != 25 or row["bit_depth"] != 8 or row["deployment_eligible"] is not False:
            raise ValueError("candidate timeline/boundary mismatch")
        size = BASE_BYTES + APPEND_OVERHEAD + row["eaf_bytes"]
        if (capacity["package_bytes"] != size or capacity["remaining_bytes"] != PARTITION_BYTES - size
                or capacity["fits_partition"] is not (size <= PARTITION_BYTES)
                or any(capacity[k] is not True for k in ("existing_non_index_members_preserved", "existing_semantics_preserved", "package_round_trip_pass"))
                or capacity["package_emitted"] is not False):
            raise ValueError("candidate capacity/member preservation mismatch")
        if row in rows and (row["dimensions"] != [360, 360] or pixels["rgba_sequence_sha256"] != src["rgba_sequence_sha256"]):
            raise ValueError("full candidate changed source pixels")
    best = min(rows, key=lambda r: (r["eaf_bytes"], r["id"]))
    if report["smallest_lossless_id"] != best["id"]:
        raise ValueError("smallest lossless choice mismatch")
    crop = report["crop_research"]
    if (crop["id"] != "research-single-eye" or crop["crop"] != list(CROP) or crop["dimensions"] != [160, 200] or crop["lossless_to_full_source"] is not False
            or crop["visual_accepted"] is not False or not 1 <= crop["full_screen_reference_difference_frames"] <= 188):
        raise ValueError("crop visual boundary mismatch")
    for selected in (best, crop):
        native, r = selected["official_player"], selected["official_player"]["renderer"]
        if (selected["native_checked"] is not True or native["result"] != "pass" or native["candidate_sha256"] != selected["eaf_sha256"]
                or r["rendered_frame_count"] != 376 or len(r["checks"]) != 376
                or {(c["layout"], c["frame"]) for c in r["checks"]} != {(layout, i) for layout in ("full_screen", "eye_anim") for i in range(188)}
                or any(c["mismatch_pixels"] != 0 or c["guard_clean"] is not True or c["actual_sha256"] != c["expected_sha256"] for c in r["checks"])):
            raise ValueError("official C per-frame/guard evidence mismatch")
        segment = r["explicit_range"]
        if segment["renderer_accepts_range"] is not True or segment["sampled_current_frames"] != segment["expected_current_frames"] or segment["prepared_image_range"] != [50, 149]:
            raise ValueError("official C segment timer evidence mismatch")
        timer = native["default_product_timer"]
        if ([o["current_frame"] for o in timer["trace"]] != list(range(1, 188)) + [187] * 3
                or timer["clamped_end"] != 187 or timer["one_frame_done_count"] != 187
                or timer["all_188_images_scheduled"] is not False or timer["guards_and_release_clean"] is not True):
            raise ValueError("default product timer limit mismatch")
    if report["timing_research"] != timing_research():
        raise ValueError("FPS-only projection must not imply savings or resampling")
    for key, width, height in (("full", 360, 360), ("eye", 160, 200)):
        atlas = report["preview_atlases"][key]
        if (atlas["path"] != "preview-" + key + ".png" or atlas["frame_width"] != width
                or atlas["frame_height"] != height or atlas["frame_count"] != 188
                or atlas["columns"] != 8 or atlas["rows"] != 24):
            raise ValueError("preview atlas geometry/path mismatch")


def check_local(report, output):
    if not (output / "report.json").is_file():
        return  # A portable clone can validate the public report without media.
    best = next(r for r in report["lossless_variants"] if r["id"] == report["smallest_lossless_id"])
    for row in (best, report["crop_research"]):
        path = output / "candidates" / (row["id"] + ".eaf")
        if sha(path.read_bytes()) != row["eaf_sha256"]:
            raise ValueError("retained candidate hash drift")
        evidence = output / "native" / row["id"] / "result.json"
        if json.loads(evidence.read_text(encoding="utf-8")) != row["official_player"]:
            raise ValueError("native local evidence differs from report")
    for atlas in report["preview_atlases"].values():
        if sha((output / atlas["path"]).read_bytes()) != atlas["sha256"]:
            raise ValueError("local atlas hash drift")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--render-worker", action="store_true", help=argparse.SUPPRESS)
    for name in ("candidate-id", "candidate-sha", "dll-sha"):
        parser.add_argument("--" + name, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.render_worker:
        return worker(args)
    if args.check:
        output = safe_output(args.output)
        path = output / "report.json"
        if not path.exists() and output == OUTPUT:
            path = PUBLIC / "report.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        validate_report(report)
        check_local(report, output)
    else:
        report = audit(args.output)
    best = next(r for r in report["lossless_variants"] if r["id"] == report["smallest_lossless_id"])
    print(json.dumps({"result": report["result"], "smallest_lossless_bytes": best["eaf_bytes"],
                      "capacity": best["capacity"], "crop_bytes": report["crop_research"]["eaf_bytes"],
                      "deployment_eligible": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
