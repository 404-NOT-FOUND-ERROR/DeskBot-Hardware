from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from protocol.body_facts_consumer import BodyFactsConsumer


WHEN = "2026-10-18T09:00:00.000Z"


def hello(*, boot: str = "boot-a", session: str = "session-a", message_id: str = "hello-a", sequence: int = 0) -> dict[str, object]:
    return {
        "schema": "deskbot.body.v1", "message_type": "hello", "message_id": message_id,
        "device_id": "device-a", "boot_id": boot, "session_id": session, "sequence": sequence,
        "occurred_at": WHEN,
        "payload": {"kind": "hello", "firmware_id": "official-r1", "capabilities": ["base_events", "magnetic_sample", "shell_observation"], "max_event_backlog": 24},
    }


def snapshot(*, boot: str = "boot-a", session: str = "session-a", message_id: str = "snapshot-a", sequence: int = 1, high: int = 0, gap: bool = False) -> dict[str, object]:
    return {
        "schema": "deskbot.body.v1", "message_type": "snapshot", "message_id": message_id,
        "device_id": "device-a", "boot_id": boot, "session_id": session, "sequence": sequence,
        "occurred_at": "2026-10-18T09:00:01.000Z",
        "payload": {
            "kind": "snapshot",
            "base": {"link": "online", "mechanical_status": "ready", "last_event_sequence": high},
            "magnetic": {"state": "fresh", "sensor_kind": "BMM150", "x": 190, "y": -540, "z": -940, "sample_at": WHEN, "age_ms": 0},
            "shell_observation": {"status": "known", "profile_id": "profile-shell-a", "profile_revision": 1, "sensor_kind": "BMM150", "similarity": 0.1, "margin": 0.3, "sample_count": 8, "observed_at": WHEN},
            "event_cursor": {"oldest_available_sequence": 1 if high else None, "high_watermark_sequence": high, "gap_detected": gap},
        },
    }


def event(*, boot: str = "boot-a", session: str = "session-a", message_id: str = "event-1", sequence: int = 2, event_sequence: int = 1) -> dict[str, object]:
    return {
        "schema": "deskbot.body.v1", "message_type": "event", "message_id": message_id,
        "device_id": "device-a", "boot_id": boot, "session_id": session, "sequence": sequence,
        "occurred_at": "2026-10-18T09:00:02.000Z",
        "payload": {"kind": "event", "event_type": "base.action.completed", "event_sequence": event_sequence, "facts": {"action": "shake_head"}},
    }


class BodyFactsConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.consumer = BodyFactsConsumer()

    def test_snapshot_first_and_event_duplicate_are_idempotent(self) -> None:
        self.assertEqual(self.consumer.consume(hello()).status, "accepted")
        self.assertEqual(self.consumer.consume(snapshot()).status, "accepted")
        first = event()
        self.assertEqual(self.consumer.consume(first).status, "accepted")
        duplicate = self.consumer.consume(deepcopy(first))
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(self.consumer.state.last_event_sequence, 1)
        self.assertEqual(len(self.consumer.state.events), 1)

    def test_event_before_hello_or_snapshot_requests_sync(self) -> None:
        self.assertEqual(self.consumer.consume(event()).status, "needs_snapshot")
        self.assertEqual(self.consumer.consume(hello()).status, "accepted")
        self.assertEqual(self.consumer.consume(event()).status, "needs_snapshot")

    def test_event_sequence_gap_requires_new_snapshot(self) -> None:
        self.consumer.consume(hello())
        self.consumer.consume(snapshot())
        self.assertEqual(self.consumer.consume(event(event_sequence=1)).status, "accepted")
        gap = self.consumer.consume(event(message_id="event-3", sequence=3, event_sequence=3))
        self.assertEqual(gap.status, "needs_snapshot")
        self.assertEqual(gap.reason, "event_sequence_gap")
        self.assertTrue(self.consumer.state.needs_snapshot)
        self.assertEqual(self.consumer.consume(event(message_id="event-2", sequence=4, event_sequence=2)).status, "needs_snapshot")

    def test_snapshot_resynchronizes_after_gap(self) -> None:
        self.consumer.consume(hello())
        self.consumer.consume(snapshot())
        self.consumer.consume(event(event_sequence=1))
        self.consumer.consume(event(message_id="event-3", sequence=3, event_sequence=3))
        self.assertEqual(self.consumer.consume(snapshot(message_id="snapshot-resync", sequence=4, high=3, gap=True)).status, "accepted")
        self.assertFalse(self.consumer.state.needs_snapshot)
        self.assertEqual(self.consumer.state.last_event_sequence, 3)
        self.assertEqual(self.consumer.consume(event(message_id="event-4", sequence=5, event_sequence=4)).status, "accepted")
        self.assertEqual(self.consumer.state.last_event_sequence, 4)

    def test_snapshot_watermark_cannot_regress_within_boot(self) -> None:
        self.consumer.consume(hello())
        self.consumer.consume(snapshot(high=2))
        stale = snapshot(message_id="snapshot-stale", sequence=2, high=1)
        result = self.consumer.consume(stale)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "snapshot_watermark_regressed")
        self.assertEqual(self.consumer.state.last_event_sequence, 2)

    def test_reconnect_same_boot_requires_snapshot_but_preserves_identity(self) -> None:
        self.consumer.consume(hello())
        self.consumer.consume(snapshot())
        self.consumer.consume(event())
        reconnect = self.consumer.consume(hello(session="session-b", message_id="hello-b", sequence=10))
        self.assertEqual(reconnect.status, "accepted")
        self.assertFalse(reconnect.boot_changed)
        self.assertTrue(self.consumer.state.needs_snapshot)
        old_session = hello(session="session-a", message_id="delayed-old-session-hello", sequence=11)
        self.assertEqual(self.consumer.consume(old_session).status, "rejected")
        self.assertIn("retired_session", self.consumer.consume(old_session).reason)
        self.assertEqual(self.consumer.consume(event(session="session-b", message_id="event-rejected", sequence=11, event_sequence=2)).status, "needs_snapshot")
        self.assertEqual(self.consumer.consume(snapshot(session="session-b", message_id="snapshot-b", sequence=12, high=1)).status, "accepted")

    def test_new_boot_resets_event_cursor_and_scopes_message_ids(self) -> None:
        self.consumer.consume(hello(message_id="same-id"))
        self.consumer.consume(snapshot())
        self.consumer.consume(event())
        changed = self.consumer.consume(hello(boot="boot-b", message_id="same-id", sequence=0))
        self.assertEqual(changed.status, "accepted")
        self.assertTrue(changed.boot_changed)
        self.assertEqual(self.consumer.state.generation, 2)
        self.assertEqual(self.consumer.consume(snapshot(boot="boot-b", message_id="snapshot-b", sequence=1)).status, "accepted")
        self.assertEqual(self.consumer.state.last_event_sequence, 0)

    def test_unknown_capability_is_rejected_explicitly(self) -> None:
        message = hello()
        message["payload"]["capabilities"].append("future_magic")  # type: ignore[index]
        result = self.consumer.consume(message)
        self.assertEqual(result.status, "rejected")
        self.assertIn("unsupported capability", result.reason)

    def test_ack_requires_current_identity_and_increasing_transport_sequence(self) -> None:
        ack = {
            "schema": "deskbot.body.v1", "message_type": "ack", "message_id": "ack-a",
            "device_id": "device-a", "boot_id": "boot-a", "session_id": "session-a", "sequence": 0,
            "occurred_at": WHEN,
            "payload": {"kind": "ack", "ack_for": "event-1", "status": "accepted"},
        }
        self.assertEqual(self.consumer.consume(ack).status, "needs_snapshot")
        self.consumer.consume(hello())
        self.assertEqual(self.consumer.consume(ack).status, "rejected")
        ack["sequence"] = 1
        self.assertEqual(self.consumer.consume(ack).status, "accepted")
        wrong = deepcopy(ack)
        wrong["message_id"] = "ack-wrong-identity"
        wrong["sequence"] = 2
        wrong["session_id"] = "session-other"
        self.assertEqual(self.consumer.consume(wrong).status, "rejected")

    def test_event_sequence_conflict_is_rejected(self) -> None:
        self.consumer.consume(hello())
        self.consumer.consume(snapshot())
        self.assertEqual(self.consumer.consume(event()).status, "accepted")
        conflict = event(message_id="event-conflict", event_sequence=1)
        conflict["payload"]["facts"]["action"] = "look_around"  # type: ignore[index]
        self.assertEqual(self.consumer.consume(conflict).status, "rejected")

    def test_state_does_not_expose_mutable_message_objects(self) -> None:
        self.consumer.consume(hello())
        snap = snapshot()
        self.consumer.consume(snap)
        snap["payload"]["base"]["link"] = "offline"  # type: ignore[index]
        self.assertEqual(self.consumer.state.latest_snapshot["base"]["link"], "online")  # type: ignore[index]

    def test_transport_sequence_regression_is_rejected(self) -> None:
        self.consumer.consume(hello())
        self.consumer.consume(snapshot())
        stale = event(message_id="stale-transport", sequence=1, event_sequence=1)
        self.assertEqual(self.consumer.consume(stale).status, "rejected")
        self.assertIn("transport_sequence", self.consumer.consume(stale).reason)

    def test_snapshot_unknown_fields_and_invalid_shell_state_are_rejected(self) -> None:
        self.consumer.consume(hello())
        invalid = snapshot()
        invalid["payload"]["future_field"] = True  # type: ignore[index]
        self.assertEqual(self.consumer.consume(invalid).status, "rejected")
        invalid = snapshot(message_id="invalid-shell")
        invalid["payload"]["shell_observation"]["status"] = "future"  # type: ignore[index]
        self.assertEqual(self.consumer.consume(invalid).status, "rejected")

    def test_same_old_boot_cannot_roll_back_after_new_boot(self) -> None:
        self.consumer.consume(hello(message_id="boot-a-hello"))
        self.consumer.consume(snapshot())
        self.consumer.consume(hello(boot="boot-b", message_id="boot-b-hello", sequence=10))
        stale = hello(boot="boot-a", message_id="boot-a-late", sequence=11)
        self.assertEqual(self.consumer.consume(stale).status, "rejected")
        self.assertIn("retired_boot", self.consumer.consume(stale).reason)


if __name__ == "__main__":
    unittest.main()
