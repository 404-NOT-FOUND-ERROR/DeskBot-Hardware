import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import importlib.util
import sqlite3

from protocol.client_head_touch import TouchOutbox, observed_touches, milliseconds, DEVICE, FIRMWARE
from protocol.studio_identity_observation import IdentityObservation
from protocol.studio_identity_bridge import IdentityBridge

SYNC_SPEC = importlib.util.spec_from_file_location(
    "sync_head_touch", Path(__file__).parents[1] / "scripts/sync-head-touch.py")
SYNC = importlib.util.module_from_spec(SYNC_SPEC)
SYNC_SPEC.loader.exec_module(SYNC)

NOW = "2026-11-19T14:00:00.000+00:00"


def hello(boot="a", elf=FIRMWARE):
    return dict(schema="deskbot.device_link/v1", message_type="hello", device_id=DEVICE,
                boot_id="boot-" + boot*32, sequence=1, uptime_ms=10000,
                firmware=dict(edition="E3b", version="2.0.5", app_elf_sha256=elf),
                hardware=dict(chip="esp32s3", board="esp_vocat", flash_bytes=33554432, psram_bytes=16777216),
                capabilities=["device_identity"], transport="usb_console", controls_available=False,
                catalog_id=None, limits=dict(max_frame_bytes=2048, hello_interval_ms=10000))


class HeadTouchTests(unittest.TestCase):
    def setUp(self):
        self.clock = 10.
        self.reader = IdentityObservation(clock=lambda: self.clock, wall=lambda: NOW)
        self.reader.link("connected", "COM4", "single_console_owner")

    def connect(self, boot="a", elf=FIRMWARE):
        wire = f'I (10000) DeskBotLink: {json.dumps(hello(boot, elf))}\n'.encode()
        self.reader.feed(wire[:17])
        self.reader.feed(wire[17:])

    def touch(self, tick=11000):
        self.reader.feed(f"I ({tick}) Touch: Triple PRESS_DOWN detected!\r\n".encode())

    def test_fragmented_log_is_bound_to_current_device_boot_and_schema(self):
        self.connect()
        self.touch()
        self.touch()
        state = self.reader.snapshot()
        self.assertTrue(IdentityBridge().validate_snapshot(state))
        events = observed_touches(state, now_ms=milliseconds(NOW))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["uptime_ms"], 11000)
        self.assertEqual(events[0]["boot_id"], hello()["boot_id"])
        self.connect(boot="b")
        self.assertEqual(observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW)), [])
        self.touch()
        self.assertEqual(observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW))[0]["boot_id"],
                         hello("b")["boot_id"])

    def test_unverified_firmware_stale_hello_and_stale_observation_are_excluded(self):
        self.touch()
        self.assertFalse(observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW)))
        self.connect(elf="a"*64)
        self.touch()
        self.assertFalse(observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW)))
        self.reader = IdentityObservation(clock=lambda: self.clock, wall=lambda: NOW)
        self.reader.link("connected", "COM4", "single_console_owner")
        self.connect()
        self.clock += 31
        self.touch()
        self.assertFalse(observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW)))

    def test_retry_and_restart_keep_original_event_and_observation_time(self):
        self.connect()
        self.touch()
        value = observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW))[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "touch.sqlite"
            box = TouchOutbox(path)
            box.observe([value])
            box.tick(lambda body: (_ for _ in ()).throw(OSError()), milliseconds(NOW))
            self.assertEqual(box.status(), {"pending": 1})
            box.tick(lambda body: self.fail("retry backoff was ignored"), milliseconds(NOW)+999)
            self.assertEqual(box.status(), {"pending": 1})
            box.close()
            box = TouchOutbox(path)
            try:
                box.observe([{**value, "observed_at": "2026-11-19T14:00:01+00:00"}])
                def send(body):
                    self.assertEqual(body["observed_at"], NOW)
                    identity = [body[k] for k in ("device_id", "boot_id", "firmware_sha256", "uptime_ms")]
                    key = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
                    return dict(status="recorded", event_id="head-touch:"+key)
                box.tick(send, milliseconds(NOW)+2000)
                self.assertEqual(box.status(), {"recorded": 1})
                box.observe([value])
                box.tick(lambda body: self.fail("recorded event sent again"), milliseconds(NOW)+3000)
            finally:
                box.close()

    def test_expired_queued_touch_is_not_resent_as_new(self):
        self.connect()
        self.touch()
        value = observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW))[0]
        with tempfile.TemporaryDirectory() as directory:
            box = TouchOutbox(Path(directory) / "touch.sqlite")
            try:
                box.observe([value])
                box.tick(lambda body: self.fail("expired input sent"), milliseconds(NOW)+300001)
                self.assertEqual(box.status(), {"expired": 1})
            finally:
                box.close()

    def test_unexpected_receipt_keeps_touch_for_retry(self):
        self.connect()
        self.touch()
        value = observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW))[0]
        self.assertEqual(observed_touches([], now_ms=milliseconds(NOW)), [])
        with tempfile.TemporaryDirectory() as directory:
            box = TouchOutbox(Path(directory) / "touch.sqlite")
            try:
                box.observe([value])
                box.tick(lambda body: [], milliseconds(NOW))
                self.assertEqual(box.status(), {"pending": 1})
                self.assertEqual(box.last_error, "ValueError")
            finally:
                box.close()

    def test_reconnect_cycle_holds_queue_until_both_services_return(self):
        self.connect()
        self.touch()
        value = observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW))[0]
        with tempfile.TemporaryDirectory() as directory:
            box = TouchOutbox(Path(directory) / "touch.sqlite")
            try:
                box.observe([value])
                disconnected = SYNC.sync_cycle(
                    box, studio_state=lambda: {"connection": {"status": "disconnected"}},
                    client_health=lambda: (_ for _ in ()).throw(OSError()),
                    send=lambda body: self.fail("disconnected services must not send"),
                    now_ms=milliseconds(NOW),
                )
                self.assertEqual(disconnected["studio"], "disconnected")
                self.assertEqual(disconnected["client"], "offline")
                self.assertEqual(disconnected["counts"], {"pending": 1})

                calls = []
                connected = SYNC.sync_cycle(
                    box, studio_state=lambda: {"connection": {"status": "connected"},
                        "identity": {"status": "confirmed", "hello": hello()}, "events": []},
                    client_health=lambda: {"status": "ok"},
                    send=lambda body: calls.append(body) or {"status": "recorded", "event_id":
                        "head-touch:" + hashlib.sha256(json.dumps(
                            [body[k] for k in ("device_id", "boot_id", "firmware_sha256", "uptime_ms")],
                            separators=(",", ":")).encode()).hexdigest()},
                    now_ms=milliseconds(NOW),
                )
                self.assertEqual(connected["studio"], "connected")
                self.assertEqual(connected["device"], "confirmed")
                self.assertEqual(connected["client"], "online")
                self.assertEqual(connected["counts"], {"recorded": 1})
                self.assertEqual(len(calls), 1)
                box.close()
                box = TouchOutbox(Path(directory) / "touch.sqlite")
                self.assertEqual(box.status(), {"recorded": 1})
                self.assertEqual(SYNC.sync_cycle(
                    box, studio_state=lambda: {"connection": {"status": "connected"},
                        "identity": {"status": "confirmed", "hello": hello()}, "events": []},
                    client_health=lambda: {"status": "ok"},
                    send=lambda body: self.fail("recorded input resent after restart"),
                    now_ms=milliseconds(NOW)),
                    {"studio": "connected", "device": "confirmed", "client": "online", "observed": 0,
                     "last_observed_at": None, "counts": {"recorded": 1}, "error": None})
            finally:
                box.close()

    def test_failed_client_delivery_retries_after_backoff_and_expires(self):
        self.connect()
        self.touch()
        value = observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW))[0]
        with tempfile.TemporaryDirectory() as directory:
            box = TouchOutbox(Path(directory) / "touch.sqlite")
            try:
                box.observe([value])
                offline = SYNC.sync_cycle(box,
                    studio_state=lambda: {"connection": {"status": "connected"},
                        "identity": {"status": "confirmed", "hello": hello()}, "events": []},
                    client_health=lambda: (_ for _ in ()).throw(OSError()),
                    send=lambda body: self.fail("offline client must not receive"), now_ms=milliseconds(NOW))
                self.assertEqual(offline["client"], "offline")
                self.assertEqual(offline["counts"], {"pending": 1})
                box.tick(lambda body: (_ for _ in ()).throw(OSError()), milliseconds(NOW))
                self.assertEqual(box.status(), {"pending": 1})
                box.tick(lambda body: self.fail("retried before backoff"), milliseconds(NOW)+999)
                box.tick(lambda body: (_ for _ in ()).throw(OSError()), milliseconds(NOW)+1000)
                self.assertEqual(box.status(), {"pending": 1})
                box.tick(lambda body: self.fail("expired input was resent"), milliseconds(NOW)+300001)
                self.assertEqual(box.status(), {"expired": 1})
            finally:
                box.close()

    def test_later_touch_waits_for_earlier_retry(self):
        self.connect()
        self.touch()
        first = observed_touches(self.reader.snapshot(), now_ms=milliseconds(NOW))[0]
        second = {**first, "uptime_ms": first["uptime_ms"]+3000}
        with tempfile.TemporaryDirectory() as directory:
            box = TouchOutbox(Path(directory) / "touch.sqlite")
            try:
                box.observe([first])
                box.tick(lambda body: (_ for _ in ()).throw(OSError()), milliseconds(NOW))
                box.observe([second])
                box.tick(lambda body: self.fail("later input passed a waiting earlier input"), milliseconds(NOW)+999)
                sent = []
                def send(body):
                    sent.append(body["uptime_ms"])
                    identity = [body[k] for k in ("device_id", "boot_id", "firmware_sha256", "uptime_ms")]
                    return {"status": "recorded", "event_id": "head-touch:" + hashlib.sha256(
                        json.dumps(identity, separators=(",", ":")).encode()).hexdigest()}
                box.tick(send, milliseconds(NOW)+1000)
                self.assertEqual(sent, [first["uptime_ms"], second["uptime_ms"]])
                self.assertEqual(box.status(), {"recorded": 2})
            finally:
                box.close()

    def test_legacy_queue_migrates_and_only_one_owner_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "touch.sqlite"
            legacy = sqlite3.connect(path)
            try:
                legacy.execute("""CREATE TABLE touches(id TEXT PRIMARY KEY,body TEXT NOT NULL,
                    status TEXT NOT NULL,receipt TEXT,attempts INTEGER NOT NULL DEFAULT 0)""")
                legacy.commit()
            finally:
                legacy.close()
            box = TouchOutbox(path)
            try:
                with self.assertRaisesRegex(OSError, "head_touch_sync_already_running"):
                    TouchOutbox(path)
                self.assertIn("retry_after_ms", {r[1] for r in box.db.execute("PRAGMA table_info(touches)")})
            finally:
                box.close()
            reopened = TouchOutbox(path)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
