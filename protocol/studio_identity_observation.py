"""E3b identity projection on the proven bounded E3a console line reader."""
from __future__ import annotations

from copy import deepcopy
from collections import deque
import re

from protocol.body_console_observation import Observation, ANSI, aged_snapshot
from protocol.device_link import decode_hello, FRESH_MS

SCHEMA = "deskbot.body_observation/v2"
HELLO = re.compile(rb"^I \(([0-9]{1,15})\) DeskBotLink: (\{.*\})\r?$")


class IdentityObservation(Observation):
    def __init__(self, **kwargs):
        self.pinned_device = self.last_boot = None
        self.last_sequence = self.last_uptime = -1
        self.retired_boots = deque(maxlen=8)
        self.firmware_identity = None
        super().__init__(**kwargs)

    def _reset(self):
        super()._reset()
        self.hello = self.hello_at = self.hello_time = None
        self.identity_reason = "waiting_for_hello"
        self.rejected_hello = 0

    def _line(self, raw):
        match = HELLO.fullmatch(ANSI.sub(b"", raw))
        if match:
            try:
                value = decode_hello(match[2])
            except ValueError:
                self.rejected_hello += 1
                self.identity_reason = "invalid_hello"
                return
            if self.pinned_device is not None and value["device_id"] != self.pinned_device:
                    self.hello = self.hello_at = self.hello_time = None
                    self.identity_reason = "device_changed"
                    self.rejected_hello += 1
                    return
            stable = {key: value[key] for key in ("firmware", "hardware", "capabilities", "transport", "limits")}
            if (value["boot_id"] in self.retired_boots or value["boot_id"] == self.last_boot
                    and (value["sequence"] <= self.last_sequence or value["uptime_ms"] < self.last_uptime
                         or stable != self.firmware_identity)):
                self.rejected_hello += 1
                self.identity_reason = "out_of_order_hello"
                return
            if self.last_boot is not None and value["boot_id"] != self.last_boot:
                self.retired_boots.append(self.last_boot)
                self._reset()
                self._event("observation_reset", "device_boot_changed")
            self.pinned_device, self.last_boot = value["device_id"], value["boot_id"]
            self.last_sequence, self.last_uptime = value["sequence"], value["uptime_ms"]
            self.firmware_identity = stable
            self.hello = value
            self.hello_time = self.last_seen = self.clock()
            self.hello_at = self.last_seen_at = self.wall()
            self.identity_reason = "device_announced"
            return
        # Neither malformed protocol lines nor ordinary logs become identity.
        super()._line(raw)

    def snapshot(self):
        value = super().snapshot()
        value["schema"] = SCHEMA
        age = None if self.hello_time is None else max(0, int((self.clock() - self.hello_time) * 1000))
        value["identity"] = {"status": "confirmed" if self.hello and age <= FRESH_MS else "stale" if self.hello else "waiting",
                             "source": "device_announcement", "received_at": self.hello_at,
                             "age_ms": age, "reason": self.identity_reason, "hello": deepcopy(self.hello),
                             "rejected_messages": self.rejected_hello}
        return age_identity(value)


def age_identity(value):
    result = deepcopy(value)
    identity = result["identity"]
    if result["connection"]["status"] != "connected":
        identity["status"] = "disconnected"
        identity["hello"] = None
    elif identity["hello"] is not None and identity["age_ms"] > FRESH_MS:
        identity["status"] = "stale"
    return result


def aged_identity_snapshot(value, elapsed_ms, *, worker_alive=True):
    result = aged_snapshot(value, elapsed_ms, worker_alive=worker_alive)
    if result["identity"]["age_ms"] is not None:
        result["identity"]["age_ms"] += max(0, int(elapsed_ms))
    return age_identity(result)
