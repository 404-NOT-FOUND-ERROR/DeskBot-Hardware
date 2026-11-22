"""Forward observed head gestures through the original USB owner."""
import contextlib
from datetime import datetime
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.request import Request, urlopen

DEVICE = "device-9046b3127b3c912b392774b8ffadd819"
FIRMWARE = "234334de23ae70abc98b5838666ddb13df72c3569f2eaa4ba01018e0b0004e97"
MAX_PENDING_AGE_MS = 300_000


def milliseconds(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def observed_touches(state, *, now_ms):
    if not isinstance(state, dict):
        return []
    identity = state.get("identity", {})
    if not isinstance(identity, dict) or not isinstance(state.get("connection"), dict):
        return []
    hello = identity.get("hello") or {}
    if (not isinstance(hello, dict) or not isinstance(hello.get("firmware"), dict)
            or not isinstance(hello.get("boot_id"), str)
            or type(hello.get("uptime_ms")) is not int
            or not isinstance(state.get("events"), list)):
        return []
    if (state.get("connection", {}).get("status") != "connected"
            or identity.get("status") != "confirmed" or hello.get("device_id") != DEVICE
            or hello.get("firmware", {}).get("app_elf_sha256") != FIRMWARE
            or not re.fullmatch(r"boot-[a-f0-9]{32}", hello.get("boot_id", ""))):
        return []
    result = []
    for event in state.get("events", []):
        if (not isinstance(event, dict) or event.get("kind") != "head_touch"
                or not state.get("timeline_id") or event.get("timeline_id") != state["timeline_id"]
                or not isinstance(event.get("value"), str)):
            continue
        match = re.fullmatch(r"triple:([0-9]{1,12})", event.get("value", ""))
        if not match:
            continue
        tick = int(match[1])
        try:
            age = now_ms - milliseconds(event["received_at"])
        except (ValueError, TypeError, KeyError):
            continue
        if not -1000 <= age <= 300_000 or not hello["uptime_ms"] - 300_000 <= tick <= hello["uptime_ms"] + 30_000:
            continue
        result.append(dict(device_id=DEVICE, boot_id=hello["boot_id"], firmware_sha256=FIRMWARE,
                           uptime_ms=tick, observed_at=event["received_at"]))
    return result


class TouchOutbox:
    def __init__(self, path):
        self.last_error = None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = (path.with_suffix(path.suffix + ".lock")).open("a+b")
        self.lock.seek(0, os.SEEK_END)
        if not self.lock.tell():
            self.lock.write(b"0")
            self.lock.flush()
        self.lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise OSError("head_touch_sync_already_running") from None
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS touches (
            id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL,
            receipt TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            retry_after_ms INTEGER NOT NULL DEFAULT 0)""")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(touches)")}
        if "retry_after_ms" not in columns:
            self.db.execute("ALTER TABLE touches ADD COLUMN retry_after_ms INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

    def maintain(self, now_ms):
        pending = self.db.execute("SELECT id,body FROM touches WHERE status='pending'").fetchall()
        expired = [key for key, raw in pending
                   if now_ms - milliseconds(json.loads(raw)["observed_at"]) > MAX_PENDING_AGE_MS]
        self.db.executemany("UPDATE touches SET status='expired' WHERE id=?", ((key,) for key in expired))
        self.db.commit()
        return len(expired)

    def observe(self, events):
        inserted = 0
        for body in events:
            # Identity excludes observation time: reconnect keeps the first receipt.
            identity = [body[k] for k in ("device_id", "boot_id", "firmware_sha256", "uptime_ms")]
            key = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            result = self.db.execute("INSERT OR IGNORE INTO touches(id,body,status) VALUES(?,?,'pending')",
                                     (key, json.dumps(body, separators=(",", ":"))))
            inserted += result.rowcount
        self.db.commit()
        return inserted

    def tick(self, send, now_ms):
        self.last_error = None
        self.maintain(now_ms)
        rows = self.db.execute("""SELECT id,body,attempts,retry_after_ms FROM touches
            WHERE status='pending' ORDER BY rowid LIMIT 8""").fetchall()
        for key, raw, attempts, retry_after in rows:
            body = json.loads(raw)
            # Preserve order: a later touch must not pass a retrying earlier one.
            if send is None or now_ms < retry_after:
                break
            self.db.execute("UPDATE touches SET attempts=attempts+1 WHERE id=?", (key,))
            self.db.commit()
            try:
                receipt = send(body)
                expected = "head-touch:" + hashlib.sha256(json.dumps(
                    [body[k] for k in ("device_id", "boot_id", "firmware_sha256", "uptime_ms")],
                    separators=(",", ":")).encode()).hexdigest()
                if not isinstance(receipt, dict) or receipt.get("status") != "recorded" or receipt.get("event_id") != expected:
                    raise ValueError("head_touch_receipt_mismatch")
            except (OSError, ValueError) as failure:
                # Keep the same body and timestamp for an uncertain HTTP outcome.
                self.last_error = type(failure).__name__
                delay_ms = min(30, 1 << min(attempts, 5)) * 1000
                self.db.execute("UPDATE touches SET retry_after_ms=? WHERE id=?", (now_ms + delay_ms, key))
                self.db.commit()
                break
            self.db.execute("UPDATE touches SET status='recorded',receipt=? WHERE id=?",
                            (json.dumps(receipt), key))
            self.db.commit()

    def status(self):
        return dict(self.db.execute("SELECT status,count(*) FROM touches GROUP BY status"))

    def latest(self):
        row = self.db.execute("SELECT id,body FROM touches WHERE status='recorded' ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {"event_id": "head-touch:" + row[0], "observed_at": json.loads(row[1])["observed_at"]}

    def close(self):
        self.db.close()
        if not self.lock.closed:
            with contextlib.suppress(OSError):
                self.lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
            self.lock.close()


def json_request(url, body=None, *, token=None, timeout=8):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = Request(url, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("head_touch_response_limit")
            return json.loads(raw)
    except HTTPException:
        raise OSError("head_touch_http_interrupted") from None
