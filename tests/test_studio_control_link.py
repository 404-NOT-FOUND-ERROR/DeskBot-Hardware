from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
import unittest

import jsonschema
from referencing import Registry, Resource

from protocol import expression_control, studio_control_link as control, studio_display_query, studio_usb_session
from protocol.device_link import validate_hello
from protocol.studio_control_bridge import validate_control

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("studio_e3c_worker_test", ROOT / "tools/observe-studio-body-e3c.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


DEVICE = "device-" + "a" * 32
BOOT = "boot-" + "b" * 32
SESSION = "session-" + "b" * 32 + "-1"


def hello(boot="b"):
    return validate_hello({"schema": "deskbot.device_link/v1", "message_type": "hello",
        "device_id": DEVICE, "boot_id": "boot-" + boot * 32, "sequence": 1, "uptime_ms": 10000,
        "firmware": {"edition": "E3b", "version": "2.0.5", "app_elf_sha256": "c" * 64},
        "hardware": {"chip": "esp32s3", "board": "esp_vocat", "flash_bytes": 33554432, "psram_bytes": 16777216},
        "capabilities": ["device_identity"], "transport": "usb_console", "controls_available": False,
        "catalog_id": None, "limits": {"max_frame_bytes": 2048, "hello_interval_ms": 10000}})


def display(request_id, *, app="idle", page="home", player="initialized"):
    return {"schema": "deskbot.studio_display/v1", "message_type": "display", "request_id": request_id,
        "target_id": DEVICE, "boot_id": BOOT, "sequence": 2, "session_id": SESSION, "at_ms": 410,
        "lease_ms": 30000, "application": {"revision": 1, "phase": "settled", "value": app},
        "page": {"revision": 1, "phase": "settled", "value": page}, "sleep": "unknown",
        "controls_available": False, "player": {"revision": 1, "state": player},
        "preview": {"available": False, "reason": "sleep_unknown" if app == "idle" and page == "home" and player == "initialized" else studio_display_query.reason({
            "application": {"phase": "settled", "value": app}, "page": {"phase": "settled", "value": page},
            "player": {"state": player}})}}


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.link = control.ControlLink(nonce=lambda: "client-test", wall=lambda: "2026-11-05T00:00:00.000Z")
        self.link.observe(hello(), 0)

    def command(self, operation, *, session_id=None, now=None, command_id=None):
        if now is None:
            now = 410 if operation == "neutral" else 0
        self.link.command({"operation": operation, "command_id": "click-" + operation + "-1",
            "connection_id": self.link.connection_id, "target_id": DEVICE, "boot_id": BOOT,
            "session_id": session_id} | ({"command_id": command_id} if command_id else {}), now)

    def open(self, now=0):
        self.command("enable")
        raw = self.link.tick(now)
        request = studio_usb_session.validate_request(json.loads(raw))
        self.link.accept({"schema": studio_usb_session.SCHEMA, "message_type": "session",
            "request_id": request["request_id"], "target_id": DEVICE, "boot_id": BOOT,
            "sequence": 1, "session_id": SESSION, "state": "ready", "reason": "session_opened",
            "at_ms": now, "lease_ms": 30000, "controls_available": False}, now)
        return request

    def ready(self):
        self.open()
        raw = self.link.tick(400)
        request = studio_usb_session.validate_request(json.loads(raw))
        self.link.accept(display(request["request_id"]), 410)
        self.assertTrue(self.link.snapshot(410)["can_submit"])

    def test_idle_page_player_ready_then_one_neutral_request(self):
        self.ready()
        self.command("neutral", session_id=SESSION)
        raw = self.link.tick(800)
        request = expression_control.validate_request(json.loads(raw), {"catalog_id": control.CATALOG, "role_id": control.ROLE,
            "expressions": [{"semantic_id": "neutral", "requestable": True}]})
        self.assertEqual(request["command"], {"kind": "play_expression", "semantic_id": "neutral"})
        self.assertEqual(request["expires_after_ms"], 2000)
        self.assertEqual(self.link.snapshot(800)["pending"]["phase"], "waiting")

    def test_actual_device_receipt_moves_accepted_then_dispatched(self):
        self.ready(); self.command("neutral", session_id=SESSION); self.link.tick(800)
        request = self.link.pending["request"]
        for seq, status, reason, now in ((1, "accepted", "queued_for_owner", 810),
                                         (2, "dispatched", "submitted_to_display", 900)):
            row = dict(schema=expression_control.SCHEMA, message_type="receipt", target_id=DEVICE, boot_id=BOOT,
                session_id=SESSION, sequence=seq, request_id=request["request_id"], request_sequence=request["sequence"],
                status=status, reason=reason, source="device", correlated=True, original_status=None, at_ms=now)
            self.link.accept(row, now)
            state = self.link.snapshot(now)
            if status == "accepted": self.assertEqual(state["pending"]["phase"], "accepted")
            else: self.assertIsNone(state["pending"])
            self.assertEqual(state["receipts"][-1], row)

    def test_only_home_idle_initialized_allows_submission(self):
        for request_id, value in (("r-1", display("r-1", app="speaking")),
                                  ("r-2", display("r-2", page="other")),
                                  ("r-3", display("r-3", player="unavailable"))):
            self.setUp(); self.open(); self.link.client._pending = None
            value["sequence"] = 2
            self.link.client._pending = {"operation": "display", "request_id": request_id,
                "sent": 400, "session_id": SESSION}
            # Use the controller's assigned query ID so correlation remains strict.
            value["request_id"] = self.link.client._pending["request_id"]
            value["preview"]["reason"] = studio_display_query.reason(value)
            self.link.accept(value, 410)
            self.assertFalse(self.link.snapshot(410)["can_submit"])

    def test_stale_state_disconnect_and_reboot_invalidate_commands(self):
        self.ready()
        self.assertFalse(self.link.snapshot(2410)["can_submit"])
        old = self.link.connection_id
        self.link.observe(hello("d"), 500)
        self.assertFalse(self.link.enabled)
        self.assertNotEqual(self.link.connection_id, old)
        self.assertEqual(self.link.reason, "identity_changed")

    def test_request_timeout_never_resends_expression(self):
        self.ready(); self.command("neutral", session_id=SESSION); first = self.link.tick(800)
        self.assertIsNotNone(first)
        later = [self.link.tick(now) for now in (1000, 1800, 2801, 3200)]
        self.assertEqual(sum(wire is not None and b'"kind":"play_expression"' in wire for wire in later), 0)
        self.assertEqual(self.link.snapshot(3200)["local_result"]["outcome"], "timeout")

    def test_older_firmware_session_timeout_and_explicit_reenable(self):
        self.command("enable"); self.link.tick(0)
        self.assertEqual(self.link.snapshot(2001)["status"], "querying")
        self.link.client.snapshot(2001)
        self.link.tick(2400)
        self.assertFalse(self.link.enabled)
        self.assertEqual(self.link.reason, "session_reply_timeout")
        old_request = self.link.frames_sent
        self.command("enable", now=2500, command_id="click-enable-2")
        self.assertTrue(self.link.enabled)
        self.assertIsNotNone(self.link.tick(2500))
        self.assertEqual(self.link.frames_sent, old_request + 1)
        self.assertIsNone(self.link.tick(2800))

    def test_lease_expiry_stops_without_reopening(self):
        self.ready()
        self.assertIsNone(self.link.tick(30400))
        self.assertFalse(self.link.enabled)
        self.assertEqual(self.link.reason, "lease_expired")
        self.assertIsNone(self.link.client)

    def test_late_terminal_receipt_resolves_local_timeout(self):
        self.ready(); self.command("neutral", session_id=SESSION, now=410); self.link.tick(800)
        request = self.link.pending["request"]
        self.link.tick(3800)
        self.assertEqual(self.link.snapshot(3800)["local_result"]["outcome"], "timeout")
        row = dict(schema=expression_control.SCHEMA, message_type="receipt", target_id=DEVICE, boot_id=BOOT,
            session_id=SESSION, sequence=1, request_id=request["request_id"], request_sequence=request["sequence"],
            status="dispatched", reason="submitted_to_display", source="device", correlated=True,
            original_status=None, at_ms=3900)
        self.link.accept(row, 3900)
        self.assertIsNone(self.link.snapshot(3900)["local_result"])

    def test_duplicate_commands_and_old_connection_are_ignored(self):
        connection = self.link.connection_id
        command = {"operation": "enable", "command_id": "click-enable-1", "connection_id": connection,
            "target_id": DEVICE, "boot_id": BOOT, "session_id": None}
        self.link.command(command, 0); self.assertTrue(self.link.enabled)
        self.link.command(command, 0)
        self.assertEqual(self.link.last_command["status"], "processed")
        self.link.disconnect(1)
        stale = command | {"command_id": "click-enable-2"}
        self.link.command(stale, 2)
        self.assertFalse(self.link.enabled)
        self.assertEqual(self.link.last_command["reason"], "connection_changed")

    def test_control_state_is_bounded_and_json_schema_valid(self):
        self.ready()
        schema = json.loads((__import__('pathlib').Path(__file__).resolve().parents[1] /
                             "protocol/deskbot-studio-control-link-v1.schema.json").read_text(encoding="utf-8"))
        resources = {}
        for name in ("deskbot-studio-display-v1.schema.json", "deskbot-expression-control-v1.schema.json"):
            document = json.loads((ROOT / "protocol" / name).read_text(encoding="utf-8"))
            resources[document["$id"]] = Resource.from_contents(document)
        registry = Registry().with_resources(resources.items())
        validator = jsonschema.Draft202012Validator(schema, registry=registry,
            format_checker=jsonschema.FormatChecker())
        row = self.link.snapshot(410)
        self.assertTrue(validate_control(row))
        validator.validate(row)
        self.assertFalse(validate_control(row | {"receipts": [None] * 25}))
        self.assertFalse(validate_control(row | {"can_submit": True, "age_ms": 3000}))

    def test_passive_link_sends_nothing(self):
        for now in (0, 1000, 30000):
            self.assertIsNone(self.link.tick(now))
        self.assertEqual(self.link.frames_sent, 0)

    def test_open_reply_has_one_attempt_and_minimum_spacing(self):
        self.command("enable")
        self.assertIsNotNone(self.link.tick(0))
        for now in (1, 400, 1999):
            self.assertIsNone(self.link.tick(now))
        self.assertTrue(self.link.enabled)
        self.assertIsNone(self.link.tick(2000))
        self.assertFalse(self.link.enabled)
        self.assertEqual(self.link.frames_sent, 1)

    def test_page_changes_before_wire_cancel_queued_request(self):
        self.ready()
        # A query already on the wire finishes after the user's click.
        raw = self.link.tick(1400)
        self.command("neutral", session_id=SESSION, now=1410)
        row = display(json.loads(raw)["request_id"], page="other")
        row.update(sequence=3, at_ms=1420)
        self.link.accept(row, 1420)
        self.assertIsNone(self.link.tick(1800))
        self.assertIsNone(self.link.pending)
        self.assertEqual(self.link.local_result["outcome"], "cancelled")

    def test_lease_expiry_clears_unsent_expression(self):
        self.ready()
        self.command("neutral", session_id=SESSION, now=410)
        self.assertIsNone(self.link.tick(30400))
        self.assertIsNone(self.link.outgoing)
        self.assertFalse(self.link.enabled)

    def receipt(self, **changes):
        request = self.link.history[-1]
        return dict(schema=expression_control.SCHEMA, message_type="receipt", target_id=DEVICE, boot_id=BOOT,
            session_id=SESSION, sequence=1, request_id=request["request_id"], request_sequence=request["sequence"],
            status="accepted", reason="queued_for_owner", source="device", correlated=True,
            original_status=None, at_ms=810) | changes

    def test_wrong_identity_session_request_and_simulator_are_rejected(self):
        self.ready(); self.command("neutral", session_id=SESSION); self.link.tick(800)
        for changes in ({"target_id": "device-"+"d"*32}, {"boot_id": "boot-"+"d"*32},
                        {"session_id": SESSION[:-1]+"2"}, {"request_id": "other-request"},
                        {"request_sequence": 2}, {"source": "simulator"}):
            self.link.accept(self.receipt(**changes), 810)
        self.assertEqual(self.link.rejected, 6)
        self.assertEqual(list(self.link.receipts), [])
        self.assertEqual(self.link.snapshot(810)["pending"]["phase"], "waiting")

    def test_replayed_and_backwards_device_receipts_are_rejected(self):
        self.ready(); self.command("neutral", session_id=SESSION); self.link.tick(800)
        self.link.accept(self.receipt(), 810)
        self.link.accept(self.receipt(), 820)
        self.link.accept(self.receipt(sequence=2, at_ms=809), 830)
        self.assertEqual(len(self.link.receipts), 1)
        self.assertEqual(self.link.rejected, 2)

    def test_receipt_after_expired_session_is_rejected(self):
        self.ready(); self.command("neutral", session_id=SESSION); self.link.tick(800)
        self.link.accept(self.receipt(), 30400)
        self.assertEqual(self.link.rejected, 1)
        self.assertEqual(len(self.link.receipts), 0)

    def test_disable_cancels_pending_and_never_retries(self):
        self.ready(); self.command("neutral", session_id=SESSION); self.link.tick(800)
        self.command("disable", session_id=SESSION, now=900)
        self.assertFalse(self.link.enabled)
        self.assertEqual(self.link.local_result["outcome"], "cancelled")
        self.assertIsNone(self.link.tick(10000))

    def test_receipt_and_command_history_have_fixed_limits(self):
        self.ready(); self.command("neutral", session_id=SESSION); self.link.tick(800)
        for seq in range(1, 40):
            self.link.accept(self.receipt(sequence=seq, at_ms=800+seq), 800+seq)
        self.assertEqual(len(self.link.receipts), 24)
        for index in range(80):
            self.command("neutral", session_id=SESSION, now=900, command_id="click-"+str(index))
        self.assertEqual(len(self.link.command_ids), 64)

    def test_each_enable_uses_new_client_nonce(self):
        link = control.ControlLink()
        link.observe(hello(), 0)
        def command(operation, now):
            link.command(dict(operation=operation, command_id="c-"+str(now), connection_id=link.connection_id,
                target_id=DEVICE, boot_id=BOOT, session_id=None), now)
        command("enable", 0)
        first = json.loads(link.tick(0))
        link.disconnect(1)
        command("enable", 400)
        second = json.loads(link.tick(400))
        self.assertNotEqual(first["client_nonce"], second["client_nonce"])
        self.assertNotEqual(first["request_id"], second["request_id"])

    def test_invalid_worker_envelope_types_and_ids_are_rejected(self):
        self.ready(); row = self.link.snapshot(410)
        for changes in ({"connection_id": "wrong"}, {"target_id": "wrong"}, {"boot_id": "wrong"},
                        {"enabled": 1}, {"display": {}}, {"receipts": [None]},
                        {"frames_sent": -1}, {"pending": {"request_id": "r"}}, {"extra": True}):
            self.assertFalse(validate_control(row | changes), changes)


class WorkerTests(unittest.TestCase):
    def test_single_console_owner_is_passive_until_explicit_enable(self):
        elapsed, readers, writes, snapshots = [10.0], [], [], []
        port = SimpleNamespace(vid=0x303A, pid=0x1001, serial_number="host-test", device="COM4")
        listing = SimpleNamespace(comports=lambda: [port])
        class Console:
            def __init__(self, **kwargs):
                self.configuration = kwargs
                self.closed = False
                readers.append(self)
            def open(self): pass
            def read(self, size):
                elapsed[0] += .1
                return b""
            def write(self, value): writes.append(value); return len(value)
            def close(self): self.closed = True
        serial = SimpleNamespace(Serial=Console, SerialException=OSError)
        key = worker.console.ports(listing)[0]["key"]
        worker.watch(serial, listing, "COM4", key, snapshots.append, Queue(maxsize=1),
                     stop=lambda: elapsed[0] > 11, clock=lambda: elapsed[0])
        self.assertEqual(len(readers), 1)
        self.assertTrue(readers[0].closed)
        self.assertEqual(writes, [])
        self.assertTrue(snapshots)
        for value in snapshots:
            self.assertTrue(validate_control(value["control"]))
            self.assertTrue(value["observation"]["read_only"])

    def test_fragmented_identity_and_reply_share_existing_line_buffer(self):
        link = control.ControlLink()
        observation = worker.Observation(link)
        observation.link("connected", "COM4")
        wire = ("I (10000) DeskBotLink: "+json.dumps(hello())+"\n"+
                'I (10001) DeskBotSession: {"broken":true}\n').encode()
        for byte in wire:
            observation.feed(bytes([byte]))
        self.assertEqual(observation.snapshot()["identity"]["status"], "confirmed")
        self.assertEqual(link.rejected, 1)

    def test_console_receipt_is_parsed_as_protocol_not_raw_log(self):
        link = control.ControlLink()
        observation = worker.Observation(link)
        observation.link("connected", "COM4")
        observation.feed(b'I (1000) DeskBotSession: {"broken":true}\r\n')
        self.assertEqual(link.rejected, 1)
        self.assertEqual(observation.snapshot()["events"], [{"sequence": 1, "kind": "connection",
            "value": "connected", "received_at": observation.snapshot()["events"][0]["received_at"],
            "timeline_id": observation.snapshot()["timeline_id"]}])
        self.assertNotIn("DeskBotSession", json.dumps(observation.snapshot()))

    def test_host_ipc_accepts_only_one_bounded_command(self):
        commands = Queue(maxsize=1)
        base = {"operation": "enable", "command_id": "click-enable-1", "connection_id": "connection-"+"a"*32,
                "target_id": None, "boot_id": None, "session_id": None}
        worker.read_commands(io.StringIO(json.dumps(base)+"\n"+json.dumps(base | {"command_id": "click-enable-2"})+"\n"), commands)
        self.assertEqual(commands.get_nowait()["command_id"], "click-enable-1")
        with self.assertRaises(Exception): commands.get_nowait()

    def test_partial_usb_write_is_reported_and_never_replayed(self):
        class Serial:
            def write(self, data): return len(data)-1
        with self.assertRaises(OSError): worker.write_frame(Serial(), b"hello\n")


if __name__ == "__main__":
    unittest.main()
