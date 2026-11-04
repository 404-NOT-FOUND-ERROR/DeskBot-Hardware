#!/usr/bin/env python3
"""E1.33 offline import and compatibility audit for one Espressif EAF candidate.

This stage is deliberately read-only.  It audits a candidate animation against
the anchored official C player, the 360x360 layouts used by 喵伴, the source
GIF/archive and the exact E1.18 assets-partition rollback image.  It does not
edit the official mapping, build firmware, open a serial port, or create a
flashable package.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent.parent
sys.path.insert(0, str(ROOT))
# Load the checkout's bounded preview codec before loading the project-level
# C harness, which adds its own (older) protocol directory to sys.path.
from protocol import eaf_preview as PREVIEW

if Path(PREVIEW.__file__).resolve() != ROOT / "protocol/eaf_preview.py":
    raise RuntimeError("E1.33 must use this checkout's preview decoder")

EAF = PROJECT_ROOT / "tmp/research-emote-tool/sample-eaf/shy_20s_40s.eaf"
PACK = PROJECT_ROOT / "tmp/research-emote-tool/Emote-Pack.zip"
ROLLBACK = ROOT / "tmp/expression-e1-23-deployment-preflight-20261026/rollback-e1-18-assets-6mb.bin"
OFFICIAL_SOURCE = PROJECT_ROOT / "tmp/remote-esp-vocat/examples/xiaozhi-esp32"
PLAYER_SCRIPT = PROJECT_ROOT / "scripts/audit-expression-e1-11-player.py"
RESOURCE_SCRIPT = ROOT / "scripts/audit-expression-resources.py"
PACKAGE_SCRIPT = ROOT / "scripts/audit-expression-package-candidate.py"
OUTPUT = ROOT / "tmp/expression-e1-33-import-20261029-final"
PUBLIC = ROOT / "artifacts/expression-e1-33-import-20261029"
PARTITION_BYTES = 6_144_000
EAF_SHA256 = "467db1c5c912b4a7d0c2c280f3eaf158fb3c9e0fe633e2c7e47ad4b848b78293"
ROLLBACK_SHA256 = "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6"
ROLLBACK_BYTES = 6_144_000
GIF_MEMBER = "gif/shy_[2.0s, 4.0s].gif"
GIF_SHA256 = "840e028a34cf8527a05a6dc264a15bede32542478c27180ce2b8584393c50be8"
PACK_SHA256 = "946654de50253949b2c687a2b6b53bf3b66fdd699ab107950258e2a830cf15e6"
GIF_BYTES = 486_226
SOURCE_FPS = 25
SOURCE_LOOP = [50, 150]


def load_module(path: Path, name: str):
    if not path.is_file():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def immutable(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError(f"refusing to overwrite different evidence: {path}")
    if not path.exists():
        path.write_bytes(raw)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def safe_output(path: Path) -> Path:
    resolved = path.resolve()
    if (resolved.parent != (ROOT / "tmp").resolve()
            or not resolved.name.startswith("expression-e1-33-import-")):
        raise ValueError("E1.33 output must be a stage directory directly under this checkout tmp")
    if any(p.is_symlink() for p in (resolved, *resolved.parents) if p.exists()):
        raise ValueError("E1.33 output cannot traverse symlinks")
    return resolved


def validate_timing(start: int, end: int, count: int, fps: int) -> None:
    if (any(type(v) is not int for v in (start, end, count, fps))
            or not 1 <= fps <= 60 or not 0 <= start < end < count <= 4096):
        raise ValueError("loop range or FPS is outside the animation bounds")


def validate_structure(structure: dict[str, Any]) -> None:
    if (structure["dimensions"] != [[360, 360]] or structure["bit_depths"] != [8]
            or not set(structure["encodings"]).issubset({0, 1, 3})
            or structure["metadata_frame_count"] != 0):
        raise ValueError("candidate dimensions, depth, encoding or metadata do not match the E1.33 contract")


def parse_gif(raw: bytes) -> dict[str, Any]:
    """Inspect timing and dimensions, rejecting incomplete GIF block streams."""
    if len(raw) < 13 or raw[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("source GIF header is invalid")
    width, height = struct.unpack_from("<HH", raw, 6)
    if not width or not height:
        raise ValueError("source GIF dimensions are empty")
    pos = 13

    def take(count: int) -> bytes:
        nonlocal pos
        if count < 0 or pos + count > len(raw):
            raise ValueError("source GIF block stream is truncated")
        value = raw[pos:pos + count]
        pos += count
        return value

    def subblocks() -> None:
        while True:
            size = take(1)[0]
            if not size:
                return
            take(size)

    packed = raw[10]
    if packed & 0x80:
        take(3 * (2 ** ((packed & 7) + 1)))
    frames = 0
    delays: list[int] = []
    pending_delay = None
    while True:
        marker = take(1)[0]
        if marker == 0x3B:
            if pos != len(raw) or frames == 0:
                raise ValueError("source GIF has trailing bytes or no image frames")
            break
        if marker == 0x21:
            label = take(1)[0]
            if label == 0xF9:
                if take(1)[0] != 4:
                    raise ValueError("source GIF graphic-control size is invalid")
                control = take(4)
                pending_delay = struct.unpack_from("<H", control, 1)[0]
                if take(1)[0] != 0:
                    raise ValueError("source GIF control terminator is invalid")
            else:
                subblocks()
        elif marker == 0x2C:
            descriptor = take(9)
            x, y, w, h, local_packed = struct.unpack("<HHHHB", descriptor)
            if not w or not h or x + w > width or y + h > height:
                raise ValueError("source GIF image dimensions are out of bounds")
            if local_packed & 0x80:
                take(3 * (2 ** ((local_packed & 7) + 1)))
            if not 2 <= take(1)[0] <= 8:
                raise ValueError("source GIF LZW code size is invalid")
            subblocks()
            frames += 1
            if pending_delay is not None:
                delays.append(pending_delay)
                pending_delay = None
        else:
            raise ValueError(f"unsupported GIF block marker 0x{marker:02x}")
    return {"width": width, "height": height, "frame_count": frames,
            "delay_centiseconds": delays,
            "fps_estimate": round(100 / (sum(delays) / len(delays))) if delays and sum(delays) else None}


def parse_eaf_structure(raw: bytes) -> dict[str, Any]:
    if len(raw) < 16 or raw[:4] not in (b"\x89EAF", b"\x89AAF"):
        raise ValueError("candidate EAF magic/header is invalid")
    frame_count, checksum, payload_len = struct.unpack_from("<III", raw, 4)
    if not 0 < frame_count <= 4096:
        raise ValueError("candidate EAF frame count is outside the supported range")
    table_end = 16 + frame_count * 8
    payload_end = 16 + payload_len
    if payload_end != len(raw) or table_end > payload_end:
        raise ValueError("candidate EAF table/payload bounds are invalid")
    payload = raw[16:]
    if sum(payload) & 0xFFFFFFFF != checksum:
        raise ValueError("candidate EAF checksum mismatch")
    encodings: list[int] = []
    dimensions: set[tuple[int, int]] = set()
    bit_depths: set[int] = set()
    metadata = 0
    for index in range(frame_count):
        size, offset = struct.unpack_from("<II", raw, 16 + index * 8)
        start = table_end + offset
        frame = raw[start:start + size]
        if len(frame) != size or frame[:2] != b"ZZ":
            raise ValueError(f"candidate frame {index} is invalid")
        content = frame[2:]
        if content[:2] == b"_C":
            metadata += 1
            continue
        if content[:2] != b"_S" or len(content) < 18:
            raise ValueError(f"candidate frame {index} is not a drawable _S frame")
        bit_depth = content[9]
        width, height, blocks, block_height = struct.unpack_from("<HHHH", content, 10)
        if bit_depth != 8 or width != 360 or height != 360:
            raise ValueError("candidate frame dimensions or bit depth mismatch")
        bit_depths.add(bit_depth)
        dimensions.add((width, height))
        if not (0 < blocks <= 256 and block_height > 0):
            raise ValueError(f"candidate frame {index} block table is invalid")
        if 18 + blocks * 4 + (1 << bit_depth) * 4 > len(content):
            raise ValueError("candidate block table or palette is truncated")
        lengths = struct.unpack_from("<" + "I" * blocks, content, 18)
        data_start = 18 + blocks * 4 + (1 << bit_depth) * 4
        cursor = data_start
        for block_length in lengths:
            if cursor + block_length > len(content) or block_length < 1:
                raise ValueError(f"candidate frame {index} block bounds are invalid")
            encodings.append(content[cursor])
            cursor += block_length
        if cursor != len(content):
            raise ValueError(f"candidate frame {index} has trailing bytes")
    if len(dimensions) != 1 or len(bit_depths) != 1:
        raise ValueError("candidate changes dimensions or bit depth between frames")
    return {"stored_frame_count": frame_count, "image_frame_count": frame_count - metadata,
            "metadata_frame_count": metadata, "dimensions": [list(next(iter(dimensions)))],
            "bit_depths": sorted(bit_depths), "encodings": sorted(set(encodings)),
            "checksum": checksum, "payload_bytes": payload_len}


def mutate_rejection_cases(raw: bytes, decoder: Any) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    def rejected(name: str, fn: Any) -> None:
        try:
            fn()
        except (ValueError, TypeError, KeyError, struct.error) as exc:
            cases[name] = {"rejected": True, "error": str(exc)}
        else:
            cases[name] = {"rejected": False, "error": "invalid input was accepted"}
    bad_checksum = bytearray(raw)
    bad_checksum[-1] ^= 0x01
    rejected("checksum_corruption", lambda: decoder.parse_eaf_bytes(bytes(bad_checksum)))
    rejected("truncated_eaf", lambda: parse_eaf_structure(raw[:-1]))
    rejected("loop_reversed", lambda: validate_timing(150, 50, 188, 25))
    rejected("loop_out_of_bounds", lambda: validate_timing(50, 188, 188, 25))
    rejected("invalid_fps", lambda: validate_timing(50, 150, 188, 0))
    structure = parse_eaf_structure(raw)
    changed = dict(structure, dimensions=[[848, 848]])
    rejected("dimension_mismatch", lambda: validate_structure(changed))
    rejected("unsupported_encoding", lambda: validate_structure(dict(structure, encodings=[2])))
    return cases


def native_render(player: Any, raw: bytes, decoded: Any, dll_path: Path, stage: Path) -> dict[str, Any]:
    native = player.PlayerNative(dll_path)
    layouts = {"full_screen": 0, "eye_anim": 1}
    checks: list[dict[str, Any]] = []
    key_frames = sorted({0, 24, 49, 50, 75, 100, 125, 150, 151, len(decoded.frames) - 1})
    previews: dict[str, list[bytes]] = {"full_screen": [], "eye_anim": []}
    for name, layout in layouts.items():
        native.open(raw, layout, SOURCE_FPS, False, 0, 0, 0, len(decoded.frames) - 1)
        try:
            for frame in range(len(decoded.frames)):
                native.prepare(frame)
                actual, obs = native.render((0, 0, 360, 360), 32, False, 0)
                expected = player.RENDER.compose565(decoded.frames[frame], decoded.width, decoded.height, layout)
                mismatches = sum(got != want for got, want in zip(actual, expected))
                checks.append({"layout": name, "frame": frame, "mismatch_pixels": mismatches,
                               "guard_clean": player.RENDER.clean_observation(obs),
                               "actual_sha256": player.RENDER.hash_u16(actual),
                               "expected_sha256": player.RENDER.hash_u16(expected)})
                if frame in key_frames:
                    rgba = bytearray(360 * 360 * 4)
                    for pixel, rgb565 in enumerate(actual):
                        r, g, b = (rgb565 >> 11) & 31, (rgb565 >> 5) & 63, rgb565 & 31
                        pos = pixel * 4
                        rgba[pos:pos + 4] = bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4),
                                                  (b << 3) | (b >> 2), 255))
                    previews[name].append(bytes(rgba))
        finally:
            close = native.close()
            if close.live_allocations != 0 or not player.clean(close):
                raise RuntimeError("official renderer cleanup/guard check failed")
    # Repeated EAF frames can share a data pointer. The old harness's
    # prepared_frame observation then returns the earliest alias rather than
    # the logical frame number. Use current_frame and completion counters to
    # test the actual, deterministic C timer; do not change the C player.
    start, end = SOURCE_LOOP
    opened = native.open(raw, 0, SOURCE_FPS, True, 0, 0, start, end)
    sequence: list[int] = []
    observations: list[dict[str, int]] = []
    try:
        for _ in range(2 * (end - start + 1) + 2):
            obs = native.tick(1000 // SOURCE_FPS)
            if not player.clean(obs):
                raise RuntimeError("timer range guard check failed")
            sequence.append(int(obs.current_frame))
            observations.append({"current_frame": int(obs.current_frame),
                                 "pointer_alias_frame": int(obs.prepared_frame),
                                 "one_done": int(obs.one_done),
                                 "all_done": int(obs.all_done)})
    finally:
        close = native.close()
        if close.live_allocations != 0 or not player.clean(close):
            raise RuntimeError("timer cleanup/guard check failed")
    # Official end_frame is exclusive: prepare start..end-1, hold the last
    # image for one completion tick, reset current_frame, then prepare start.
    cycle = list(range(start + 1, end + 1)) + [start]
    expected_sequence = (cycle * 3)[:len(sequence)]
    preview_info = {}
    for name, frames in previews.items():
        preview_path = stage / f"preview-{name}.png"
        preview_info[name] = PREVIEW.write_rgba_atlas(preview_path, tuple(frames), 360, 360, columns=5)
        preview_info[name].update({"path": preview_path.name, "sha256": file_sha(preview_path),
                                   "source_frames": key_frames})
    return {"checks": checks, "rendered_frame_count": len(checks), "preview_atlases": preview_info,
            "all_reference_frames_equal": all(i["mismatch_pixels"] == 0 for i in checks),
            "all_guards_clean": all(i["guard_clean"] for i in checks),
            "explicit_range": {"start": start, "end": end,
                "end_exclusive": True,
                "prepared_image_range": [start, end - 1],
                "completion_holds_last_image_for_one_tick": True,
                "sampled_current_frames": sequence,
                "expected_current_frames": expected_sequence,
                "observations": observations,
                "pointer_alias_trace_not_used_as_logical_frame_number": True,
                "renderer_accepts_range": (opened.current_frame == start
                    and sequence == expected_sequence
                    and observations[-1]["all_done"] == 2
                    and (end, start) in list(zip(sequence, sequence[1:])) )}}



def render_audit(player: Any, raw: bytes, decoded: Any, stage: Path) -> dict[str, Any]:
    stage.mkdir(parents=True, exist_ok=True)
    anchors = {"player_source": player.RENDER.source_anchor_report(),
               "compression_source": player.RENDER.COMPRESSION._verify_player_anchor(),
               "harness_sha256": file_sha(player.HARNESS),
               "renderer_harness_sha256": file_sha(PROJECT_ROOT / "tests/eaf_renderer_host/gfx_renderer_harness.c")}
    cache = stage / "toolchain.json"
    dll_path = stage / "player-audit-e1-11.dll"
    if cache.is_file():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        toolchain = saved["toolchain"]
        if saved["anchors"] != anchors or file_sha(dll_path) != toolchain["dll_sha256"]:
            raise ValueError("retained native harness or source anchor drift")
    else:
        toolchain = player.compile_harness(stage)
        immutable(cache, json_bytes({"anchors": anchors, "toolchain": toolchain}))
    dll_path = PROJECT_ROOT / toolchain["dll"]
    eaf_path = stage / "candidate.eaf"
    immutable(eaf_path, raw)
    result_path = stage / "render-result-v2.json"
    command = [sys.executable, str(Path(__file__).resolve()), "--render-worker",
               "--eaf", str(eaf_path), "--dll", str(dll_path), "--result", str(result_path),
               "--stage", str(stage)]
    child = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)
    (stage / "worker.log").write_text(child.stdout + child.stderr, encoding="utf-8")
    if child.returncode or not result_path.is_file():
        raise RuntimeError("isolated official C renderer failed; " +
                           (child.stderr or child.stdout or f"exit {child.returncode}")[-1200:])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("result") != "pass":
        raise RuntimeError("official C renderer rejected candidate: " + json.dumps(result, ensure_ascii=False))
    return {"anchors": anchors, "toolchain": {key: toolchain[key] for key in
                          ("dll_sha256", "dll_size_bytes", "compiler_sha256", "compiler_version", "optimization")},
            **result["renderer"]}


def decode_candidate(player: Any) -> Any:
    """Use the checkout's bounded decoder, including official zero-bit fills."""
    return PREVIEW.decode_eaf(EAF, fps=SOURCE_FPS)


def capacity_report(resources: Any, packer: Any, candidate_raw: bytes) -> dict[str, Any]:
    rollback_partition = ROLLBACK.read_bytes()
    if len(rollback_partition) != ROLLBACK_BYTES or sha(rollback_partition) != ROLLBACK_SHA256:
        raise ValueError("E1.18 rollback partition hash/size mismatch")
    if len(rollback_partition) < 12:
        raise ValueError("E1.18 rollback partition is shorter than the legacy package header")
    _, _, payload_size = struct.unpack_from("<III", rollback_partition, 0)
    rollback_package_bytes = 12 + payload_size
    if rollback_package_bytes > len(rollback_partition):
        raise ValueError("E1.18 rollback package exceeds its partition")
    rollback_raw = rollback_partition[:rollback_package_bytes]
    if rollback_partition[rollback_package_bytes:] != b"\xff" * (PARTITION_BYTES - rollback_package_bytes):
        raise ValueError("E1.18 rollback padding is not all 0xFF")
    parsed = resources.parse_package_bytes(rollback_raw, modern=False)
    members = parsed["members"]
    if packer.build_legacy_package(members) != rollback_raw:
        raise ValueError("E1.18 package is not byte-reproducible by the anchored packer")
    index = json.loads(members["index.json"].decode("utf-8"))
    candidate_index = json.loads(json.dumps(index))
    candidate_index["emoji_collection"].append({"name": "shy_candidate", "file": "shy_candidate.eaf",
                                                   "eaf": {"loop": True, "fps": SOURCE_FPS}})
    added = dict(members)
    added["index.json"] = (json.dumps(candidate_index, ensure_ascii=False, indent=4) + "\n").encode()
    added["shy_candidate.eaf"] = candidate_raw
    added_package = packer.build_legacy_package(added)
    eaf_sizes = {name: len(data) for name, data in members.items() if name.endswith(".eaf")}
    replacements = {}
    for name in ("Happy.eaf", "shocked.eaf"):
        replaced = dict(members)
        replaced["index.json"] = members["index.json"]
        replaced[name] = candidate_raw
        package = packer.build_legacy_package(replaced)
        replacements[name] = {"original_bytes": eaf_sizes[name], "package_bytes": len(package),
                              "remaining_bytes": PARTITION_BYTES - len(package),
                              "fits_partition": len(package) <= PARTITION_BYTES}
    return {"rollback_partition_bytes": len(rollback_partition), "rollback_partition_sha256": sha(rollback_partition),
            "rollback_package_bytes": rollback_package_bytes,
            "rollback_package_sha256": sha(rollback_raw), "partition_bytes": PARTITION_BYTES,
            "package_member_count": len(members), "eaf_member_count": len(eaf_sizes),
            "remaining_bytes": PARTITION_BYTES - rollback_package_bytes,
            "append_candidate": {"package_bytes": len(added_package), "remaining_bytes": PARTITION_BYTES - len(added_package),
                                  "fits_partition": len(added_package) <= PARTITION_BYTES},
            "replace_candidates": replacements,
            "shared_happy_semantics": [e["name"] for e in index["emoji_collection"] if e["file"] == "Happy.eaf"],
            "deployment_eligible": False,
            "note": "capacity is projected from the exact E1.18 rollback partition; no package is emitted"}


def audit(output: Path) -> dict[str, Any]:
    output = safe_output(output)
    output.mkdir(parents=True, exist_ok=True)
    candidate_raw = EAF.read_bytes()
    if sha(candidate_raw) != EAF_SHA256 or len(candidate_raw) != 717_402:
        raise ValueError("candidate EAF hash does not match the immutable source anchor")
    if file_sha(PACK) != PACK_SHA256:
        raise ValueError("source GIF archive hash drift")
    with zipfile.ZipFile(PACK) as archive:
        gif_raw = archive.read(GIF_MEMBER)
    gif_sha = sha(gif_raw)
    if gif_sha != GIF_SHA256 or len(gif_raw) != GIF_BYTES:
        raise ValueError("source GIF hash/size drift")
    player = load_module(PLAYER_SCRIPT, "deskbot_e133_player")
    resources = load_module(RESOURCE_SCRIPT, "deskbot_e133_resources")
    packer = load_module(PACKAGE_SCRIPT, "deskbot_e133_packer")
    decoded = decode_candidate(player)
    structure = parse_eaf_structure(candidate_raw)
    validate_structure(structure)
    validate_timing(*SOURCE_LOOP, len(decoded.frames), SOURCE_FPS)
    gif = parse_gif(gif_raw)
    if (gif["frame_count"] != len(decoded.frames) or gif["fps_estimate"] != SOURCE_FPS
            or gif["delay_centiseconds"] != [4] * len(decoded.frames)):
        raise ValueError("source GIF timing/frame count does not match the EAF candidate")
    renderer_stage = output / "native"
    renderer_stage.mkdir(parents=True, exist_ok=True)
    renderer = render_audit(player, candidate_raw, decoded, renderer_stage)
    rejection = mutate_rejection_cases(candidate_raw, resources)
    capacities = capacity_report(resources, packer, candidate_raw)
    official_manifest = OFFICIAL_SOURCE / "main/boards/esp_vocat/assets/360_360/emote.json"
    required_pass = (renderer["all_reference_frames_equal"] and renderer["all_guards_clean"]
                     and renderer["explicit_range"]["renderer_accepts_range"]
                     and all(case["rejected"] for case in rejection.values()))
    if not required_pass:
        raise ValueError("E1.33 required player/reference/rejection audit failed")
    report = {
        "schema": "deskbot.expression_e1_33_import/v1", "edition": "E1.33",
        "result": "offline_audit_complete_deployment_blocked", "date": "2026-10-29",
        "audit_pass": True,
        "hardware_accessed": False, "firmware_changed": False, "c61_changed": False,
        "deployment_eligible": False, "flash_authorized": False,
        "candidate": {"path": str(EAF.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                      "sha256": sha(candidate_raw), "bytes": len(candidate_raw),
                      "structure": structure, "source_fps": SOURCE_FPS, "source_loop": SOURCE_LOOP,
                      "source_gif": {"archive": str(PACK.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                                    "member": GIF_MEMBER, "sha256": gif_sha, "bytes": len(gif_raw),
                                    "expected_bytes": GIF_BYTES, "metadata": gif}},
        "official_player": {"source_commit": resources.git_commit(OFFICIAL_SOURCE),
                            "manifest_path": str(official_manifest.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                            "manifest_sha256": file_sha(official_manifest), "renderer": renderer},
        "negative_cases": rejection, "capacity": capacities,
        "compatibility": {"format_supported": (set(structure["encodings"]).issubset({0, 1, 3})
                                                  and structure["dimensions"] == [[360, 360]]),
                          "renderer_layouts_passed": renderer["all_reference_frames_equal"] and renderer["all_guards_clean"],
                          "loop_metadata_directly_representable": False,
                          "loop_reason": "official emote.json supports loop/fps only; source [50,150] endpoints have no direct field",
                          "capacity_publishable": capacities["append_candidate"]["fits_partition"],
                          "status": "audit_passed_deployment_blocked"},
        "next_gate": "preprocess or replace a bounded resource only after visual review; rerun capacity, official renderer, rollback and one-semantic deployment gates",
        "scope_exclusions": ["serial", "device", "NVS", "C61", "firmware", "formal mapping change", "flashable package"],
    }
    immutable(output / "report.json", json_bytes(report))
    return report


def validate_report(report: dict[str, Any]) -> None:
    """Recompute portable report contracts, never trusting pass flags alone."""
    if (report.get("schema") != "deskbot.expression_e1_33_import/v1"
            or report.get("edition") != "E1.33"
            or report.get("audit_pass") is not True
            or report.get("result") != "offline_audit_complete_deployment_blocked"
            or any(report.get(key) is not False for key in (
                "hardware_accessed", "firmware_changed", "c61_changed", "deployment_eligible", "flash_authorized"))):
        raise ValueError("audit identity or offline boundary mismatch")
    candidate = report["candidate"]
    if (candidate["sha256"] != EAF_SHA256 or candidate["bytes"] != 717_402
            or candidate["source_fps"] != SOURCE_FPS or candidate["source_loop"] != SOURCE_LOOP
            or candidate["source_gif"]["sha256"] != GIF_SHA256
            or candidate["source_gif"]["bytes"] != GIF_BYTES):
        raise ValueError("candidate source anchor mismatch")
    structure = candidate["structure"]
    validate_structure(structure)
    if (structure["image_frame_count"] != 188 or structure["stored_frame_count"] != 188
            or structure["encodings"] != [0, 1, 3]
            or candidate["source_gif"]["metadata"]["delay_centiseconds"] != [4] * 188):
        raise ValueError("candidate frame structure or timing mismatch")
    renderer = report["official_player"]["renderer"]
    checks = renderer["checks"]
    expected = [(layout, frame) for layout in ("full_screen", "eye_anim") for frame in range(188)]
    if ([(c["layout"], c["frame"]) for c in checks] != expected
            or renderer["rendered_frame_count"] != 376
            or not all(c["mismatch_pixels"] == 0 and c["guard_clean"] is True
                and c["actual_sha256"] == c["expected_sha256"]
                and re.fullmatch(r"[0-9a-f]{64}", c["actual_sha256"]) for c in checks)
            or renderer["all_reference_frames_equal"] is not True
            or renderer["all_guards_clean"] is not True):
        raise ValueError("official C per-frame evidence is incomplete or failed")
    loop = renderer["explicit_range"]
    cycle = list(range(51, 151)) + [50]
    expected_sequence = (cycle * 3)[:204]
    if (loop["start"] != 50 or loop["end"] != 150 or loop["end_exclusive"] is not True
            or loop["prepared_image_range"] != [50, 149]
            or loop["sampled_current_frames"] != expected_sequence
            or loop["expected_current_frames"] != expected_sequence
            or loop["renderer_accepts_range"] is not True):
        raise ValueError("official C segment timer evidence mismatch")
    observations = loop["observations"]
    if len(observations) != 204:
        raise ValueError("official C timer trace is incomplete")
    for tick, item in enumerate(observations, 1):
        completed = tick // 101
        if (item["current_frame"] != expected_sequence[tick - 1]
                or item["all_done"] != completed or item["one_done"] != tick - completed):
            raise ValueError("official C timer completion counts mismatch")
    capacity = report["capacity"]
    if (capacity["partition_bytes"] != PARTITION_BYTES
            or capacity["rollback_partition_bytes"] != PARTITION_BYTES
            or capacity["rollback_partition_sha256"] != ROLLBACK_SHA256
            or capacity["rollback_package_sha256"] != "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1"
            or capacity["rollback_package_bytes"] != 5_538_351
            or capacity["remaining_bytes"] != 605_649):
        raise ValueError("E1.18 rollback or capacity anchor mismatch")
    plans = [capacity["append_candidate"], *capacity["replace_candidates"].values()]
    if (len(plans) != 3 or any(p["remaining_bytes"] != PARTITION_BYTES - p["package_bytes"]
            or p["fits_partition"] is not (p["package_bytes"] <= PARTITION_BYTES) for p in plans)
            or any(p["fits_partition"] for p in plans)):
        raise ValueError("candidate capacity arithmetic mismatch")
    negative = report["negative_cases"]
    if (set(negative) != {"checksum_corruption", "truncated_eaf", "loop_reversed", "loop_out_of_bounds",
                         "invalid_fps", "dimension_mismatch", "unsupported_encoding"}
            or any(case["rejected"] is not True for case in negative.values())):
        raise ValueError("negative rejection cases missing or failed")
    if (report["compatibility"]["format_supported"] is not True
            or report["compatibility"]["renderer_layouts_passed"] is not True
            or report["compatibility"]["loop_metadata_directly_representable"] is not False
            or report["compatibility"]["capacity_publishable"] is not False):
        raise ValueError("compatibility conclusion mismatch")


def check_local_evidence(report: dict[str, Any], output: Path) -> None:
    """Check available private inputs/evidence; never rebuild or mutate them."""
    for path, expected in ((EAF, EAF_SHA256), (PACK, PACK_SHA256), (ROLLBACK, ROLLBACK_SHA256)):
        if path.is_file() and file_sha(path) != expected:
            raise ValueError("local source anchor drift: " + path.name)
    if EAF.is_file() and parse_eaf_structure(EAF.read_bytes()) != report["candidate"]["structure"]:
        raise ValueError("candidate structure changed")
    if PACK.is_file():
        with zipfile.ZipFile(PACK) as archive:
            metadata = parse_gif(archive.read(GIF_MEMBER))
        if metadata != report["candidate"]["source_gif"]["metadata"]:
            raise ValueError("source GIF metadata changed")
    native_result = output / "native/render-result-v2.json"
    if native_result.is_file():
        renderer = json.loads(native_result.read_text(encoding="utf-8"))
        if renderer["result"] != "pass" or any(
            report["official_player"]["renderer"][key] != value
            for key, value in renderer["renderer"].items()
        ):
            raise ValueError("retained official C results differ from report")
    for meta in report["official_player"]["renderer"]["preview_atlases"].values():
        path = output / "native" / meta["path"]
        if path.is_file() and file_sha(path) != meta["sha256"]:
            raise ValueError("native preview evidence hash drift")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render-worker", action="store_true", help=argparse.SUPPRESS)
    for name in ("eaf", "dll", "stage", "result"):
        parser.add_argument("--" + name, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.render_worker:
            if not all((args.eaf, args.dll, args.stage, args.result)):
                raise ValueError("incomplete isolated renderer arguments")
            stage = args.stage.resolve()
            if not stage.is_relative_to((ROOT / "tmp").resolve()):
                raise ValueError("native worker stage must stay under checkout tmp")
            if any(p.resolve().parent != stage for p in (args.eaf, args.dll, args.result)):
                raise ValueError("native worker inputs/outputs must be direct stage children")
            raw = args.eaf.read_bytes()
            if sha(raw) != EAF_SHA256:
                raise ValueError("native worker EAF hash drift")
            player = load_module(PLAYER_SCRIPT, "deskbot_e133_worker_player")
            decoded = PREVIEW.decode_eaf(args.eaf, fps=SOURCE_FPS)
            renderer = native_render(player, raw, decoded, args.dll, stage)
            passed = (renderer["all_reference_frames_equal"] and renderer["all_guards_clean"]
                      and renderer["explicit_range"]["renderer_accepts_range"])
            immutable(args.result, json_bytes({"result": "pass" if passed else "fail", "renderer": renderer}))
            return 0 if passed else 2
        if args.check:
            report_path = args.output / "report.json"
            if not report_path.is_file() and args.output == OUTPUT:
                report_path = PUBLIC / "report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            validate_report(report)
            check_local_evidence(report, args.output)
        else:
            report = audit(args.output)
        print(json.dumps({"result": report["result"], "deployment_eligible": False,
                          "candidate_bytes": report["candidate"]["bytes"],
                          "encodings": report["candidate"]["structure"]["encodings"],
                          "renderer_layouts_passed": report["compatibility"]["renderer_layouts_passed"],
                          "capacity_publishable": report["compatibility"]["capacity_publishable"],
                          "report": str((args.output / "report.json").resolve())}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired,
            zipfile.BadZipFile, struct.error) as exc:
        print(json.dumps({"result": "rejected", "hardware_accessed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
