#!/usr/bin/env python3
"""Read the real E1.45 directory and request three explicit layout switches."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import uuid
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_e3f4c_reference import APP_ELF_SHA
from protocol.studio_resource_installation import E3F4C_APP_SHA

WORK = ROOT / "tmp/studio-resources-e3f4d"
PUBLIC = ROOT / "artifacts/studio-resources-e3f4d"
HTTP = build_opener(ProxyHandler({}))
BASE = "http://127.0.0.1:8775"


def api(path, body=None, header="e3c6"):
    request = Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-DeskBot-Studio": header})
    with HTTP.open(request, timeout=10) as response:
        return json.load(response)


def save(name, value, *, public=False):
    folder = PUBLIC if public else WORK
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def command(operation, state, hello, *, command_id=None):
    return api("/api/studio/command", dict(operation=operation, command_id=command_id or "e3f4d-" + uuid.uuid4().hex,
        connection_id=state["connection_id"], target_id=hello["device_id"], boot_id=hello["boot_id"],
        session_id=None if operation == "enable" else state["session_id"]))


def prepare():
    if api("/api/body/state")["connection"]["status"] != "connected":
        port = next(row for row in api("/api/body/ports")["ports"] if row["port"] == "COM4")
        api("/api/body/connect", dict(port=port["port"], key=port["key"]), "e3a")
    deadline = time.monotonic() + 25
    hello = None
    while time.monotonic() < deadline:
        body = api("/api/body/state")
        hello = body.get("identity", {}).get("hello")
        if hello:
            if hello["firmware"]["app_elf_sha256"] != APP_ELF_SHA:
                raise ValueError("accepted layout App identity changed")
            break
        time.sleep(0.5)
    if not hello:
        raise TimeoutError("current identity not received")
    save("live-body.json", body)
    state = api("/api/studio/state")
    if not state["enabled"]:
        command("enable", state, hello)
    deadline = time.monotonic() + 110
    requested = False
    while time.monotonic() < deadline:
        state = api("/api/studio/state")
        if state.get("boot_id") not in (None, hello["boot_id"]):
            raise ValueError("boot changed during directory read")
        if state["catalog"]["status"] == "complete":
            break
        if state["catalog"]["status"] == "failed":
            raise ValueError("installed directory read failed")
        if (not requested and state.get("session_id") and state.get("display")
                and state["catalog"]["status"] != "reading" and not state.get("host_command")):
            command("read_catalog", state, hello)
            requested = True
        time.sleep(0.5)
    save("live-control.json", state)
    actual = state["catalog"].get("manifest")
    expected = json.loads((PUBLIC / "expected-directory.json").read_bytes())
    if actual != expected:
        raise ValueError("installed directory differs from native E1.45 output")
    report = dict(schema="deskbot.studio_resource_e3f4d_live/v1", result="app_and_directory_verified",
        recorded_at=datetime.now(timezone.utc).isoformat(), app="E3f-4c", resources="E1.45", port="COM4",
        app_sha256=E3F4C_APP_SHA, reported_elf_sha256=hello["firmware"]["app_elf_sha256"],
        connection_id=state["connection_id"], target_id=hello["device_id"], boot_id=hello["boot_id"], session_id=state["session_id"],
        entries=len(actual["entries"]), catalog_id=actual["catalog_id"], directory_matches_expected=True,
        embarrassed=next(row for row in actual["entries"] if row["kind"] == "expression" and row["id"] == "embarrassed"),
        eat=next(row for row in actual["entries"] if row["kind"] == "expression" and row["id"] == "eat"),
        full_frame=next(row for row in actual["entries"] if row["kind"] == "layout" and row["id"] == "full_frame"),
        screen_observation="pending", daily_interaction="pending", next="geometry_reflow_before_shy_request")
    save("live-result.json", report, public=True)
    print(json.dumps(dict(result=report["result"], entries=report["entries"])), flush=True)


def checked_context(report, state, hello):
    keys = ("connection_id", "target_id", "boot_id", "session_id")
    context = tuple(state[key] for key in keys)
    if (tuple(report.get(key) for key in keys) != context or not all(context)
            or hello["firmware"]["app_elf_sha256"] != APP_ELF_SHA
            or (hello["device_id"], hello["boot_id"]) != context[1:3]
            or state["catalog"]["status"] != "complete"
            or state["catalog"]["manifest"]["catalog_id"] != report["catalog_id"]):
        raise ValueError("device or session changed; reread directory")
    return context


def selected_receipts(state, before, context, command_id):
    rows = [row for row in state["receipts"] if row["request_id"] not in before]
    if rows and ((state.get("last_command") or {}).get("command_id") != command_id
                 or any(row.get("source") != "device" or row.get("correlated") is not True
                        or tuple(row.get(key) for key in ("target_id", "boot_id", "session_id")) != context[1:]
                        for row in rows)
                 or len({(row["request_id"], row["request_sequence"]) for row in rows}) != 1):
        raise ValueError("ambiguous or unrelated playback receipt")
    return rows


def dispatch_verified(rows):
    # A final device dispatch is authoritative even if its preceding queue
    # acknowledgement was not observed. Preserve that distinction in evidence.
    allowed = {"accepted": "queued_for_owner", "dispatched": "submitted_to_display"}
    if (any(row.get("status") not in allowed or row.get("reason") != allowed[row["status"]] for row in rows)
            or any(sum(row["status"] == status for row in rows) > 1 for status in allowed)):
        raise ValueError("device declined playback or receipt has conflicting status")
    return any(row["status"] == "dispatched" for row in rows)


def plays():
    initial = api("/api/studio/state")
    hello = api("/api/body/state")["identity"]["hello"]
    report = json.loads((PUBLIC / "live-result.json").read_bytes())
    context = checked_context(report, initial, hello)
    saved = WORK / "live-plays.json"
    results = json.loads(saved.read_bytes()) if saved.exists() else []
    operations = ("eat", "neutral", "eat")
    if len(results) > len(operations):
        raise ValueError("saved playback progress changed")
    for index, row in enumerate(results):
        check = initial | dict(receipts=row["receipts"], last_command=dict(command_id=row["command_id"]))
        if (row["operation"] != operations[index]
                or not dispatch_verified(selected_receipts(check, set(), context, row["command_id"]))):
            raise ValueError("saved playback context changed")
    for operation in operations[len(results):]:
        deadline = time.monotonic() + 16
        while time.monotonic() < deadline:
            started = time.monotonic()
            state = api("/api/studio/state")
            if tuple(state[key] for key in ("connection_id", "target_id", "boot_id", "session_id")) != context:
                raise ValueError("session changed during explicit playback")
            if (state["can_submit"] and state["age_ms"] is not None
                    and state["age_ms"] + (time.monotonic() - started) * 1000 < 350):
                break
            time.sleep(0.15)
        else:
            save("play-not-ready.json", state)
            raise ValueError("playback waiting: " + state["reason"])
        before = {row["request_id"] for row in state["receipts"]}
        command_id = "e3f4d-" + uuid.uuid4().hex
        submitted = command(operation, state, hello, command_id=command_id)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = api("/api/studio/state")
            if tuple(state[key] for key in ("connection_id", "target_id", "boot_id", "session_id")) != context:
                raise ValueError("session changed while waiting for receipt")
            receipts = selected_receipts(state, before, context, command_id)
            if dispatch_verified(receipts):
                break
            if any(row["status"] in ("rejected", "expired", "cancelled") for row in receipts):
                raise ValueError("device declined explicit playback")
            time.sleep(0.15)
        else:
            save("play-pending.json", state)
            raise TimeoutError("playback receipt pending; request sent once")
        results.append(dict(operation=operation, command_id=command_id, submitted=submitted, receipts=receipts,
                            accepted_observed=any(row["status"] == "accepted" for row in receipts)))
        save("live-plays.json", results)
        print(json.dumps(dict(operation=operation, result="device_dispatched")), flush=True)
        time.sleep(4)
    report.update(result="app_directory_and_three_dispatches_verified",
                  explicit_play_sequence=[dict(operation=row["operation"], command_id=row["command_id"],
                    accepted_observed=row["accepted_observed"], receipts=row["receipts"]) for row in results])
    save("live-result.json", report, public=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "plays"))
    args = parser.parse_args()
    (prepare if args.phase == "prepare" else plays)()
