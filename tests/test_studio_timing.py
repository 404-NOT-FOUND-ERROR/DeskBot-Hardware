"""Slow USB, IPC delay and receipt deadlines using production host components."""
import importlib.util
import io
import json
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from protocol import studio_timing as timing
from protocol.studio_control_bridge import ControlBridge
from protocol.studio_identity_observation import IdentityObservation

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load("timing_link_fixtures", "tests/test_studio_control_link.py")
directory = load("timing_catalog_fixtures", "tests/test_studio_catalog_query.py")
worker = fixture.worker


def command(link, operation, now, *, queued_at=None):
    link.command(dict(operation=operation, command_id=f"click-{operation}-{now}",
        connection_id=link.connection_id, target_id=fixture.DEVICE, boot_id=fixture.BOOT,
        session_id=link.client.session_id if link.client else None), now, queued_at=queued_at)


def slow_ready():
    link = fixture.control.ControlLink(nonce=lambda: "client-slow")
    link.observe(fixture.hello(), 0)
    command(link, "enable", 0)
    wire = json.loads(link.tick(0))
    link.accept(dict(schema=fixture.studio_usb_session.SCHEMA, message_type="session",
        request_id=wire["request_id"], target_id=fixture.DEVICE, boot_id=fixture.BOOT,
        sequence=1, session_id=fixture.SESSION, state="ready", reason="session_opened",
        at_ms=2700, lease_ms=30000, controls_available=False), 2700)
    query = json.loads(link.tick(2700))
    link.accept(fixture.display(query["request_id"]) | dict(at_ms=5900), 5900)
    return link


def ready():
    case = fixture.LinkTests()
    case.setUp()
    case.ready()
    return case.link


def receipt(link, status="accepted", sequence=1, at_ms=1000):
    request = link.history[-1]
    return dict(schema=fixture.expression_control.SCHEMA, message_type="receipt",
        target_id=fixture.DEVICE, boot_id=fixture.BOOT, session_id=fixture.SESSION,
        sequence=sequence, request_id=request["request_id"], request_sequence=request["sequence"],
        status=status, reason="queued_for_owner" if status == "accepted" else "submitted_to_display",
        source="device", correlated=True, original_status=None, at_ms=at_ms)


class SlowLinkTests(unittest.TestCase):
    def test_slow_open_and_display_then_exact_reply_cache_boundary(self):
        link = slow_ready()
        self.assertTrue(link.snapshot(5900)["can_submit"])
        self.assertEqual(link.snapshot(7899)["age_ms"], 1999)
        self.assertIsNone(link.snapshot(7900)["display"])
        self.assertFalse(link.snapshot(7900)["can_submit"])

    def test_slow_replies_schedule_from_completion_without_request_overlap(self):
        link = slow_ready()
        self.assertIsNone(link.tick(6899))
        query = json.loads(link.tick(6900))
        self.assertFalse(link.snapshot(6900)["can_submit"])
        self.assertIsNone(link.tick(10099))
        link.accept(fixture.display(query["request_id"]) | dict(sequence=3, at_ms=10100), 10100)
        self.assertTrue(link.snapshot(10100)["can_submit"])
        self.assertIsNone(link.tick(11099))
        self.assertEqual(json.loads(link.tick(11100))["operation"], "display")

    def test_request_expires_at_eight_seconds_and_never_auto_reopens(self):
        link = fixture.control.ControlLink(nonce=lambda: "client-boundary")
        link.observe(fixture.hello(), 0)
        command(link, "enable", 0)
        link.tick(0)
        self.assertIsNone(link.tick(7999))
        self.assertTrue(link.enabled)
        self.assertIsNone(link.tick(8000))
        self.assertFalse(link.enabled)
        self.assertIsNone(link.tick(9000))
        self.assertEqual(link.frames_sent, 1)

    def test_slow_catalog_pages_have_priority_then_display_resumes(self):
        link = slow_ready()
        command(link, "read_catalog", 6000)
        manifest = directory.fixture_manifest()
        for index, (sent, received) in enumerate(((6000, 9200), (9200, 12400))):
            query = json.loads(link.tick(sent))
            self.assertEqual(query["operation"], f"catalog.{2*index}")
            self.assertIsNone(link.tick(received-1))
            page = directory.catalog.make_page(manifest, request_id=query["request_id"],
                target_id=fixture.DEVICE, boot_id=fixture.BOOT, session_id=fixture.SESSION,
                sequence=3+index, at_ms=received, cursor=2*index)
            link.accept(page, received)
        self.assertEqual(link.catalog["manifest"], manifest)
        self.assertEqual(json.loads(link.tick(12400))["operation"], "display")

    def test_late_page_cannot_clear_new_display_or_new_catalog_request(self):
        link = slow_ready()
        command(link, "read_catalog", 6000)
        old = json.loads(link.tick(6000))
        late = directory.catalog.make_page(directory.fixture_manifest(), request_id=old["request_id"],
            target_id=fixture.DEVICE, boot_id=fixture.BOOT, session_id=fixture.SESSION,
            sequence=3, at_ms=14000, cursor=0)
        query = json.loads(link.tick(14000))
        link.accept(late, 14001)
        self.assertEqual(link.client._pending["request_id"], query["request_id"])
        self.assertEqual(link.catalog["reason"], "page_timeout")
        link.accept(fixture.display(query["request_id"]) | dict(sequence=4, at_ms=17200), 17200)
        command(link, "read_catalog", 17201)
        new = json.loads(link.tick(17201))
        link.accept(late, 17202)
        self.assertEqual(link.catalog["status"], "reading")
        self.assertEqual(link.client._pending["request_id"], new["request_id"])

    def test_open_lease_remains_anchored_to_send(self):
        link = slow_ready()
        self.assertIsNone(link.tick(32700))
        self.assertFalse(link.enabled)
        self.assertEqual(link.reason, "lease_expired")

    def test_replay_and_backwards_clocks_do_not_refresh_cache(self):
        link = slow_ready()
        old = link.snapshot(5900)["display"]
        link.accept(old, 6000)
        self.assertEqual(link.snapshot(6100)["age_ms"], 200)
        with self.assertRaises(ValueError):
            link.client.snapshot(6099)
        self.assertFalse(link.client.connected)


class ExpressionTimingTests(unittest.TestCase):
    def test_first_receipt_at_or_after_deadline_without_intermediate_tick(self):
        for received in (8799, 8800, 8801):
            link = ready()
            command(link, "neutral", 410)
            link.tick(800)
            link.accept(receipt(link, at_ms=received), received)
            if received < 8800:
                self.assertEqual(link.pending["accepted_at"], received)
                self.assertIsNone(link.local_result)
            else:
                self.assertIsNone(link.pending)
                self.assertEqual(link.local_result["outcome"], "timeout")
            self.assertEqual(len(link.receipts), 1)

    def test_first_receipt_budget_includes_serial_transfer(self):
        link = slow_ready()
        command(link, "neutral", 6000)
        wire = json.loads(link.tick(6000))
        self.assertEqual(wire["expires_after_ms"], 2000)
        self.assertIsNone(link.tick(10999))
        link.accept(receipt(link, at_ms=11000), 11000)
        self.assertEqual(link.snapshot(11000)["pending"]["phase"], "accepted")
        self.assertIsNone(link.tick(13999))
        link.tick(14000)
        self.assertEqual(link.local_result["outcome"], "timeout")

    def test_duplicate_accepted_does_not_extend_terminal_deadline(self):
        link = ready()
        command(link, "neutral", 410)
        link.tick(800)
        link.accept(receipt(link, at_ms=5000), 5000)
        link.accept(receipt(link, sequence=2, at_ms=7000), 7000)
        self.assertEqual(link.pending["accepted_at"], 5000)
        self.assertIsNone(link.tick(7999))
        link.tick(8000)
        self.assertEqual(link.local_result["outcome"], "timeout")
        link.accept(receipt(link, "dispatched", 3, 8100), 8100)
        self.assertIsNone(link.local_result)
        self.assertEqual(len(link.history), 1)

    def test_terminal_receipt_uses_separate_accepted_deadline(self):
        link = ready()
        command(link, "neutral", 410)
        link.tick(800)
        link.accept(receipt(link, at_ms=8500), 8500)
        self.assertIsNone(link.tick(11499))
        link.accept(receipt(link, "dispatched", 2, 11499), 11499)
        self.assertIsNone(link.pending)
        self.assertIsNone(link.local_result)

    def test_queued_click_is_not_rejuvenated_after_blocking_usb_write(self):
        link = ready()
        command(link, "neutral", 410, queued_at=0)
        self.assertIsNone(link.tick(2000))
        self.assertIsNone(link.pending)
        self.assertEqual(link.local_result["reason"], "request_expired")
        self.assertEqual(len(link.history), 0)

    def test_old_or_future_ipc_click_cannot_create_an_expression(self):
        for queued_at, reason in ((3900, "request_expired"), (5901, "command_clock_invalid")):
            link = slow_ready()
            command(link, "neutral", 5900, queued_at=queued_at)
            self.assertIsNone(link.outgoing)
            self.assertEqual(link.last_command["reason"], reason)

    def test_reader_captures_arrival_before_main_loop_processing(self):
        link = ready()
        row = dict(operation="neutral", command_id="click-late", connection_id=link.connection_id,
            target_id=fixture.DEVICE, boot_id=fixture.BOOT, session_id=fixture.SESSION)
        queue = Queue(maxsize=1)
        worker.read_commands(io.StringIO(json.dumps(row)+"\n"), queue, clock=lambda: 0)
        value, queued_at = queue.get_nowait()
        link.command(value, 2000, queued_at=queued_at)
        self.assertEqual(link.last_command["reason"], "request_expired")


class BridgeTimingTests(unittest.TestCase):
    def bridge_trace(self, generated_times, *, now=13, validation_delay=0):
        link = ready()
        observation = IdentityObservation(clock=lambda: 10)
        observation.link("connected", "COM4")
        observation.feed(("I (10000) DeskBotLink: "+json.dumps(fixture.hello())+"\n").encode())
        envelopes = [dict(schema="deskbot.studio_transport/v1", generated_at_ms=t,
            observation=observation.snapshot(), control=link.snapshot(410)) for t in generated_times]
        captured = []
        clock = [now]
        bridge = ControlBridge(clock=lambda: clock[0])
        class Stream(io.StringIO):
            def readline(self, limit=-1):
                if self.tell():
                    captured.append(bridge.control())
                value = super().readline(limit)
                if not value:
                    process.finished = True
                return value
        class Process:
            finished = False
            stdin = io.StringIO()
            def poll(self): return 0 if self.finished else None
            def terminate(self): self.finished = True
            def wait(self, **kwargs): return 0
        process = Process()
        process.stdout = Stream("".join(json.dumps(row)+"\n" for row in envelopes))
        bridge.process = process
        def validate(value):
            result = fixture.validate_control(value)
            clock[0] += validation_delay
            return result
        with patch("protocol.studio_control_bridge.validate_control", side_effect=validate):
            bridge._read(process)
        return captured, bridge

    def test_ipc_delay_is_added_to_reply_age(self):
        captured, _ = self.bridge_trace([12000])
        self.assertEqual(captured[0]["age_ms"], 1000)
        self.assertTrue(captured[0]["can_submit"])

    def test_delayed_snapshot_does_not_become_fresh_on_arrival(self):
        captured, _ = self.bridge_trace([10000])
        self.assertEqual(captured[0]["age_ms"], 3000)
        self.assertIsNone(captured[0]["display"])
        self.assertFalse(captured[0]["can_submit"])

    def test_future_and_boolean_generation_times_are_rejected(self):
        for generated in (13001, True, -1):
            captured, bridge = self.bridge_trace([generated])
            self.assertEqual(captured, [])
            self.assertEqual(bridge.control_state["frames_sent"], 0)

    def test_backwards_worker_generation_time_is_rejected(self):
        captured, bridge = self.bridge_trace([12000, 11999])
        self.assertEqual(len(captured), 1)
        self.assertEqual(bridge.control_state["age_ms"], 1000)

    def test_schema_validation_time_is_included_in_reply_age(self):
        captured, _ = self.bridge_trace([12000], validation_delay=2)
        self.assertEqual(captured[0]["age_ms"], 3000)
        self.assertFalse(captured[0]["can_submit"])


class WorkerTimingTests(unittest.TestCase):
    def test_production_worker_slow_expression_receives_accepted_and_dispatched(self):
        elapsed, writes, snapshots, traces = [10.0], [], [], []
        queue = Queue(maxsize=1)
        port = SimpleNamespace(vid=0x303A, pid=0x1001, serial_number="expression-test", device="COM4")
        listing = SimpleNamespace(comports=lambda: [port])
        class Console:
            def __init__(self, **kwargs):
                self.input = ("I (10000) DeskBotLink: "+json.dumps(fixture.hello())+"\n").encode()
                self.sequence = 0
            def open(self): pass
            def close(self): pass
            def read(self, size):
                elapsed[0] += .1
                value, self.input = self.input, b""
                return value
            def flush(self): raise AssertionError("unexpected USB flush")
            def write(self, wire):
                duration = len(wire)*.01
                if duration >= self.write_timeout:
                    raise OSError("modeled write timeout")
                elapsed[0] += duration
                request = json.loads(wire)
                writes.append((request, duration))
                self.sequence += 1
                if request.get("operation") == "open":
                    rows = [dict(schema=fixture.studio_usb_session.SCHEMA, message_type="session",
                        request_id=request["request_id"], target_id=fixture.DEVICE, boot_id=fixture.BOOT,
                        sequence=self.sequence, session_id=fixture.SESSION, state="ready", reason="session_opened",
                        at_ms=int(elapsed[0]*1000), lease_ms=30000, controls_available=False)]
                elif request.get("operation") == "display":
                    rows = [fixture.display(request["request_id"]) |
                            dict(sequence=self.sequence, at_ms=int(elapsed[0]*1000))]
                else:
                    rows = [dict(schema=fixture.expression_control.SCHEMA, message_type="receipt",
                        request_id=request["request_id"], request_sequence=request["sequence"],
                        target_id=fixture.DEVICE, boot_id=fixture.BOOT, session_id=fixture.SESSION,
                        sequence=n+1, status=status, reason=reason, source="device", correlated=True,
                        original_status=None, at_ms=int(elapsed[0]*1000)+n)
                        for n, (status, reason) in enumerate((("accepted", "queued_for_owner"),
                                                             ("dispatched", "submitted_to_display")))]
                self.input += b"".join(("I (10000) DeskBotSession: "+json.dumps(row)+"\n").encode() for row in rows)
                return len(wire)
        def emit(value):
            snapshots.append(value)
            state = value["control"]
            operation = "enable" if len(snapshots) == 1 else "neutral" if state["can_submit"] and len(writes) == 2 else None
            if operation:
                queue.put_nowait((dict(operation=operation, command_id="click-"+operation,
                    connection_id=state["connection_id"], target_id=fixture.DEVICE,
                    boot_id=fixture.BOOT, session_id=state["session_id"]), int(elapsed[0]*1000)))
        serial = SimpleNamespace(Serial=Console, SerialException=OSError)
        key = worker.console.ports(listing)[0]["key"]
        worker.watch(serial, listing, "COM4", key, emit, queue,
            stop=lambda: elapsed[0] > 40 or bool(snapshots and len(snapshots[-1]["control"]["receipts"]) == 2),
            clock=lambda: elapsed[0], trace=traces.append)
        state = snapshots[-1]["control"]
        self.assertEqual([row["status"] for row in state["receipts"]], ["accepted", "dispatched"])
        self.assertIsNone(state["pending"])
        self.assertIsNone(state["local_result"])
        self.assertEqual(state["rejected_frames"], 0)
        expressions = [(row, duration) for row, duration in writes if "command" in row]
        self.assertEqual(len(expressions), 1)
        self.assertGreater(expressions[0][1], 5)
        self.assertLess(expressions[0][1], worker.WRITE_TIMEOUT_SECONDS)

    def test_expression_sized_production_write_fits_bounded_write_timeout(self):
        link = ready()
        command(link, "neutral", 410)
        wire = link.tick(800)
        # Model the observed one byte per 10 ms drain, including timeout behavior.
        class Console:
            write_timeout = worker.WRITE_TIMEOUT_SECONDS
            calls = 0
            def write(self, value):
                self.calls += 1
                if len(value)*.01 >= self.write_timeout:
                    raise OSError("modeled serial write timeout")
                return len(value)
            def flush(self): raise AssertionError("unexpected serial flush")
        device = Console()
        self.assertGreater(len(wire)*.01, 5)
        self.assertLess(worker.WRITE_TIMEOUT_SECONDS*1000, timing.REQUEST_TIMEOUT_MS)
        worker.write_frame(device, wire)
        self.assertEqual(device.calls, 1)

    def test_blocking_production_write_keeps_one_owner_and_no_flush(self):
        elapsed, writes, traces, snapshots, readers = [10.0], [], [], [], []
        queue = Queue(maxsize=1)
        port = SimpleNamespace(vid=0x303A, pid=0x1001, serial_number="slow-test", device="COM4")
        listing = SimpleNamespace(comports=lambda: [port])
        class Console:
            def __init__(self, **kwargs):
                self.input = ("I (10000) DeskBotLink: "+json.dumps(fixture.hello())+"\n").encode()
                readers.append(self)
            def open(self): pass
            def close(self): pass
            def flush(self): raise AssertionError("USB flush is not part of production writes")
            def read(self, size):
                elapsed[0] += .1
                value, self.input = self.input, b""
                return value
            def write(self, wire):
                writes.append(wire)
                elapsed[0] += 3.2
                return len(wire)
        def emit(value):
            snapshots.append(value)
            if len(snapshots) == 1:
                state = value["control"]
                row = dict(operation="enable", command_id="click-enable", connection_id=state["connection_id"],
                    target_id=fixture.DEVICE, boot_id=fixture.BOOT, session_id=None)
                queue.put_nowait((row, int(elapsed[0]*1000)))
        serial = SimpleNamespace(Serial=Console, SerialException=OSError)
        key = worker.console.ports(listing)[0]["key"]
        worker.watch(serial, listing, "COM4", key, emit, queue, stop=lambda: elapsed[0] > 14,
                     clock=lambda: elapsed[0], trace=traces.append)
        self.assertEqual(len(readers), 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual([row["event"] for row in traces], ["write_start", "write_return"])
        self.assertAlmostEqual(traces[1]["at_ms"]-traces[0]["at_ms"], 3200, delta=1)
        self.assertTrue(all(row["generated_at_ms"] >= 10000 for row in snapshots))
        self.assertTrue(snapshots[-1]["control"]["enabled"])


if __name__ == "__main__":
    unittest.main()
