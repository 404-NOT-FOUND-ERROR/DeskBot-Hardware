#!/usr/bin/env python3
"""E1.3e: offline dirty-render audit of the anchored Espressif EAF renderer.

This runner compiles the unchanged production renderer into a host-only DLL,
then compares its RGB565 output with the bounded Python preview model while
exercising invalidation, dirty-area merging, rendering and chunked flushing.  It
never opens a serial port, builds firmware, edits a shared component, or
creates a deployable package.  Every candidate is executed in a child process
because the production decoder/renderer is intentionally being audited, not
trusted as a sandbox.
"""
from __future__ import annotations

import argparse
import array
import ctypes
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocol.eaf_encode import encode_rgba_frames
from protocol.eaf_compression import transcode_legacy_eaf
from protocol.eaf_preview import DecodedAnimation, compose_display_frame, decode_eaf

HARNESS = ROOT / "tests/eaf_renderer_host"
COMPILER = ROOT / "tmp/zig-x86_64-windows-0.15.2/zig.exe"
O2F2 = ROOT / "tmp/o2f2-o2d-readonly-20261018"
PLAYER = O2F2 / "source/managed_components/espressif2022__esp_emote_gfx"
OFFICIAL_SOURCE = ROOT / "tmp/remote-esp-vocat/examples/xiaozhi-esp32"
OFFICIAL_PACKAGE = ROOT / "tmp/remote-esp-vocat-official-build/b-events-v1/expression_assets.bin"
CANDIDATE_4 = ROOT / "tmp/expression-e1-3b-20261020/candidate/shy-quantized-4bit-15color-host-only-do-not-flash.eaf"
CANDIDATE_8 = ROOT / "tmp/expression-e1-3b-20261020/candidate/shy-smallest-lossless-8bit-device-playback-unverified-do-not-flash.eaf"
REFERENCE = ROOT / "tmp/expression-e1-2-20261020/shy_legacy.eaf"
PARTITION_BYTES = 6_144_000

EXPECTED = {
    "source_commit": "46d427255756fe2a148777ea3f256b615b52e160",
    "official_package": "56b39e7fdd14b9dd0de9b22d3aedc9b04383376dbdef70925675f639d763cd5b",
    "candidate_4": "7144c594e066c2411e43a492c7ff8f7c4befb0ac282f05250a0656f1b637c9fd",
    "candidate_8": "916d56e048525bc68a38f4b14afab54369d2556ef6a1362323dcbccc9ac598cf",
    "reference": "4dd57777598dc1517fcf770fd859894567fbaa434373ef604e4d080e9331a5b1",
    "gfx_anim": "95aa86482b7dec9616c5db5d14598004ae5769ed2e56a1ba22978030906d7c0f",
    "gfx_eaf_dec": "61931bc0f6f8c1fe28e7efd491a0b15ca659267c285f9a913aeff23dd94f6c96",
    "gfx_refr": "fda105ebe24a16827280c2de15d0950999ac36e002872ee39e87692dc1544706",
    "gfx_timer": "40536a14be4343e621e4757f13cc408fcf8589ef5d732a8c4a0cf76d8689bbc0",
    "gfx_render": "d85705c65f09ac90d2d5b7833b8a9791b77145a9db31f8243928f885c0a30670",
    "gfx_obj": "f32d2faa274ed1e5968430369b31154124522bc3e22af87efcf2b90902fe58cc",
    "gfx_types": "38446fa74bea4cda22d3b70f98c09df8129ea468ccd4d6448a31da6d61aeda36",
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha256(path.read_bytes())


def immutable(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"refusing to overwrite different evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def safe_output(path: Path) -> Path:
    resolved = path.resolve()
    tmp_root = (ROOT / "tmp").resolve()
    if resolved == tmp_root or not resolved.is_relative_to(tmp_root):
        raise ValueError("E1.3e output must be a stage directory inside project tmp")
    return resolved


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RESOURCES = load_module(ROOT / "scripts/audit-expression-resources.py", "deskbot_e13d_resources")
PACKAGES = load_module(ROOT / "scripts/audit-expression-package-candidate.py", "deskbot_e13d_packages")
COMPRESSION = load_module(ROOT / "scripts/audit-expression-compression.py", "deskbot_e13d_compression")


class Observation(ctypes.Structure):
    _fields_ = [
        ("prepared_frame", ctypes.c_int32),
        ("current_frame", ctypes.c_uint32),
        ("end_frame", ctypes.c_uint32),
        ("is_playing", ctypes.c_uint32),
        ("period_ms", ctypes.c_uint32),
        ("one_done", ctypes.c_uint32),
        ("all_done", ctypes.c_uint32),
        ("invalid_areas", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("corruptions", ctypes.c_uint32),
        ("prefix_changes", ctypes.c_uint32),
        ("max_tail_bytes", ctypes.c_uint32),
        ("live_allocations", ctypes.c_uint32),
        ("flushes", ctypes.c_uint32),
        ("flush_bounds_error", ctypes.c_uint32),
        ("pixel_capacity", ctypes.c_uint32),
    ]


class RectSnapshot(ctypes.Structure):
    _fields_ = [("x1", ctypes.c_int32), ("y1", ctypes.c_int32),
                ("x2", ctypes.c_int32), ("y2", ctypes.c_int32)]


class DirtyObservation(ctypes.Structure):
    _fields_ = [
        ("dirty_before", ctypes.c_uint32),
        ("dirty_after_merge", ctypes.c_uint32),
        ("merge_count", ctypes.c_uint32),
        ("flushes", ctypes.c_uint32),
        ("flush_bounds_error", ctypes.c_uint32),
        ("flush_capacity_error", ctypes.c_uint32),
        ("max_flush_height", ctypes.c_uint32),
        ("max_flush_pixels", ctypes.c_uint32),
        ("buffer_capacity_pixels", ctypes.c_uint32),
        ("flush_trace_count", ctypes.c_uint32),
        ("flush_trace_truncated", ctypes.c_uint32),
        ("corruptions", ctypes.c_uint32),
        ("prefix_changes", ctypes.c_uint32),
        ("max_tail_bytes", ctypes.c_uint32),
        ("merge_capture_seen", ctypes.c_uint32),
        ("object_x", ctypes.c_int32),
        ("object_y", ctypes.c_int32),
        ("object_width", ctypes.c_uint32),
        ("object_height", ctypes.c_uint32),
        ("invalidated", RectSnapshot * 16),
        ("merged", RectSnapshot * 16),
        ("merged_flags", ctypes.c_uint8 * 16),
        ("flush_rects", RectSnapshot * 512),
    ]


def obs_dict(obs: Observation) -> dict[str, int]:
    return {name: int(getattr(obs, name)) for name, _ in Observation._fields_}


def rect_dict(rect: RectSnapshot) -> dict[str, int]:
    return {name: int(getattr(rect, name)) for name, _ in RectSnapshot._fields_}


def dirty_obs_dict(obs: DirtyObservation) -> dict[str, Any]:
    dirty_before = min(int(obs.dirty_before), 16)
    dirty_after = min(int(obs.dirty_after_merge), 16)
    trace_count = min(int(obs.flush_trace_count), 512)
    return {
        **{name: int(getattr(obs, name)) for name, _ in DirtyObservation._fields_
           if name not in {"invalidated", "merged", "merged_flags", "flush_rects"}},
        "invalidated": [rect_dict(obs.invalidated[index]) for index in range(dirty_before)],
        "merged": [rect_dict(obs.merged[index]) for index in range(dirty_after)],
        "merged_flags": [int(obs.merged_flags[index]) for index in range(dirty_after)],
        "flush_rects": [rect_dict(obs.flush_rects[index]) for index in range(trace_count)],
    }


class NativeRenderer:
    def __init__(self, dll: Path):
        self.lib = ctypes.CDLL(str(dll))
        self.lib.db_open.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32,
                                     ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.POINTER(Observation)]
        self.lib.db_open.restype = ctypes.c_int
        self.lib.db_prepare.argtypes = [ctypes.c_uint32, ctypes.POINTER(Observation)]
        self.lib.db_prepare.restype = ctypes.c_int
        self.lib.db_render.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
                                       ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32,
                                       ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint16),
                                       ctypes.c_uint32, ctypes.POINTER(Observation)]
        self.lib.db_render.restype = ctypes.c_int
        self.lib.db_render_dirty.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                             ctypes.c_uint32, ctypes.c_uint32,
                                             ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint16),
                                             ctypes.c_uint32, ctypes.POINTER(DirtyObservation)]
        self.lib.db_render_dirty.restype = ctypes.c_int
        self.lib.db_tick.argtypes = [ctypes.c_uint32, ctypes.POINTER(Observation)]
        self.lib.db_tick.restype = ctypes.c_int
        self.lib.db_control.argtypes = [ctypes.c_uint32, ctypes.POINTER(Observation)]
        self.lib.db_control.restype = ctypes.c_int
        self.lib.db_close.argtypes = [ctypes.POINTER(Observation)]
        self.lib.db_close.restype = ctypes.c_int
        self.raw_buffer: Any = None

    def open(self, raw: bytes, layout: int, fps: int, repeat: bool,
             x: int, y: int, start: int, end: int) -> Observation:
        self.raw_buffer = ctypes.create_string_buffer(raw)
        obs = Observation()
        result = self.lib.db_open(self.raw_buffer, len(raw), layout, fps, int(repeat),
                                  x, y, start, end, ctypes.byref(obs))
        if result != 0:
            raise RuntimeError(f"renderer db_open failed: {result}")
        return obs

    def prepare(self, frame: int) -> Observation:
        obs = Observation()
        result = self.lib.db_prepare(frame, ctypes.byref(obs))
        if result != 0:
            raise RuntimeError(f"renderer db_prepare({frame}) failed: {result}")
        return obs

    def render(self, rect: tuple[int, int, int, int], rows: int,
               swap: bool, background: int) -> tuple[array.array, Observation]:
        x1, y1, x2, y2 = rect
        width, height = x2 - x1, y2 - y1
        pixels = (ctypes.c_uint16 * (width * height))()
        obs = Observation()
        result = self.lib.db_render(x1, y1, x2, y2, rows, int(swap), background,
                                    pixels, width * height, ctypes.byref(obs))
        if result != 0:
            raise RuntimeError(f"renderer db_render({rect}) failed: {result}")
        return array.array("H", pixels), obs

    def render_dirty(self, frame: int, rows: int, swap: bool, background: int,
                     dirty_mode: int) -> tuple[array.array, DirtyObservation, bool]:
        count = 360 * 360
        sentinel = 0xA55A
        storage = (ctypes.c_uint16 * (count + 2))()
        storage[0] = sentinel
        storage[count + 1] = sentinel
        pixels_ptr = ctypes.cast(ctypes.byref(storage, ctypes.sizeof(ctypes.c_uint16)),
                                 ctypes.POINTER(ctypes.c_uint16))
        obs = DirtyObservation()
        result = self.lib.db_render_dirty(frame, rows, int(swap), background,
                                          dirty_mode, pixels_ptr, count, ctypes.byref(obs))
        if result != 0:
            raise RuntimeError(f"renderer db_render_dirty(frame={frame}, mode={dirty_mode}) failed: {result}")
        clean_sentinels = storage[0] == sentinel and storage[count + 1] == sentinel
        return array.array("H", storage[1:count + 1]), obs, clean_sentinels

    def tick(self, delta_ms: int) -> Observation:
        obs = Observation()
        result = self.lib.db_tick(delta_ms, ctypes.byref(obs))
        if result != 0:
            raise RuntimeError(f"renderer db_tick({delta_ms}) failed: {result}")
        return obs

    def control(self, action: int) -> Observation:
        obs = Observation()
        result = self.lib.db_control(action, ctypes.byref(obs))
        if result != 0:
            raise RuntimeError(f"renderer db_control({action}) failed: {result}")
        return obs

    def close(self) -> Observation:
        obs = Observation()
        result = self.lib.db_close(ctypes.byref(obs))
        self.raw_buffer = None
        if result != 0:
            raise RuntimeError(f"renderer db_close failed: {result}")
        return obs


def rgb565(rgba: bytes, background: int) -> int:
    if len(rgba) < 4 or rgba[3] == 0:
        return background
    return ((rgba[0] >> 3) << 11) | ((rgba[1] >> 2) << 5) | (rgba[2] >> 3)


def swap565(value: int) -> int:
    return ((value & 0xFF) << 8) | (value >> 8)


def compose565(frame: bytes, width: int, height: int, layout: int,
               x_arg: int = 0, y_arg: int = 0, background: int = 0,
               swap: bool = False) -> array.array:
    """Mirror gfx_anim geometry and renderer semantics into an RGB565 canvas."""
    canvas = array.array("H", [background]) * (360 * 360)
    if layout == 1:
        x, y, mirror = 10, (360 - height) // 2 + 10, True
        object_width = 340
        mirror_offset = object_width - 2 * width
    elif layout == 2:
        x, y, mirror = (360 - width) // 2, 25, False
        object_width, mirror_offset = width, 0
    elif layout == 3:
        x, y, mirror = x_arg, y_arg, False
        object_width, mirror_offset = width, 0
    else:
        x, y, mirror = (360 - width) // 2, (360 - height) // 2, False
        object_width, mirror_offset = width, 0

    for source_y in range(height):
        screen_y = y + source_y
        if not 0 <= screen_y < 360:
            continue
        row = source_y * width * 4
        for source_x in range(min(width, object_width)):
            value = rgb565(frame[row + source_x * 4:row + source_x * 4 + 4], background)
            if swap:
                value = swap565(value)
            screen_x = x + source_x
            if 0 <= screen_x < 360 and frame[row + source_x * 4 + 3] != 0:
                canvas[screen_y * 360 + screen_x] = value
            if mirror:
                mirrored = width + mirror_offset + width - 1 - source_x
                screen_x = x + mirrored
                if 0 <= mirrored < object_width and 0 <= screen_x < 360 and frame[row + source_x * 4 + 3] != 0:
                    canvas[screen_y * 360 + screen_x] = value
    return canvas


def crop565(canvas: array.array, rect: tuple[int, int, int, int]) -> array.array:
    x1, y1, x2, y2 = rect
    width = x2 - x1
    return array.array("H", (canvas[(y1 + row) * 360 + x1 + col]
                              for row in range(y2 - y1) for col in range(width)))


def hash_u16(values: array.array) -> str:
    return sha256(values.tobytes())


def clean_observation(obs: Observation) -> bool:
    return (obs.corruptions == 0 and obs.prefix_changes == 0 and
            obs.max_tail_bytes == 0 and obs.flush_bounds_error == 0)


def render_check(native: NativeRenderer, decoded: DecodedAnimation, raw: bytes,
                 frame_index: int, layout: int, rect: tuple[int, int, int, int],
                 rows: int, background: int, swap: bool, x: int = 0, y: int = 0) -> dict[str, Any]:
    native.prepare(frame_index)
    actual, obs = native.render(rect, rows, swap, background)
    expected = crop565(compose565(decoded.frames[frame_index], decoded.width, decoded.height,
                                   layout, x, y, background, swap), rect)
    mismatch = [index for index, (got, want) in enumerate(zip(actual, expected)) if got != want]
    return {
        "frame": frame_index, "layout": layout, "rect": list(rect), "rows": rows,
        "background": background, "swap": swap,
        "pixels": len(expected), "mismatch_pixels": len(mismatch),
        "first_mismatch_index": mismatch[0] if mismatch else None,
        "actual_sha256": hash_u16(actual), "expected_sha256": hash_u16(expected),
        "guard_clean": clean_observation(obs),
        "flushes": int(obs.flushes), "flush_bounds_error": int(obs.flush_bounds_error),
        "corruptions": int(obs.corruptions), "prefix_changes": int(obs.prefix_changes),
        "max_tail_bytes": int(obs.max_tail_bytes),
    }


def dirty_render_check(native: NativeRenderer, decoded: DecodedAnimation,
                       frame_index: int, layout: int, rows: int,
                       background: int, swap: bool, dirty_mode: int) -> dict[str, Any]:
    prepared = native.prepare(frame_index)
    actual, dirty_obs, sentinels_clean = native.render_dirty(
        frame_index, rows, swap, background, dirty_mode)
    trace = dirty_obs_dict(dirty_obs)
    expected = compose565(decoded.frames[frame_index], decoded.width, decoded.height,
                          layout, background=background, swap=swap)
    mismatch = [index for index, (got, want) in enumerate(zip(actual, expected)) if got != want]

    expected_flushes: list[list[int]] = []
    for index, area in enumerate(trace["merged"]):
        if trace["merged_flags"][index]:
            continue
        width = area["x2"] - area["x1"] + 1
        height = area["y2"] - area["y1"] + 1
        if width <= 0 or height <= 0:
            continue
        per_flush = trace["buffer_capacity_pixels"] // width
        if per_flush <= 0:
            continue
        current_y = area["y1"]
        while current_y <= area["y2"]:
            next_y = min(area["y2"] + 1, current_y + per_flush)
            expected_flushes.append([area["x1"], current_y, area["x2"] + 1, next_y])
            current_y = next_y

    expected_geometry = (int(prepared.x), int(prepared.y),
                         int(prepared.width), int(prepared.height))
    actual_geometry = (trace["object_x"], trace["object_y"],
                       trace["object_width"], trace["object_height"])
    local = dirty_mode == 1
    merge_required = local and (expected_geometry[2] >= 3 or expected_geometry[3] >= 3)
    flags_merge_count = sum(trace["merged_flags"])
    flush_coords_match = trace["flush_rects"] == [
        {"x1": rect[0], "y1": rect[1], "x2": rect[2], "y2": rect[3]}
        for rect in expected_flushes
    ]
    clean = (trace["corruptions"] == 0 and trace["prefix_changes"] == 0 and
             trace["max_tail_bytes"] == 0 and trace["flush_bounds_error"] == 0 and
             trace["flush_capacity_error"] == 0 and sentinels_clean)
    pipeline_ok = (
        trace["dirty_before"] == (3 if local and trace["dirty_before"] >= 3 else
                                  2 if local else 1) and
        trace["dirty_after_merge"] >= 1 and
        trace["merge_capture_seen"] == 1 and
        (not merge_required or flags_merge_count >= 1) and
        trace["merge_count"] == flags_merge_count and
        trace["flush_trace_truncated"] == 0 and
        trace["flush_trace_count"] == trace["flushes"] and
        trace["flushes"] == len(expected_flushes) and
        flush_coords_match and trace["max_flush_pixels"] <= trace["buffer_capacity_pixels"]
    )
    return {
        "frame": frame_index, "layout": layout, "rows": rows,
        "background": background, "swap": swap,
        "dirty_mode": "local_overlapping" if local else "full_screen",
        "pixels": len(expected), "mismatch_pixels": len(mismatch),
        "first_mismatch_index": mismatch[0] if mismatch else None,
        "actual_sha256": hash_u16(actual), "expected_sha256": hash_u16(expected),
        "geometry_unchanged": actual_geometry == expected_geometry,
        "expected_geometry": list(expected_geometry), "actual_geometry": list(actual_geometry),
        "dirty_pipeline_ok": pipeline_ok, "memory_and_bounds_clean": clean,
        "output_sentinels_clean": sentinels_clean,
        "flush_coordinates_match": flush_coords_match,
        "expected_flushes": expected_flushes,
        "observation": trace,
    }


def run_animation(native: NativeRenderer, raw: bytes, decoded: DecodedAnimation,
                   name: str, *, full_all_frames: bool = True,
                   synthetic: bool = False) -> dict[str, Any]:
    frame_count = len(decoded.frames)
    frame_indices = list(range(frame_count)) if full_all_frames else sorted({0, frame_count // 2, frame_count - 1})
    cases: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    all_dirty_checks: list[dict[str, Any]] = []
    layouts = (0, 1, 2) if not synthetic else (0, 1, 2, 3)
    for layout in layouts:
        x_arg, y_arg = (-3, -2) if layout == 3 else (0, 0)
        native.open(raw, layout, 25, False, x_arg, y_arg, 0, frame_count - 1)
        try:
            for frame in frame_indices:
                rect = (0, 0, 360, 360)
                result = render_check(native, decoded, raw, frame, layout, rect, 32, 0, False, x_arg, y_arg)
                cases.append(result)
                if result["mismatch_pixels"] or not result["guard_clean"]:
                    failures.append(result)
            if not synthetic:
                for frame in sorted({0, frame_count - 1}):
                    for rows in (1, 16, 32):
                        for swap in (False, True):
                            for dirty_mode in (0, 1):
                                result = dirty_render_check(native, decoded, frame, layout, rows,
                                                            0x1234, swap, dirty_mode)
                                all_dirty_checks.append(result)
                                if (result["mismatch_pixels"] or not result["memory_and_bounds_clean"] or
                                        not result["geometry_unchanged"] or not result["dirty_pipeline_ok"]):
                                    failures.append(result)
            # Boundary, odd-width, split-row and transparent-background checks.
            selected = sorted({0, frame_count - 1})
            rects = [(0, 0, 1, 1), (359, 359, 360, 360), (30, 20, 125, 100),
                     (31, 20, 126, 100), (120, 359, 241, 360), (359, 0, 360, 360)]
            if layout == 3:
                x_arg, y_arg = (-3, -2)
                rects += [(0, 0, 13, 13), (347, 347, 360, 360)]
            for frame in selected:
                for rect in rects:
                    for rows in (1, 17, 32):
                        result = render_check(native, decoded, raw, frame, layout, rect, rows, 0x1234, False, x_arg, y_arg)
                        cases.append(result)
                        if result["mismatch_pixels"] or not result["guard_clean"]:
                            failures.append(result)
                result = render_check(native, decoded, raw, frame, layout, (30, 20, 125, 100), 17, 0x1234, True, x_arg, y_arg)
                cases.append(result)
                if result["mismatch_pixels"] or not result["guard_clean"]:
                    failures.append(result)
            # Explicitly exercise nibble-addressing and paired-pixel writes at
            # the same one-pixel clip that exposed the 4-bit renderer defect.
            if decoded.bit_depth == 4:
                targeted_rects = (
                    ("odd_origin_1x1", (95, 118, 96, 119)),
                    ("odd_origin_3x1", (95, 118, 98, 119)),
                    ("even_origin_3x1", (94, 118, 97, 119)),
                    ("odd_origin_1x2", (95, 118, 96, 120)),
                )
                for frame in selected:
                    for clip_name, rect in targeted_rects:
                        result = render_check(native, decoded, raw, frame, layout, rect, 1,
                                              0x1234, False, x_arg, y_arg)
                        result["clip_contract_case"] = clip_name
                        cases.append(result)
                        if result["mismatch_pixels"] or not result["guard_clean"]:
                            failures.append(result)
            # Visibility must leave the supplied background untouched, then restore the frame.
            native.prepare(0)
            hidden = native.control(2)
            pixels, hidden_obs = native.render((0, 0, 360, 360), 32, False, 0x1234)
            hidden_ok = all(value == 0x1234 for value in pixels) and clean_observation(hidden_obs)
            shown = native.control(3)
            shown_result = render_check(native, decoded, raw, 0, layout, (0, 0, 360, 360), 32, 0x1234, False, x_arg, y_arg)
            shown_ok = shown_result["mismatch_pixels"] == 0 and shown_result["guard_clean"] and clean_observation(shown)
            visibility = {"layout": layout, "hidden_background_only": hidden_ok, "shown_matches": shown_ok,
                          "hidden_observation": obs_dict(hidden_obs), "show_observation": obs_dict(shown)}
            cases.append({"visibility": visibility})
            if not hidden_ok or not shown_ok:
                failures.append(visibility)
        finally:
            close_obs = native.close()
            if close_obs.live_allocations != 0:
                failures.append({"layout": layout, "close_live_allocations": int(close_obs.live_allocations)})

    clip_cases = [item for item in cases if "clip_contract_case" in item]
    clip_failures = [item for item in clip_cases
                     if item["mismatch_pixels"] or not item["guard_clean"]]
    dirty_failures = [item for item in failures if "dirty_mode" in item]
    clip_status = ("not_applicable" if decoded.bit_depth != 4 else
                   "pass" if clip_cases and not clip_failures else "fail")
    return {
        "name": name, "sha256": sha256(raw), "size_bytes": len(raw),
        "frame_count": frame_count, "dimensions": [decoded.width, decoded.height],
        "bit_depth": decoded.bit_depth, "fps": decoded.frame_rate,
        "cases": len(cases), "failures": len(failures),
        "dirty_render_checks": len(all_dirty_checks),
        "dirty_render_failures": sum(
            1 for item in failures if "dirty_mode" in item),
        "dirty_pipeline_status": ("not_run" if not all_dirty_checks else
                                   "pass" if not dirty_failures else "fail"),
        "dirty_checks": all_dirty_checks,
        "full_frame_checks": len(frame_indices) * len(layouts),
        "all_compared_pixels_equal": not any(item.get("mismatch_pixels", 0) for item in failures),
        "all_guard_and_flush_checks_clean": not any(not item.get("guard_clean", True) for item in failures),
        "status": "pass" if not failures else "fail",
        "failure_examples": failures[:24],
        "clipping_contract": {
            "status": clip_status,
            "case_count": len(clip_cases),
            "failed_case_count": len(clip_failures),
            "cases": sorted({item["clip_contract_case"] for item in clip_cases}),
            "failure_examples": clip_failures[:12],
        },
        "observed_output_sha256_sequence": sha256("\n".join(item.get("actual_sha256", "") for item in cases).encode()),
        "expected_output_sha256_sequence": sha256("\n".join(item.get("expected_sha256", "") for item in cases).encode()),
        "scope_note": ("anchored candidates use first/middle/last frames for regular rendering; "
                       "boundary/layout and dirty-pipeline checks use first and last frames; "
                       "synthetic cases exercise their complete tiny frame sets"),
    }


def timer_check(native: NativeRenderer, raw: bytes, decoded: DecodedAnimation) -> dict[str, Any]:
    end = min(2, len(decoded.frames)) - 1
    native.open(raw, 0, 25, False, 0, 0, 0, end)
    try:
        native.prepare(0)
        observations = [{"at": 0, **obs_dict(native.tick(0))}]
        for delta in (39, 1, 40, 40, 40):
            observations.append({"delta": delta, **obs_dict(native.tick(delta))})
        nonloop = {"observations": observations,
                   "period_ms": observations[0]["period_ms"],
                   "prepared_end_frame_observed": any(row["prepared_frame"] == end for row in observations),
                   "stopped_after_end": observations[-1]["is_playing"] == 0,
                   "all_frame_done_event": observations[-1]["all_done"] > 0}
    finally:
        native.close()

    native.open(raw, 0, 25, True, 0, 0, 0, end)
    try:
        native.prepare(0)
        loop_obs = [obs_dict(native.tick(40)) for _ in range(6)]
    finally:
        native.close()
    nonloop["loop"] = {
        "observations": loop_obs,
        "all_frame_done_events": max(row["all_done"] for row in loop_obs),
        "current_frame_revisited_start": any(row["current_frame"] == 0 for row in loop_obs),
    }
    nonloop["interpretation"] = (
        "official timer reaches completion/repeat at current_frame >= end_frame; the observed final frame prepare behavior is recorded, not corrected"
    )
    return nonloop


def make_synthetic() -> tuple[bytes, bytes]:
    def frames(width: int) -> tuple[bytes, ...]:
        output = []
        for phase in range(2):
            frame = bytearray(width * 35 * 4)
            for y in range(35):
                for x in range(width):
                    pos = (y * width + x) * 4
                    if (x + y + phase) % 5 == 0:
                        frame[pos:pos + 4] = b"\0\0\0\0"
                    elif (x + phase) % 3 == 0:
                        frame[pos:pos + 4] = bytes((255, 0, 0, 255))
                    elif y % 2:
                        frame[pos:pos + 4] = bytes((0, 255, 0, 255))
                    else:
                        frame[pos:pos + 4] = bytes((0, 0, 255, 255))
            output.append(bytes(frame))
        return tuple(output)
    source8 = encode_rgba_frames(frames(7), 7, 35, block_height=32, palette_colors=8)
    source4 = transcode_legacy_eaf(encode_rgba_frames(frames(8), 8, 35, block_height=32, palette_colors=8),
                                   encoding=0, bit_depth=4, deduplicate=False)
    return source8, source4


def package_report() -> dict[str, Any]:
    official_raw = OFFICIAL_PACKAGE.read_bytes()
    if sha256(official_raw) != EXPECTED["official_package"]:
        raise ValueError("official package hash differs from immutable anchor")
    official = RESOURCES.parse_package_bytes(official_raw, modern=False)
    if PACKAGES.build_legacy_package(official["members"]) != official_raw:
        raise ValueError("official package cannot be reproduced byte-for-byte")
    index = json.loads(official["members"]["index.json"])
    source_manifest = RESOURCES.read_json(OFFICIAL_SOURCE / "main/boards/esp_vocat/assets/360_360/emote.json")
    source_map = {item["emote"]: item["src"] for item in source_manifest}
    index_map = PACKAGES._index_semantics(index)
    if source_map != index_map or RESOURCES.git_commit(OFFICIAL_SOURCE) != EXPECTED["source_commit"]:
        raise ValueError("package index and official emote manifest differ")

    candidates = {}
    for name, path, expected in (("candidate_4bit", CANDIDATE_4, EXPECTED["candidate_4"]),
                                 ("candidate_8bit", CANDIDATE_8, EXPECTED["candidate_8"])):
        raw = path.read_bytes()
        if sha256(raw) != expected:
            raise ValueError(f"{name} hash differs from E1.3b anchor")
        candidate_index = json.loads(json.dumps(index))
        candidate_index["emoji_collection"].append({"name": "shy_candidate", "file": "shy_candidate.eaf",
                                                     "eaf": {"loop": True, "fps": 25}})
        members = dict(official["members"])
        members["index.json"] = (json.dumps(candidate_index, ensure_ascii=False, indent=4) + "\n").encode()
        members["shy_candidate.eaf"] = raw
        package = PACKAGES.build_legacy_package(members)
        parsed = RESOURCES.parse_package_bytes(package, modern=False)
        if parsed["members"] != members:
            raise ValueError(f"{name} package round-trip changed members")
        if any(parsed["members"][member] != raw_value
               for member, raw_value in official["members"].items() if member != "index.json"):
            raise ValueError(f"{name} package changed an official non-index member")
        if PACKAGES._index_semantics(json.loads(parsed["members"]["index.json"])) != index_map | {
                "shy_candidate": "shy_candidate.eaf"}:
            raise ValueError(f"{name} package changed official semantics")
        candidates[name] = {"eaf_sha256": expected, "eaf_size_bytes": len(raw),
                            "package_sha256": sha256(package), "package_size_bytes": len(package),
                            "remaining_partition_bytes": PARTITION_BYTES - len(package),
                            "fits_partition": len(package) <= PARTITION_BYTES,
                            "device_candidate_allowed_before_renderer_gate": False}
    return {
        "official": {"sha256": sha256(official_raw), "size_bytes": len(official_raw),
                      "partition_bytes": PARTITION_BYTES, "remaining_bytes": PARTITION_BYTES - len(official_raw),
                      "member_count": official["header"]["member_count"],
                      "members": [{"name": name, "size_bytes": len(raw), "sha256": sha256(raw)}
                                  for name, raw in sorted(official["members"].items())]},
        "source_commit": EXPECTED["source_commit"], "semantic_count": len(source_manifest),
        "candidate_packages": candidates,
        "all_official_members_unchanged": True, "official_index_matches_source_manifest": True,
        "package_round_trip_byte_identical": True,
    }


def source_anchor_report() -> dict[str, Any]:
    files = {"gfx_anim": PLAYER / "src/widget/gfx_anim.c",
             "gfx_eaf_dec": PLAYER / "src/lib/eaf/gfx_eaf_dec.c",
             "gfx_refr": PLAYER / "src/core/gfx_refr.c",
             "gfx_timer": PLAYER / "src/core/gfx_timer.c",
             "gfx_render": PLAYER / "src/core/gfx_render.c",
             "gfx_obj": PLAYER / "src/core/gfx_obj.c",
             "gfx_types": PLAYER / "include/core/gfx_types.h"}
    actual = {name: file_sha(path) for name, path in files.items()}
    if any(actual[name] != EXPECTED[name] for name in actual):
        raise ValueError(f"renderer source anchor changed: {actual}")
    source_commit = RESOURCES.git_commit(OFFICIAL_SOURCE)
    if source_commit != EXPECTED["source_commit"]:
        raise ValueError("official source checkout commit differs from immutable anchor")
    return {"source_commit": source_commit, "files": {
        name: {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": actual[name]}
        for name, path in files.items()}, "shared_source_modified": False}


def compile_renderer(output: Path) -> tuple[Path, dict[str, Any]]:
    if not COMPILER.is_file():
        raise FileNotFoundError(f"existing Zig compiler not found: {COMPILER}")
    dll = output / "renderer-audit-v1.dll"
    command = [str(COMPILER), "cc", "-std=c11", "-O1", "-Wall", "-Wextra",
               "-I", str(HARNESS), "-I", str(PLAYER / "include"), "-I", str(PLAYER / "src"),
               "-shared", "-fPIC", str(HARNESS / "gfx_renderer_harness.c"), "-o", str(dll)]
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(output / "cache-local"),
               ZIG_GLOBAL_CACHE_DIR=str(output / "cache-global"))
    result = subprocess.run(command, cwd=output, env=env, capture_output=True, text=True, timeout=180)
    compile_text = "COMMAND: " + subprocess.list2cmdline(command) + "\n" + result.stdout + result.stderr
    immutable(output / "compile.log", compile_text.encode())
    if result.returncode:
        raise RuntimeError(f"renderer harness compilation failed; see {output / 'compile.log'}")
    return dll, {"command": command, "returncode": result.returncode,
                 "dll_sha256": file_sha(dll), "dll_size_bytes": dll.stat().st_size}


def worker(dll: Path, result_path: Path, case: str) -> None:
    native = NativeRenderer(dll)
    synthetic8, synthetic4 = make_synthetic()
    synthetic8_path = result_path.parent / "synthetic-8bit-7x35.eaf"
    synthetic4_path = result_path.parent / "synthetic-4bit-8x35.eaf"
    immutable(synthetic8_path, synthetic8)
    immutable(synthetic4_path, synthetic4)
    cases = {
        "candidate_4bit": (CANDIDATE_4, EXPECTED["candidate_4"], False, False),
        "candidate_8bit": (CANDIDATE_8, EXPECTED["candidate_8"], False, False),
        "e1_2_reference": (REFERENCE, EXPECTED["reference"], False, False),
        "synthetic_8bit_7x35": (synthetic8_path, None, True, True),
        "synthetic_4bit_8x35": (synthetic4_path, None, True, True),
    }
    if case not in cases:
        raise ValueError(f"unknown isolated renderer case: {case}")
    path, expected_hash, full, synthetic = cases[case]
    raw = path.read_bytes()
    if expected_hash and sha256(raw) != expected_hash:
        raise ValueError(f"immutable asset hash mismatch for {case}")
    decoded = decode_eaf(path, fps=25)
    animation = run_animation(native, raw, decoded, case, full_all_frames=full, synthetic=synthetic)
    timing = timer_check(native, raw, decoded) if case == "synthetic_8bit_7x35" else None
    result = {"case": case, "animation": animation, "timer": timing}
    immutable(result_path, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())


def build_and_audit(output: Path) -> dict[str, Any]:
    output = safe_output(output)
    output.mkdir(parents=True, exist_ok=True)
    anchors = source_anchor_report()
    player_anchor = COMPRESSION._verify_player_anchor()
    packages = package_report()
    dll, toolchain = compile_renderer(output)
    child_logs = []
    native_cases = {}
    case_names = ("candidate_4bit", "candidate_8bit", "e1_2_reference",
                  "synthetic_8bit_7x35", "synthetic_4bit_8x35")
    for case in case_names:
        case_result_path = output / f"renderer-{case}.json"
        child = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                                "--case", case, "--dll", str(dll), "--result", str(case_result_path)],
                               cwd=output, capture_output=True, text=True, timeout=180)
        child_logs.append(f"CASE {case} EXIT {child.returncode}\n{child.stdout}{child.stderr}")
        if child.returncode:
            immutable(output / "renderer-run.log", "\n".join(child_logs).encode())
            raise RuntimeError(f"renderer worker {case} failed ({child.returncode}); see {output / 'renderer-run.log'}")
        native_cases[case] = json.loads(case_result_path.read_text(encoding="utf-8"))
    immutable(output / "renderer-run.log", "\n".join(child_logs).encode())
    native = {"animations": {name: value["animation"] for name, value in native_cases.items()},
              "timer": native_cases["synthetic_8bit_7x35"]["timer"]}
    # Keep the worker evidence immutable but give the final report a stable, concise schema.
    candidate4 = native["animations"]["candidate_4bit"]
    candidate8 = native["animations"]["candidate_8bit"]
    candidate4_clipping = candidate4["clipping_contract"]
    candidate4_reason = (
        f"renderer clipping contract failed ({candidate4_clipping['failed_case_count']} of "
        f"{candidate4_clipping['case_count']} targeted cases); capacity fit is not sufficient"
        if candidate4_clipping["status"] != "pass" else
        "renderer scope passed, but deployment still requires separate approval"
    )
    candidate8_reason = (
        "8-bit lossless package exceeds the 6,144,000-byte assets partition"
        if not packages["candidate_packages"]["candidate_8bit"]["fits_partition"] else
        "renderer scope passed, but deployment still requires separate approval"
    )
    result = {
        "schema": "deskbot.expression_e1_3e_dirty_render_audit/v1",
        "result": "offline_audit_complete_no_flash_candidate",
        "hardware_accessed": False, "firmware_changed": False, "c61_changed": False,
        "device_package_eligible": False,
        "anchors": anchors, "player_anchor": player_anchor,
        "toolchain": toolchain, "package": packages,
        "renderer": native,
        "deployment_gate": {
            "candidate_4bit": {"renderer_scope_pass": candidate4["status"] == "pass",
                               "clipping_contract": candidate4_clipping,
                               "fits_partition": packages["candidate_packages"]["candidate_4bit"]["fits_partition"],
                               "device_eligible": False,
                               "reason": candidate4_reason},
            "candidate_8bit": {"renderer_scope_pass": candidate8["status"] == "pass",
                               "fits_partition": packages["candidate_packages"]["candidate_8bit"]["fits_partition"],
                               "device_eligible": False,
                               "reason": candidate8_reason},
            "official_resource_package_remains_rollback": True,
        },
        "next_gate": "keep the official package; do not flash E2. Dirty merge/render/flush passed on the anchored 8-bit/reference paths, while 4-bit odd-coordinate clipping still fails. Isolate a bounded 4-bit renderer contract experiment or design a smaller complete package, then repeat source, rendering, package, build, partition, rollback, and hardware regression gates",
        "scope_exclusions": ["serial/NVS/C61", "firmware build", "LCD/target ABI", "DeepSeek/client integration", "malformed-input fuzzing"],
    }
    immutable(output / "renderer-audit-v1.json", (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="new evidence directory inside project tmp; existing evidence is never overwritten")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--case", choices=("candidate_4bit", "candidate_8bit", "e1_2_reference",
                                             "synthetic_8bit_7x35", "synthetic_4bit_8x35"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--dll", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            if not args.dll or not args.result or not args.case:
                parser.error("worker requires --case, --dll and --result")
            if args.dll.resolve().parent != safe_output(args.result.parent):
                parser.error("worker DLL and result must share the isolated output stage")
            worker(args.dll, args.result, args.case)
            return 0
        if args.output is None:
            parser.error("--output is required; choose a fresh stage directory inside project tmp")
        result = build_and_audit(args.output)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, struct.error) as error:
        print(json.dumps({"result": "rejected", "hardware_accessed": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"result": result["result"], "device_package_eligible": False,
                      "candidate_4bit": result["deployment_gate"]["candidate_4bit"],
                      "candidate_8bit": result["deployment_gate"]["candidate_8bit"],
                      "report": str((args.output / "renderer-audit-v1.json").resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
