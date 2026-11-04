from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from protocol.expression_runtime_observation_e1_26 import (
    COALESCE_WINDOW_MS,
    ExpressionObservationRing,
    MAX_SEQUENCE,
    UINT32_MAX,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "protocol/expression-runtime-observation-e1-26.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


class ExpressionObservationE126Tests(unittest.TestCase):
    def test_disabled_is_noop(self):
        ring = ExpressionObservationRing()
        self.assertFalse(ring.observe("happy", 1))
        self.assertEqual(ring.snapshot(2)["records"], [])

    def test_contention_counter_is_exported_and_clearable(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.note_contention_drop(3)
        self.assertEqual(ring.snapshot(4)["records_dropped_contention"], 3)
        ring.clear()
        self.assertEqual(ring.snapshot(5)["records_dropped_contention"], 0)

    def test_contention_counter_uses_bounded_uint32_modulo_arithmetic(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.note_contention_drop(UINT32_MAX)
        ring.note_contention_drop(2)
        self.assertEqual(ring.snapshot(1)["records_dropped_contention"], 1)

    def test_wrap_and_coalesce_rules_match_firmware_contract(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("happy", UINT32_MAX - 100)
        ring.observe("happy", 149)
        self.assertEqual(len(ring.snapshot(150)["records"]), 1)
        ring.observe("sad", 500)
        ring.observe("happy", 600)
        records = ring.snapshot(700)["records"]
        self.assertEqual([row["semantic"] for row in records], ["happy", "sad", "happy"])
        self.assertEqual(records[0]["repeat_count"], 2)

    def test_unknown_input_is_collapsed_without_retaining_text(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("secret_user_text", 1)
        snapshot = ring.snapshot(2)
        self.assertEqual(snapshot["records"][0]["semantic"], "other")
        self.assertNotIn("secret_user_text", json.dumps(snapshot))

    def test_schema_and_sequence_limit(self):
        ring = ExpressionObservationRing(enabled=True, initial_sequence=MAX_SEQUENCE)
        ring.observe("shocked", 42)
        self.assertFalse(ring.observe("sad", 1000))
        snapshot = ring.snapshot(1001)
        VALIDATOR.validate(snapshot)
        self.assertEqual(snapshot["records_dropped_sequence_exhausted"], 1)

    def test_window_boundary(self):
        ring = ExpressionObservationRing(enabled=True)
        ring.observe("happy", 10)
        ring.observe("happy", 10 + COALESCE_WINDOW_MS)
        self.assertEqual(ring.snapshot(300)["records"][0]["repeat_count"], 2)


if __name__ == "__main__":
    unittest.main()
