"""Observe the workbench and save live head gestures in the client."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.client_head_touch import DEVICE, FIRMWARE, TouchOutbox, observed_touches, json_request


def sync_cycle(outbox, *, studio_state, client_health, send, now_ms):
    """Run one reconnect-safe pass; all durable work stays in the outbox."""
    studio = "unavailable"
    device = "unknown"
    client = "unavailable"
    error = None
    observed = []
    new_events = 0
    try:
        state = studio_state()
        if not isinstance(state, dict):
            raise ValueError("head_touch_observation_invalid")
        connection = state.get("connection", {})
        identity = state.get("identity", {})
        if not isinstance(connection, dict) or not isinstance(identity, dict):
            raise ValueError("head_touch_observation_invalid")
        connected = connection.get("status") == "connected"
        studio = "connected" if connected else "disconnected"
        hello = identity.get("hello") or {}
        if not isinstance(hello, dict) or not isinstance(hello.get("firmware", {}), dict):
            raise ValueError("head_touch_observation_invalid")
        device = "confirmed" if (connected and identity.get("status") == "confirmed"
            and hello.get("device_id") == DEVICE
            and hello.get("firmware", {}).get("app_elf_sha256") == FIRMWARE) else "unconfirmed"
        observed = observed_touches(state, now_ms=now_ms)
        new_events = outbox.observe(observed)
    except (OSError, ValueError) as failure:
        error = type(failure).__name__

    try:
        health = client_health()
        client = "online" if isinstance(health, dict) and health.get("status") == "ok" else "unavailable"
        if client != "online" and error is None:
            error = "client_unavailable"
    except (OSError, ValueError) as failure:
        client = "offline"
        if error is None:
            error = type(failure).__name__

    # Expiry still advances while either service is offline.
    outbox.tick(send if client == "online" else None, now_ms)
    error = outbox.last_error or error
    return dict(studio=studio, device=device, client=client, observed=new_events,
                last_observed_at=max((item["observed_at"] for item in observed), default=None),
                counts=outbox.status(), error=error)


def run(folder, token_file, *, studio_port=8780, client_port=4311):
    token = Path(token_file).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ValueError("head_touch_local_token_required")
    folder.mkdir(parents=True, exist_ok=True)
    outbox = TouchOutbox(folder / "outbox.sqlite")
    started_at = datetime.now(timezone.utc).isoformat()
    status = dict(schema="deskbot.head-touch-sync.v1", pid=os.getpid(), started_at=started_at,
                  run_state="starting", studio="unknown", device="unknown", client="unknown")
    previous_state = None

    def publish(run_state):
        nonlocal previous_state
        status.update(run_state=run_state, at=datetime.now(timezone.utc).isoformat())
        temporary = folder / "status.tmp"
        temporary.write_text(json.dumps(status), encoding="utf-8")
        temporary.replace(folder / "status.json")
        state_key = tuple(status.get(k) for k in ("run_state", "studio", "device", "client", "error"))
        if state_key != previous_state:
            path = folder / "transitions.jsonl"
            if path.exists() and path.stat().st_size > 262_144:
                path.replace(folder / "transitions.previous.jsonl")
            with path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(status) + "\n")
            previous_state = state_key

    try:
        publish("running")
        while not (folder / "stop").exists():
            now = datetime.now(timezone.utc)
            now_ms = int(now.timestamp() * 1000)
            cycle = sync_cycle(
                outbox,
                studio_state=lambda: json_request(f"http://127.0.0.1:{studio_port}/api/body/state", timeout=2),
                client_health=lambda: json_request(f"http://127.0.0.1:{client_port}/health", timeout=2),
                send=lambda value: json_request(
                    f"http://127.0.0.1:{client_port}/api/hardware/head-touch", value, token=token),
                now_ms=now_ms,
            )
            previous_observed = status.get("last_observed_at")
            status.update(cycle)
            status["last_observed_at"] = cycle["last_observed_at"] or previous_observed
            status["last_recorded"] = outbox.latest()
            publish("running")
            time.sleep(1)
    finally:
        try:
            publish("stopped")
        finally:
            outbox.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--stop", action="store_true")
    parser.add_argument("--studio-port", type=int, default=8780)
    parser.add_argument("--client-port", type=int, default=4311)
    args = parser.parse_args()
    if args.status:
        path = args.folder / "status.json"
        print(path.read_text(encoding="utf-8") if path.is_file() else '{"run_state":"not_started"}')
    elif args.stop:
        args.folder.mkdir(parents=True, exist_ok=True)
        (args.folder / "stop").touch()
        print('{"run_state":"stopping"}')
    else:
        if not args.token_file or not (1 <= args.studio_port <= 65535 and 1 <= args.client_port <= 65535):
            parser.error("运行需要本机凭证和有效端口")
        run(args.folder, args.token_file, studio_port=args.studio_port, client_port=args.client_port)
