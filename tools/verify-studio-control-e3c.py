#!/usr/bin/env python3
"""Verify one neutral request through the running Studio's sole USB owner."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
import uuid
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol import studio_e3c_deployment as reviewed
from protocol.device_link import validate_hello
from protocol.studio_control_bridge import validate_control

spec = importlib.util.spec_from_file_location("e3c_live_deploy", ROOT / "scripts/deploy-deskbot-studio-e3c.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
ORIGIN = "http://127.0.0.1:8766"
OPENER = build_opener(ProxyHandler({}))


def api(path, value=None, *, control=False):
    request = Request(ORIGIN + path, data=None if value is None else json.dumps(value).encode(),
        headers={"Origin": ORIGIN, "Content-Type": "application/json",
                 "X-DeskBot-Studio": "e3c6" if control else "e3a"})
    with OPENER.open(request, timeout=5) as response:
        return json.loads(response.read(65536))


def wait(test, label, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = test()
        if value is not None:
            return value
        time.sleep(0.25)
    raise TimeoutError(label)


def connect():
    rows = [p for p in api("/api/body/ports")["ports"] if p["port"] == "COM4" and p["usb_id"] == "303A:1001"]
    if len(rows) != 1:
        raise ValueError("fresh COM4 USB enumeration required")
    return api("/api/body/connect", {k: rows[0][k] for k in ("port", "key")})


def identity(expected, after=-1):
    row = api("/api/body/state")
    if row["identity"]["status"] != "confirmed" or row["identity"]["age_ms"] > 30000:
        return None
    hello = validate_hello(row["identity"]["hello"])
    if any(hello[k] != expected[k] for k in ("device_id", "boot_id", "firmware", "hardware")):
        raise ValueError("live device identity differs from verified boot")
    return row if hello["sequence"] > after else None


def checked_state(row):
    # HTTP adds host IPC progress to the worker's validated control payload.
    if not isinstance(row, dict) or not validate_control({k: v for k, v in row.items() if k != "host_command"}):
        raise ValueError("invalid live Studio control state")
    host = row.get("host_command")
    if host is not None and (not isinstance(host, dict)
            or set(host) != {"command_id", "operation", "status"}
            or not isinstance(host["command_id"], str)
            or host["operation"] not in ("enable", "disable", "neutral")
            or host["status"] not in ("queued", "timeout")):
        raise ValueError("invalid host command progress")
    return row


def state():
    return checked_state(api("/api/studio/state"))


def command(operation, control, hello):
    return {"operation": operation, "command_id": "live-" + uuid.uuid4().hex,
            "connection_id": control["connection_id"], "target_id": hello["device_id"],
            "boot_id": hello["boot_id"], "session_id": control["session_id"]}


def run(folder, *, evidence_name='control-evidence.json', query_only=False):
    plan = reviewed.check_plan(tx.load(folder))
    journal = tx.Journal(folder, plan).load()
    if journal is None or journal["phase"] != "installed":
        raise ValueError("verified E3c installation required")
    expected = json.loads((folder / "restart-result.json").read_bytes())["boot"]["hello"]
    evidence = {"result": "in_progress", "expression_requests": 0,
                "physical_acceptance": "pending", "screen_playback_confirmed": False}
    if evidence_name != 'control-evidence.json' and not re.fullmatch(r'control-evidence-[a-z0-9-]{1,32}\.json', evidence_name):
        raise ValueError('evidence filename must stay within the deployment folder')
    result_path = folder / evidence_name
    # Reserve the evidence path before any connection or command.
    with result_path.open("xb") as stream:
        try:
            api("/api/body/disconnect", {})
            connect()
            first = wait(lambda: identity(expected), "identity after connect")
            hello = first["identity"]["hello"]
            passive = state()
            evidence.update(first=first, passive=passive)
            if passive["enabled"] or passive["frames_sent"] != 0:
                raise ValueError("passive connection sent a query")
            enable = command("enable", passive, hello)
            api("/api/studio/command", enable, control=True)
            queried = wait(lambda: (row if (row := state())["display"] else None), "USB display state reply")
            evidence["queried"] = queried
            if query_only:
                evidence.update(result='display_state_verified', plan_id=plan.id,
                    current_display_reason=queried['reason'])
                return evidence
            ready = wait(lambda: (row if (row := state())["can_submit"] else None), "idle home/player readiness")
            evidence["ready"] = ready
            neutral = command("neutral", ready, hello)
            api("/api/studio/command", neutral, control=True)
            evidence["expression_requests"] = 1

            def completed():
                row = state()
                receipts = row["receipts"]
                if row["local_result"]:
                    raise ValueError("local result: " + row["local_result"]["reason"])
                if any(r["status"] in ("rejected", "expired", "interrupted", "busy") for r in receipts):
                    raise ValueError("expression rejected or interrupted")
                return row if [r["status"] for r in receipts] == ["accepted", "dispatched"] else None

            terminal = wait(completed, "accepted and dispatched receipts", 8)
            evidence["terminal"] = terminal
            receipts = terminal["receipts"]
            if (receipts[0]["request_id"] != receipts[1]["request_id"]
                    or any(r["source"] != "device" for r in receipts)):
                raise ValueError("receipt correlation differs")
            # Repeating the same host command must not create a new expression.
            api("/api/studio/command", neutral, control=True)
            time.sleep(1.1)
            after_duplicate = state()
            if after_duplicate["receipts"] != receipts or after_duplicate["pending"] is not None:
                raise ValueError("repeated host command created another expression")
            disconnected = api("/api/body/disconnect", {})
            off = state()
            if (disconnected["identity"]["hello"] is not None or off["session_id"] is not None
                    or off["enabled"] or off["can_submit"]):
                raise ValueError("disconnect retained control state")
            connect()
            later = wait(lambda: identity(expected, hello["sequence"]), "identity after passive reconnect")
            reconnected = state()
            if reconnected["enabled"] or reconnected["frames_sent"] != 0:
                raise ValueError("reconnect revived the old session")
            if reconnected["connection_id"] == passive["connection_id"]:
                raise ValueError("connection id did not change")
            api("/api/studio/command", command("enable", reconnected, later["identity"]["hello"]), control=True)
            ready_again = wait(lambda: (row if (row := state())["display"] else None), "query after reconnect")
            if ready_again["session_id"] == ready["session_id"]:
                raise ValueError("reconnect reused the old session")
            evidence.update(result="neutral_receipts_and_reconnect_passed", plan_id=plan.id,
                first=first, passive=passive, ready=ready, host_command=neutral, terminal=terminal,
                after_duplicate=after_duplicate, disconnected=disconnected, disabled=off,
                reconnected=later, passive_after_reconnect=reconnected, ready_after_reconnect=ready_again,
                request_correlated=True, repeated_host_command_did_not_reexecute=True,
                old_session_cleared=True, fresh_session_after_reconnect=True)
            return evidence
        except BaseException as error:
            evidence.update(result="failed", error_type=type(error).__name__, error=str(error))
            raise
        finally:
            stream.write(tx.canonical(evidence))
            stream.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument('--profile', choices=('e3c7', 'e3c8'), default='e3c7')
    parser.add_argument('--evidence-name', default='control-evidence.json')
    parser.add_argument('--query-only', action='store_true')
    args = parser.parse_args()
    if args.profile == 'e3c8':
        from protocol import studio_e3c8_deployment as reviewed
    result = run(deploy.folder_for(args.run), evidence_name=args.evidence_name, query_only=args.query_only)
    print('E3c display state verified; no expression requested.' if args.query_only else
          'E3c: one neutral request accepted/dispatched; duplicate host command, reconnect and new session verified.')
