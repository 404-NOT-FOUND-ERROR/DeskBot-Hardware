#!/usr/bin/env python3
"""E1.3f: isolate the official 4-bit EAF clipping contract.

This audit enumerates horizontal source origins and clip widths against the
hash-anchored Espressif host renderer. It does not change the production
player, official resources, C61 firmware, or any device. A case is considered
safe only when the renderer output is pixel-identical to the Python reference,
the renderer's temporary allocations remain guarded, caller output sentinels
remain intact, and no flush boundary is rejected.
"""
from __future__ import annotations

import argparse
import array
import ctypes
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_renderer_module():
    path = ROOT / "scripts/audit-eaf-renderer.py"
    spec = importlib.util.spec_from_file_location("deskbot_e13e_renderer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load renderer audit: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RENDERER = load_renderer_module()
from protocol.eaf_compression import transcode_legacy_eaf  # noqa: E402
from protocol.eaf_encode import encode_rgba_frames  # noqa: E402
from protocol.eaf_preview import decode_eaf  # noqa: E402

OUTPUT_SCHEMA = "deskbot.expression_e1_3f_4bit_contract_audit/v1"
OBJECT_X = 120
OBJECT_Y = 120
BACKGROUND = 0x1234
SENTINEL = 0xA55A


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def immutable(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"refusing to overwrite different evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_probe_eaf() -> bytes:
    """Make a deterministic 16x8, four-colour frame with no transparency."""
    width, height = 16, 8
    colors = ((255, 0, 0, 255), (0, 255, 0, 255),
              (0, 0, 255, 255), (255, 255, 0, 255))
    frame = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            pos = (y * width + x) * 4
            frame[pos:pos + 4] = bytes(colors[(x + 2 * y) % len(colors)])
    source8 = encode_rgba_frames((bytes(frame),), width, height,
                                 block_height=8, palette_colors=4)
    return transcode_legacy_eaf(source8, encoding=0, bit_depth=4,
                                deduplicate=False)


def contract_allows(source_x: int, clip_width: int) -> bool:
    return source_x >= 0 and clip_width > 0 and source_x % 2 == 0 and clip_width % 2 == 0


def guarded_render(native: Any, rect: tuple[int, int, int, int], rows: int,
                    background: int) -> tuple[array.array, Any, bool, int]:
    """Call db_render with caller-side sentinels around the output array."""
    x1, y1, x2, y2 = rect
    width, height = x2 - x1, y2 - y1
    count = width * height
    storage = (ctypes.c_uint16 * (count + 2))()
    storage[0] = SENTINEL
    storage[count + 1] = SENTINEL
    pixels = ctypes.cast(ctypes.byref(storage, ctypes.sizeof(ctypes.c_uint16)),
                          ctypes.POINTER(ctypes.c_uint16))
    obs = RENDERER.Observation()
    result = native.lib.db_render(x1, y1, x2, y2, rows, 0, background,
                                  pixels, count, ctypes.byref(obs))
    actual = array.array("H", storage[1:count + 1])
    sentinels_clean = storage[0] == SENTINEL and storage[count + 1] == SENTINEL
    return actual, obs, sentinels_clean, int(result)


def render_case(native: Any, decoded: Any, raw: bytes, source_x: int,
                source_y: int, clip_width: int, clip_height: int) -> dict[str, Any]:
    rect = (OBJECT_X + source_x, OBJECT_Y + source_y,
            OBJECT_X + source_x + clip_width,
            OBJECT_Y + source_y + clip_height)
    native.open(raw, 3, 25, False, OBJECT_X, OBJECT_Y, 0, 0)
    try:
        native.prepare(0)
        actual, obs, sentinels_clean, result = guarded_render(native, rect, 1, BACKGROUND)
        expected_canvas = RENDERER.compose565(decoded.frames[0], decoded.width,
                                              decoded.height, 3, OBJECT_X, OBJECT_Y,
                                              BACKGROUND, False)
        expected = RENDERER.crop565(expected_canvas, rect)
        mismatch = [i for i, (got, want) in enumerate(zip(actual, expected))
                    if got != want]
        guard_clean = RENDERER.clean_observation(obs)
        safe_observed = (result == 0 and not mismatch and guard_clean and
                         sentinels_clean)
        contract_allowed = contract_allows(source_x, clip_width)
        return {
            "source_origin": [source_x, source_y],
            "clip_size": [clip_width, clip_height],
            "clip_rect": list(rect),
            "contract_allowed": contract_allowed,
            "result": result,
            "mismatch_pixels": len(mismatch),
            "first_mismatch_index": mismatch[0] if mismatch else None,
            "actual_sha256": RENDERER.hash_u16(actual),
            "expected_sha256": RENDERER.hash_u16(expected),
            "renderer_guard_clean": guard_clean,
            "output_sentinels_clean": sentinels_clean,
            "flush_bounds_error": int(obs.flush_bounds_error),
            "corruptions": int(obs.corruptions),
            "prefix_changes": int(obs.prefix_changes),
            "max_tail_bytes": int(obs.max_tail_bytes),
            "safe_observed": safe_observed,
        }
    finally:
        native.close()


def enumerate_cases(native: Any, decoded: Any, raw: bytes) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    # Exhaustive enough to cover all horizontal parity pairs while keeping the
    # clip well inside the 360x360 host canvas.
    for source_y in range(3):
        for clip_height in range(1, 4):
            if source_y + clip_height > decoded.height:
                continue
            for source_x in range(8):
                for clip_width in range(1, 9):
                    if source_x + clip_width > decoded.width:
                        continue
                    cases.append(render_case(native, decoded, raw, source_x,
                                             source_y, clip_width, clip_height))
    return cases


def worker(output: Path) -> dict[str, Any]:
    output = RENDERER.safe_output(output)
    if output.exists():
        raise ValueError(f"output stage must be new; refusing to reuse: {output}")
    output.mkdir(parents=True, exist_ok=True)
    raw = make_probe_eaf()
    eaf_path = output / "synthetic-4bit-contract-probe.eaf"
    immutable(eaf_path, raw)
    decoded = decode_eaf(eaf_path, fps=25)
    if decoded.bit_depth != 4 or (decoded.width, decoded.height) != (16, 8):
        raise AssertionError("synthetic probe did not produce anchored 4-bit geometry")
    dll, toolchain = RENDERER.compile_renderer(output)
    native = RENDERER.NativeRenderer(dll)
    cases = enumerate_cases(native, decoded, raw)
    allowed = [case for case in cases if case["contract_allowed"]]
    disallowed = [case for case in cases if not case["contract_allowed"]]
    allowed_failures = [case for case in allowed if not case["safe_observed"]]
    disallowed_failures = [case for case in disallowed if not case["safe_observed"]]
    disallowed_passes = [case for case in disallowed if case["safe_observed"]]
    parity: dict[str, dict[str, int]] = {}
    for case in cases:
        key = f"origin_{case['source_origin'][0] % 2}_width_{case['clip_size'][0] % 2}"
        row = parity.setdefault(key, {"total": 0, "safe": 0, "failures": 0})
        row["total"] += 1
        row["safe"] += int(case["safe_observed"])
        row["failures"] += int(not case["safe_observed"])
    report = {
        "schema": OUTPUT_SCHEMA,
        "result": "offline_contract_audit_complete_no_flash_candidate",
        "hardware_accessed": False,
        "firmware_changed": False,
        "production_player_modified": False,
        "official_resources_modified": False,
        "source_anchor": RENDERER.source_anchor_report(),
        "player_anchor": RENDERER.COMPRESSION._verify_player_anchor(),
        "toolchain": toolchain,
        "probe": {"path": str(eaf_path.relative_to(ROOT)).replace("\\", "/"),
                  "sha256": sha256(raw), "size_bytes": len(raw),
                  "dimensions": [decoded.width, decoded.height],
                  "bit_depth": decoded.bit_depth, "frame_count": len(decoded.frames)},
        "contract": {
            "safe_conditions": [
                "EAF frame width is even (required by packed 4-bit format)",
                "source horizontal clip origin is even",
                "clip width is even",
            ],
            "case_count": len(cases),
            "allowed_case_count": len(allowed),
            "allowed_failure_count": len(allowed_failures),
            "allowed_status": "pass" if not allowed_failures else "fail",
            "out_of_contract_case_count": len(disallowed),
            "out_of_contract_failure_count": len(disallowed_failures),
            "out_of_contract_pass_count": len(disallowed_passes),
            "parity_matrix": parity,
            "allowed_failures": allowed_failures[:24],
            "out_of_contract_passes": disallowed_passes[:24],
        },
        "cases": cases,
        "deployment_gate": {
            "candidate_allowed": False,
            "reason": ("safe 4-bit contract is confirmed in the isolated host fixture; "
                       "this does not create a deployable resource candidate"),
            "official_package_remains_rollback": True,
        },
        "next_gate": ("keep the official animation package; apply the safe contract to "
                       "future 4-bit assets and continue complete-package capacity and "
                       "render regression audits before any E2 deployment decision"),
    }
    immutable(output / "contract-audit-v1.json",
              (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="fresh evidence directory inside project tmp")
    args = parser.parse_args(argv)
    try:
        if args.output is None:
            parser.error("--output is required; choose a fresh stage directory inside project tmp")
        report = worker(args.output)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, AssertionError) as error:
        print(json.dumps({"result": "rejected", "hardware_accessed": False,
                          "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"result": report["result"],
                      "allowed_status": report["contract"]["allowed_status"],
                      "allowed_failure_count": report["contract"]["allowed_failure_count"],
                      "case_count": report["contract"]["case_count"],
                      "report": str((args.output / "contract-audit-v1.json").resolve())},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
