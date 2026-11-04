"""Extract bounded E1.42.3 serial records, without claiming MCP provenance."""
from __future__ import annotations

import json
import re

MARKER = "[DESKBOT_E1423 "
RECORD = re.compile(r"\[DESKBOT_E1423 (capacity|probe) bytes=([0-9]{1,5})\] (.*)$")


def extract_records(log: str) -> dict[str, dict]:
    records = {}
    for line in log.splitlines():
        if MARKER not in line:
            continue
        match = RECORD.search(line)
        if match is None:
            raise ValueError("malformed diagnostic record")
        kind, length, payload = match.groups()
        if not 0 < int(length) <= 8192 or len(payload.encode("utf-8")) != int(length):
            raise ValueError("diagnostic record length mismatch")
        if kind in records:
            raise ValueError("duplicate record: isolate a single boot capture")
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("invalid diagnostic JSON") from error
        if not isinstance(value, dict) or "error" in value:
            raise ValueError("diagnostic report unavailable")
        records[kind] = value
    if set(records) != {"capacity", "probe"}:
        raise ValueError("incomplete capacity/probe capture")
    return records
