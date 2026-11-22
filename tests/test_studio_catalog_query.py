import json
from pathlib import Path
import unittest

from protocol import studio_control_link as control
from protocol import studio_installed_catalog as catalog
from protocol import studio_usb_session as session
from protocol.device_link import validate_hello
from protocol.studio_control_bridge import validate_control


ROOT = Path(__file__).resolve().parents[1]
DEVICE = "device-" + "a" * 32
BOOT = "boot-" + "b" * 32
SESSION = "session-" + "b" * 32 + "-1"


def device_hello():
    return validate_hello({"schema": "deskbot.device_link/v1", "message_type": "hello",
        "device_id": DEVICE, "boot_id": BOOT, "sequence": 1, "uptime_ms": 10000,
        "firmware": {"edition": "E3b", "version": "2.0.5", "app_elf_sha256": "c" * 64},
        "hardware": {"chip": "esp32s3", "board": "esp_vocat", "flash_bytes": 33554432, "psram_bytes": 16777216},
        "capabilities": ["device_identity"], "transport": "usb_console", "controls_available": False,
        "catalog_id": None, "limits": {"max_frame_bytes": 2048, "hello_interval_ms": 10000}})


def display_reply(request_id):
    value = {"schema": "deskbot.studio_display/v1", "message_type": "display", "request_id": request_id,
        "target_id": DEVICE, "boot_id": BOOT, "sequence": 2, "session_id": SESSION, "at_ms": 410,
        "lease_ms": 30000, "application": {"revision": 1, "phase": "settled", "value": "idle"},
        "page": {"revision": 1, "phase": "settled", "value": "home"}, "sleep": "unknown",
        "controls_available": False, "player": {"revision": 1, "state": "initialized"},
        "preview": {"available": False, "reason": "sleep_unknown"}}
    return value


def fixture_manifest():
    rows = [
        {"kind": "layout", "id": "eye_anim", "align": "GFX_ALIGN_CENTER", "x": 0, "y": 0, "mirror": "auto"},
        {"kind": "action", "id": "look_around", "wire_command": 2, "wire_value": 2, "completion": "fact_only"},
        {"kind": "expression", "id": "happy", "resource_id": "happy.eaf", "resource_sha256": "d" * 64,
         "bytes": 1200, "layout_id": "eye_anim", "fps": 24, "loop": True, "implicit_action_id": "look_around"},
        {"kind": "expression", "id": "sad", "resource_id": "sad.eaf", "resource_sha256": "e" * 64,
         "bytes": 900, "layout_id": "eye_anim", "fps": 20, "loop": False, "implicit_action_id": None},
    ]
    return catalog.build_manifest(rows, "a" * 64, "b" * 64)


class CatalogQueryTests(unittest.TestCase):
    def setUp(self):
        self.link = control.ControlLink(nonce=lambda: "client-catalog", wall=lambda: "2026-11-05T00:00:00.000Z")
        self.link.observe(device_hello(), 0)
        self._command("enable", now=0)
        request = session.validate_request(json.loads(self.link.tick(0)))
        self.link.accept({"schema": session.SCHEMA, "message_type": "session",
            "request_id": request["request_id"], "target_id": DEVICE, "boot_id": BOOT,
            "sequence": 1, "session_id": SESSION, "state": "ready", "reason": "session_opened",
            "at_ms": 100, "lease_ms": 30000, "controls_available": False}, 0)
        request = session.validate_request(json.loads(self.link.tick(400)))
        self.link.accept(display_reply(request["request_id"]), 410)

    def _command(self, operation, *, now, session_id=None):
        self.link.command({"operation": operation, "command_id": f"click-{operation}-{now}",
            "connection_id": self.link.connection_id, "target_id": DEVICE, "boot_id": BOOT,
            "session_id": session_id}, now)

    def _start_read(self):
        self._command("read_catalog", now=410, session_id=SESSION)
        self.assertEqual(self.link.catalog["status"], "reading")
        self.assertFalse(self.link.snapshot(410)["can_submit"])

    def _page(self, manifest, request, *, cursor, sequence, at_ms, **changes):
        page = catalog.make_page(manifest, request_id=request["request_id"], target_id=DEVICE,
            boot_id=BOOT, session_id=SESSION, sequence=sequence, at_ms=at_ms, cursor=cursor)
        return page | changes

    def test_same_session_pages_are_throttled_and_only_complete_digest_is_live(self):
        manifest = fixture_manifest()
        self._start_read()
        first_wire = self.link.tick(800)
        first = session.validate_request(json.loads(first_wire))
        self.assertEqual(first["operation"], "catalog.0")
        self.link.accept(self._page(manifest, first, cursor=0, sequence=3, at_ms=420), 810)
        self.assertEqual(self.link.catalog["received_entries"], 2)
        self.assertIsNone(self.link.catalog["manifest"])
        self.assertTrue(validate_control(self.link.snapshot(810)))

        second_wire = self.link.tick(1200)
        second = session.validate_request(json.loads(second_wire))
        self.assertEqual(second["operation"], "catalog.2")
        self.assertEqual(second["client_nonce"], first["client_nonce"])
        self.link.accept(self._page(manifest, second, cursor=2, sequence=4, at_ms=430), 1210)
        row = self.link.snapshot(1210)
        self.assertEqual(row["catalog"]["status"], "complete")
        self.assertEqual(row["catalog"]["manifest"], manifest)
        self.assertTrue(row["can_submit"])
        self.assertTrue(validate_control(row))

    def test_mixed_page_digest_invalidates_partial_directory(self):
        manifest = fixture_manifest()
        self._start_read()
        first = session.validate_request(json.loads(self.link.tick(800)))
        self.link.accept(self._page(manifest, first, cursor=0, sequence=3, at_ms=420), 810)
        second = session.validate_request(json.loads(self.link.tick(1200)))
        altered = self._page(manifest, second, cursor=2, sequence=4, at_ms=430,
                             catalog_id="f" * 64)
        self.link.accept(altered, 1210)
        row = self.link.snapshot(1210)
        self.assertEqual(row["catalog"]["status"], "failed")
        self.assertEqual(row["catalog"]["reason"], "invalid_page")
        self.assertIsNone(row["catalog"]["manifest"])
        self.assertEqual(row["catalog"]["received_entries"], 0)
        self.assertTrue(validate_control(row))

    def test_page_timeout_clears_partial_snapshot(self):
        self._start_read()
        self.assertIsNotNone(self.link.tick(800))
        self.link.tick(8800)
        row = self.link.snapshot(8800)
        self.assertEqual(row["catalog"]["status"], "failed")
        self.assertEqual(row["catalog"]["reason"], "page_timeout")
        self.assertIsNone(row["catalog"]["manifest"])
        self.assertTrue(validate_control(row))

    def test_disconnect_clears_verified_directory(self):
        manifest = fixture_manifest()
        self._start_read()
        first = session.validate_request(json.loads(self.link.tick(800)))
        self.link.accept(self._page(manifest, first, cursor=0, sequence=3, at_ms=420), 810)
        second = session.validate_request(json.loads(self.link.tick(1200)))
        self.link.accept(self._page(manifest, second, cursor=2, sequence=4, at_ms=430), 1210)
        self.link.disconnect(1300)
        row = self.link.snapshot(1300)
        self.assertEqual(row["catalog"]["status"], "disconnected")
        self.assertIsNone(row["catalog"]["manifest"])
        self.assertTrue(validate_control(row))

    def test_replayed_catalog_page_does_not_create_live_state(self):
        manifest = fixture_manifest()
        self._start_read()
        request = session.validate_request(json.loads(self.link.tick(800)))
        page = self._page(manifest, request, cursor=0, sequence=3, at_ms=420)
        self.link.accept(page, 810)
        self.link.accept(page, 820)
        self.assertEqual(self.link.rejected, 1)
        self.assertEqual(self.link.catalog["received_entries"], 2)
        self.assertIsNone(self.link.catalog["manifest"])

    def test_session_expiry_clears_directory_even_when_only_snapshot_is_read(self):
        manifest = fixture_manifest()
        self._start_read()
        first = session.validate_request(json.loads(self.link.tick(800)))
        self.link.accept(self._page(manifest, first, cursor=0, sequence=3, at_ms=420), 810)
        second = session.validate_request(json.loads(self.link.tick(1200)))
        self.link.accept(self._page(manifest, second, cursor=2, sequence=4, at_ms=430), 1210)
        row = self.link.snapshot(31200)
        self.assertIsNone(row["session_id"])
        self.assertEqual(row["catalog"]["status"], "disconnected")
        self.assertIsNone(row["catalog"]["manifest"])
        self.assertTrue(validate_control(row))

    def test_reread_starts_at_zero_and_old_manifest_is_cleared(self):
        manifest = fixture_manifest()
        self._start_read()
        first = session.validate_request(json.loads(self.link.tick(800)))
        self.link.accept(self._page(manifest, first, cursor=0, sequence=3, at_ms=420), 810)
        second = session.validate_request(json.loads(self.link.tick(1200)))
        self.assertEqual(second["operation"], "catalog.2")
        self.link.tick(9200)
        self.assertEqual(self.link.catalog["reason"], "page_timeout")
        self._command("read_catalog", now=9210, session_id=SESSION)
        self.assertIsNone(self.link.catalog["manifest"])
        self.assertEqual(self.link.catalog["next_cursor"], 0)

    def test_completed_reread_clears_manifest_and_starts_new_page_request(self):
        manifest = fixture_manifest()
        self._start_read()
        first = session.validate_request(json.loads(self.link.tick(800)))
        self.link.accept(self._page(manifest, first, cursor=0, sequence=3, at_ms=420), 810)
        second = session.validate_request(json.loads(self.link.tick(1200)))
        self.link.accept(self._page(manifest, second, cursor=2, sequence=4, at_ms=430), 1210)
        self.assertEqual(self.link.catalog["manifest"], manifest)
        self._command("read_catalog", now=1220, session_id=SESSION)
        self.assertIsNone(self.link.catalog["manifest"])
        self.assertEqual(self.link.catalog["received_entries"], 0)
        self.assertEqual(self.link.catalog["next_cursor"], 0)
        restarted = session.validate_request(json.loads(self.link.tick(1600)))
        self.assertEqual(restarted["operation"], "catalog.0")
        self.assertNotEqual(restarted["request_id"], first["request_id"])

    def test_display_queries_resume_after_directory_pages(self):
        manifest = fixture_manifest()
        self._start_read()
        first = session.validate_request(json.loads(self.link.tick(800)))
        self.link.accept(self._page(manifest, first, cursor=0, sequence=3, at_ms=420), 810)
        second = session.validate_request(json.loads(self.link.tick(1200)))
        self.link.accept(self._page(manifest, second, cursor=2, sequence=4, at_ms=430), 1210)
        query = session.validate_request(json.loads(self.link.tick(1600)))
        self.assertEqual(query["operation"], "display")
        self.assertIsNone(self.link.tick(1700))


if __name__ == "__main__":
    unittest.main()
