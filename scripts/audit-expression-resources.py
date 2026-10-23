#!/usr/bin/env python3
"""Read-only audit of DeskBot expression resources and the E1 master library.

The audit deliberately separates the current VoCat device format from the
Espressif research package. It never opens a serial port, builds firmware, or
writes an assets partition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DEFAULT = ROOT / "tmp/remote-esp-vocat/examples/xiaozhi-esp32"
OFFICIAL_PACKAGE_DEFAULT = ROOT / "tmp/remote-esp-vocat-official-build/b-events-v1/expression_assets.bin"
RESEARCH_BUNDLE_DEFAULT = ROOT / "tmp/research-emote-tool/emote_assets.bin"
MANIFEST_DEFAULT = ROOT / "protocol/expression-master-library-v1.json"
OUTPUT_DEFAULT = ROOT / "tmp/expression-e1-20261019/resource-audit.json"
PARTITION_BYTES_DEFAULT = 6000 * 1024
MAX_EAF_FRAMES = 4096
MAX_FILE_BYTES = 32 * 1024 * 1024
IDENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_library_manifest(manifest: Any) -> None:
    if (not isinstance(manifest, dict) or manifest.get("schema") != "deskbot.expression_master_library/v1"
            or type(manifest.get("version")) is not int or manifest["version"] != 1):
        raise ValueError("unsupported master library manifest")
    display = manifest.get("display", {})
    if any(display.get(key) != value for key, value in
           {"width": 360, "height": 360, "shape": "circle", "alignment_px": 4}.items()):
        raise ValueError("invalid master library display contract")
    if any(manifest.get("policy", {}).get(key) is not True for key in
           ("official_baseline_immutable", "research_is_not_device_input", "candidate_requires_capacity_audit")):
        raise ValueError("invalid master library policy")
    collections = manifest.get("collections")
    if not isinstance(collections, list) or not collections:
        raise ValueError("master library has no collections")
    ids: set[str] = set()
    for item in collections:
        if (not isinstance(item, dict) or not IDENT_RE.fullmatch(item.get("id", ""))
                or item["id"] in ids or item.get("origin") not in ("official_device", "research_only", "candidate")
                or not isinstance(item.get("source"), dict) or not isinstance(item.get("format"), str)
                or not isinstance(item.get("compatibility"), str) or type(item.get("device_package_eligible")) is not bool):
            raise ValueError("invalid or duplicate master library collection")
        if item["origin"] == "research_only" and item["device_package_eligible"]:
            raise ValueError("research collection cannot be device eligible")
        ids.add(item["id"])


def _safe_name(name: str) -> bool:
    return bool(IDENT_RE.fullmatch(name)) and name not in (".", "..")


def parse_package_bytes(raw: bytes, *, modern: bool) -> dict[str, Any]:
    """Inspect the two observed mmap layouts; never extract member paths."""
    header_size = 32 if modern else 12
    if len(raw) < header_size or len(raw) > MAX_FILE_BYTES:
        raise ValueError("invalid package size/header")
    if modern:
        if raw[:4] != b"MMAP":
            raise ValueError("research bundle has an invalid MMAP header")
        version, name_len, count, checksum, payload_size = struct.unpack_from("<5I", raw, 4)
        if version != 0x10000:
            raise ValueError("unsupported research MMAP version")
    else:
        count, checksum, payload_size = struct.unpack_from("<3I", raw)
        version, name_len = None, 32  # verified CONFIG_MMAP_FILE_NAME_LENGTH
    if payload_size != len(raw) - header_size or sum(raw[header_size:]) & 0xFFFF != checksum:
        raise ValueError("bundle checksum/header mismatch")
    if not 1 <= name_len <= 256 or not 1 <= count <= 4096:
        raise ValueError("bundle name slot or member count is invalid")
    payload = raw[header_size:]
    stride = name_len + 12
    table_size = stride * count
    if table_size > len(payload):
        raise ValueError("bundle index table is out of bounds")
    members: dict[str, bytes] = {}
    ranges: list[tuple[int, int]] = []
    for index in range(count):
        start = index * stride
        name = payload[start:start + name_len].split(b"\0", 1)[0].decode("utf-8")
        if not _safe_name(name) or name in members:
            raise ValueError(f"unsafe or duplicate package member name: {name!r}")
        size, offset, _, _ = struct.unpack_from("<IIHH", payload, start + name_len)
        actual, end = table_size + offset, table_size + offset + 2 + size
        if not size or end > len(payload) or payload[actual:actual + 2] != b"ZZ":
            raise ValueError(f"package member {name} is out of bounds")
        if any(actual < previous_end and previous_start < end for previous_start, previous_end in ranges):
            raise ValueError(f"package member {name} overlaps another member")
        ranges.append((actual, end))
        members[name] = payload[actual + 2:end]
    return {"header": {"version": version, "name_slot_bytes": name_len, "member_count": count,
                       "header_size": header_size, "payload_size": payload_size, "checksum_valid": True},
            "members": members}


def git_commit(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _eaf_frame_details(raw: bytes, frame_count: int, payload_len: int) -> tuple[list[dict[str, Any]], set[int]]:
    table_end = 16 + frame_count * 8
    payload_end = 16 + payload_len
    if payload_len < frame_count * 8 or table_end > len(raw) or payload_end > len(raw):
        raise ValueError("EAF table or payload length is out of bounds")
    region_size = payload_end - table_end
    ranges: list[tuple[int, int]] = []
    frames: list[dict[str, Any]] = []
    encodings: set[int] = set()
    for index in range(frame_count):
        size, offset = struct.unpack_from("<II", raw, 16 + index * 8)
        if size < 4 or offset > region_size or size > region_size - offset:
            raise ValueError(f"EAF frame {index} is out of bounds")
        end = offset + size
        for previous_start, previous_end in ranges:
            overlaps = offset < previous_end and previous_start < end
            if overlaps and (offset, end) != (previous_start, previous_end):
                raise ValueError(f"EAF frame {index} partially overlaps another frame")
        ranges.append((offset, end))
        frame = raw[table_end + offset:table_end + end]
        if frame[:2] != b"ZZ":
            raise ValueError(f"EAF frame {index} does not start with ZZ")
        kind = frame[2:4]
        item: dict[str, Any] = {"index": index, "kind": kind.decode("ascii", errors="replace"), "size_bytes": size}
        if kind == b"_C":
            frames.append(item)
            continue
        if kind != b"_S":
            raise ValueError(f"EAF frame {index} has unsupported frame kind {kind!r}")
        data = frame[2:]
        if len(data) < 22:
            raise ValueError(f"EAF frame {index} static header is truncated")
        bit_depth = data[9]
        width, height, blocks, block_height = struct.unpack_from("<HHHH", data, 10)
        if (bit_depth not in (1, 2, 4, 8, 24) or not 0 < width <= 1024 or not 0 < height <= 1024
                or not 0 < blocks <= 256 or not 0 < block_height <= 1024
                or blocks * block_height < height or (blocks - 1) * block_height >= height):
            raise ValueError(f"EAF frame {index} has invalid dimensions or block count")
        palette_bytes = 0 if bit_depth == 24 else (1 << bit_depth) * 4
        data_start = 18 + blocks * 4 + palette_bytes
        if data_start > len(data):
            raise ValueError(f"EAF frame {index} block table or palette is truncated")
        lengths = [struct.unpack_from("<I", data, 18 + 4 * block)[0] for block in range(blocks)]
        if any(length == 0 for length in lengths) or data_start + sum(lengths) > len(data):
            raise ValueError(f"EAF frame {index} block data is invalid")
        block_encodings = []
        cursor = data_start
        for block_length in lengths:
            encoding = data[cursor]
            cursor += block_length
            block_encodings.append(encoding)
            encodings.add(encoding)
        item.update({"width": width, "height": height, "bit_depth": bit_depth,
                     "blocks": blocks, "block_height": block_height,
                     "encodings": sorted(set(block_encodings))})
        frames.append(item)
    return frames, encodings


def parse_eaf_bytes(raw: bytes, *, fps: int | None = None, loops: list[int] | None = None) -> dict[str, Any]:
    if len(raw) < 16 or len(raw) > MAX_FILE_BYTES or raw[0] != 0x89 or raw[1:4] not in (b"EAF", b"AAF"):
        raise ValueError("invalid EAF magic/header")
    frame_count, checksum, payload_len = struct.unpack_from("<III", raw, 4)
    if not 0 < frame_count <= MAX_EAF_FRAMES:
        raise ValueError("EAF frame count is outside the supported range")
    if payload_len != len(raw) - 16 or payload_len < frame_count * 8:
        raise ValueError("EAF table or payload length is out of bounds")
    if sum(raw[16:16 + payload_len]) & 0xFFFFFFFF != checksum:
        raise ValueError("EAF checksum mismatch")
    frames, encodings = _eaf_frame_details(raw, frame_count, payload_len)
    drawable = [item for item in frames if item["kind"] == "_S"]
    if not drawable:
        raise ValueError("EAF contains no drawable image frames")
    dimensions = sorted({(item["width"], item["height"]) for item in drawable})
    preview = {"result": "format_supported", "evidence": "header and block encoding inspection only"}
    unsupported = sorted(encodings - {0})
    if unsupported:
        preview = {"result": "rejected", "reason": f"current E1 decoder supports encoding 0 only; found {unsupported}"}
    elif any(item["bit_depth"] != 8 for item in drawable) or len(dimensions) != 1:
        preview = {"result": "rejected", "reason": "current E1 decoder requires fixed dimensions and 8-bit indexed pixels"}
    result: dict[str, Any] = {
        "format": raw[1:4].decode("ascii"),
        "size_bytes": len(raw),
        "sha256": sha256_bytes(raw),
        "stored_frame_count": frame_count,
        "image_frame_count": len(drawable),
        "metadata_frame_count": frame_count - len(drawable),
        "payload_length": payload_len,
        "dimensions": [{"width": width, "height": height} for width, height in dimensions],
        "encodings": sorted(encodings),
        "preview": preview,
        "native_canvas_is_360x360": dimensions == [(360, 360)],
    }
    if fps is not None:
        result["fps"] = fps
    if loops is not None:
        result["loop_points"] = loops
    if dimensions and len(dimensions) != 1:
        result["layout_note"] = "动画在不同帧使用了不同尺寸，需先统一裁切/布局"
    return result


def _official_collection(source_root: Path, package_path: Path) -> dict[str, Any]:
    manifest_path = source_root / "main/boards/esp_vocat/assets/360_360/emote.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, list):
        raise ValueError("official emote.json must be an array")
    by_src: dict[str, dict[str, Any]] = {}
    semantics: set[str] = set()
    for item in manifest:
        if not isinstance(item, dict) or not isinstance(item.get("emote"), str) or not isinstance(item.get("src"), str):
            raise ValueError("official emote.json has an invalid entry")
        src = item["src"]
        if not _safe_name(src) or item["emote"] in semantics:
            raise ValueError("official emote.json has an unsafe source or duplicate semantic")
        semantics.add(item["emote"])
        path = source_root / "main/boards/esp_vocat/assets/emoji_large" / src
        if not path.is_file():
            raise ValueError(f"missing official EAF: {src}")
        if src not in by_src:
            parsed = parse_eaf_bytes(path.read_bytes(), fps=int(item.get("fps", 20)))
            parsed.update({"name": src, "path": str(path.relative_to(source_root)).replace("\\", "/"),
                           "semantic_ids": [item["emote"]], "loop": bool(item.get("loop")),
                           "origin": "official_device", "device_package_eligible": True})
            by_src[src] = parsed
        else:
            by_src[src]["semantic_ids"].append(item["emote"])
            by_src[src]["loop"] = by_src[src]["loop"] or bool(item.get("loop"))
    if not package_path.is_file():
        raise ValueError(f"official package not found: {package_path}")
    semantic_asset_count = len(by_src)
    listen_path = source_root / "main/boards/esp_vocat/assets/emoji_large/listen.eaf"
    listening = parse_eaf_bytes(listen_path.read_bytes(), fps=15)
    listening.update({"name": "listen.eaf", "path": listen_path.relative_to(source_root).as_posix(),
                      "semantic_ids": [], "usage": "listen_anim", "loop": True,
                      "origin": "official_device", "device_package_eligible": True})
    by_src["listen.eaf"] = listening
    package_raw = package_path.read_bytes()
    package_size = len(package_raw)
    package = parse_package_bytes(package_raw, modern=False)
    for name, asset in by_src.items():
        if name not in package["members"] or sha256_bytes(package["members"][name]) != asset["sha256"]:
            raise ValueError(f"official package/source asset mismatch: {name}")
    by_src_values = sorted(by_src.values(), key=lambda item: item["name"].casefold())
    preview_compatible_count = sum(1 for item in by_src.values()
                                   if item.get("preview", {}).get("result") == "format_supported")
    return {
        "id": "official-vocat", "origin": "official_device", "format": "eaf",
        "source_commit": git_commit(source_root), "manifest": str(manifest_path),
        "package": {"path": str(package_path), "size_bytes": package_size, "sha256": sha256_bytes(package_raw),
                    "format": "legacy_mmap_12_byte_header", "header": package["header"],
                    "source_eaf_hashes_match_members": True},
        "assets": by_src_values,
        "semantic_count": len(manifest), "unique_asset_count": len(by_src),
        "semantic_asset_count": semantic_asset_count,
        "preview_compatible_asset_count": preview_compatible_count,
        "screen": {
            "width": 360, "height": 360, "shape": "circle",
            "composition": "official layout.json composes sprite EAFs onto the 360x360 screen",
            "raw_eaf_dimensions_are_not_required_to_be_360x360": True,
        },
        "compatibility": "current_esp_vocat_player", "device_package_eligible": True,
    }


def _research_collection(bundle_path: Path) -> dict[str, Any]:
    raw = bundle_path.read_bytes()
    package = parse_package_bytes(raw, modern=True)
    members = package["members"]
    if "index.json" not in members:
        raise ValueError("research bundle is missing index.json")
    index_asset = json.loads(members["index.json"])
    if not isinstance(index_asset, list):
        raise ValueError("research index.json must be an array")
    index_by_file: dict[str, dict[str, Any]] = {}
    for item in index_asset:
        if (not isinstance(item, dict) or not isinstance(item.get("file"), str)
                or not _safe_name(item["file"]) or not item["file"].endswith(".eaf")
                or item["file"] not in members or item["file"] in index_by_file
                or not isinstance(item.get("name"), str)):
            raise ValueError("research index contains an invalid, missing or duplicate animation")
        index_by_file[item["file"]] = item
    if set(index_by_file) != {name for name in members if name.endswith(".eaf")}:
        raise ValueError("research index does not cover the EAF members exactly")
    entries: list[dict[str, Any]] = []
    for name, item in sorted(index_by_file.items()):
        entry = parse_eaf_bytes(members[name], fps=25)
        loop = item.get("loop", [])
        if (not isinstance(loop, list) or len(loop) > 2 or
                any(type(point) is not int or not 0 <= point < entry["image_frame_count"] for point in loop)
                or (len(loop) == 2 and loop[0] >= loop[1])):
            raise ValueError(f"research animation {name} has invalid loop points")
        entry["name"] = name
        entry["display_name"] = item["name"]
        entry["loop_points"] = loop
        entry["fps_source"] = "research player default; not stored in EAF"
        entry["origin"] = "research_only"
        entry["device_package_eligible"] = False
        entry["compatibility"] = "research_player_required_device_unverified"
        entries.append(entry)
    return {
        "id": "espressif-emote-gen-sample", "origin": "research_only", "format": "mmap_emote_assets_v1",
        "package": {"path": str(bundle_path), "size_bytes": len(raw), "sha256": sha256_bytes(raw)},
        "header": package["header"],
        "assets": entries, "index_count": len(index_asset or []),
        "screen": {
            "width": 360, "height": 360,
            "composition": "research package assets are authored as native 360x360 frames",
        },
        "compatibility": "research_player_encoding_3", "device_package_eligible": False,
    }


def capacity_report(partition_bytes: int, package_bytes: int) -> dict[str, Any]:
    if type(partition_bytes) is not int or partition_bytes <= 0 or package_bytes <= 0:
        raise ValueError("partition and package sizes must be positive")
    remaining = partition_bytes - package_bytes
    return {"partition_bytes": partition_bytes, "official_package_bytes": package_bytes,
            "remaining_bytes": remaining, "used_percent": round(package_bytes / partition_bytes * 100, 4),
            "status": ("overflow" if remaining < 0 else "official_baseline_near_full"
                       if remaining < partition_bytes * 0.1 else "available"),
            "measurement": "built expression_assets.bin; not source-directory sum",
            "candidate_package_built": False, "device_partition_read_this_run": False}


def attach_source_archive(collection: dict[str, Any], archive_path: Path, expected_sha: str) -> None:
    """Inventory GIF/Lottie sources without extracting or converting the ZIP."""
    if sha256(archive_path) != expected_sha:
        raise ValueError("research source archive differs from the library anchor")
    sources: dict[str, dict[str, Any]] = {}
    lotties: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.infolist()) > 256 or sum(item.file_size for item in archive.infolist()) > MAX_FILE_BYTES:
            raise ValueError("research source archive exceeds inventory limits")
        for member in archive.infolist():
            path = Path(member.filename)
            if member.is_dir():
                continue
            if path.is_absolute() or path.drive or ".." in path.parts:
                raise ValueError("unsafe research source archive path")
            if path.suffix.lower() not in (".gif", ".json"):
                continue
            raw = archive.read(member)
            record = {"member": member.filename, "size_bytes": len(raw), "sha256": sha256_bytes(raw)}
            if path.suffix.lower() == ".gif":
                if raw[:6] not in (b"GIF87a", b"GIF89a") or len(raw) < 10:
                    raise ValueError("invalid GIF source header")
                width, height = struct.unpack_from("<HH", raw, 6)
                record.update({"width": width, "height": height})
                name = path.stem.split("_[", 1)[0]
                if name in sources:
                    raise ValueError("duplicate GIF source identity")
                sources[name] = {"name": name, "gif": record, "device_package_eligible": False}
            else:
                value = json.loads(raw)
                record["category"] = path.parent.name
                record["frame_rate"] = value.get("fr")
                name = "smile_static" if path.stem == "static" else path.stem
                if name in lotties:
                    raise ValueError("duplicate Lottie source identity")
                lotties[name] = record
    if set(sources) != set(lotties):
        raise ValueError("GIF and Lottie source identities do not match")
    for asset in collection["assets"]:
        candidates = [name for name in sources if asset["display_name"] == name or asset["display_name"].startswith(name + "_")]
        if not candidates:
            raise ValueError(f"packed research animation lacks a source: {asset['name']}")
        # smile_static must win over smile; never infer a device semantic.
        name = max(candidates, key=len)
        if sources[name].get("packed_member"):
            raise ValueError("multiple packed members map to one research source")
        sources[name]["packed_member"] = asset["name"]
        asset["source_name"] = name
    for name, source in sources.items():
        source["lottie"] = lotties[name]
        source["stage"] = "packed_research" if source.get("packed_member") else "source_only"
    collection["source_assets"] = sorted(sources.values(), key=lambda item: item["name"])
    collection["source_archive"] = {"path": str(archive_path), "sha256": expected_sha,
                                    "size_bytes": archive_path.stat().st_size,
                                    "source_count": len(sources), "extracted": False}


def verify_source_files(source_root: Path, expected_commit: str, files: list[str]) -> dict[str, Any]:
    actual = git_commit(source_root)
    if actual != expected_commit:
        raise ValueError("official source commit differs from the library anchor")
    checks = []
    for relative in sorted(set(files)):
        raw = (source_root / relative).read_bytes()
        committed = subprocess.check_output(["git", "-C", str(source_root), "show", f"{actual}:examples/xiaozhi-esp32/{relative}"],
                                            stderr=subprocess.DEVNULL)
        text = relative.endswith(".json") or relative.endswith(".csv")
        matches = raw.replace(b"\r\n", b"\n") == committed.replace(b"\r\n", b"\n") if text else raw == committed
        if not matches:
            raise ValueError(f"official resource differs from source anchor: {relative}")
        checks.append({"path": relative, "sha256": sha256_bytes(raw), "matches_commit": True})
    return {"commit": actual, "resource_inputs_match_commit": True, "files": checks}


def audit(args: argparse.Namespace, *, write_output: bool = True) -> dict[str, Any]:
    manifest = read_json(args.library_manifest)
    validate_library_manifest(manifest)
    official = _official_collection(args.source_root.resolve(), args.official_package.resolve())
    research = _research_collection(args.research_bundle.resolve())
    definitions = {item["id"]: item for item in manifest["collections"]}
    if set(definitions) != {official["id"], research["id"]}:
        raise ValueError("E1.1 audit supports exactly the two anchored collections")
    for collection in (official, research):
        definition = definitions[collection["id"]]
        if (collection["package"]["sha256"] != definition["source"].get("package_sha256")
                or collection["origin"] != definition["origin"]
                or collection["device_package_eligible"] is not definition["device_package_eligible"]):
            raise ValueError(f"package identity/policy differs from library anchor: {collection['id']}")
        collection["source"] = definition["source"]
    research_source = definitions[research["id"]]["source"]
    attach_source_archive(research, ROOT / research_source["source_archive"], research_source["source_archive_sha256"])
    official_source = definitions[official["id"]]["source"]
    partition_path = args.source_root.resolve() / official_source["partition_table"]
    rows = [line.split(",") for line in partition_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    size_spec = next(row[4].strip().lower() for row in rows if row[0].strip() == "assets")
    partition = int(size_spec[:-1]) * 1024 if size_spec.endswith("k") else int(size_spec, 0)
    if args.partition_bytes != partition:
        raise ValueError("partition size differs from the anchored official table")
    files = [item["path"] for item in official["assets"]] + [official_source["manifest"], official_source["layout"], official_source["partition_table"]]
    official["resource_provenance"] = verify_source_files(args.source_root.resolve(), official_source["commit"], files)
    result = {
        "schema": "deskbot.expression_resource_audit/v1", "result": "read_only_audit",
        "hardware_accessed": False, "firmware_changed": False,
        "display": manifest["display"], "library_manifest": str(args.library_manifest.resolve()),
        "capacity": capacity_report(partition, official["package"]["size_bytes"]),
        "collections": [official, research],
        "summary": {"official_semantic_count": official["semantic_count"],
                    "official_semantic_asset_count": official["semantic_asset_count"],
                    "official_unique_asset_count": official["unique_asset_count"],
                    "official_preview_compatible_asset_count": official["preview_compatible_asset_count"],
                    "research_asset_count": len(research["assets"]),
                    "research_source_count": len(research["source_assets"]),
                    "research_source_only": [item["name"] for item in research["source_assets"] if item["stage"] == "source_only"],
                    "research_member_count": research["header"]["member_count"]},
        "policy": manifest["policy"],
    }
    if write_output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--official-package", type=Path, default=OFFICIAL_PACKAGE_DEFAULT)
    parser.add_argument("--research-bundle", type=Path, default=RESEARCH_BUNDLE_DEFAULT)
    parser.add_argument("--library-manifest", type=Path, default=MANIFEST_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--partition-bytes", type=int, default=PARTITION_BYTES_DEFAULT)
    args = parser.parse_args(argv)
    try:
        report = audit(args)
    except (OSError, ValueError, KeyError, TypeError, struct.error, subprocess.CalledProcessError, zipfile.BadZipFile, StopIteration) as exc:
        print(json.dumps({"result": "rejected", "hardware_accessed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"result": report["result"], "output": str(args.output.resolve()),
                      "capacity": report["capacity"], "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
