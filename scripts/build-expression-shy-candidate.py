#!/usr/bin/env python3
"""Reproduce E1.2 shy from the anchored GIF; never access hardware."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.eaf_encode import encode_rgba_frames
from protocol.eaf_preview import decode_eaf, write_rgba_atlas

ARCHIVE = ROOT / "tmp/research-emote-tool/Emote-Pack.zip"
ARCHIVE_SHA = "946654de50253949b2c687a2b6b53bf3b66fdd699ab107950258e2a830cf15e6"
GIF_MEMBER = "gif/shy_[2.0s, 4.0s].gif"
LOTTIE_MEMBER = "lottie/Social & Contextual Emotions/shy.json"
OUTPUT = ROOT / "tmp/expression-e1-2-20261020"
MANIFEST = ROOT / "protocol/expression-e1-2-shy.manifest.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest(manifest: object, frame_count: int) -> tuple[dict, dict]:
    if not isinstance(manifest, dict):
        raise ValueError("E1.2 manifest must be a JSON object")
    if set(manifest) - {"schema", "expressions", "bindings", "actions"}:
        raise ValueError("E1.2 manifest contains unsupported fields")
    if manifest.get("schema") != "deskbot.expression_capabilities/v1":
        raise ValueError("E1.2 manifest schema is invalid")
    entries = manifest.get("expressions")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("E1.2 manifest must contain exactly one expression object")
    if manifest.get("bindings", []) != [] or manifest.get("actions", []) != []:
        raise ValueError("E1.2 candidate must not bind official semantics or define actions")

    candidate = entries[0]
    if set(candidate) != {"id", "src", "loop", "fps", "layout", "segments"}:
        raise ValueError("E1.2 expression contains unsupported or missing fields")
    if (candidate.get("id") != "shy_candidate" or candidate.get("src") != "shy_legacy.eaf"
            or candidate.get("fps") != 25 or candidate.get("loop") is not False
            or candidate.get("layout") != "full_screen"):
        raise ValueError("E1.2 candidate identity, timing, or layout differs from the audited manifest")
    segments = candidate.get("segments")
    if not isinstance(segments, dict) or set(segments) != {"enter", "loop", "exit"}:
        raise ValueError("E1.2 must declare enter, loop, and exit segments")
    cursor = 0
    normalized_segments = {}
    for name in ("enter", "loop", "exit"):
        bounds = segments[name]
        if (not isinstance(bounds, list) or len(bounds) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in bounds)):
            raise ValueError(f"segment {name} must be a two-integer frame range")
        start, end = bounds
        if start != cursor or end <= start or end > frame_count:
            raise ValueError(f"segment {name} must be non-empty, contiguous, and within the GIF")
        normalized_segments[name] = [start, end]
        cursor = end
    if cursor != frame_count:
        raise ValueError("segments must cover every GIF frame exactly once")
    return candidate, normalized_segments


def build(output: Path) -> dict:
    output = output.resolve()
    output.relative_to(ROOT / "tmp")
    if digest(ARCHIVE) != ARCHIVE_SHA:
        raise ValueError("source archive hash differs from E1.1")
    output.mkdir(parents=True, exist_ok=True)
    source_dir = output / "sources"
    source_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(ARCHIVE) as archive:
        inputs = {}
        for member, name in [(GIF_MEMBER, "shy.gif"), (LOTTIE_MEMBER, "shy.lottie.json")]:
            raw = archive.read(member)
            target = source_dir / name
            target.write_bytes(raw)
            inputs[name] = {"archive_member": member, "sha256": digest(target), "size_bytes": len(raw)}

    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise ValueError("Windows PowerShell with System.Drawing is required; no dependency is installed")
    raw_path, metadata_path = output / "shy.rgba", output / "gif-extract.json"
    subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(ROOT / "scripts/extract-gif-frames.ps1"), "-InputGif", str(source_dir / "shy.gif"),
                    "-OutputRaw", str(raw_path), "-OutputMetadata", str(metadata_path)], check=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    if (metadata["frame_count"] != 188 or metadata["delay_centiseconds"] != [4] * 188
            or (metadata["output_width"], metadata["output_height"]) != (360, 360)):
        raise ValueError("source timing or dimensions changed; review segmentation before conversion")
    raw = raw_path.read_bytes()
    frame_bytes = 360 * 360 * 4
    if len(raw) != 188 * frame_bytes:
        raise ValueError("extracted RGBA byte count is invalid")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    candidate, segments = validate_manifest(manifest, metadata["frame_count"])
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    frames = tuple(raw[start:start + frame_bytes] for start in range(0, len(raw), frame_bytes))
    path = output / "shy_legacy.eaf"
    path.write_bytes(encode_rgba_frames(frames, 360, 360))
    decoded = decode_eaf(path, fps=25)
    if len(decoded.frames) != 188 or decoded.stored_frame_count != 188:
        raise ValueError("EAF round-trip frame count mismatch")

    # Check representative pixels against the source after RGB565 conversion.
    total_error = 0
    visible = 0
    clipped_visible = total_nonblack = 0
    for original, converted in zip(frames, decoded.frames):
        for y in range(360):
            for x in range(360):
                pos = (y * 360 + x) * 4
                if original[pos + 3] < 128:
                    continue
                total_error += sum(abs(original[pos + c] - converted[pos + c]) for c in (0, 1, 2))
                visible += 1
                if max(original[pos:pos + 3]) > 16:
                    total_nonblack += 1
                    if (x + 0.5 - 180) ** 2 + (y + 0.5 - 180) ** 2 > 180 ** 2:
                        clipped_visible += 1
    # Contact sheet shows entry, loop boundaries, and end; full preview is built separately.
    chosen = [0, 25, 49, 50, 99, 149, 150, 187]
    write_rgba_atlas(output / "contact-sheet.png", tuple(decoded.frames[i] for i in chosen), 360, 360, columns=4)
    mean_error = total_error / max(1, visible * 3)
    if mean_error > 12:
        raise ValueError("RGB565/quantization mean error exceeds the frozen E1.2 budget")
    report = {
        "schema": "deskbot.expression_candidate_build/v1", "result": "offline_candidate_created",
        "candidate_id": "shy_candidate", "hardware_accessed": False, "firmware_changed": False,
        "device_playback_verified": False, "device_package_built": False,
        "archive": {"path": str(ARCHIVE.relative_to(ROOT)), "sha256": ARCHIVE_SHA},
        "source_url": "https://dl.espressif.com/AE/Emote%20Pack.zip", "sources": inputs,
        "conversion": metadata | {"fps": 25, "duration_ms": 7520, "palette": "global RGB555 weighted median cut, 255 colors + transparency",
                                   "encoding": 0, "alpha_threshold": 128, "device_color_model": "RGB565",
                                   "resampling": "848x848 -> 360x360; source frame count/timing preserved"},
        "manifest": {"file": "manifest.json", "sha256": digest(output / "manifest.json")},
        "segments": {"convention": "zero-based, start inclusive, end exclusive", "enter": segments["enter"], "loop": segments["loop"], "exit": segments["exit"],
                     "basis": "research index loop [50,150], interpreted as half-open for this preview; not a current-firmware loop contract"},
        "layout": {"kind": "full_screen", "mirror": False, "width": 360, "height": 360, "alignment_px": 4,
                   "device_layout_validated": False, "note": "Full-face candidate is not suitable for official eye_anim auto-mirror."},
        "quality": {"mean_rgb_channel_absolute_error": round(mean_error, 4), "mean_error_limit": 12,
                    "circle_clipped_nonblack_pixels": clipped_visible, "total_nonblack_pixels": total_nonblack,
                    "contact_frames_zero_based": chosen},
        "eaf": {"file": path.name, "sha256": digest(path), "size_bytes": path.stat().st_size,
                "frame_count": 188, "encoding": [0], "format": "current_legacy_eaf_8bit_rle"},
        "protection": {"official_semantics_replaced": [], "protected_semantics_replaced": [], "role_bindings_changed": False},
        "next_gate": "E1.3 full package + layout audit after visual acceptance; no device deployment",
    }
    (output / "candidate-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    try:
        report = build(args.output)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(json.dumps({"result": "rejected", "hardware_accessed": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"result": report["result"], "eaf": report["eaf"], "quality": report["quality"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
