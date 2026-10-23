from __future__ import annotations

import unittest

from protocol.base_event_journal import BaseEventJournal


def value_bytes(value: int) -> bytes:
    return value.to_bytes(2, "big")


class O2bEventJournalTests(unittest.TestCase):
    def test_heartbeat_records_edges_and_refreshes_online_time(self) -> None:
        journal = BaseEventJournal()
        connected = journal.ingest(0x00, value_bytes(1), 100)
        self.assertIsNotNone(connected)
        self.assertTrue(journal.online)
        self.assertEqual(journal.last_heartbeat_ms, 100)
        self.assertIsNone(journal.ingest(0x00, value_bytes(1), 200))
        self.assertEqual(journal.last_heartbeat_ms, 200)
        self.assertEqual(len(journal.records()), 1)

        self.assertIsNone(journal.check_timeout(1200))
        disconnected = journal.check_timeout(1201)
        self.assertIsNotNone(disconnected)
        self.assertEqual(disconnected.type, "base.link.disconnected")
        self.assertTrue(disconnected.synthetic)
        self.assertFalse(journal.online)
        self.assertIsNone(journal.check_timeout(4000))

        reconnected = journal.ingest(0x00, value_bytes(1), 1300)
        self.assertEqual(reconnected.type, "base.link.connected")
        self.assertEqual([event.sequence for event in journal.records()], [1, 2, 3])

    def test_timeout_uses_uint32_uptime_wraparound(self) -> None:
        journal = BaseEventJournal(timeout_ms=1000)
        journal.ingest(0x00, value_bytes(1), 0xFFFFFFF0)
        self.assertIsNone(journal.check_timeout(0x000002D8))  # 744 ms elapsed
        disconnected = journal.check_timeout(0x000003E8)  # 1016 ms elapsed
        self.assertEqual(disconnected.type, "base.link.disconnected")

    def test_repeated_mechanical_status_is_deduplicated(self) -> None:
        journal = BaseEventJournal()
        journal.ingest(0x05, value_bytes(0), 10)
        self.assertIsNone(journal.ingest(0x05, value_bytes(0), 20))
        ready = journal.ingest(0x05, value_bytes(1), 30)
        self.assertEqual(ready.payload["status"], "ready")
        self.assertEqual(len(journal.records()), 2)

    def test_disconnect_invalidates_mechanics_and_reconnect_same_status_records(self) -> None:
        journal = BaseEventJournal()
        journal.ingest(0x00, value_bytes(1), 10)
        journal.ingest(0x05, value_bytes(1), 20)
        journal.check_timeout(1011)
        self.assertIsNone(journal.mechanical_status)
        self.assertEqual(journal.records()[-1].frame, "")
        journal.ingest(0x00, value_bytes(1), 1100)
        ready = journal.ingest(0x05, value_bytes(1), 1110)
        self.assertEqual(ready.type, "base.mechanical.status")
        self.assertEqual(ready.payload["status"], "ready")

    def test_telemetry_is_not_recorded(self) -> None:
        journal = BaseEventJournal()
        telemetry = bytes((1, 0, 190, 0xFD, 0xDC, 0xFC, 0x54))
        self.assertIsNone(journal.ingest(0x06, telemetry, 10))
        self.assertEqual(journal.total_recorded, 0)
        self.assertEqual(journal.records(), ())

    def test_raw_data_and_normalized_frame_are_preserved(self) -> None:
        journal = BaseEventJournal()
        record = journal.ingest(0x03, value_bytes(0x0008), 123)
        self.assertEqual(record.data, b"\x00\x08")
        self.assertEqual(record.data.hex().upper(), "0008")
        self.assertEqual(record.frame, "AA5500030300080B")
        self.assertEqual(record.payload["accessory"], "fish")

    def test_unknown_events_are_kept_without_guessing(self) -> None:
        journal = BaseEventJournal()
        record = journal.ingest(0x7F, value_bytes(0x1234), 10)
        self.assertFalse(record.synthetic)
        self.assertEqual(record.type, "base.uart.unknown_event")
        self.assertEqual(record.value, 0x1234)
        self.assertEqual(record.frame, "AA5500037F1234C5")

    def test_ring_keeps_latest_24_records_and_monotonic_sequences(self) -> None:
        journal = BaseEventJournal()
        for value in range(30):
            journal.ingest(0x03, value_bytes(0x0100 + value), value)
        records = journal.records()
        self.assertEqual(len(records), 24)
        self.assertEqual(journal.total_recorded, 30)
        self.assertEqual(records[0].sequence, 7)
        self.assertEqual(records[-1].sequence, 30)
        self.assertEqual(records[-1].timestamp_ms, 29)

    def test_clear_resets_state_and_sequences(self) -> None:
        journal = BaseEventJournal()
        journal.ingest(0x00, value_bytes(1), 10)
        journal.clear()
        self.assertFalse(journal.online)
        self.assertIsNone(journal.last_heartbeat_ms)
        self.assertEqual(journal.total_recorded, 0)
        self.assertEqual(journal.records(), ())
        record = journal.ingest(0x02, value_bytes(0x10), 20)
        self.assertEqual(record.sequence, 1)

    def test_invalid_timestamp_is_rejected(self) -> None:
        journal = BaseEventJournal()
        with self.assertRaises(ValueError):
            journal.ingest(0x02, value_bytes(0x10), -1)
        with self.assertRaises(ValueError):
            journal.check_timeout(0x1_0000_0000)
        with self.assertRaises(ValueError):
            journal.ingest(0x100, value_bytes(1), 0)
        with self.assertRaises(ValueError):
            journal.ingest(0x03, bytes(250), 0)


if __name__ == "__main__":
    unittest.main()
