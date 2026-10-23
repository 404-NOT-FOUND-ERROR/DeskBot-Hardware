#!/usr/bin/env python3
"""E1.3g: audit the anchored 4-bit candidate through official layouts.

This is an offline, host-only gate.  It reconstructs an additive audit package
in memory, checks the official members and capacity, then runs every candidate
frame through the unchanged host build of Espressif's renderer.  It does not
connect to serial ports, build firmware, modify the production player, or
create a device-eligible image.  The three layouts model the official
rectangular 360x360 compositor; they do not prove a physical LCD circle mask.
"""
from __future__ import annotations

import argparse
import array
import ctypes
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


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RENDERER = _load("deskbot_e13g_renderer", ROOT / "scripts/audit-eaf-renderer.py")
PACKAGES = _load("deskbot_e13g_packages", ROOT / "scripts/audit-expression-package-candidate.py")
RESOURCES = _load("deskbot_e13g_resources", ROOT / "scripts/audit-expression-resources.py")
from protocol.eaf_preview import decode_eaf  # noqa: E402


OFFICIAL_PACKAGE = ROOT / "tmp/remote-esp-vocat-official-build/b-events-v1/expression_assets.bin"
CANDIDATE_EAF = ROOT / "tmp/expression-e1-3b-20261020/candidate/shy-quantized-4bit-15color-host-only-do-not-flash.eaf"
EXPECTED_OFFICIAL = "56b39e7fdd14b9dd0de9b22d3aedc9b04383376dbdef70925675f639d763cd5b"
EXPECTED_CANDIDATE = "7144c594e066c2411e43a492c7ff8f7c4befb0ac282f05250a0656f1b637c9fd"
EXPECTED_SOURCE = "46d427255756fe2a148777ea3f256b615b52e160"
PARTITION_BYTES = 6_144_000
DISPLAY_SIZE = 360


LAYOUTS: dict[int, dict[str, Any]] = {
    0: {"name": "full_screen_center", "mirror": False},
    1: {"name": "eye_left_mid_auto_mirror", "mirror": True},
    2: {"name": "listen_top_mid_y25", "mirror": False},
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


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
        raise ValueError("E1.3g output must be a fresh stage directory inside project tmp")
    return resolved


def layout_geometry(layout: int, width: int, height: int,
                    display: int = DISPLAY_SIZE) -> dict[str, Any]:
    """Return the official rectangular compositor geometry and visible source."""
    if layout == 1:
        x, y, mirror = 10, (display - height) // 2 + 10, True
        object_width = display - 2 * x
    elif layout == 2:
        x, y, mirror = (display - width) // 2, 25, False
        object_width = width
    elif layout == 0:
        x, y, mirror = (display - width) // 2, (display - height) // 2, False
        object_width = width
    else:
        raise ValueError("layout must be 0, 1, or 2")

    visible_x0 = max(0, x)
    visible_x1 = min(display, x + object_width)
    visible_y0 = max(0, y)
    visible_y1 = min(display, y + height)
    source_x0 = max(0, visible_x0 - x)
    source_x1 = min(width, visible_x1 - x)
    source_y0 = max(0, visible_y0 - y)
    source_y1 = min(height, visible_y1 - y)
    return {
        "layout": layout,
        "name": LAYOUTS[layout]["name"],
        "x": x,
        "y": y,
        "object_width": object_width,
        "object_height": height,
        "mirror": mirror,
        "visible_rect": [visible_x0, visible_y0, visible_x1, visible_y1],
        "source_rect": [source_x0, source_y0, source_x1, source_y1],
        "source_width": max(0, source_x1 - source_x0),
        "source_height": max(0, source_y1 - source_y0),
        "horizontal_4bit_contract": (
            source_x0 >= 0 and source_x0 % 2 == 0 and
            (source_x1 - source_x0) > 0 and (source_x1 - source_x0) % 2 == 0
        ),
    }


def build_audit_package() -> tuple[bytes, dict[str, Any], bytes]:
    official_raw = OFFICIAL_PACKAGE.read_bytes()
    if sha256(official_raw) != EXPECTED_OFFICIAL:
        raise ValueError("official package hash differs from the immutable anchor")
    official = RESOURCES.parse_package_bytes(official_raw, modern=False)
    if PACKAGES.build_legacy_package(official["members"]) != official_raw:
        raise ValueError("official package was not reproduced byte-for-byte")
    if RESOURCES.git_commit(PACKAGES.OFFICIAL_SOURCE) != EXPECTED_SOURCE:
        raise ValueError("official source checkout differs from the immutable anchor")
    candidate_raw = CANDIDATE_EAF.read_bytes()
    if sha256(candidate_raw) != EXPECTED_CANDIDATE:
        raise ValueError("4-bit candidate hash differs from the E1.3b anchor")
    decoded = decode_eaf(CANDIDATE_EAF, fps=25)
    if decoded.bit_depth != 4 or decoded.width != 360 or decoded.height != 360:
        raise ValueError("candidate is no longer the anchored 360x360 4-bit asset")
    index = json.loads(official["members"]["index.json"])
    candidate_index = json.loads(json.dumps(index))
    candidate_index["emoji_collection"].append({
        "name": "shy_candidate", "file": "shy_candidate.eaf",
        "eaf": {"loop": True, "fps": 25},
    })
    members = dict(official["members"])
    members["index.json"] = (json.dumps(candidate_index, ensure_ascii=False, indent=4) + "\n").encode()
    members["shy_candidate.eaf"] = candidate_raw
    package = PACKAGES.build_legacy_package(members)
    parsed = RESOURCES.parse_package_bytes(package, modern=False)
    if parsed["members"] != members:
        raise ValueError("audit package round-trip changed a member")
    for name, raw in official["members"].items():
        if name != "index.json" and parsed["members"].get(name) != raw:
            raise ValueError(f"official package member changed: {name}")
    official_semantics = PACKAGES._index_semantics(index)
    candidate_semantics = PACKAGES._index_semantics(candidate_index)
    if candidate_semantics != official_semantics | {"shy_candidate": "shy_candidate.eaf"}:
        raise ValueError("candidate changed an official semantic binding")
    package_report = {
        "official_sha256": sha256(official_raw),
        "official_size_bytes": len(official_raw),
        "candidate_eaf_sha256": sha256(candidate_raw),
        "candidate_eaf_size_bytes": len(candidate_raw),
        "candidate_package_sha256": sha256(package),
        "candidate_package_size_bytes": len(package),
        "partition_bytes": PARTITION_BYTES,
        "remaining_partition_bytes": PARTITION_BYTES - len(package),
        "member_count": parsed["header"]["member_count"],
        "official_member_count": official["header"]["member_count"],
        "official_non_index_members_unchanged": True,
        "official_semantics_unchanged_additive_only": True,
        "candidate_semantic_added": "shy_candidate",
    }
    return package, package_report, candidate_raw


def _guarded_render(native: Any, decoded: Any, raw: bytes, frame: int,
                    layout: int, rect: tuple[int, int, int, int], rows: int,
                    background: int, swap: bool) -> dict[str, Any]:
    native.prepare(frame)
    x1, y1, x2, y2 = rect
    width, height = x2 - x1, y2 - y1
    count = width * height
    sentinel = 0xA55A
    storage = (ctypes.c_uint16 * (count + 2))()
    storage[0] = sentinel
    storage[count + 1] = sentinel
    pixels = ctypes.cast(ctypes.byref(storage, ctypes.sizeof(ctypes.c_uint16)),
                          ctypes.POINTER(ctypes.c_uint16))
    obs = RENDERER.Observation()
    result = native.lib.db_render(x1, y1, x2, y2, rows, int(swap), background,
                                  pixels, count, ctypes.byref(obs))
    actual = array.array("H", storage[1:count + 1])
    expected_canvas = RENDERER.compose565(decoded.frames[frame], decoded.width,
                                           decoded.height, layout,
                                           background=background, swap=swap)
    expected = RENDERER.crop565(expected_canvas, rect)
    mismatch = sum(got != want for got, want in zip(actual, expected))
    return {
        "frame": frame,
        "layout": layout,
        "rect": list(rect),
        "rows": rows,
        "background": background,
        "swap": swap,
        "result": int(result),
        "pixels": count,
        "mismatch_pixels": mismatch,
        "actual_sha256": sha256(actual.tobytes()),
        "expected_sha256": sha256(expected.tobytes()),
        "guard_clean": RENDERER.clean_observation(obs),
        "output_sentinels_clean": storage[0] == sentinel and storage[count + 1] == sentinel,
        "flush_bounds_error": int(obs.flush_bounds_error),
        "corruptions": int(obs.corruptions),
        "prefix_changes": int(obs.prefix_changes),
        "max_tail_bytes": int(obs.max_tail_bytes),
    }


def worker(dll: Path, result_path: Path) -> dict[str, Any]:
    raw = CANDIDATE_EAF.read_bytes()
    if sha256(raw) != EXPECTED_CANDIDATE:
        raise ValueError("worker candidate hash mismatch")
    decoded = decode_eaf(CANDIDATE_EAF, fps=25)
    native = RENDERER.NativeRenderer(dll)
    frame_count = len(decoded.frames)
    checks: list[dict[str, Any]] = []
    geometry: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    representative_frames = sorted({0, frame_count // 2, frame_count - 1})
    for layout in (0, 1, 2):
        spec = layout_geometry(layout, decoded.width, decoded.height)
        geometry.append(spec)
        native.open(raw, layout, 25, False, 0, 0, 0, frame_count - 1)
        try:
            for frame in range(frame_count):
                prepared = native.prepare(frame)
                actual_geometry = [int(prepared.x), int(prepared.y),
                                   int(prepared.width), int(prepared.height)]
                expected_geometry = [spec["x"], spec["y"], spec["object_width"], spec["object_height"]]
                if actual_geometry != expected_geometry:
                    failures.append({"kind": "geometry", "frame": frame,
                                     "layout": layout, "expected": expected_geometry,
                                     "actual": actual_geometry})
                check = _guarded_render(native, decoded, raw, frame, layout,
                                        (0, 0, DISPLAY_SIZE, DISPLAY_SIZE),
                                        32, 0, False)
                check["case"] = "display_full_all_frames"
                checks.append(check)
                if (check["result"] != 0 or check["mismatch_pixels"] or
                        not check["guard_clean"] or not check["output_sentinels_clean"]):
                    failures.append(check)
                if frame in representative_frames:
                    for name, rect, rows, background, swap in (
                        ("visible_rect_chunked", tuple(spec["visible_rect"]), 16, 0x1234, False),
                        ("display_rows1_swapped", (0, 0, DISPLAY_SIZE, DISPLAY_SIZE), 1, 0x1234, True),
                    ):
                        check = _guarded_render(native, decoded, raw, frame, layout,
                                                rect, rows, background, swap)
                        check["case"] = name
                        checks.append(check)
                        if (check["result"] != 0 or check["mismatch_pixels"] or
                                not check["guard_clean"] or not check["output_sentinels_clean"]):
                            failures.append(check)
        finally:
            closed = native.close()
            if int(closed.live_allocations) != 0:
                failures.append({"kind": "live_allocations", "layout": layout,
                                 "value": int(closed.live_allocations)})
    report = {
        "frame_count": frame_count,
        "dimensions": [decoded.width, decoded.height],
        "bit_depth": decoded.bit_depth,
        "frame_rate": decoded.frame_rate,
        "all_frames_checked_for_each_layout": len(checks) >= frame_count * len(LAYOUTS),
        "representative_frames_for_chunked_checks": representative_frames,
        "layouts": geometry,
        "render_check_count": len(checks),
        "failure_count": len(failures),
        "all_rgb565_outputs_match": not any(item.get("mismatch_pixels", 0) for item in failures),
        "all_guards_and_sentinels_clean": not any(
            not item.get("guard_clean", True) or not item.get("output_sentinels_clean", True)
            for item in failures),
        "checks": checks,
        "failure_examples": failures[:24],
    }
    immutable(result_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


def audit(output: Path) -> dict[str, Any]:
    output = safe_output(output)
    output.mkdir(parents=True, exist_ok=True)
    package, package_report, _candidate_raw = build_audit_package()
    package_report["audit_package_written_to_disk"] = False
    package_report["audit_package_matches_in_memory"] = sha256(package) == package_report["candidate_package_sha256"]
    package_report["candidate_resource_written_to_disk"] = False
    immutable(output / "package-report.json",
              (json.dumps(package_report, ensure_ascii=False, indent=2) + "\n").encode())

    dll, toolchain = RENDERER.compile_renderer(output)
    result_path = output / "renderer-layout-audit.json"
    child = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                            "--worker", "--dll", str(dll), "--result", str(result_path)],
                           cwd=output, capture_output=True, text=True, timeout=240)
    immutable(output / "renderer-run.log",
              ("COMMAND EXIT %d\n%s%s" % (child.returncode, child.stdout, child.stderr)).encode())
    if child.returncode:
        raise RuntimeError(f"renderer worker failed ({child.returncode}); see {output / 'renderer-run.log'}")
    renderer_report = json.loads(result_path.read_text(encoding="utf-8"))
    source_crop_safe = all(item["horizontal_4bit_contract"]
                           for item in renderer_report["layouts"])
    result = {
        "schema": "deskbot.expression_e1_3g_package_layout_audit/v1",
        "result": ("offline_actual_4bit_layout_audit_pass_no_flash_candidate"
                    if renderer_report["failure_count"] == 0 and source_crop_safe
                    else "offline_actual_4bit_layout_audit_failed_no_flash_candidate"),
        "hardware_accessed": False,
        "firmware_changed": False,
        "production_player_modified": False,
        "official_resources_modified": False,
        "device_package_eligible": False,
        "source_anchor": RENDERER.source_anchor_report(),
        "toolchain": toolchain,
        "package": package_report,
        "renderer": renderer_report,
        "contract": {
            "horizontal_4bit_source_crop_safe": source_crop_safe,
            "all_frames_checked": renderer_report["frame_count"],
            "layouts_checked": [0, 1, 2],
            "physical_circle_mask_checked": False,
            "limitation": "host audit models the official 360x360 rectangular compositor only; it does not prove LCD circular-mask pixels or target timing",
        },
        "deployment_gate": {
            "candidate_package_fits_partition": package_report["remaining_partition_bytes"] >= 0,
            "renderer_scope_pass": renderer_report["failure_count"] == 0,
            "device_package_eligible": False,
            "official_package_remains_rollback": True,
            "next_gate": "independent firmware build and segment/boundary audit, then explicit device regression; do not flash this audit package",
        },
        "scope_exclusions": ["serial/NVS/C61", "firmware build", "LCD physical circle mask", "DeepSeek/client integration"],
    }
    immutable(output / "e1-3g-report.json",
              (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            if not args.dll or not args.result:
                parser.error("--worker requires --dll and --result")
            report = worker(args.dll.resolve(), args.result.resolve())
            print(json.dumps({"result": "worker_complete", "failures": report["failure_count"],
                              "report": str(args.result.resolve())}, ensure_ascii=False, indent=2))
        else:
            if args.output is None:
                parser.error("--output is required; choose a fresh stage directory inside project tmp")
            report = audit(args.output)
            print(json.dumps({"result": report["result"],
                              "device_package_eligible": report["device_package_eligible"],
                              "package_remaining_bytes": report["package"]["remaining_partition_bytes"],
                              "renderer_failures": report["renderer"]["failure_count"],
                              "report": str((args.output / "e1-3g-report.json").resolve())},
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"result": "rejected", "hardware_accessed": False,
                          "error": str(error)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
