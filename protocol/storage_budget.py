"""Offline Flash/MMU arithmetic. No serial, firmware mutation or runtime claims."""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct

PAGE_BYTES = 65536
FLASH_BYTES = 16 * 1024 * 1024


def page_span(start: int, size: int, page_bytes: int = PAGE_BYTES) -> int:
    if any(type(value) is not int for value in (start, size, page_bytes)):
        raise ValueError("integer addresses and sizes required")
    if start < 0 or size <= 0 or page_bytes <= 0 or page_bytes & (page_bytes - 1):
        raise ValueError("invalid address, size or page size")
    return ((start % page_bytes) + size + page_bytes - 1) // page_bytes


def validate_layout(rows: list[dict], flash_bytes: int = FLASH_BYTES) -> dict[str, dict]:
    if type(flash_bytes) is not int or flash_bytes <= 0 or not rows:
        raise ValueError("invalid Flash capacity or empty layout")
    by_name = {}
    previous_end = 0
    for row in rows:
        name = row.get("name")
        start, size = row.get("start"), row.get("size_bytes")
        if not isinstance(name, str) or not name or name in by_name:
            raise ValueError("missing/duplicate partition name")
        if type(start) is not int or type(size) is not int or start < 0 or size <= 0:
            raise ValueError("invalid partition address/size")
        if start % 4096 or size % 4096:
            raise ValueError("partition is not sector aligned")
        if row.get("type") == 0 and start % PAGE_BYTES:
            raise ValueError("App is not 64 KiB aligned")
        end = start + size
        if row.get("end_exclusive") != end or start < previous_end or end > flash_bytes:
            raise ValueError("overlap, out-of-order, wrong end or Flash overflow")
        by_name[name] = dict(row)
        previous_end = end
    return by_name


def compare_layouts(old_rows: list[dict], new_rows: list[dict]) -> dict:
    old, new = validate_layout(old_rows), validate_layout(new_rows)
    if old.keys() != new.keys() or set(old) != {"nvs", "otadata", "phy_init", "ota_0", "assets", "model"}:
        raise ValueError("unexpected partition set")
    for name in ("nvs", "otadata", "phy_init", "ota_0"):
        if old[name] != new[name]:
            raise ValueError("protected partition changed: " + name)
    if old["assets"]["size_bytes"] != 6000 * 1024 or new["assets"]["size_bytes"] != 8 * 1024 * 1024:
        raise ValueError("unexpected assets capacity")
    for name in ("assets", "model"):
        for field in ("type", "subtype", "flags"):
            if old[name][field] != new[name][field]:
                raise ValueError("partition metadata changed")
    if old["assets"]["start"] != new["assets"]["start"]:
        raise ValueError("assets start changed")
    if old["model"]["size_bytes"] != new["model"]["size_bytes"]:
        raise ValueError("model capacity changed")
    return {
        "assets_start": new["assets"]["start"],
        "current_tail_bytes": FLASH_BYTES - old["model"]["end_exclusive"],
        "candidate_tail_bytes": FLASH_BYTES - new["model"]["end_exclusive"],
        "current_assets_span_pages": page_span(old["assets"]["start"], old["assets"]["size_bytes"]),
        "candidate_assets_span_pages": page_span(new["assets"]["start"], new["assets"]["size_bytes"]),
        "current_model_span_pages_if_mapped": page_span(old["model"]["start"], old["model"]["size_bytes"]),
        "candidate_model_span_pages_if_mapped": page_span(new["model"]["start"], new["model"]["size_bytes"]),
        "new_assets_overwrites_entire_old_model": (
            new["assets"]["start"] <= old["model"]["start"]
            and new["assets"]["end_exclusive"] >= old["model"]["end_exclusive"]),
        "runtime_mmap_proven": False,
    }


def reserve_projection(package_bytes: int, app_bytes: int, assets_bytes: int = 8 * 1024 * 1024,
                       assets_headroom_bytes: int = 1024 * 1024) -> dict:
    values = (package_bytes, app_bytes, assets_bytes, assets_headroom_bytes)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("positive integer budget required")
    app_capacity = 6000 * 1024
    if app_bytes > app_capacity or package_bytes > assets_bytes or assets_headroom_bytes >= assets_bytes:
        raise ValueError("budget exceeds capacity")
    soft_limit = assets_bytes - assets_headroom_bytes
    return {"assets_capacity_bytes": assets_bytes, "package_bytes": package_bytes,
            "assets_free_bytes": assets_bytes - package_bytes,
            "proposed_release_headroom_bytes": assets_headroom_bytes,
            "proposed_package_soft_limit_bytes": soft_limit,
            "growth_to_soft_limit_bytes": soft_limit - package_bytes,
            "within_proposed_soft_limit": package_bytes <= soft_limit,
            "app_capacity_bytes": app_capacity, "app_bytes": app_bytes,
            "app_free_bytes": app_capacity - app_bytes,
            "policy_status": "planning_only_not_firmware_enforced"}


def mapped_image_segments(raw: bytes) -> list[dict]:
    """Read ESP32 extended image segment ranges, not a signature validator."""
    if len(raw) < 24 or raw[0] != 0xE9 or not 1 <= raw[1] <= 16:
        raise ValueError("invalid ESP32 image header")
    offset, result = 24, []
    for _ in range(raw[1]):
        if offset + 8 > len(raw):
            raise ValueError("truncated segment header")
        start, size = struct.unpack_from("<II", raw, offset)
        offset += 8
        if size > len(raw) - offset:
            raise ValueError("truncated segment payload")
        for kind, base in (("DROM", 0x3C000000), ("IROM", 0x42000000)):
            if base <= start < base + 0x2000000:
                if size <= 0 or start + size > base + 0x2000000:
                    raise ValueError("mapped segment exceeds virtual window")
                result.append({"kind": kind, "virtual_start": start, "bytes": size,
                               "span_pages": page_span(start, size)})
        offset += size
    if not result:
        raise ValueError("image has no mapped Flash segments")
    return result


def valid_build_checkpoint(result: dict, root: Path, work: Path, enabled: bool, layout: str,
                           files: tuple[str, ...], expected_table_sha256: str) -> bool:
    """Reuse a completed local build only if identity, paths and bytes still match."""
    if type(result.get("observer_enabled")) is not bool or layout not in {"old", "8m"}:
        return False
    if result["observer_enabled"] is not enabled or result.get("layout") != layout:
        return False
    archive = root / result.get("archive", "")
    prefix = f"{'on' if enabled else 'off'}-{layout}"
    if archive.resolve() != (work / "outputs" / prefix).resolve() or not archive.is_dir():
        return False
    if not archive.resolve().is_relative_to(work.resolve()) or archive.is_symlink():
        return False
    steps = result.get("steps", [])
    if len(steps) != 2:
        return False

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    for step, suffix in zip(steps, ("configure", "build")):
        log = root / step.get("log", "")
        if log.resolve() != (work / f"{prefix}-{suffix}.log").resolve() or not log.is_file():
            return False
        if step.get("returncode") != 0 or digest(log) != step.get("log_sha256"):
            return False
    for name in files:
        path = archive / name
        info = result.get("artifacts", {}).get(name)
        if not path.is_file() or not isinstance(info, dict):
            return False
        if path.stat().st_size != info.get("bytes") or digest(path) != info.get("sha256"):
            return False
    return (digest(archive / "partition_table/partition-table.bin") == expected_table_sha256
            and result.get("observer_source_members") == int(enabled))
