#!/usr/bin/env python3
"""E1.11 host-only audit of E1.10 resources through the anchored C player.

This stage does not build firmware, open COM ports, modify production sources,
write NVS, or create a deployable resource package.  It compiles a read-only
host harness around the hash-anchored Espressif EAF decoder/animation/timer and
compares the original and 8-bit lossless candidates for insert/eat.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RENDER_SCRIPT = ROOT / "scripts/audit-eaf-renderer.py"
COMPILER = ROOT / "tmp/zig-x86_64-windows-0.15.2/zig.exe"
HARNESS = ROOT / "tests/eaf_player_host/gfx_player_harness.c"
PLAYER = ROOT / "tmp/o2f2-o2d-readonly-20261018/source/managed_components/espressif2022__esp_emote_gfx"
EMOTE_ASSETS = ROOT / "tmp/o2f2-o2d-readonly-20261018/source/managed_components/espressif2022__esp_emote_assets"
EMOTE_EXPRESSION = ROOT / "tmp/o2f2-o2d-readonly-20261018/source/managed_components/espressif2022__esp_emote_expression"
EMOTE_INDEX = EMOTE_ASSETS / "scripts/spiffs_assets/build/face/output/index.json"
EMOTE_OP = EMOTE_EXPRESSION / "src/emote_op.c"
OUTPUT_DEFAULT = ROOT / "tmp/expression-e1-11-player-20261023-run1"
AUDIT_TOOLS = {
    "audit_script": Path(__file__).resolve(),
    "player_harness": HARNESS,
    "renderer_harness": ROOT / "tests/eaf_renderer_host/gfx_renderer_harness.c",
    "renderer_audit": RENDER_SCRIPT,
    "compression_audit": ROOT / "scripts/audit-expression-compression.py",
}
# JSON list index 0 is eye_anim; the C harness's layout mode 1 selects it.
OFFICIAL_LAYOUT = {"index": 0, "align": "GFX_ALIGN_LEFT_MID", "x": 10, "y": 10,
                   "mirror": "auto"}

INPUTS: dict[str, tuple[str, str]] = {
    "insert_original": (
        "artifacts/expression-e1-10-review-20261022/eaf/insert-original.eaf",
        "1d3d2babc0770594f8fdf0701614638e3e03647b18c32674beae3fc3d6459ef1",
    ),
    "insert_best_8bit": (
        "artifacts/expression-e1-10-review-20261022/eaf/insert-best-8bit.eaf",
        "2eb63094840a657a7e6bc5841dbacbfdda809151f344c403dee4255c9743f744",
    ),
    "eat_original": (
        "artifacts/expression-e1-10-review-20261022/eaf/eat-original.eaf",
        "df8d5e00c5e5dd9abd618162d19a9bffbb50d6769c4cc09f88c6977b9d8485da",
    ),
    "eat_best_8bit": (
        "artifacts/expression-e1-10-review-20261022/eaf/eat-best-8bit.eaf",
        "2abe739070ca8e3ae0bd437360f94b24a74d2bf0be6a86eb3c33ba223fef5030",
    ),
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RENDER = load_module(RENDER_SCRIPT, "deskbot_e111_renderer")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"refusing to overwrite different evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def safe_output(path: Path) -> Path:
    resolved = path.resolve()
    tmp_root = (ROOT / "tmp").resolve()
    if resolved == tmp_root or resolved.parent != tmp_root:
        raise ValueError("E1.11 output must be a direct child of project tmp")
    if not resolved.name.startswith("expression-e1-11-player-"):
        raise ValueError("E1.11 output name must start with expression-e1-11-player-")
    if resolved.exists() and any(resolved.iterdir()):
        raise ValueError(f"refusing to reuse non-empty E1.11 evidence directory: {resolved}")
    return resolved


class PlayerNative(RENDER.NativeRenderer):
    """Renderer harness plus the E1.11 read-only observations."""

    def __init__(self, dll: Path):
        super().__init__(dll)
        self.lib.db_frame_type.argtypes = [RENDER.ctypes.c_uint32,
                                           RENDER.ctypes.POINTER(RENDER.ctypes.c_int32)]
        self.lib.db_frame_type.restype = RENDER.ctypes.c_int
        self.lib.db_frame_type_counts.argtypes = [RENDER.ctypes.POINTER(RENDER.ctypes.c_uint32),
                                                  RENDER.ctypes.POINTER(RENDER.ctypes.c_uint32),
                                                  RENDER.ctypes.POINTER(RENDER.ctypes.c_uint32)]
        self.lib.db_frame_type_counts.restype = RENDER.ctypes.c_int
        self.lib.db_tick_render.argtypes = [RENDER.ctypes.c_uint32, RENDER.ctypes.c_uint32,
                                            RENDER.ctypes.c_uint32, RENDER.ctypes.c_uint32,
                                            RENDER.ctypes.POINTER(RENDER.ctypes.c_uint16),
                                            RENDER.ctypes.c_uint32,
                                            RENDER.ctypes.POINTER(RENDER.DirtyObservation),
                                            RENDER.ctypes.POINTER(RENDER.Observation)]
        self.lib.db_tick_render.restype = RENDER.ctypes.c_int

    def frame_type(self, frame: int) -> int:
        result = RENDER.ctypes.c_int32()
        code = self.lib.db_frame_type(frame, RENDER.ctypes.byref(result))
        if code != 0:
            raise RuntimeError(f"frame type query failed for {frame}: {code}")
        return int(result.value)

    def frame_type_counts(self) -> dict[str, int]:
        valid = RENDER.ctypes.c_uint32()
        flag = RENDER.ctypes.c_uint32()
        invalid = RENDER.ctypes.c_uint32()
        code = self.lib.db_frame_type_counts(RENDER.ctypes.byref(valid),
                                              RENDER.ctypes.byref(flag),
                                              RENDER.ctypes.byref(invalid))
        if code != 0:
            raise RuntimeError(f"frame type counts failed: {code}")
        return {"valid_image": int(valid.value), "metadata_flag": int(flag.value),
                "invalid": int(invalid.value)}

    def prepare_code(self, frame: int) -> tuple[int, RENDER.Observation]:
        obs = RENDER.Observation()
        code = self.lib.db_prepare(frame, RENDER.ctypes.byref(obs))
        return int(code), obs

    def tick_render(self, delta_ms: int, rows: int = 32,
                    swap: bool = False, background: int = 0) -> tuple[int, array.array,
                                                                     RENDER.DirtyObservation,
                                                                     RENDER.Observation]:
        count = 360 * 360
        pixels = (RENDER.ctypes.c_uint16 * count)()
        dirty = RENDER.DirtyObservation()
        obs = RENDER.Observation()
        result = self.lib.db_tick_render(delta_ms, rows, int(swap), background,
                                         pixels, count, RENDER.ctypes.byref(dirty),
                                         RENDER.ctypes.byref(obs))
        if result < 0:
            raise RuntimeError(f"renderer db_tick_render failed: {result}")
        return result, array.array("H", pixels), dirty, obs


def compile_harness(output: Path) -> dict[str, Any]:
    if not COMPILER.is_file():
        raise FileNotFoundError(f"Zig compiler missing: {COMPILER}")
    if not HARNESS.is_file():
        raise FileNotFoundError(f"E1.11 harness missing: {HARNESS}")
    dll = output / "player-audit-e1-11.dll"
    command = [str(COMPILER), "cc", "-std=c11", "-O1", "-Wall", "-Wextra",
               "-I", str(HARNESS.parent.parent / "eaf_renderer_host"),
               "-I", str(PLAYER / "include"), "-I", str(PLAYER / "src"),
               "-shared", "-fPIC", str(HARNESS), "-o", str(dll)]
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(output / "cache-local"),
               ZIG_GLOBAL_CACHE_DIR=str(output / "cache-global"))
    try:
        result = subprocess.run(command, cwd=output, env=env, capture_output=True,
                                text=True, timeout=600)
    except subprocess.TimeoutExpired as error:
        partial = as_text(error.stdout) + as_text(error.stderr)
        immutable(output / "compile-timeout.log",
                  ("COMMAND: " + subprocess.list2cmdline(command) + "\n" + partial).encode())
        raise RuntimeError(f"E1.11 C harness compilation timed out after 600s; see {output / 'compile-timeout.log'}") from error
    compile_text = "COMMAND: " + subprocess.list2cmdline(command) + "\n" + result.stdout + result.stderr
    immutable(output / "compile.log", compile_text.encode())
    if result.returncode:
        raise RuntimeError(f"E1.11 C harness compilation failed; see {output / 'compile.log'}")
    version_result = subprocess.run([str(COMPILER), "version"], capture_output=True,
                                    text=True, timeout=30, check=False)
    return {"command": command, "returncode": result.returncode,
            "dll": str(dll.relative_to(ROOT)).replace("\\", "/"),
            "dll_sha256": file_sha(dll), "dll_size_bytes": dll.stat().st_size,
            "compiler": str(COMPILER.relative_to(ROOT)).replace("\\", "/"),
            "compiler_sha256": file_sha(COMPILER),
            "compiler_version": (version_result.stdout + version_result.stderr).strip(),
            "optimization": "O1"}


def clean(obs: RENDER.Observation) -> bool:
    return (obs.corruptions == 0 and obs.prefix_changes == 0 and
            obs.max_tail_bytes == 0 and obs.flush_bounds_error == 0)


def dirty_summary(dirty: RENDER.DirtyObservation) -> dict[str, Any]:
    detail = RENDER.dirty_obs_dict(dirty)
    return {key: detail[key] for key in (
        "dirty_before", "dirty_after_merge", "merge_count", "flushes",
        "flush_bounds_error", "flush_capacity_error", "max_flush_height",
        "max_flush_pixels", "buffer_capacity_pixels", "flush_trace_count",
        "flush_trace_truncated", "corruptions", "prefix_changes",
        "max_tail_bytes", "merge_capture_seen")}


def observation_summary(obs: RENDER.Observation) -> dict[str, int]:
    return RENDER.obs_dict(obs)


def render_hash(native: PlayerNative, decoded: Any, frame: int) -> dict[str, Any]:
    native.prepare(frame)
    actual, obs = native.render((0, 0, 360, 360), 32, False, 0x0000)
    expected = RENDER.crop565(
        RENDER.compose565(decoded.frames[frame], decoded.width, decoded.height, 1,
                           background=0, swap=False), (0, 0, 360, 360))
    mismatch = sum(got != want for got, want in zip(actual, expected))
    return {"frame": frame, "rendered": True,
            "actual_sha256": RENDER.hash_u16(actual),
            "expected_sha256": RENDER.hash_u16(expected),
            "mismatch_pixels": mismatch, "guard_clean": clean(obs),
            "observation": observation_summary(obs)}


def render_prepared(native: PlayerNative, decoded: Any, frame: int,
                    actual: array.array, dirty: RENDER.DirtyObservation,
                    obs: RENDER.Observation, rendered: bool) -> dict[str, Any]:
    expected = RENDER.crop565(
        RENDER.compose565(decoded.frames[frame], decoded.width, decoded.height, 1,
                           background=0, swap=False), (0, 0, 360, 360))
    return {"frame": frame, "actual_sha256": RENDER.hash_u16(actual),
            "expected_sha256": RENDER.hash_u16(expected),
            "mismatch_pixels": sum(got != want for got, want in zip(actual, expected)),
            "rendered": rendered,
            "guard_clean": (clean(obs) and dirty.corruptions == 0 and
                            dirty.prefix_changes == 0 and dirty.max_tail_bytes == 0 and
                            dirty.flush_bounds_error == 0 and dirty.flush_capacity_error == 0 and
                            dirty.flush_trace_truncated == 0),
            "dirty": dirty_summary(dirty),
            "observation": observation_summary(obs)}


def timer_trace(native: PlayerNative, raw: bytes, decoded: Any, image_count: int,
                repeat: bool) -> dict[str, Any]:
    total = decoded.stored_frame_count
    fps = decoded.frame_rate
    tick_ms = max(1, round(1000 / fps))
    native.open(raw, 1, fps, repeat, 0, 0, 0, total - 1)
    prepared: list[int] = []
    observations: list[dict[str, Any]] = []
    render_checks: list[dict[str, Any]] = []
    no_render_ticks: list[dict[str, Any]] = []
    wrap_tick = None
    revisit_tick = None
    try:
        # The official timer completes at the end boundary without preparing
        # the trailing metadata frame.  In loop mode it resets current_frame
        # on the completion tick, then prepares frame zero on the *next* tick.
        limit = image_count + 3
        for index in range(limit):
            rendered, pixels, dirty, obs = native.tick_render(tick_ms)
            row = {"tick": index, "rendered": rendered == 1,
                   **observation_summary(obs)}
            observations.append(row)
            if obs.prepared_frame >= 0:
                prepared.append(int(obs.prepared_frame))
                if rendered == 1:
                    render_checks.append(render_prepared(
                        native, decoded, int(obs.prepared_frame), pixels, dirty, obs, True))
                else:
                    no_render_ticks.append({
                        "tick": index,
                        "dirty": dirty_summary(dirty),
                        "observation": observation_summary(obs),
                        "guard_clean": (clean(obs) and dirty.corruptions == 0 and
                                        dirty.prefix_changes == 0 and dirty.max_tail_bytes == 0 and
                                        dirty.flush_bounds_error == 0 and
                                        dirty.flush_capacity_error == 0 and
                                        dirty.flush_trace_truncated == 0),
                    })
            if repeat and obs.all_done > 0 and wrap_tick is None:
                wrap_tick = index
            if not repeat and obs.is_playing == 0:
                break
            if (repeat and wrap_tick is not None and len(prepared) > image_count and
                    prepared[-1] == 0):
                revisit_tick = index
                break
        last = observations[-1]
        unique = sorted(set(prepared))
        metadata_prepared = [frame for frame in prepared if frame >= image_count]
        expected_prepared = list(range(image_count)) + [image_count - 1]
        expected_rendered = list(range(image_count))
        if repeat:
            expected_prepared.append(0)
            expected_rendered.extend([image_count - 1, 0])
        rendered_sequence = [item["frame"] for item in render_checks]
        no_render_contract = (
            repeat and not no_render_ticks
        ) or (
            not repeat and len(no_render_ticks) == 1 and
            no_render_ticks[0]["tick"] == len(observations) - 1 and
            no_render_ticks[0]["observation"]["is_playing"] == 0 and
            no_render_ticks[0]["observation"]["all_done"] > 0 and
            no_render_ticks[0]["dirty"]["flushes"] == 0 and
            no_render_ticks[0]["guard_clean"]
        )
        return {
            "repeat": repeat, "ticks": len(observations), "prepared_sequence": prepared,
            "unique_prepared_frames": unique, "metadata_prepared_frames": metadata_prepared,
            "expected_prepared_sequence": expected_prepared,
            "prepared_sequence_exact": prepared == expected_prepared,
            "rendered_sequence": rendered_sequence,
            "expected_rendered_sequence": expected_rendered,
            "rendered_sequence_exact": rendered_sequence == expected_rendered,
            "all_image_frames_observed": unique == list(range(image_count)),
            "no_metadata_prepared": not metadata_prepared,
            "no_render_ticks": no_render_ticks,
            "no_render_contract": no_render_contract,
            "fps": fps, "tick_ms": tick_ms,
            "period_ms": observations[0]["period_ms"] if observations else None,
            "stopped_after_end": (not repeat and last["is_playing"] == 0),
            "completion_event": (not repeat and last["all_done"] > 0),
            "loop_event": (repeat and wrap_tick is not None),
            "loop_wrap_tick": wrap_tick,
            "loop_revisit_tick": revisit_tick,
            "loop_revisited_start": (repeat and revisit_tick is not None),
            "render_checks": render_checks,
            "render_all_equal": (rendered_sequence == expected_rendered and
                                  all(item["mismatch_pixels"] == 0 for item in render_checks)),
            "render_all_guard_clean": (rendered_sequence == expected_rendered and
                                        all(item["guard_clean"] for item in render_checks)),
            "observations": observations,
        }
    finally:
        close = native.close()
        if close.live_allocations != 0 or not clean(close):
            raise RuntimeError(f"timer close was not clean: {observation_summary(close)}")


def run_case(native: PlayerNative, name: str, path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    expected_hash = INPUTS[name][1]
    if sha(raw) != expected_hash:
        raise ValueError(f"{name}: immutable input hash mismatch")
    decoded = RENDER.decode_eaf(path, fps=20)
    total = decoded.stored_frame_count
    image_count = total - decoded.metadata_frame_count
    if decoded.metadata_frame_count != 1 or image_count <= 0:
        raise ValueError(f"{name}: expected one metadata frame and image frames")

    native.open(raw, 1, decoded.frame_rate, False, 0, 0, 0, total - 1)
    try:
        type_counts = native.frame_type_counts()
        frame_types = [native.frame_type(frame) for frame in range(total)]
        initial = native.prepare(0)
        render_checks = [render_hash(native, decoded, frame) for frame in range(image_count)]
        before_bad = native.prepare(image_count - 1)
        bad_code, after_bad = native.prepare_code(image_count)
        metadata_safety = {
            "code_nonzero": bad_code != 0,
            "code": bad_code,
            "prepared_image_before": int(before_bad.prepared_frame),
            "prepared_after": int(after_bad.prepared_frame),
            "prepared_frame_preserved": (after_bad.prepared_frame == image_count - 1),
            "allocations_preserved": (after_bad.live_allocations == before_bad.live_allocations),
            "guards_clean": clean(after_bad),
        }
        metadata_safety["pass"] = (
            metadata_safety["code_nonzero"] and
            metadata_safety["prepared_frame_preserved"] and
            metadata_safety["allocations_preserved"] and
            metadata_safety["guards_clean"])
        stop = native.control(0)
        restart = native.control(1)
        restart_tick = native.tick(max(1, round(1000 / decoded.frame_rate)))
        restart_check = {"stop_playing": int(stop.is_playing),
                         "restart_playing": int(restart.is_playing),
                         "restart_prepared_frame": int(restart_tick.prepared_frame),
                         "restart_starts_at_zero": restart_tick.prepared_frame == 0}
    finally:
        close = native.close()
        close_clean = close.live_allocations == 0 and clean(close)

    reloads = []
    for _ in range(3):
        native.open(raw, 1, decoded.frame_rate, False, 0, 0, 0, total - 1)
        try:
            native.prepare(0)
        finally:
            close = native.close()
            reloads.append({"live_allocations": int(close.live_allocations),
                            "guards_clean": clean(close)})

    nonloop = timer_trace(native, raw, decoded, image_count, False)
    loop = timer_trace(native, raw, decoded, image_count, True)
    case = {
        "name": name, "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha(raw), "size_bytes": len(raw),
        "stored_frame_count": total, "image_frame_count": image_count,
        "metadata_frame_count": decoded.metadata_frame_count,
        "decoded_frame_sha256": [sha(frame) for frame in decoded.frames],
        "fps": decoded.frame_rate, "loop": False,
        "layout": "eye_anim_auto_mirror",
        "frame_type_counts": type_counts, "frame_types": frame_types,
        "frame_types_image_then_metadata": frame_types == [0] * image_count + [3],
        "initial_prepare_ok": initial.prepared_frame == 0,
        "render_frame_count": len(render_checks),
        "render_checks": render_checks,
        "render_all_equal": all(item["mismatch_pixels"] == 0 for item in render_checks),
        "render_all_guard_clean": all(item["guard_clean"] for item in render_checks),
        "metadata_prepare_safety": metadata_safety,
        "stop_restart": restart_check,
        "close_clean": close_clean,
        "reloads": reloads,
        "reloads_clean": all(item["live_allocations"] == 0 and item["guards_clean"] for item in reloads),
        "nonloop": nonloop, "loop": loop,
    }
    errors = case_contract_errors(case)
    case["contract_errors"] = errors
    case["case_pass"] = not errors
    return case


def verify_inputs() -> dict[str, dict[str, Any]]:
    pinned = {}
    for name, (relative, expected) in INPUTS.items():
        path = ROOT / relative
        actual = file_sha(path)
        if actual != expected:
            raise ValueError(f"pinned input drift: {name}: {actual}")
        pinned[name] = {"path": relative, "sha256": actual, "size_bytes": path.stat().st_size}
    return pinned


def verify_audit_tools() -> dict[str, dict[str, Any]]:
    tools = {}
    for name, path in AUDIT_TOOLS.items():
        if not path.is_file():
            raise FileNotFoundError(f"E1.11 audit tool missing: {path}")
        tools[name] = {"path": str(path.relative_to(ROOT)).replace("\\", "/"),
                       "sha256": file_sha(path), "size_bytes": path.stat().st_size}
    return tools


def case_contract_errors(case: dict[str, Any]) -> list[str]:
    name = case.get("name")
    image_count = case.get("image_frame_count")
    errors: list[str] = []
    if name not in INPUTS or not isinstance(image_count, int) or image_count <= 0:
        return ["case identity or image-frame count is invalid"]
    expected_hash = INPUTS[name][1]
    if case.get("sha256") != expected_hash:
        errors.append("pinned input hash mismatch")
    if case.get("metadata_frame_count") != 1:
        errors.append("expected exactly one trailing metadata frame")
    if case.get("layout") != "eye_anim_auto_mirror":
        errors.append("official eye_anim layout was not used")
    if case.get("frame_type_counts") != {"valid_image": image_count,
                                          "metadata_flag": 1, "invalid": 0}:
        errors.append("frame type counts differ from image frames plus one metadata frame")
    if case.get("frame_types") != [0] * image_count + [3]:
        errors.append("frame type sequence is not images followed by metadata")
    manual = case.get("render_checks")
    if not isinstance(manual, list) or len(manual) != image_count:
        errors.append("manual C render evidence does not cover every image frame")
    elif any(item.get("frame") != index or item.get("rendered") is not True or
             item.get("actual_sha256") != item.get("expected_sha256") or
             item.get("mismatch_pixels") != 0 or not item.get("guard_clean")
             for index, item in enumerate(manual)):
        errors.append("one or more manual C frame renders mismatch or corrupt a guard")
    metadata = case.get("metadata_prepare_safety", {})
    if not all(metadata.get(key) is True for key in (
            "code_nonzero", "prepared_frame_preserved", "allocations_preserved",
            "guards_clean", "pass")):
        errors.append("metadata frame rejection safety evidence is incomplete or failed")
    if not case.get("stop_restart", {}).get("restart_starts_at_zero"):
        errors.append("stop/start did not restart from frame zero")
    if not case.get("close_clean"):
        errors.append("close did not return to zero live allocations with clean guards")
    reloads = case.get("reloads")
    if not isinstance(reloads, list) or len(reloads) != 3 or any(
            item.get("live_allocations") != 0 or not item.get("guards_clean")
            for item in reloads):
        errors.append("three repeated reload/close cycles did not all close cleanly")

    expected_prepared = list(range(image_count)) + [image_count - 1]
    expected_rendered = list(range(image_count))
    traces = (("nonloop", False), ("loop", True))
    for key, repeat in traces:
        trace = case.get(key, {})
        prepared_expected = list(expected_prepared)
        rendered_expected = list(expected_rendered)
        if repeat:
            prepared_expected.append(0)
            rendered_expected.extend([image_count - 1, 0])
        if trace.get("prepared_sequence") != prepared_expected:
            errors.append(f"{key} prepared-frame sequence differs from the official timer contract")
        if trace.get("expected_prepared_sequence") != prepared_expected or not trace.get("prepared_sequence_exact"):
            errors.append(f"{key} prepared-frame evidence is missing or internally inconsistent")
        if trace.get("rendered_sequence") != rendered_expected or trace.get("expected_rendered_sequence") != rendered_expected or not trace.get("rendered_sequence_exact"):
            errors.append(f"{key} actual dirty-render sequence differs from the expected timer path")
        if trace.get("unique_prepared_frames") != list(range(image_count)):
            errors.append(f"{key} did not observe every image frame")
        if trace.get("metadata_prepared_frames") != [] or trace.get("no_metadata_prepared") is not True:
            errors.append(f"{key} prepared the metadata frame or omitted that evidence")
        checks = trace.get("render_checks")
        if not isinstance(checks, list) or [item.get("frame") for item in checks] != rendered_expected:
            errors.append(f"{key} is missing actual renderer output for an expected tick")
        elif any(item.get("rendered") is not True or
                 item.get("actual_sha256") != item.get("expected_sha256") or
                 item.get("mismatch_pixels") != 0 or not item.get("guard_clean")
                 for item in checks):
            errors.append(f"{key} renderer pixels or guards failed")
        if trace.get("render_all_equal") is not True or trace.get("render_all_guard_clean") is not True:
            errors.append(f"{key} aggregate render evidence failed")
        if not trace.get("no_render_contract"):
            errors.append(f"{key} no-render timer tick contract failed")
        if repeat:
            if trace.get("loop_event") is not True or trace.get("loop_revisited_start") is not True:
                errors.append("loop completion did not revisit frame zero")
            if trace.get("no_render_ticks") != []:
                errors.append("loop path unexpectedly skipped a dirty-render tick")
        else:
            if trace.get("stopped_after_end") is not True or trace.get("completion_event") is not True:
                errors.append("nonloop path did not stop with a completion event")
            no_render = trace.get("no_render_ticks")
            if (not isinstance(no_render, list) or len(no_render) != 1 or
                    no_render[0].get("observation", {}).get("is_playing") != 0 or
                    no_render[0].get("observation", {}).get("all_done", 0) <= 0 or
                    no_render[0].get("dirty", {}).get("flushes") != 0):
                errors.append("nonloop completion tick did not preserve the expected no-refresh behavior")
    return errors


def production_route_report() -> dict[str, Any]:
    index_raw = EMOTE_INDEX.read_bytes()
    index = json.loads(index_raw.decode("utf-8"))
    entries = {entry["name"]: entry for entry in index.get("emoji_collection", [])}
    operation_raw = EMOTE_OP.read_bytes()
    operation_text = operation_raw.decode("utf-8", errors="strict")
    expected_calls = (
        "gfx_anim_set_src(obj, src_data, emoji->size)",
        "gfx_anim_set_segment(obj, 0, 0xFFFF, emoji->fps > 0 ? emoji->fps : EMOTE_DEF_ANIMATION_FPS, emoji->loop)",
        "gfx_anim_start(obj)",
    )
    layout = next((item for item in index.get("layout", [])
                   if item.get("name") == "eye_anim"), None)
    if layout is None or layout.get("anim", {}).get("mirror") != "auto":
        raise ValueError("official eye_anim auto-mirror metadata changed")
    observed_layout = {"index": index.get("layout", []).index(layout),
                       "align": layout.get("align"), "x": layout.get("x"),
                       "y": layout.get("y"),
                       "mirror": layout.get("anim", {}).get("mirror")}
    if observed_layout != OFFICIAL_LAYOUT:
        raise ValueError(f"official eye_anim layout changed: {observed_layout}")

    resources = {}
    for name in ("insert", "eat"):
        entry = entries.get(name)
        if not entry or entry.get("file") != f"{name}.eaf":
            raise ValueError(f"official emoji metadata missing expected {name} resource")
        params = entry.get("eaf", {})
        if params != {"loop": False, "fps": 20}:
            raise ValueError(f"official {name} playback metadata changed: {params}")
        resources[name] = {"file": entry["file"], "fps": params["fps"],
                           "loop": params["loop"]}

    missing = [token for token in expected_calls if token not in operation_text]
    if missing:
        raise ValueError(f"official expression wrapper playback path changed: {missing}")
    return {
        "index_path": str(EMOTE_INDEX.relative_to(ROOT)).replace("\\", "/"),
        "index_sha256": sha(index_raw),
        "operation_path": str(EMOTE_OP.relative_to(ROOT)).replace("\\", "/"),
        "operation_sha256": sha(operation_raw),
        "resources": resources,
        "display_object": "eye_anim",
        "eye_layout": layout,
        "tested_layout_contract": OFFICIAL_LAYOUT,
        "operation_calls_verified": list(expected_calls),
        "scope_note": "asset FPS/loop, eye layout and animation set_src/segment/start are verified; event dispatch and GUI scheduling are not emulated",
    }


def validate_worker_paths(case: str, dll: Path, result_path: Path,
                          expected_dll_sha256: str) -> Path:
    if case not in INPUTS:
        raise ValueError(f"unknown case: {case}")
    dll = dll.resolve()
    result_path = result_path.resolve()
    stage = result_path.parent
    tmp_root = (ROOT / "tmp").resolve()
    if stage.parent != tmp_root or not stage.name.startswith("expression-e1-11-player-"):
        raise ValueError("worker evidence must stay in a direct E1.11 child of project tmp")
    if result_path.name != f"case-{case}.json":
        raise ValueError("worker result filename does not match the fixed case")
    if dll.parent != stage or dll.name != "player-audit-e1-11.dll" or not dll.is_file():
        raise ValueError("worker DLL must be the fixed harness DLL in the same evidence directory")
    if len(expected_dll_sha256) != 64 or file_sha(dll) != expected_dll_sha256:
        raise ValueError("worker DLL SHA-256 does not match the parent audit")
    return stage


def run_isolated_case(output: Path, dll: Path, dll_sha256: str,
                      name: str) -> dict[str, Any]:
    result_path = output / f"case-{name}.json"
    log_path = output / f"worker-{name}.log"
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
               "--case", name, "--dll", str(dll), "--dll-sha256", dll_sha256,
               "--result", str(result_path)]
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                   timeout=240, check=False)
    except subprocess.TimeoutExpired as error:
        partial = as_text(error.stdout) + as_text(error.stderr)
        immutable(log_path, ("COMMAND: " + subprocess.list2cmdline(command) +
                             "\nWORKER TIMEOUT after 240 seconds\n" + partial).encode())
        raise RuntimeError(f"worker {name} timed out; see {log_path}") from error
    log = "COMMAND: " + subprocess.list2cmdline(command) + "\n" + completed.stdout + completed.stderr
    immutable(log_path, log.encode())
    if not result_path.is_file():
        raise RuntimeError(f"worker {name} produced no result; see {log_path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("result") not in ("pass", "rejected"):
        raise RuntimeError(f"worker {name} returned an invalid status; see {log_path}")
    case = result.get("case")
    if not isinstance(case, dict):
        raise RuntimeError(f"worker {name} returned no case details: {result.get('error', 'unknown error')}; see {log_path}")
    if (result["result"] == "pass") != (completed.returncode == 0):
        raise RuntimeError(f"worker {name} status and exit code disagree; see {log_path}")
    case["worker"] = {"returncode": completed.returncode,
                       "result": result["result"],
                       "log": str(log_path.relative_to(ROOT)).replace("\\", "/"),
                       "log_sha256": file_sha(log_path),
                       "dll_sha256": dll_sha256}
    return case


def audit(output: Path) -> dict[str, Any]:
    output = safe_output(output)
    output.mkdir(parents=True, exist_ok=True)
    pinned = verify_inputs()
    tool_hashes = verify_audit_tools()

    anchor = RENDER.source_anchor_report()
    compression_anchor = RENDER.COMPRESSION._verify_player_anchor()
    production_route = production_route_report()
    toolchain = compile_harness(output)
    cases = {}
    dll = ROOT / toolchain["dll"]
    for name in INPUTS:
        cases[name] = run_isolated_case(output, dll, toolchain["dll_sha256"], name)

    after_anchor = RENDER.source_anchor_report()
    after_compression_anchor = RENDER.COMPRESSION._verify_player_anchor()
    after_pinned = verify_inputs()
    after_tool_hashes = verify_audit_tools()
    after_route = production_route_report()
    if (anchor != after_anchor or compression_anchor != after_compression_anchor or
            pinned != after_pinned or tool_hashes != after_tool_hashes or
            production_route != after_route):
        raise RuntimeError("player/source/input anchors changed during E1.11 audit")

    pairs = {
        "insert": (cases["insert_original"], cases["insert_best_8bit"]),
        "eat": (cases["eat_original"], cases["eat_best_8bit"]),
    }
    pair_report = {}
    for resource, (original, candidate) in pairs.items():
        pair_report[resource] = {
            "decoded_frame_sequence_equal": original["decoded_frame_sha256"] == candidate["decoded_frame_sha256"],
            "c_render_trajectory_equal": [item["actual_sha256"] for item in original["render_checks"]] ==
                                          [item["actual_sha256"] for item in candidate["render_checks"]],
            "nonloop_trace_equal": original["nonloop"]["prepared_sequence"] == candidate["nonloop"]["prepared_sequence"],
            "loop_trace_equal": original["loop"]["prepared_sequence"] == candidate["loop"]["prepared_sequence"],
            "nonloop_render_trajectory_equal": [item["actual_sha256"] for item in original["nonloop"]["render_checks"]] ==
                                               [item["actual_sha256"] for item in candidate["nonloop"]["render_checks"]],
            "loop_render_trajectory_equal": [item["actual_sha256"] for item in original["loop"]["render_checks"]] ==
                                            [item["actual_sha256"] for item in candidate["loop"]["render_checks"]],
            "original_case_pass": original["case_pass"], "candidate_case_pass": candidate["case_pass"],
        }

    audit_pass = all(case["case_pass"] for case in cases.values()) and all(
        pair["decoded_frame_sequence_equal"] and
        pair["c_render_trajectory_equal"] and
        pair["nonloop_trace_equal"] and pair["loop_trace_equal"] and
        pair["nonloop_render_trajectory_equal"] and
        pair["loop_render_trajectory_equal"] and
        pair["original_case_pass"] and pair["candidate_case_pass"]
        for pair in pair_report.values())
    report = {
        "schema": "deskbot.expression_e1_11_player_audit/v1",
        "result": "offline_c_player_audit_complete_no_deployment",
        "audit_pass": audit_pass,
        "hardware_accessed": False, "serial_accessed": False, "firmware_built": False,
        "firmware_changed": False, "c61_changed": False, "nvs_changed": False,
        "device_package_eligible": False,
        "scope": {
            "resources": "E1.10 insert/eat originals and 8-bit lossless candidates",
            "player": "hash-anchored Espressif eaf decoder + gfx_anim + timer + renderer",
            "layout": "official eye_anim auto-mirror layout; resource FPS/loop metadata plus separate loop stress pass",
            "excluded": ["firmware build", "resource partition integration", "LCD hardware", "COM4/COM8", "NVS", "flash"],
        },
        "pinned_inputs": pinned, "audit_tool_hashes": tool_hashes,
        "production_route": production_route,
        "player_anchor": anchor,
        "compression_anchor": compression_anchor, "toolchain": toolchain,
        "cases": cases, "pair_comparison": pair_report,
        "deployment_gate": {
            "insert_best_8bit": False, "eat_best_8bit": False,
            "reason": ("offline C player contract passed, but full package/firmware/release and device gates remain unrun"
                       if audit_pass else
                       "E1.11 offline C player contract rejected one or more required checks"),
        },
        "next_gate": "retain official resources; if compression is still desired, run isolated package integration and independent firmware build before any explicit deployment review",
    }
    if not report["audit_pass"]:
        report["result"] = "offline_c_player_audit_rejected_no_deployment"
    immutable(output / "player-audit-e1-11.json",
              (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


def worker_main(case: str, dll: Path, dll_sha256: str, result_path: Path) -> int:
    try:
        stage = validate_worker_paths(case, dll, result_path, dll_sha256)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        # Invalid caller paths never become a destination for rejection logs.
        print(json.dumps({"result": "rejected", "case": case, "error": str(error)},
                         ensure_ascii=False, indent=2))
        return 1
    result_path = stage / f"case-{case}.json"
    dll = stage / "player-audit-e1-11.dll"
    try:
        relative, expected = INPUTS[case]
        path = ROOT / relative
        raw = path.read_bytes()
        if sha(raw) != expected:
            raise ValueError(f"immutable input hash mismatch: {case}")
        native = PlayerNative(dll)
        case_result = run_case(native, case, path)
        payload = {"result": "pass" if case_result["case_pass"] else "rejected",
                   "case": case_result}
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        payload = {"result": "rejected", "case": case, "error": str(error)}
    immutable(result_path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["result"] == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--case")
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--dll-sha256")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.worker:
        if not args.case or not args.dll or not args.dll_sha256 or not args.result:
            parser.error("--worker requires --case, --dll, --dll-sha256 and --result")
        return worker_main(args.case, args.dll, args.dll_sha256, args.result)
    try:
        report = audit(args.output)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({"result": "rejected", "hardware_accessed": False,
                          "device_package_eligible": False, "error": str(error)},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"result": report["result"], "audit_pass": report["audit_pass"],
                      "device_package_eligible": False,
                      "pair_comparison": report["pair_comparison"],
                      "report": str((args.output / "player-audit-e1-11.json").resolve())},
                     ensure_ascii=False, indent=2))
    return 0 if report["audit_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
