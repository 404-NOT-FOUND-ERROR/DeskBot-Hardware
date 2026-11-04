from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from protocol.expression_runtime_observation import (
    CAPACITY,
    COALESCE_WINDOW_MS,
    ExpressionObservationRing,
    MAX_SEQUENCE,
    SCHEMA_ID,
    UINT32_MAX,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "protocol/expression-runtime-observation-e1-25.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


class ExpressionObservationE125Tests(unittest.TestCase):
    def test_default_disabled_is_a_noop(self):
        ring = ExpressionObservationRing()
        self.assertFalse(ring.enabled)
        self.assertFalse(ring.observe("happy", -1))
        self.assertEqual(ring.snapshot(0)["records"], [])

    def test_semantic_calls_coalesce_only_when_consecutive_and_in_window(self):
        ring = ExpressionObservationRing(enabled=True)
        self.assertTrue(ring.observe("happy", 100))
        self.assertTrue(ring.observe("happy", 100 + COALESCE_WINDOW_MS))
        self.assertTrue(ring.observe("sad", 500))
        self.assertTrue(ring.observe("happy", 600))
        records = ring.snapshot(700)["records"]
        self.assertEqual([row["semantic"] for row in records], ["happy", "sad", "happy"])
        self.assertEqual(records[0]["repeat_count"], 2)
        self.assertEqual(records[0]["first_seen_ms"], 100)
        self.assertEqual(records[0]["last_seen_ms"], 350)

    def test_coalescing_window_and_uptime_wrap_are_wrap_safe(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("happy", UINT32_MAX - 100)
        ring.observe("happy", 149)
        self.assertEqual(len(ring.snapshot(150)["records"]), 1)
        ring.observe("happy", 400)
        self.assertEqual(len(ring.snapshot(401)["records"]), 2)

    def test_unknown_and_long_names_are_collapsed_without_retaining_input(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("secret_user_text", 1)
        ring.observe("x" * 10000, 2)
        records = ring.snapshot(3)["records"]
        self.assertEqual([row["semantic"] for row in records], ["other"])
        self.assertNotIn("secret_user_text", json.dumps(ring.snapshot(3)))

    def test_empty_calls_are_ignored_and_timestamp_is_validated_when_enabled(self):
        ring = ExpressionObservationRing(enabled=True)
        self.assertFalse(ring.observe(None, 0))
        self.assertFalse(ring.observe("", 0))
        with self.assertRaises(ValueError):
            ring.observe("happy", UINT32_MAX + 1)

    def test_ring_is_bounded_and_new_records_overwrite_oldest(self):
        ring = ExpressionObservationRing(enabled=True)
        for index in range(CAPACITY + 3):
            ring.observe(f"semantic-{index}", index * 1000)
        snapshot = ring.snapshot((CAPACITY + 3) * 1000)
        self.assertEqual(len(snapshot["records"]), 24)
        self.assertEqual(snapshot["records_overwritten"], 3)
        self.assertEqual(snapshot["records"][0]["sequence"], 4)
        self.assertEqual(snapshot["records"][-1]["sequence"], 27)

    def test_sequence_limit_is_explicit_and_does_not_wrap(self):
        ring = ExpressionObservationRing(enabled=True, initial_sequence=MAX_SEQUENCE)
        ring.observe("happy", 1)
        self.assertFalse(ring.observe("sad", 1000))
        snapshot = ring.snapshot(1001)
        self.assertEqual([row["sequence"] for row in snapshot["records"]], [MAX_SEQUENCE])
        self.assertEqual(snapshot["records_dropped_sequence_exhausted"], 1)

    def test_repeat_count_saturates_without_creating_unbounded_records(self):
        ring = ExpressionObservationRing(enabled=True)
        for timestamp in range(65536):
            ring.observe("happy", 1)
        records = ring.snapshot(10)["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["repeat_count"], 65535)

    def test_clear_resets_evidence_but_does_not_rewind_sequence(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("happy", 1)
        ring.note_contention_drop(2)
        ring.clear()
        self.assertEqual(ring.snapshot(2)["records"], [])
        self.assertEqual(ring.snapshot(2)["records_dropped_contention"], 0)
        ring.observe("sad", 3)
        self.assertEqual(ring.snapshot(4)["records"][0]["sequence"], 2)

    def test_snapshot_satisfies_schema_and_limits_claim_to_call_site(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("shocked", 42)
        snapshot = ring.snapshot(50)
        VALIDATOR.validate(snapshot)
        self.assertEqual(snapshot["schema"], SCHEMA_ID)
        self.assertEqual(snapshot["claim_scope"], "set_emotion_call_only")
        self.assertEqual(snapshot["records"][0]["observation"], "set_emotion_call_entered")

    def test_schema_rejects_unbounded_or_sensitive_extension_fields(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("happy", 1)
        snapshot = ring.snapshot(2)
        snapshot["transcript"] = "must not be present"
        self.assertTrue(list(VALIDATOR.iter_errors(snapshot)))

    def test_schema_rejects_over_capacity_and_unrecognized_semantic_values(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("happy", 1)
        snapshot = ring.snapshot(2)
        snapshot["records"] = snapshot["records"] * 25
        self.assertTrue(list(VALIDATOR.iter_errors(snapshot)))
        snapshot = ring.snapshot(2)
        snapshot["records"][0]["semantic"] = "arbitrary-user-text"
        self.assertTrue(list(VALIDATOR.iter_errors(snapshot)))


if __name__ == "__main__":
    unittest.main()
