#!/usr/bin/env python3
"""Export and offline-validate ESP-VoCat expression/action capabilities.

This tool only reads source files and candidate assets.  It deliberately does
not mount, write, or flash an ESP32 resource partition.  A successful validate
run therefore means ``offline_validated``; it is not a device playback test.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA = "deskbot.expression_capabilities/v1"
MAX_FPS = 120
MAX_STEPS = 16
MAX_STEP_MS = 30_000
MAX_TOTAL_MS = 120_000
MAX_NAME_LENGTH = 31
IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
MAX_EAF_FRAMES = 4096
# ESP-IDF partition CSV suffix K means 1024 bytes (6000K, not 6 MiB).
DEFAULT_ASSETS_PARTITION_BYTES = 6000 * 1024

ACTION_IDS = {
    "shark_head": 0x0000,
    "shark_head_decay": 0x0001,
    "look_around": 0x0002,
    "beat_swing": 0x0003,
    "cat_nuzzle": 0x0004,
    # These two are official MCP tool actions, not raw C61 protocol values.
    # Keep them in the catalog so a client can request the same validated
    # semantics without pretending that they are wire-level action IDs.
    "calibrate": None,
    "go_home": None,
}
EMOTION_ACTION = {
    "happy": "shark_head", "laughing": "shark_head", "funny": "shark_head",
    "loving": "shark_head", "confident": "shark_head", "delicious": "shark_head",
    "thinking": "shark_head", "embarrassed": "cat_nuzzle", "winking": "cat_nuzzle",
    "sad": "shark_head_decay", "crying": "shark_head_decay", "sleepy": "shark_head_decay",
    "silly": "look_around", "confused": "look_around", "surprised": "look_around",
    "shocked": "look_around", "relaxed": "look_around", "angry": "beat_swing",
}
PROTECTED = {"listen", "idle", "neutral", "sleepy", "insert"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_eaf(path: Path) -> dict[str, Any]:
    """Validate the EAF container and return inexpensive structural metadata."""
    raw = path.read_bytes()
    if len(raw) < 16 or raw[0] != 0x89 or raw[1:4] not in (b"EAF", b"AAF"):
        raise ValueError("invalid EAF magic/header")
    frames, checksum, payload_len = struct.unpack_from("<III", raw, 4)
    if frames <= 0:
        raise ValueError("EAF frame count is zero")
    if frames > MAX_EAF_FRAMES:
        raise ValueError("EAF frame count exceeds safety limit")
    table_end = 16 + frames * 8
    if table_end > len(raw) or payload_len < frames * 8 or 16 + payload_len > len(raw):
        raise ValueError("EAF table/payload length is out of bounds")
    if (sum(raw[16:16 + payload_len]) & 0xFFFFFFFF) != checksum:
        raise ValueError("EAF checksum mismatch")
    frame_region = 16 + frames * 8
    region_end = 16 + payload_len
    first_kind = None
    image_frames = 0
    metadata_frames = 0
    ranges: list[tuple[int, int]] = []
    for i in range(frames):
        size, offset = struct.unpack_from("<II", raw, 16 + i * 8)
        if size < 2 or offset > region_end - frame_region or size > region_end - frame_region - offset:
            raise ValueError(f"EAF frame {i} is out of bounds")
        frame = raw[frame_region + offset:frame_region + offset + size]
        if frame[:2] != b"ZZ":
            raise ValueError(f"EAF frame {i} does not start with ZZ")
        end = offset + size
        # The official encoder deduplicates identical frames.  Exact duplicate
        # ranges are valid; partial overlap is ambiguous and is rejected.
        for previous_start, previous_end in ranges:
            overlaps = offset < previous_end and previous_start < end
            identical = offset == previous_start and end == previous_end
            if overlaps and not identical:
                raise ValueError(f"EAF frame {i} partially overlaps another frame")
        ranges.append((offset, end))
        if first_kind is None:
            first_kind = frame[2:4].decode("ascii", errors="replace")
        kind = frame[2:4]
        if kind == b"_S":
            _validate_static_frame(frame, i)
            image_frames += 1
        elif kind == b"_C":
            metadata_frames += 1
        elif kind != b"_C":
            raise ValueError(f"EAF frame {i} has unsupported frame kind {kind!r}")
    return {"format": raw[1:4].decode("ascii"), "frames": frames,
            "image_frames": image_frames, "metadata_frames": metadata_frames,
            "payload_length": payload_len, "first_frame_kind": first_kind}


def _validate_static_frame(frame: bytes, index: int) -> None:
    """Validate the block table used by the gfx EAF decoder.

    This intentionally stops at structural checks.  Decompression and pixel
    dimensions still belong to the firmware decoder and the device test.
    """
    # Two-byte ZZ marker is excluded from the decoder's file_data pointer.
    data = frame[2:]
    if len(data) < 22:
        raise ValueError(f"EAF frame {index} static header is truncated")
    bit_depth = data[9]
    if bit_depth not in (4, 8, 24):
        raise ValueError(f"EAF frame {index} has invalid bit depth {bit_depth}")
    width, height, blocks, block_height = struct.unpack_from("<HHHH", data, 10)
    if width == 0 or height == 0 or blocks == 0 or block_height == 0:
        raise ValueError(f"EAF frame {index} has invalid dimensions or block count")
    palette_bytes = 0 if bit_depth == 24 else (1 << bit_depth) * 4
    table_end = 18 + blocks * 4
    data_start = table_end + palette_bytes
    if data_start > len(data):
        raise ValueError(f"EAF frame {index} block table/palette is truncated")
    lengths = [struct.unpack_from("<I", data, 18 + 4 * block)[0]
               for block in range(blocks)]
    if any(length == 0 for length in lengths):
        raise ValueError(f"EAF frame {index} contains an empty block")
    if data_start + sum(lengths) > len(data):
        raise ValueError(f"EAF frame {index} block data is out of bounds")
    for block, length in enumerate(lengths):
        encoding = data[data_start + sum(lengths[:block])]
        if encoding > 5:
            raise ValueError(f"EAF frame {index} block {block} has unknown encoding {encoding}")


def find_source_root(value: str | None) -> Path:
    if value:
        return Path(value).resolve()
    here = Path(__file__).resolve().parents[1]
    candidates = [
        Path(r"D:\dbclean-source\examples\xiaozhi-esp32"),
        here / "tmp" / "remote-esp-vocat" / "examples" / "xiaozhi-esp32",
        Path(r"D:\dbstage1-source\examples\xiaozhi-esp32"),
    ]
    for candidate in candidates:
        if (candidate / "main/boards/esp_vocat/assets/360_360/emote.json").is_file():
            return candidate.resolve()
    raise SystemExit("source root not found; pass --source-root explicitly")


def git_commit(root: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_status(root: Path) -> dict[str, Any]:
    """Return source checkout dirtiness and concrete porcelain paths."""
    try:
        status = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        lines = [line for line in status.splitlines() if line.strip()]
        return {"source_dirty": bool(lines), "source_dirty_paths": lines}
    except (OSError, subprocess.CalledProcessError):
        return {"source_dirty": None, "source_dirty_paths": None}


def git_dirty(root: Path) -> bool | None:
    """Compatibility helper for callers that only need the boolean."""
    return git_status(root)["source_dirty"]


def _partition_size_bytes(value: str) -> int:
    value = value.strip()
    match = re.fullmatch(r"(\d+)([KkMm]?)", value)
    if not match:
        raise ValueError(f"unsupported partition size: {value!r}")
    amount, suffix = int(match.group(1)), match.group(2).lower()
    return amount * {"": 1, "k": 1024, "m": 1024 * 1024}[suffix]


def assets_partition_bytes(root: Path) -> int | None:
    """Read the configured assets partition from the official CSV."""
    table = root / "partitions/v2/16m.csv"
    try:
        with table.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                if row and not row[0].lstrip().startswith("#") and row[0].strip() == "assets":
                    if len(row) < 5:
                        raise ValueError("assets partition row is truncated")
                    return _partition_size_bytes(row[4])
    except (OSError, ValueError):
        return None
    return None


def tracked_expression_input_status(root: Path) -> dict[str, Any]:
    """Identify tracked changes in files that define expression inputs."""
    paths = [
        "main/boards/esp_vocat/assets",
        "main/display/emote_display.cc",
        "main/display/emote_display.h",
        "main/boards/esp_vocat/dev_tools.cc",
        "main/boards/esp_vocat/dev_tools.h",
        "partitions/v2/16m.csv",
    ]
    try:
        output = subprocess.check_output(
            ["git", "-C", str(root), "diff", "HEAD", "--name-status", "--", *paths],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return {"tracked_inputs_clean": None, "tracked_input_changes": None,
                "tracked_input_paths": paths}
    changes = [line for line in output.splitlines() if line.strip()]
    return {"tracked_inputs_clean": not changes, "tracked_input_changes": changes,
            "tracked_input_paths": paths}


def export_capabilities(root: Path) -> dict[str, Any]:
    assets = root / "main/boards/esp_vocat/assets"
    emote_path = assets / "360_360/emote.json"
    entries = json.loads(emote_path.read_text(encoding="utf-8"))
    expressions = []
    for entry in entries:
        src = str(entry["src"])
        path = assets / "emoji_large" / src
        item: dict[str, Any] = {"id": entry["emote"], "src": src,
                                "loop": bool(entry["loop"]), "fps": int(entry["fps"]),
                                "path": path.relative_to(root).as_posix()}
        if path.is_file():
            item.update({"size": path.stat().st_size, "sha256": sha256(path)})
            try:
                item["eaf"] = parse_eaf(path)
            except ValueError as exc:
                item["eaf_error"] = str(exc)
        else:
            item["missing"] = True
        expressions.append(item)
    listen = assets / "emoji_large/listen.eaf"
    listening = {"src": "listen.eaf", "path": listen.relative_to(root).as_posix()}
    if listen.is_file():
        listening.update({"size": listen.stat().st_size, "sha256": sha256(listen),
                          "eaf": parse_eaf(listen)})
    input_status = tracked_expression_input_status(root)
    checkout_status = git_status(root)
    partition_bytes = assets_partition_bytes(root)
    return {
        "schema": SCHEMA, "source_root": str(root), "source_commit": git_commit(root),
        "provenance": {"emote_manifest": str(emote_path.relative_to(root).as_posix()),
                        "assets_partition": "assets", "source_only": True,
                        "source_dirty": checkout_status["source_dirty"],
                        "source_dirty_paths": checkout_status["source_dirty_paths"],
                        "tracked_inputs_clean": input_status["tracked_inputs_clean"],
                        "tracked_input_changes": input_status["tracked_input_changes"],
                        "tracked_input_paths": input_status["tracked_input_paths"],
                        "assets_partition_bytes": partition_bytes},
        "expressions": expressions, "listening": listening,
        "actions": [{"id": k, "protocol_value": v, "source_supported": True}
                    for k, v in ACTION_IDS.items()],
        "emotion_action_map": EMOTION_ACTION,
        "protected_semantics": sorted(PROTECTED),
        "runtime": {"runtime_available": False, "device_playback_verified": False,
                     "activation_supported": False,
                     "note": "The source package has no public atomic hot-replacement API."},
    }


def safe_asset(root: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel or "\x00" in rel or Path(rel).is_absolute():
        raise ValueError("asset path must be relative")
    root_dir = root.resolve()
    relative = Path(rel)
    if relative.drive or relative.anchor:
        raise ValueError("asset path must be relative")
    if ".." in relative.parts:
        raise ValueError("asset path traversal is not accepted")
    if relative.suffix.lower() != ".eaf":
        raise ValueError("asset must be an EAF file")
    current = root_dir
    for part in relative.parts:
        if len(part) > MAX_NAME_LENGTH or not ASSET_NAME_RE.fullmatch(part):
            raise ValueError("asset path contains an illegal name")
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink assets are not accepted")
    candidate = current.resolve()
    try:
        candidate.relative_to(root_dir)
    except ValueError as exc:
        raise ValueError("asset path escapes candidate root") from exc
    return candidate


def validate_manifest(
    manifest_path: Path,
    candidate_root: Path,
    allow_protected: bool,
    max_package_bytes: int | None = DEFAULT_ASSETS_PARTITION_BYTES,
    package_bin: Path | None = None,
) -> dict[str, Any]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    if "schema" in data and data["schema"] != SCHEMA:
        raise ValueError("unsupported manifest schema/version")
    if max_package_bytes is not None and (
        isinstance(max_package_bytes, bool) or not isinstance(max_package_bytes, int)
        or max_package_bytes <= 0
    ):
        raise ValueError("package size limit must be a positive integer")
    expressions = data.get("expressions", [])
    if not isinstance(expressions, list):
        raise ValueError("expressions must be an array")
    seen_ids: set[str] = set()
    ids: set[str] = set()
    validated = []
    errors = []
    imported_paths: dict[Path, int] = {}
    for item in expressions:
        try:
            if not isinstance(item, dict):
                raise ValueError("expression entry must be an object")
            ident, src = item["id"], item["src"]
            if not isinstance(ident, str) or len(ident) > MAX_NAME_LENGTH or not IDENT_RE.fullmatch(ident):
                raise ValueError("expression id has an illegal name")
            if not isinstance(src, str):
                raise ValueError("expression src must be a string")
            if ident in seen_ids:
                raise ValueError("duplicate expression id")
            seen_ids.add(ident)
            if not isinstance(item.get("loop"), bool):
                raise ValueError("loop must be boolean")
            fps_value = item.get("fps", 20)
            if isinstance(fps_value, bool) or not isinstance(fps_value, int):
                raise ValueError("fps must be an integer")
            fps = fps_value
            if not (1 <= fps <= MAX_FPS):
                raise ValueError("fps out of bounds")
            path = safe_asset(candidate_root, src)
            if not path.is_file():
                raise ValueError("missing asset")
            eaf = parse_eaf(path)
            layout = item.get("layout")
            if "layout" in item and (not isinstance(layout, str) or layout not in {"full_screen", "eye", "dialog", "listen"}):
                raise ValueError("layout must be full_screen, eye, dialog, or listen")
            segments = item.get("segments")
            if "segments" in item:
                if not isinstance(segments, dict) or set(segments) != {"enter", "loop", "exit"}:
                    raise ValueError("segments must define exactly enter, loop, and exit")
                if layout is None:
                    raise ValueError("segmented animations must declare a layout")
                cursor = 0
                checked_segments = {}
                for name in ("enter", "loop", "exit"):
                    bounds = segments[name]
                    if (not isinstance(bounds, list) or len(bounds) != 2
                            or any(isinstance(value, bool) or not isinstance(value, int) for value in bounds)):
                        raise ValueError(f"segment {name} must be a two-integer frame range")
                    start, end = bounds
                    if start != cursor or end <= start or end > eaf["image_frames"]:
                        raise ValueError(f"segment {name} must be non-empty, contiguous, and within the EAF")
                    checked_segments[name] = [start, end]
                    cursor = end
                if cursor != eaf["image_frames"]:
                    raise ValueError("segments must cover every EAF frame exactly once")
            size = path.stat().st_size
            validated_item = {"id": ident, "src": src, "loop": item["loop"],
                              "fps": fps, "size": size,
                              "sha256": sha256(path), "eaf": eaf}
            if layout is not None:
                validated_item["layout"] = layout
            if "segments" in item:
                validated_item["segments"] = checked_segments
            validated.append(validated_item)
            imported_paths[path] = size
            ids.add(ident)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            errors.append({"entry": item, "error": str(exc)})
    source_files_checked = not errors
    bindings = data.get("bindings", [])
    if not isinstance(bindings, list):
        errors.append({"entry": "bindings", "error": "bindings must be an array"})
        bindings = []
    resolved_bindings = []
    binding_ids: set[str] = set()
    for binding in bindings:
        try:
            if not isinstance(binding, dict):
                raise ValueError("binding entry must be an object")
            semantic, target = binding["id"], binding["target"]
            if not isinstance(semantic, str) or len(semantic) > MAX_NAME_LENGTH or not IDENT_RE.fullmatch(semantic):
                raise ValueError("binding id has an illegal name")
            if not isinstance(target, str):
                raise ValueError("binding target must be a string")
            if semantic in binding_ids:
                raise ValueError("duplicate binding id")
            binding_ids.add(semantic)
            mode = binding.get("mode", "alias")
            if not isinstance(mode, str):
                raise ValueError("binding mode must be a string")
            if mode not in ("alias", "replace"):
                raise ValueError("binding mode must be alias or replace")
            if semantic in PROTECTED and not allow_protected:
                raise ValueError("protected semantic requires --allow-protected")
            if target not in ids:
                raise ValueError("binding target is not in imported expressions")
            resolved_bindings.append({"id": semantic, "target": target, "mode": mode})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"entry": binding, "error": str(exc)})
    action_defs = data.get("actions", [])
    if not isinstance(action_defs, list):
        errors.append({"entry": "actions", "error": "actions must be an array"})
        action_defs = []
    validated_actions = []
    action_ids: set[str] = set()
    for action in action_defs:
        try:
            if not isinstance(action, dict):
                raise ValueError("action entry must be an object")
            aid = action["id"]
            steps = action["steps"]
            if not isinstance(aid, str) or len(aid) > MAX_NAME_LENGTH or not IDENT_RE.fullmatch(aid):
                raise ValueError("action id has an illegal name")
            if aid in ACTION_IDS or aid in action_ids or not isinstance(steps, list) or not (1 <= len(steps) <= MAX_STEPS):
                raise ValueError("invalid action id/step count")
            action_ids.add(aid)
            total = 0
            out_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    raise ValueError("action step must be an object")
                base = step["action"]
                if not isinstance(base, str):
                    raise ValueError("action step name must be a string")
                if base not in ACTION_IDS or ACTION_IDS[base] is None:
                    raise ValueError(f"unknown base action: {base}")
                duration_value = step.get("duration_ms", 0)
                if isinstance(duration_value, bool) or not isinstance(duration_value, int):
                    raise ValueError("step duration must be an integer")
                duration = duration_value
                if not (0 <= duration <= MAX_STEP_MS):
                    raise ValueError("step duration out of bounds")
                total += duration
                out_steps.append({"action": base, "duration_ms": duration})
            if total > MAX_TOTAL_MS:
                raise ValueError("action duration exceeds limit")
            validated_actions.append({"id": aid, "steps": out_steps})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"entry": action, "error": str(exc)})
    imported_bytes = sum(imported_paths.values())
    if package_bin is not None:
        if not package_bin.exists() or not package_bin.is_file():
            raise ValueError("--package-bin does not exist or is not a file")
        package_bytes = package_bin.stat().st_size
        if package_bytes == 0:
            raise ValueError("--package-bin is empty")
    else:
        package_bytes = None
    measured_bytes = package_bytes if package_bytes is not None else imported_bytes
    if max_package_bytes is not None and measured_bytes > max_package_bytes:
        errors.append({
            "entry": "package",
            "error": f"package size {measured_bytes} exceeds limit {max_package_bytes}",
        })
    return {"schema": SCHEMA, "result": "offline_validated" if not errors else "rejected",
            "device_playback_verified": False, "activation_supported": False,
            "expressions": validated, "bindings": resolved_bindings,
            "actions": validated_actions, "errors": errors,
            "package": {
                "measured_bytes": measured_bytes,
                "imported_asset_bytes": imported_bytes,
                "package_binary_bytes": package_bytes,
                "source_files_checked": source_files_checked,
                "built_package_capacity_verified": (
                    package_bytes is not None and max_package_bytes is not None
                    and package_bytes <= max_package_bytes
                ),
                "measurement_kind": "built_package" if package_bytes is not None else "source_asset_sum",
                "limit_bytes": max_package_bytes,
                "note": "Source asset sums exclude other bundled assets and filesystem metadata; only a built package can verify partition capacity.",
            }}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--source-root", type=Path)
    exp.add_argument("--output", required=True, type=Path)
    val = sub.add_parser("validate")
    val.add_argument("--manifest", required=True, type=Path)
    val.add_argument("--candidate-root", required=True, type=Path)
    val.add_argument("--allow-protected", action="store_true")
    val.add_argument("--max-package-bytes", type=int,
                     default=DEFAULT_ASSETS_PARTITION_BYTES,
                     help="maximum package size (default: official 6000K / 6,144,000-byte assets partition)")
    val.add_argument("--package-bin", type=Path,
                     help="measure an already-built assets binary instead of source files")
    args = parser.parse_args()
    try:
        if args.command == "export":
            result = export_capabilities(find_source_root(str(args.source_root) if args.source_root else None))
        else:
            result = validate_manifest(
                args.manifest.resolve(), args.candidate_root.resolve(), args.allow_protected,
                args.max_package_bytes,
                args.package_bin.resolve() if args.package_bin else None,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "rejected", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True) if args.command == "export" else None
    if args.command == "export":
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("result", "offline_validated") != "rejected" else 2


if __name__ == "__main__":
    sys.exit(main())
