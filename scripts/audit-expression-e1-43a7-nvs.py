#!/usr/bin/env python3
"""Compare retained A7 NVS captures with the installed IDF parser; no device I/O."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PARSER = Path(r"D:\Espressif\frameworks\esp-idf-v5.5.5\components\nvs_flash\nvs_partition_tool\nvs_parser.py")
sys.path.insert(0, str(PARSER.parent))
from nvs_parser import NVS_Partition

RUN = ROOT / "tmp/e143a7-live-20261103"
PUBLIC = ROOT / "artifacts/expression-upper-snapshot-e1-43a7-20261103/nvs-observation.json"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def inventory(path: Path) -> tuple[dict, dict]:
    raw = path.read_bytes()
    if len(raw) != 0x4000:
        raise ValueError("NVS capture length differs")
    partition = NVS_Partition(path.name, bytearray(raw))
    pages = sorted((p for p in partition.pages if not p.is_empty), key=lambda p: p.header["page_index"])
    namespaces, entries, chunks, indexes = {}, {}, {}, {}
    for page in pages:
        crc = page.header["crc"]
        if crc["original"] != crc["computed"]:
            raise ValueError("NVS page CRC differs")
        for entry in page.entries:
            if entry.state != "Written":
                continue
            crc = entry.metadata["crc"]
            if crc["original"] != crc["computed"] or entry.key is None or entry.data is None:
                raise ValueError("NVS written entry integrity differs")
            if entry.metadata["namespace"] == 0:
                namespaces[entry.data["value"]] = entry.key
    for page in pages:
        for entry in page.entries:
            if entry.state != "Written" or entry.metadata["namespace"] == 0:
                continue
            namespace = namespaces[entry.metadata["namespace"]]
            kind = entry.metadata["type"]
            if kind in ("string", "blob", "blob_data"):
                crc = entry.metadata["crc"]
                if crc["data_original"] != crc["data_computed"]:
                    raise ValueError("NVS variable entry CRC differs")
                payload = b"".join(bytes(child.raw) for child in entry.children)[:entry.data["size"]]
            else:
                payload = json.dumps(entry.data, sort_keys=True, separators=(",", ":")).encode("ascii")
            key = namespace + ":" + entry.key
            if kind == "blob_data":
                chunks[(key, entry.metadata["chunk_index"])] = payload
            elif kind == "blob_index":
                indexes[key] = entry.data
            else:
                entries[key] = sha(kind.encode("ascii") + b"\0" + payload)
    for key, index in indexes.items():
        start, count = index["chunk_start"], index["chunk_count"]
        payload = b"".join(chunks[(key, start + offset)] for offset in range(count))
        if len(payload) != index["size"]:
            raise ValueError("NVS blob index and payload size differ")
        entries[key] = sha(b"blob\0" + payload)
    facts = {"file": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": len(raw),
             "sha256": sha(raw), "logical_keys": len(entries),
             "page_and_written_entry_crc_passed": True}
    return entries, facts


def make_report() -> dict:
    before, first = inventory(RUN / "captures/129-snapshot-nvs-00009000-00004000.bin")
    after, last = inventory(RUN / "captures/275-snapshot-nvs-00009000-00004000.bin")
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = sorted(key for key in before.keys() & after.keys() if before[key] != after[key])
    return {"schema": "deskbot.upper_pattern.nvs_observation.e1_43a7/v1", "date": "2026-11-03",
            "before": first, "after": last, "added_keys": added, "removed_keys": removed,
            "changed_keys": changed, "bytes_equal": first["sha256"] == last["sha256"],
            "device_accessed": False, "values_exported": False,
            "parser_sha256": sha(PARSER.read_bytes()), "auditor_sha256": sha(Path(__file__).read_bytes())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = make_report()
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.check or PUBLIC.exists():
        if PUBLIC.read_bytes() != raw:
            raise ValueError("retained NVS observation differs")
    else:
        with PUBLIC.open("xb") as stream:
            stream.write(raw)
    print(json.dumps({key: report[key] for key in ("added_keys", "removed_keys", "changed_keys", "bytes_equal")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
