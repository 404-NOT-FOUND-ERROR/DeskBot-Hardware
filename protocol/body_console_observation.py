"""Bounded, passive observation of existing Application/SystemInfo logs."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import re
import time
import uuid

SCHEMA = "deskbot.body_observation/v1"
FRESH_MS = 30_000
MAX_LINE = 2048
MAX_EVENTS = 24
STATES = ("unknown", "starting", "configuring", "idle", "connecting", "listening",
          "speaking", "upgrading", "activating", "audio_testing", "fatal_error", "invalid_state")
LINK_STATES = ("disabled", "connecting", "connected", "missing", "mismatch", "busy", "error")
ANSI = re.compile(rb"\x1b\[[0-9;]{0,40}m")
LOG = re.compile(r"^I \(([0-9]{1,15})\) (Application|SystemInfo): (.*)$")
HEAP = re.compile(r"free sram: ([0-9]{1,10}) minimal sram: ([0-9]{1,10})$")


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Observation:
    def __init__(self, *, clock=time.monotonic, wall=stamp):
        self.clock, self.wall = clock, wall
        self.status, self.port, self.reason = "disabled", None, "not_connected"
        self.opened = False
        self.events = deque(maxlen=MAX_EVENTS)
        self.sequence = 0
        self.dropped_lines = 0
        self.buffer = bytearray()
        self.discarding = False
        self._reset()

    def _reset(self):
        # A local observation segment, not an authenticated device boot ID.
        self.timeline = "observation-" + uuid.uuid4().hex
        self.uptime = None
        self.last_seen = self.last_seen_at = None
        self.state = self.state_at = self.state_time = None
        self.heap = self.heap_at = self.heap_time = None
        self.buffer.clear()
        self.discarding = False

    def _event(self, kind, value):
        self.sequence += 1
        self.events.append({"sequence": self.sequence, "kind": kind, "value": value,
                            "received_at": self.wall(), "timeline_id": self.timeline})

    def link(self, status, port=None, reason=""):
        if status not in LINK_STATES:
            raise ValueError("unsupported console connection state")
        changed = (status, port, reason) != (self.status, self.port, self.reason)
        if status == "connected" and (self.status != status or port != self.port):
            self._reset()
            self.opened = True
        elif status != "connected":
            self.buffer.clear()
            self.discarding = False
        self.status, self.port, self.reason = status, port, reason
        if changed:
            self._event("connection", status)

    def feed(self, raw: bytes):
        if self.status != "connected":
            return
        # Process bytes without ever keeping an unbounded or raw log history.
        for byte in raw:
            if byte == 10:
                if not self.discarding:
                    line = bytes(self.buffer)
                    self.buffer.clear()
                    self._line(line)
                self.buffer.clear()
                self.discarding = False
            elif not self.discarding:
                self.buffer.append(byte)
                if len(self.buffer) > MAX_LINE:
                    self.buffer.clear()
                    self.discarding = True
                    self.dropped_lines += 1

    def _line(self, raw):
        line = ANSI.sub(b"", raw).decode("utf-8", errors="replace").rstrip("\r")
        if line.startswith("ESP-ROM:esp32s3"):
            self._reset()
            self._event("observation_reset", "boot_banner")
            return
        match = LOG.fullmatch(line)
        if not match:
            return
        uptime, tag, message = int(match[1]), match[2], match[3]
        if uptime > 9_007_199_254_740_991:
            return
        state = message[7:] if tag == "Application" and message.startswith("STATE: ") else None
        heap_match = HEAP.fullmatch(message) if tag == "SystemInfo" else None
        if state not in STATES and heap_match is None:
            return
        heap = None
        if heap_match:
            heap = {"free_internal_bytes": int(heap_match[1]), "minimum_internal_bytes": int(heap_match[2])}
            if not 0 <= heap["minimum_internal_bytes"] <= heap["free_internal_bytes"] <= 0xFFFFFFFF:
                return
        if self.uptime is not None and uptime + 2000 < self.uptime:
            self._reset()
            self._event("observation_reset", "device_clock_reset")
        self.uptime = max(uptime, self.uptime or 0)
        self.last_seen, self.last_seen_at = self.clock(), self.wall()
        if state in STATES:
            if state != self.state:
                self._event("application_state", state)
            self.state, self.state_at, self.state_time = state, self.wall(), self.clock()
        if heap is not None:
            self.heap, self.heap_at, self.heap_time = heap, self.wall(), self.clock()

    def snapshot(self):
        def age(value):
            return None if value is None else max(0, int((self.clock() - value) * 1000))
        last_age = age(self.last_seen)
        freshness = ("disabled" if self.status == "disabled" else "disconnected" if self.status != "connected"
                     else "waiting" if last_age is None else "fresh" if last_age <= FRESH_MS else "stale")
        return {"schema": SCHEMA, "source": "usb_console", "read_only": True, "controls_available": False,
                "generated_at": self.wall(), "hardware_accessed": self.opened, "device_written": False,
                "connection": {"status": self.status, "port": self.port, "reason": self.reason},
                "freshness": freshness, "fresh_for_ms": FRESH_MS, "live": freshness == "fresh",
                "timeline_id": self.timeline, "device_log_time_ms": self.uptime,
                "last_seen_at": self.last_seen_at, "last_seen_age_ms": last_age,
                "application_state": None if self.state is None else {"value": self.state, "received_at": self.state_at, "age_ms": age(self.state_time)},
                "internal_memory": None if self.heap is None else {**self.heap, "received_at": self.heap_at, "age_ms": age(self.heap_time)},
                "events": deepcopy(list(self.events)), "dropped_lines": self.dropped_lines}


def aged_snapshot(value, elapsed_ms, *, worker_alive=True):
    """Age a worker snapshot using the host's monotonic receipt clock."""
    result = deepcopy(value)
    elapsed_ms = max(0, int(elapsed_ms))
    for key in ("last_seen_age_ms",):
        if result[key] is not None:
            result[key] += elapsed_ms
    for key in ("application_state", "internal_memory"):
        if result[key] is not None:
            result[key]["age_ms"] += elapsed_ms
    if not worker_alive:
        result["connection"]["status"] = "error"
        result["connection"]["reason"] = "reader_unavailable"
        result["freshness"] = "disconnected"
    elif result["freshness"] == "fresh" and result["last_seen_age_ms"] > result["fresh_for_ms"]:
        result["freshness"] = "stale"
    result["live"] = result["freshness"] == "fresh"
    result["generated_at"] = stamp()
    return result
