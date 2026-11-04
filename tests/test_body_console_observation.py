from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import jsonschema

from protocol.body_console_observation import Observation, aged_snapshot, FRESH_MS, MAX_EVENTS, MAX_LINE, STATES
from protocol.studio_body_bridge import ConsoleBridge

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("console_worker_test", ROOT / "tools/observe-studio-body-e3a.py")
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.o = Observation(clock=lambda: self.now, wall=lambda: "2026-11-04T04:00:00.000+00:00")
        self.o.link("connected", "COM4", "passive_console")

    def feed(self, text):
        self.o.feed(text.encode())
        return self.o.snapshot()

    def test_open_is_waiting_and_state_is_unknown(self):
        s = self.o.snapshot()
        self.assertEqual(s["freshness"], "waiting")
        self.assertIsNone(s["application_state"])
        self.assertFalse(s["live"])

    def test_fragmented_ansi_and_multiple_lines(self):
        self.feed('\x1b[0;32mI (200) Applica')
        s = self.feed('tion: STATE: listening\x1b[0m\r\nI (300) SystemInfo: free sram: 100000 minimal sram: 80000\n')
        self.assertEqual(s["application_state"]["value"], "listening")
        self.assertEqual(s["internal_memory"]["free_internal_bytes"], 100000)
        self.assertTrue(s["live"])

    def test_chat_credentials_and_other_tags_are_not_retained(self):
        s = self.feed('I (1) Application: << private transcript\nI (2) WebSocket: Authorization: secret\nI (3) Other: STATE: speaking\nI (4) Application: STATE: made_up\n')
        self.assertIsNone(s["last_seen_at"])
        self.assertNotIn('secret', json.dumps(s))
        self.assertNotIn('transcript', json.dumps(s))

    def test_malformed_heap_does_not_refresh(self):
        for line in ('free sram: -1 minimal sram: 0', 'free sram: 10 minimal sram: 11',
                     'free sram: 4294967296 minimal sram: 0'):
            self.feed(f'I (22) SystemInfo: {line}\n')
        self.assertIsNone(self.o.snapshot()["last_seen_age_ms"])

    def test_overlong_line_is_discarded_and_next_frame_recovers(self):
        s = self.feed('x' * (MAX_LINE * 3) + 'I (1) Application: STATE: idle\nI (2) Application: STATE: speaking\n')
        self.assertEqual(s["dropped_lines"], 1)
        self.assertEqual(s["application_state"]["value"], "speaking")
        self.assertLessEqual(len(self.o.buffer), MAX_LINE)

    def test_duplicate_state_and_heap_updates_do_not_flood_events(self):
        self.feed('I (1) Application: STATE: idle\n' * 50)
        self.feed('I (20000) SystemInfo: free sram: 100 minimal sram: 90\n' * 100)
        self.assertEqual(len(self.o.snapshot()["events"]), 2)

    def test_history_is_bounded_and_sequences_increase(self):
        for i in range(60):
            self.feed(f'I ({i}) Application: STATE: {"idle" if i % 2 else "speaking"}\n')
        s = self.o.snapshot()
        self.assertEqual(len(s["events"]), MAX_EVENTS)
        self.assertEqual(s["events"][-1]["sequence"], 61)

    def test_freshness_uses_host_clock_and_ages_each_field(self):
        self.feed('I (1) Application: STATE: idle\n')
        self.now += 29
        self.feed('I (29000) SystemInfo: free sram: 100 minimal sram: 90\n')
        self.now += 2
        s = self.o.snapshot()
        self.assertEqual(s["freshness"], "fresh")
        self.assertEqual(s["application_state"]["age_ms"], 31000)
        self.assertEqual(s["internal_memory"]["age_ms"], 2000)
        self.now += 29
        self.assertEqual(self.o.snapshot()["freshness"], "stale")

    def test_disconnect_marks_historical_values_and_reconnect_clears(self):
        self.feed('I (100) Application: STATE: idle\n')
        old = self.o.snapshot()["timeline_id"]
        self.o.link("missing", "COM4", "usb_not_present")
        self.assertEqual(self.o.snapshot()["freshness"], "disconnected")
        self.o.link("connected", "COM4", "passive_console")
        self.assertIsNone(self.o.snapshot()["application_state"])
        self.assertNotEqual(self.o.snapshot()["timeline_id"], old)

    def test_boot_banner_and_clock_reset_start_observation_segments(self):
        self.feed('I (10000) Application: STATE: speaking\n')
        old = self.o.snapshot()["timeline_id"]
        self.feed('ESP-ROM:esp32s3-20210327\n')
        self.assertNotEqual(self.o.snapshot()["timeline_id"], old)
        self.assertIsNone(self.o.snapshot()["application_state"])
        self.feed('I (10000) Application: STATE: idle\n')
        old = self.o.snapshot()["timeline_id"]
        self.feed('I (10) Application: STATE: starting\n')
        self.assertNotEqual(self.o.snapshot()["timeline_id"], old)
        self.assertEqual(self.o.snapshot()["events"][-2]["value"], "device_clock_reset")

    def test_worker_snapshot_ages_and_exit_cannot_stay_live(self):
        self.feed('I (1) Application: STATE: idle\n')
        s = aged_snapshot(self.o.snapshot(), FRESH_MS + 1)
        self.assertFalse(s["live"])
        self.assertEqual(s["freshness"], "stale")
        s = aged_snapshot(self.o.snapshot(), 0, worker_alive=False)
        self.assertFalse(s["live"])
        self.assertEqual(s["connection"]["reason"], "reader_unavailable")

    def test_output_schema_for_waiting_live_stale_disconnect(self):
        schema = json.loads((ROOT / 'protocol/deskbot-body-observation-v1.schema.json').read_text())
        for value in (self.o.snapshot(), self.feed('I (1) Application: STATE: idle\n'),
                      aged_snapshot(self.o.snapshot(), FRESH_MS + 1),
                      aged_snapshot(self.o.snapshot(), 0, worker_alive=False)):
            jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)

    def test_contract_matches_actual_firmware_log_sites(self):
        app = (ROOT / 'firmware/body/main/application.cc').read_text(encoding='utf-8')
        state_block = app.split('STATE_STRINGS[] = {', 1)[1].split('};', 1)[0]
        self.assertEqual(tuple(re.findall(r'"([a-z_]+)"', state_block)), STATES)
        self.assertIn('ESP_LOGI(TAG, "STATE: %s"', app)
        self.assertIn('clock_ticks_ % 10 == 0', app)
        self.assertIn('free sram: %u minimal sram: %u', (ROOT / 'firmware/body/main/system_info.cc').read_text())


class UsbTests(unittest.TestCase):
    def test_control_lines_are_idle_before_open_and_no_writes(self):
        events = []
        class Device:
            def __init__(self, **kwargs):
                self.dtr = self.rts = True
                self.kwargs = kwargs
            def open(self):
                events.append((self.dtr, self.rts, self.port, self.kwargs))
        class Module:
            Serial = Device
        W.open_console(Module, 'COM4')
        self.assertEqual(events[0][:3], (False, False, 'COM4'))
        self.assertIsNone(events[0][3]['port'])
        self.assertFalse(events[0][3]['rtscts'])
        self.assertFalse(events[0][3]['dsrdtr'])

    def test_enumeration_filters_and_hashes_identity(self):
        class Port:
            def __init__(self, name, vid, pid, serial_number):
                self.device, self.vid, self.pid, self.serial_number = name, vid, pid, serial_number
        class Listing:
            @staticmethod
            def comports():
                return [Port('COM4',0x303A,0x1001,'private-serial'), Port('COM8',0x303A,0x4002,'other'), Port('COM3',None,None,None)]
        rows = W.ports(Listing)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['key']), 64)
        self.assertNotIn('private-serial', json.dumps(rows))

    def test_no_serial_runtime_does_not_spawn_or_access_hardware(self):
        b = ConsoleBridge()
        self.assertFalse(b.ports()['available'])
        self.assertFalse(b.snapshot()['hardware_accessed'])
        with self.assertRaises(ValueError):
            b.connect('COM4', 'a' * 64)

    def test_stale_port_key_is_rejected_before_worker(self):
        b = ConsoleBridge(ROOT / 'README.md')
        b.ports = lambda: {'available': True, 'ports': [{'port':'COM4','key':'a' * 64}]}
        with self.assertRaisesRegex(ValueError, 'USB'):
            b.connect('COM4', 'b' * 64)
        self.assertIsNone(b.process)

    def test_busy_missing_changed_identity_then_reconnect(self):
        statuses = []
        records = []
        key = 'a' * 64
        listings = [[{'port':'COM4','key':key}], [], [{'port':'COM4','key':'b' * 64}], [{'port':'COM4','key':key}]]
        ticks = iter(i * 1.1 for i in range(100))
        class Device:
            def read(self, size):
                records.append(size)
                return b'I (100) Application: STATE: idle\n'
            def close(self):
                pass
        class Module:
            class SerialException(Exception):
                pass
        def listing(_):
            return listings.pop(0) if len(listings)>1 else listings[0]
        attempts = []
        def opening(*_):
            attempts.append(1)
            if len(attempts) == 1:
                raise Module.SerialException()
            return Device()
        with patch.object(W, 'ports', listing), patch.object(W, 'open_console', opening), patch.object(W.time, 'monotonic', lambda: next(ticks)), patch.object(W.time, 'sleep', lambda _: None):
            W.watch(Module, None, 'COM4', key, lambda value: statuses.append(value), stop=lambda: len(statuses)>=8)
        values = [s['connection']['status'] for s in statuses]
        self.assertIn('busy', values)
        self.assertIn('missing', values)
        self.assertIn('mismatch', values)
        self.assertIn('connected', values)
        self.assertTrue(records)


if __name__ == '__main__':
    unittest.main()
