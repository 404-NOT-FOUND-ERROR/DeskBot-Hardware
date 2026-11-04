#!/usr/bin/env python3
"""E1.44 offline resource adaptation, native player and 8 MiB package audit."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.eaf_legacy_terminal import (OFFICIAL_TERMINAL, append_official_terminal,
                                         inspect_stream, terminal_evidence)
from protocol.eaf_preview import decode_eaf, write_rgba_atlas
from protocol.storage_continuous_mmap_baseline import load_migrated_baseline

OUTPUT = ROOT / "tmp/expression-e1-44-adaptation-20261104"
PUBLIC = ROOT / "artifacts/expression-e1-44-adaptation-20261104"
PARTITION_BYTES = 8 * 1024 * 1024
SOFT_LIMIT = 7 * 1024 * 1024
FPS, IMAGES = 25, 188
MEMBER = "shy_candidate.eaf"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


E134 = load_module(ROOT / "scripts/audit-expression-e1-34-preprocess.py", "deskbot_e134_for_e144")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def immutable(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("different retained evidence: " + str(path))
    if not path.exists():
        path.write_bytes(raw)


def source_state():
    paths = ["firmware/body/SOURCE_MANIFEST.sha256", "firmware/body/main/display/emote_display.cc",
             "firmware/body/main/assets.cc", "protocol/eaf_legacy_terminal.py"]
    return {name: sha((ROOT / name).read_bytes()) for name in paths}


def projection(raw, context):
    resources, packer, members, index, base = context
    proposed = copy.deepcopy(index)
    before = next(row for row in proposed["emoji_collection"] if row["name"] == "embarrassed")
    if before != {"name": "embarrassed", "file": "Happy.eaf", "eaf": {"loop": True, "fps": 20}}:
        raise ValueError("embarrassed baseline changed")
    before["file"], before["eaf"]["fps"] = MEMBER, FPS
    after_members = dict(members)
    after_members[MEMBER] = raw
    after_members["index.json"] = (json.dumps(proposed, ensure_ascii=False, indent=4) + "\n").encode()
    package = packer.build_legacy_package(after_members)
    parsed = resources.parse_package_bytes(package, modern=False)["members"]
    deltas = [{"name": old["name"], "before": old, "after": new}
              for old, new in zip(index["emoji_collection"], proposed["emoji_collection"]) if old != new]
    untouched = all(parsed[key] == value for key, value in members.items() if key != "index.json")
    if (parsed != after_members or not untouched or [row["name"] for row in deltas] != ["embarrassed"]
            or {k: v for k, v in proposed.items() if k != "emoji_collection"}
            != {k: v for k, v in index.items() if k != "emoji_collection"}):
        raise ValueError("single semantic package isolation failed")
    return {"package_bytes": len(package), "package_sha256": sha(package),
            "remaining_bytes": PARTITION_BYTES - len(package),
            "soft_limit_remaining_bytes": SOFT_LIMIT - len(package),
            "fits_partition": len(package) <= PARTITION_BYTES,
            "fits_publication_soft_limit": len(package) <= SOFT_LIMIT,
            "package_growth_bytes": len(package) - base["package_bytes"],
            "index_table_net_growth_bytes": len(package) - base["package_bytes"] - len(raw),
            "member_count": len(parsed), "added": [MEMBER], "removed": [], "changed": ["index.json"],
            "semantic_changes": deltas, "layout_unchanged": True,
            "all_existing_non_index_members_byte_exact": untouched,
            "other_happy_references": [row["name"] for row in proposed["emoji_collection"]
                                       if row["file"] == "Happy.eaf"],
            "surprised_resource": next(row["file"] for row in proposed["emoji_collection"]
                                       if row["name"] == "surprised"),
            "package_round_trip_pass": True, "package_emitted": False}


def timer_audit(player, dll, raw, decoded, *, loop, layout):
    native = player.PlayerNative(dll)
    opened = native.open(raw, layout, FPS, loop, 0, 0, 0, 0xffff)
    if opened.end_frame != IMAGES:
        raise ValueError("default end must exclude only the terminal flag")
    traces, scheduled, last_hash, before_count, completion_hashes = [], [], None, 0, []
    ticks = 2 * (IMAGES + 1) + 2 if loop else IMAGES + 3
    expected_last = player.RENDER.hash_u16(
        player.RENDER.compose565(decoded.frames[-1], decoded.width, decoded.height, layout))
    try:
        for _ in range(ticks):
            obs = native.tick(40)
            current, one_done, all_done = int(obs.current_frame), int(obs.one_done), int(obs.all_done)
            if not player.clean(obs):
                raise ValueError("native timer guard failure")
            if one_done > before_count:
                logical = current - 1
                scheduled.append(logical)
                if logical == IMAGES - 1:
                    pixels, rendered = native.render((0, 0, 360, 360), 32, False, 0)
                    last_hash = player.RENDER.hash_u16(pixels)
                    if not player.clean(rendered) or last_hash != expected_last:
                        raise ValueError("last scheduled frame does not match the reference")
            elif all_done:
                pixels, rendered = native.render((0, 0, 360, 360), 32, False, 0)
                held = player.RENDER.hash_u16(pixels)
                if not player.clean(rendered) or held != expected_last:
                    raise ValueError("completion did not preserve last image")
                completion_hashes.append(held)
            traces.append({"current_frame": current, "one_done": one_done,
                           "all_done": all_done, "playing": bool(obs.is_playing)})
            before_count = one_done
        expected_cycle = list(range(1, IMAGES + 1)) + [0]
        expected_current = ((expected_cycle * 3)[:ticks] if loop
                            else list(range(1, IMAGES + 1)) + [IMAGES] * 3)
        expected_scheduled = list(range(IMAGES)) * 2 + [0, 1] if loop else list(range(IMAGES))
        if ([row["current_frame"] for row in traces] != expected_current
                or scheduled != expected_scheduled or traces[-1]["all_done"] != (2 if loop else 1)
                or traces[-1]["playing"] != loop):
            raise ValueError("default timer logical frame or completion sequence differs")
    finally:
        closed = native.close()
        if not player.clean(closed) or closed.live_allocations:
            raise ValueError("native timer release failure")
    return {"loop": loop, "layout": "full_screen" if layout == 0 else "eye_anim",
            "requested_end": 65535, "end_exclusive": IMAGES,
            "all_188_images_scheduled": True, "image_range": [0, 187],
            "terminal_scheduled": False, "last_frame_rgb565_sha256": last_hash,
            "last_image_held_on_completion": bool(completion_hashes),
            "completion_hold_ticks_per_loop": 1, "all_done": traces[-1]["all_done"],
            "logical_frames_checked_by_counters": True, "guards_and_release_clean": True,
            "trace": traces}


def native_audit(raw, decoded, stage, player_info):
    player, dll, _ = player_info
    stage.mkdir(parents=True, exist_ok=True)
    result = E134.E133.native_render(player, raw, decoded, dll, stage)
    native = player.PlayerNative(dll)
    native.open(raw, 0, FPS, False, 0, 0, 0, 65535)
    try:
        counts = native.frame_type_counts()
        terminal_type = native.frame_type(IMAGES)
        native.prepare(IMAGES - 1)
        code, observation = native.prepare_code(IMAGES)
        if (counts != {"valid_image": 188, "metadata_flag": 1, "invalid": 0}
                or terminal_type != 3 or code == 0 or not player.clean(observation)):
            raise ValueError("terminal was not treated as a non-image flag")
    finally:
        closed = native.close()
        if not player.clean(closed) or closed.live_allocations:
            raise ValueError("native flag query release failure")
    timers = [timer_audit(player, dll, raw, decoded, loop=loop, layout=layout)
              for layout in (0, 1) for loop in (False, True)]
    if not (result["all_reference_frames_equal"] and result["all_guards_clean"]
            and result["explicit_range"]["renderer_accepts_range"]):
        raise ValueError("native frame/reference/segment comparison failed")
    return {"full_frame_render": result, "frame_types": counts, "terminal_type": terminal_type,
            "terminal_prepare_rejected": code != 0, "default_timers": timers,
            "native_pass": True}


def run():
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise ValueError("retain previous evidence; use --check for the completed E1.44 run")
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
           for p in (OUTPUT, *OUTPUT.parents) if p.exists()):
        raise ValueError("stage output cannot traverse links")
    state = source_state()
    baseline = load_migrated_baseline()
    context = E134.baseline_context()
    resources, _, members, _, base = context
    # Derive the marker from the exact verified resource; never infer a blank
    # image or claim the opaque fields configure the legacy player's loop.
    happy = members["Happy.eaf"]
    happy_info = resources.parse_eaf_bytes(happy)
    count = happy_info["stored_frame_count"]
    import struct
    length, offset = struct.unpack_from("<II", happy, 16 + (count - 1) * 8)
    if (sha(happy) != terminal_evidence()["source_eaf_sha256"] or count != 43
            or happy[16 + count * 8 + offset:16 + count * 8 + offset + length] != OFFICIAL_TERMINAL):
        raise ValueError("official terminal source differs")
    e134 = json.loads((E134.PUBLIC / "report.json").read_bytes())
    E134.validate_report(e134)
    source = E134.E133.EAF.read_bytes()
    crop_row = e134["crop_research"]
    eye = (E134.OUTPUT / "candidates/research-single-eye.eaf").read_bytes()
    if sha(source) != E134.SOURCE_SHA256 or sha(eye) != crop_row["eaf_sha256"]:
        raise ValueError("retained original or single-eye candidate differs")
    player_info = E134.player_context()
    variants = []
    for key, raw in (("full", source), ("eye", eye)):
        before = inspect_stream(raw)
        adapted = append_official_terminal(raw)
        after = inspect_stream(adapted)
        path = OUTPUT / (key + "-terminal.eaf")
        immutable(path, adapted)
        anim = decode_eaf(path, fps=FPS)
        source_path = E134.E133.EAF if key == "full" else E134.OUTPUT / "candidates/research-single-eye.eaf"
        original = decode_eaf(source_path, fps=FPS)
        if (before.images != after.images or before.entries != after.entries[:-1]
                or before.storage != after.storage[:-49] or anim.frames != original.frames
                or len(anim.frames) != 188 or anim.metadata_frame_count != 1):
            raise ValueError("adaptation changed image payloads or timeline")
        native = native_audit(adapted, anim, OUTPUT / "native" / key, player_info)
        atlas_path = OUTPUT / (key + "-atlas.png")
        atlas = write_rgba_atlas(atlas_path, anim.frames, anim.width, anim.height, columns=8)
        variants.append({"id": key, "eaf_bytes": len(adapted), "eaf_sha256": sha(adapted),
                         "input_bytes": len(raw), "input_sha256": sha(raw),
                         "dimensions": [anim.width, anim.height], "image_frames": 188,
                         "stored_entries": 189, "metadata_entries": 1, "fps": 25,
                         "image_payloads_byte_exact": True, "image_table_offsets_preserved": True,
                         "rgba_sequence_sha256": E134.sequence_sha(anim.frames),
                         "rgba_sequence_unchanged": True, "native": native,
                         "atlas": {**atlas, "path": atlas_path.name, "bytes": atlas_path.stat().st_size,
                                   "sha256": sha(atlas_path.read_bytes())},
                         "capacity": projection(adapted, context),
                         "layout": {"reference": "centered_full_face" if key == "full" else "eye_anim_auto_mirror",
                                    "current_eye_route_compatible": key == "eye",
                                    "full_composition_preserved": key == "full",
                                    "visual_accepted": False}})
    action = (ROOT / "firmware/body/main/display/emote_display.cc").read_text(encoding="utf-8")
    if not re.search(r'\{"embarrassed",\s*VOCAT_BASE_CMD_SET_ACTION_CAT_NUZZLE\}', action):
        raise ValueError("official embarrassed action branch differs")
    if source_state() != state:
        raise ValueError("source inputs changed during the audit")
    report = {"schema": "deskbot.expression_e1_44_adaptation/v1", "edition": "E1.44", "date": "2026-11-04",
              "result": "offline_adaptation_passed_visual_choice_pending", "audit_pass": True,
              "hardware_accessed": False, "firmware_changed": False, "package_emitted": False,
              "baseline": {"layout": baseline["layout"], "migration_report": baseline["report"],
                           "saved_readback_sha256": baseline["saved_readback_sha256"],
                           "package_sha256": base["package_sha256"], "package_bytes": base["package_bytes"],
                           "member_count": base["member_count"], "partition_bytes": PARTITION_BYTES,
                           "publication_soft_limit_bytes": SOFT_LIMIT,
                           "free_bytes": PARTITION_BYTES - base["package_bytes"]},
              "terminal": terminal_evidence(), "variants": variants,
              "official_player_anchors": player_info[2]["anchors"],
              "toolchain": {key: player_info[2]["toolchain"][key]
                            for key in ("dll_sha256", "dll_size_bytes", "compiler_sha256", "compiler_version")},
              "source_state": state,
              "semantic_draft": {"semantic": "embarrassed", "current_resource": "Happy.eaf",
                                 "candidate_member": MEMBER, "official_action": "CAT_NUZZLE",
                                 "action_branch_changed": False, "device_mapping_changed": False},
              "timing": {"fps": 25, "image_frames": 188, "image_timeline_seconds": 7.52,
                         "loop_completion_hold_ms": 40, "loop_cycle_seconds": 7.56,
                         "source_local_range": [50, 150], "local_range_enabled_in_product": False,
                         "draft_playback": "whole_timeline_loop"},
              "media": {"public": "metadata_and_existing_Happy_preview",
                        "candidate_media": "ignored_local_preview", "redistribution_license_confirmed": False},
              "next": ["visual_choice", "continuous_mapping_3min_hardware_test",
                       "single_semantic_resource_package_preflight", "resource_only_deployment_and_regression"]}
    validate_report(report)
    immutable(OUTPUT / "report.json", json_bytes(report))
    immutable(PUBLIC / "report.json", json_bytes(report))
    return report


def validate_report(report):
    if (report.get("schema") != "deskbot.expression_e1_44_adaptation/v1" or report.get("edition") != "E1.44"
            or report.get("result") != "offline_adaptation_passed_visual_choice_pending"
            or report.get("audit_pass") is not True):
        raise ValueError("E1.44 report identity differs")
    if any(report.get(key) is not False for key in ("hardware_accessed", "firmware_changed", "package_emitted")):
        raise ValueError("offline evidence scope differs")
    base = report["baseline"]
    if (base["layout"] != "upper-assets-8m" or base["partition_bytes"] != PARTITION_BYTES
            or base["publication_soft_limit_bytes"] != SOFT_LIMIT or base["package_bytes"] != 5538351
            or base["package_sha256"] != "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1"
            or base["free_bytes"] != PARTITION_BYTES - base["package_bytes"]):
        raise ValueError("migrated baseline budget differs")
    if report["terminal"] != terminal_evidence() or [row["id"] for row in report["variants"]] != ["full", "eye"]:
        raise ValueError("terminal or candidates differ")
    if report["semantic_draft"] != {"semantic": "embarrassed", "current_resource": "Happy.eaf",
                                    "candidate_member": MEMBER, "official_action": "CAT_NUZZLE",
                                    "action_branch_changed": False, "device_mapping_changed": False}:
        raise ValueError("semantic/action draft differs")
    for row in report["variants"]:
        cap = row["capacity"]
        if (row["eaf_bytes"] - row["input_bytes"] != 57 or row["image_frames"] != 188
                or row["stored_entries"] != 189 or row["metadata_entries"] != 1
                or not row["image_payloads_byte_exact"] or not row["rgba_sequence_unchanged"]
                or cap["remaining_bytes"] != PARTITION_BYTES - cap["package_bytes"]
                or cap["soft_limit_remaining_bytes"] != SOFT_LIMIT - cap["package_bytes"]
                or cap["package_growth_bytes"] != row["eaf_bytes"] + cap["index_table_net_growth_bytes"]
                or cap["member_count"] != 30 or cap["surprised_resource"] != "shocked.eaf"
                or len(cap["other_happy_references"]) != 8 or cap["package_emitted"] is not False
                or [delta["name"] for delta in cap["semantic_changes"]] != ["embarrassed"]
                or not cap["layout_unchanged"] or not cap["package_round_trip_pass"]
                or not cap["all_existing_non_index_members_byte_exact"]):
            raise ValueError("candidate isolation or budget differs")
        expected_delta = {"name": "embarrassed",
                          "before": {"name": "embarrassed", "file": "Happy.eaf", "eaf": {"loop": True, "fps": 20}},
                          "after": {"name": "embarrassed", "file": MEMBER, "eaf": {"loop": True, "fps": FPS}}}
        if (cap["semantic_changes"] != [expected_delta]
                or row["layout"] != {"reference": "centered_full_face" if row["id"] == "full" else "eye_anim_auto_mirror",
                                     "current_eye_route_compatible": row["id"] == "eye",
                                     "full_composition_preserved": row["id"] == "full", "visual_accepted": False}):
            raise ValueError("semantic/layout proposal differs")
        native = row["native"]
        if (not native["native_pass"] or native["full_frame_render"]["rendered_frame_count"] != 376
                or not native["terminal_prepare_rejected"] or native["terminal_type"] != 3
                or len(native["default_timers"]) != 4
                or any(not t["all_188_images_scheduled"] or t["terminal_scheduled"]
                       or not t["guards_and_release_clean"] for t in native["default_timers"])):
            raise ValueError("native playback evidence differs")
        renders = native["full_frame_render"]["checks"]
        expected_renders = [(layout, frame) for layout in ("full_screen", "eye_anim") for frame in range(IMAGES)]
        if ([(check["layout"], check["frame"]) for check in renders] != expected_renders
                or any(check["mismatch_pixels"] != 0 or not check["guard_clean"]
                       or check["actual_sha256"] != check["expected_sha256"] for check in renders)):
            raise ValueError("complete native render comparisons differ")
        if [(t["layout"], t["loop"]) for t in native["default_timers"]] != [
                (layout, loop) for layout in ("full_screen", "eye_anim") for loop in (False, True)]:
            raise ValueError("timer scenarios differ")
        for timer in native["default_timers"]:
            loop = timer["loop"]
            ticks = 2 * (IMAGES + 1) + 2 if loop else IMAGES + 3
            expected = []
            for tick in range(ticks):
                completed = (tick + 1) // (IMAGES + 1) if loop else int(tick >= IMAGES)
                expected.append({"current_frame": tick % (IMAGES + 1) + 1 if loop and tick % (IMAGES + 1) < IMAGES
                                 else 0 if loop else min(tick + 1, IMAGES),
                                 "one_done": tick + 1 - completed if loop else min(tick + 1, IMAGES),
                                 "all_done": completed, "playing": loop or tick < IMAGES})
            final_hash = next(check["expected_sha256"] for check in renders
                              if check["layout"] == timer["layout"] and check["frame"] == 187)
            if (timer["trace"] != expected or timer["last_frame_rgb565_sha256"] != final_hash
                    or not timer["last_image_held_on_completion"] or timer["end_exclusive"] != 188):
                raise ValueError("default frame sequence or tail image differs")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = json.loads((PUBLIC / "report.json").read_bytes()) if args.check else run()
    validate_report(report)
    if args.check and OUTPUT.is_dir():
        for row in report["variants"]:
            raw = (OUTPUT / (row["id"] + "-terminal.eaf")).read_bytes()
            if sha(raw) != row["eaf_sha256"] or len(raw) != row["eaf_bytes"] or not inspect_stream(raw).has_terminal:
                raise ValueError("saved adapted candidate differs")
    print(json.dumps({"result": "checked" if args.check else report["result"],
                      "native_frame_layout_comparisons": 752,
                      "variants": [{"id": row["id"], "eaf_bytes": row["eaf_bytes"],
                                    "package_bytes": row["capacity"]["package_bytes"],
                                    "remaining_bytes": row["capacity"]["remaining_bytes"]}
                                   for row in report["variants"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
