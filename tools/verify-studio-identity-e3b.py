#!/usr/bin/env python3
"""Verify identity clearing and passive reconnect through the local Studio."""
import argparse
import json
from pathlib import Path
import re
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol.device_link import validate_hello

ORIGIN = "http://127.0.0.1:8766"


def api(path, value=None):
    headers = {"X-DeskBot-Studio": "e3a", "Content-Type": "application/json"}
    request = Request(ORIGIN + path, data=None if value is None else json.dumps(value).encode(), headers=headers)
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read(65536))


def connect():
    ports = api("/api/body/ports")
    rows = [p for p in ports["ports"] if p["port"] == "COM4" and p["usb_id"] == "303A:1001"]
    if len(rows) != 1:
        raise ValueError("fresh COM4 USB enumeration required")
    return api("/api/body/connect", {k: rows[0][k] for k in ("port", "key")})


def wait_identity(expected, after=-1):
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        value = api("/api/body/state")
        i = value["identity"]
        if i["status"] == "confirmed":
            hello = validate_hello(i["hello"])
            if any(hello[k] != expected[k] for k in ("device_id", "boot_id", "firmware", "hardware")):
                raise ValueError("live identity differs from verified boot")
            if hello["sequence"] > after:
                return value
        time.sleep(0.5)
    raise TimeoutError("fresh periodic identity not received")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"e3b-live-[0-9]{8}-[a-z0-9-]{1,32}", args.run) is None:
        raise ValueError("invalid run label")
    folder = ROOT / "tmp" / args.run
    expected = json.loads((folder / "restart-result.json").read_bytes())["boot"]["hello"]
    connect()
    first = wait_identity(expected)
    disconnected = api("/api/body/disconnect", {})
    if (disconnected["connection"]["status"] not in {"disabled", "disconnected"}
            or disconnected["identity"]["status"] != "disconnected" or disconnected["identity"]["hello"] is not None):
        raise ValueError("disconnect did not clear device identity")
    connect()
    reconnected = wait_identity(expected, first["identity"]["hello"]["sequence"])
    later = wait_identity(expected, reconnected["identity"]["hello"]["sequence"])
    tx.write_new(folder / "reconnect-evidence.json", tx.canonical({"first": first, "disconnected": disconnected,
                 "reconnected": reconnected, "later": later}))
    print("Studio identity confirmed; passive reconnect kept device/boot identity; periodic sequence advanced.")


if __name__ == "__main__":
    main()
