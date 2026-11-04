from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/export-expression-e1-29.py"
SPEC = importlib.util.spec_from_file_location("e1_29_export", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
EXPORT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EXPORT
SPEC.loader.exec_module(EXPORT)


def capture(snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = {
            "ok": True, "read_only": True, "best_effort": True,
            "schema": "deskbot.expression.runtime_observation.e1_26/v1",
            "observation_point": "EmoteDisplay::SetEmotion",
            "claim_scope": "set_emotion_call_only", "captured_at_ms": 123456,
            "records_overwritten": 4, "records_dropped_contention": 2,
            "records_dropped_sequence_exhausted": 0,
            "records": [{"sequence": 10, "observation": "set_emotion_call_entered",
                         "semantic": "happy", "first_seen_ms": 4294967290,
                         "last_seen_ms": 12, "repeat_count": 3}],
        }
    response_text = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    return {
        "schema": "deskbot.expression.mcp_capture_input.e1_29/v1",
        "host_received_at_utc": "2026-10-28T04:05:06.123Z",
        "request_message": {
            "session_id": "private-session-must-not-leak",
            "type": "mcp", "payload": {
                "jsonrpc": "2.0", "id": 17, "method": "tools/call",
                "params": {"name": "self.expression.observation", "arguments": {}},
            },
        },
        "response_message": {
            "session_id": "private-session-must-not-leak",
            "type": "mcp", "payload": {
                "jsonrpc": "2.0", "id": 17,
                "result": {"content": [{"type": "text", "text": response_text}], "isError": False},
            },
        },
    }


class ExpressionE129CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture_schema = json.loads(EXPORT.INPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.snapshot_schema = json.loads(EXPORT.SNAPSHOT_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_valid_paired_capture_normalizes_without_rewriting_device_time(self):
        item = capture()
        Draft202012Validator(self.capture_schema).validate(item)
        raw = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        snapshot, provenance = EXPORT.normalize_capture(raw)
        Draft202012Validator(self.snapshot_schema).validate(snapshot)
        self.assertEqual(snapshot["captured_at_ms"], 123456)
        self.assertEqual(snapshot["records"][0]["first_seen_ms"], 4294967290)
        self.assertEqual(snapshot["records"][0]["last_seen_ms"], 12)
        self.assertEqual(snapshot["records"][0]["repeat_count"], 3)
        self.assertEqual(provenance["source"]["host_received_at_utc"], "2026-10-28T04:05:06.123Z")
        self.assertEqual(provenance["source"]["request_id"], 17)

    def test_rejects_wrong_tool_id_mismatch_error_and_busy_snapshot(self):
        bad = capture()
        bad["request_message"]["payload"]["params"]["name"] = "self.shell.list"
        with self.assertRaisesRegex(ValueError, "tool"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        bad = capture()
        bad["response_message"]["payload"]["id"] = 18
        with self.assertRaisesRegex(ValueError, "match"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        bad = capture()
        bad["response_message"]["payload"]["result"]["isError"] = True
        with self.assertRaisesRegex(ValueError, "error"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        busy = {"ok": False, "error": "busy", "retryable": True}
        with self.assertRaises(ValueError):
            EXPORT.normalize_capture(json.dumps(capture(busy)).encode())

    def test_rejects_duplicate_keys_invalid_timestamp_extra_content_and_bad_sequence(self):
        raw = json.dumps(capture(), separators=(",", ":")).encode()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            EXPORT.normalize_capture(raw.replace(b'"id":17', b'"id":17,"id":17', 1))
        bad = capture()
        bad["host_received_at_utc"] = "2026-10-28T04:05:06+08:00"
        with self.assertRaisesRegex(ValueError, "UTC"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        bad = capture()
        bad["host_received_at_utc"] = "2026-10-28T04:05Z"
        with self.assertRaisesRegex(ValueError, "RFC 3339"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        bad = capture()
        bad["request_message"]["session_id"] = "s" * 257
        with self.assertRaisesRegex(ValueError, "256"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        bad = capture()
        bad["response_message"]["payload"]["result"]["content"].append({"type": "text", "text": "{}"})
        with self.assertRaisesRegex(ValueError, "exactly one"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        invalid = capture()
        invalid["response_message"]["payload"]["result"]["content"][0]["text"] = (
            '{"ok":true,"read_only":true,"best_effort":true,"schema":"deskbot.expression.runtime_observation.e1_26/v1",'
            '"observation_point":"EmoteDisplay::SetEmotion","claim_scope":"set_emotion_call_only",'
            '"captured_at_ms":1,"records_overwritten":0,"records_dropped_contention":0,'
            '"records_dropped_sequence_exhausted":0,"records":[]}'
        )
        invalid["response_message"]["payload"]["result"]["content"][0]["text"] = (
            invalid["response_message"]["payload"]["result"]["content"][0]["text"].replace(
                '"records":[]', '"records":[{"sequence":2,"observation":"set_emotion_call_entered","semantic":"happy","first_seen_ms":1,"last_seen_ms":1,"repeat_count":1},{"sequence":1,"observation":"set_emotion_call_entered","semantic":"happy","first_seen_ms":2,"last_seen_ms":2,"repeat_count":1}]'
            )
        )
        with self.assertRaisesRegex(ValueError, "ascending"):
            EXPORT.normalize_capture(json.dumps(invalid).encode())

    def test_non_string_semantics_are_rejected_as_validation_errors(self):
        for semantic in (None, [], {}, 17):
            bad = capture()
            payload = json.loads(bad["response_message"]["payload"]["result"]["content"][0]["text"])
            payload["records"][0]["semantic"] = semantic
            bad["response_message"]["payload"]["result"]["content"][0]["text"] = json.dumps(payload)
            with self.subTest(semantic=semantic), self.assertRaisesRegex(ValueError, "allowlist"):
                EXPORT.normalize_capture(json.dumps(bad).encode())

    def test_schema_accepts_the_documented_envelopes_and_rejects_unknown_fields(self):
        valid = capture()
        Draft202012Validator(self.capture_schema).validate(valid)
        invalid = copy.deepcopy(valid)
        invalid["response_message"]["unexpected"] = True
        self.assertTrue(list(Draft202012Validator(self.capture_schema).iter_errors(invalid)))

    def test_rejects_cross_session_pair_and_numeric_boolean(self):
        bad = capture()
        bad["response_message"]["session_id"] = "another-session"
        with self.assertRaisesRegex(ValueError, "session_id must match"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        bad = capture()
        payload = json.loads(bad["response_message"]["payload"]["result"]["content"][0]["text"])
        payload["ok"] = 1
        bad["response_message"]["payload"]["result"]["content"][0]["text"] = json.dumps(payload)
        with self.assertRaisesRegex(ValueError, "fixed contract"):
            EXPORT.normalize_capture(json.dumps(bad).encode())

    def test_limits_and_empty_snapshot(self):
        valid = capture()
        payload = json.loads(valid["response_message"]["payload"]["result"]["content"][0]["text"])
        payload["records"] = []
        snapshot, provenance = EXPORT.normalize_capture(json.dumps(capture(payload)).encode())
        self.assertEqual(snapshot["records"], [])
        self.assertIsNone(provenance["snapshot"]["first_sequence"])
        for invalid in ("unknown-semantic", True, 9007199254740992):
            bad = capture()
            payload = json.loads(bad["response_message"]["payload"]["result"]["content"][0]["text"])
            key = "semantic" if isinstance(invalid, str) else "sequence"
            payload["records"][0][key] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                EXPORT.normalize_capture(json.dumps(capture(payload)).encode())

    def test_nested_duplicate_keys_and_oversized_snapshot_are_rejected(self):
        bad = capture()
        block = bad["response_message"]["payload"]["result"]["content"][0]
        block["text"] = block["text"].replace('"ok":true', '"ok":true,"ok":true')
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            EXPORT.normalize_capture(json.dumps(bad).encode())
        bad = capture()
        bad["response_message"]["payload"]["result"]["content"][0]["text"] = " " * 65537
        with self.assertRaisesRegex(ValueError, "exceeds"):
            EXPORT.normalize_capture(json.dumps(bad).encode())

    def test_provenance_does_not_copy_session_id_path_or_raw_response(self):
        raw = json.dumps(capture(), ensure_ascii=False).encode("utf-8")
        snapshot, provenance = EXPORT.normalize_capture(raw)
        serialized = json.dumps(provenance, ensure_ascii=False)
        self.assertNotIn("private-session", serialized)
        self.assertNotIn(str(ROOT), serialized)
        self.assertIs(provenance["privacy"]["raw_capture_copied_to_output"], False)
        self.assertIs(provenance["safety"]["device_written"], False)
        self.assertEqual(snapshot["records"][0]["semantic"], "happy")

    def test_export_creates_only_snapshot_and_provenance_under_tmp(self):
        raw = json.dumps(capture(), ensure_ascii=False).encode("utf-8")
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp:
            source = Path(temp) / "input-capture.json"
            source.write_bytes(raw)
            out = Path(temp) / "result"
            result = EXPORT.export_files(source, out)
            self.assertEqual(result["records"], 1)
            self.assertEqual({p.name for p in out.iterdir()}, {"observation-snapshot.json", "provenance.json"})
            self.assertNotEqual((out / "observation-snapshot.json").read_bytes(), raw)
            with self.assertRaisesRegex(ValueError, "already exists"):
                EXPORT.export_files(source, out)

    def test_rejects_output_outside_repository_tmp_and_oversized_capture(self):
        with self.assertRaisesRegex(ValueError, "tmp"):
            EXPORT._safe_output_directory(ROOT / "artifacts" / "bad-export")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            EXPORT.strict_json(b" " * (EXPORT.MAX_CAPTURE_BYTES + 1),
                               max_bytes=EXPORT.MAX_CAPTURE_BYTES, label="MCP capture")


if __name__ == "__main__":
    unittest.main()
