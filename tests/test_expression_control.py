from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from protocol import expression_control as P

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "artifacts/deskbot-studio-e2a-20261104/index.html").read_text(encoding="utf-8")
STUDIO = json.loads(re.search(r'<script id="studio-data" type="application/json">(.*?)</script>', HTML, re.S).group(1))
CATALOG = P.build_catalog(STUDIO)
CONTROL_SCHEMA = json.loads((ROOT / "protocol/deskbot-expression-control-v1.schema.json").read_text(encoding="utf-8"))
CATALOG_SCHEMA = json.loads((ROOT / "protocol/deskbot-expression-catalog-v1.schema.json").read_text(encoding="utf-8"))


def rehash_catalog(value):
    unsigned = {key: item for key, item in value.items() if key != "catalog_id"}
    value["catalog_id"] = hashlib.sha256(P.canonical(unsigned)).hexdigest()
    return value


def request(command=None, **changes):
    value = {
        "schema": P.SCHEMA, "message_type": "request", "request_id": "e2c-test-1",
        "target_id": "deskbot-studio-simulator", "boot_id": "sim-boot-1", "session_id": "sim-session-1",
        "sequence": 1, "catalog_id": CATALOG["catalog_id"], "role_id": CATALOG["role_id"],
        "issued_at": "2026-11-04T12:00:00.000Z", "expires_after_ms": 3000,
        "command": command or {"kind": "play_expression", "semantic_id": "happy"},
    }
    value.update(changes)
    return value


class CatalogTests(unittest.TestCase):
    def test_catalog_is_content_addressed_and_schema_valid(self):
        self.assertEqual(P.validate_catalog(CATALOG)["semantics"], 29)
        Draft202012Validator(CATALOG_SCHEMA).validate(CATALOG)
        self.assertEqual(CATALOG["source"], "saved_release_record")
        self.assertFalse(CATALOG["live"])
        self.assertEqual(CATALOG["revision"], "E1.18")

    def test_voice_owned_listen_is_not_a_play_request(self):
        with self.assertRaisesRegex(ValueError, "voice state"):
            P.validate_request(request({"kind": "play_expression", "semantic_id": "listen"}), CATALOG)

    def test_duplicate_semantics_and_action_values_rejected(self):
        changed = copy.deepcopy(CATALOG)
        changed["expressions"].append(copy.deepcopy(changed["expressions"][0]))
        rehash_catalog(changed)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            P.validate_catalog(changed)
        changed = copy.deepcopy(CATALOG)
        changed["expressions"][0]["implicit_action_id"] = []
        rehash_catalog(changed)
        with self.assertRaises(ValueError):
            P.validate_catalog(changed)
        changed = copy.deepcopy(CATALOG)
        changed["actions"][1]["wire_value"] = changed["actions"][0]["wire_value"]
        rehash_catalog(changed)
        with self.assertRaisesRegex(ValueError, "action"):
            P.validate_catalog(changed)

    def test_catalog_content_change_invalidates_its_id(self):
        changed = copy.deepcopy(CATALOG)
        changed["expressions"][0]["resource_id"] = "unreviewed.eaf"
        with self.assertRaisesRegex(ValueError, "digest"):
            P.validate_catalog(changed)


class RequestTests(unittest.TestCase):
    def test_expression_and_action_requests_match_the_schema(self):
        for value in (request(), request({"kind": "play_action", "action_id": "look_around"})):
            P.validate_request(value, CATALOG)
            Draft202012Validator(CONTROL_SCHEMA, format_checker=FormatChecker()).validate(value)

    def test_rejects_stale_role_catalog_and_sequence(self):
        for change in (
            {"role_id": "role-other"}, {"catalog_id": "0" * 64}, {"sequence": 0},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                P.validate_request(request(**change), CATALOG)

    def test_rejects_extra_fields_arbitrary_resources_and_unlisted_actions(self):
        changed = request()
        changed["command"]["resource_id"] = "anything.eaf"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            P.validate_request(changed, CATALOG)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            P.validate_request(request({"kind": "play_action", "action_id": "free_motion"}), CATALOG)
        with self.assertRaisesRegex(ValueError, "envelope"):
            P.validate_request(request(expires_after_ms=50), CATALOG)

    def test_bad_calendar_time_and_boolean_integer_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid date"):
            P.validate_request(request(issued_at="2026-99-99T12:00:00.000Z"), CATALOG)
        with self.assertRaisesRegex(ValueError, "envelope"):
            P.validate_request(request(sequence=True), CATALOG)


class ReceiptTests(unittest.TestCase):
    def receipt(self, req, **changes):
        value = {"schema": P.SCHEMA, "message_type": "receipt", "target_id": req["target_id"],
                 "boot_id": req["boot_id"], "session_id": req["session_id"], "sequence": 1,
                 "request_id": req["request_id"], "request_sequence": req["sequence"], "status": "accepted",
                 "reason": "queued_for_owner", "source": "simulator", "correlated": True,
                 "original_status": None, "at_ms": 0}
        value.update(changes)
        return value

    def test_receipt_matches_target_request_and_schema(self):
        req = P.validate_request(request(), CATALOG)
        value = self.receipt(req)
        P.validate_receipt(value, req)
        Draft202012Validator(CONTROL_SCHEMA).validate(value)

    def test_receipt_rejects_wrong_target_or_request_sequence(self):
        req = request()
        for change in ({"target_id": "other-device"}, {"request_sequence": 2}, {"request_id": "other-request"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "receipt"):
                P.validate_receipt(self.receipt(req, **change), req)

    def test_receipt_checks_the_current_receiver_boot_and_session(self):
        req = request()
        context = {key: req[key] for key in ("target_id", "boot_id", "session_id")}
        for field in ("boot_id", "session_id"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "receiver"):
                P.validate_receipt(self.receipt(req, **{field: "other-cycle"}), req, receiver=context)
        new_context = {**context, "session_id": "sim-session-2"}
        P.validate_receipt(self.receipt(req, session_id="sim-session-2"), req, receiver=new_context)

    def test_protocol_accepts_device_originated_receipts(self):
        req = request()
        value = self.receipt(req, source="device")
        P.validate_receipt(value, req)
        Draft202012Validator(CONTROL_SCHEMA).validate(value)

    def test_receipt_rejects_typed_invalid_statuses_and_reasons(self):
        req = request()
        for change in ({"status": []}, {"reason": None}, {"reason": "BadReason"}, {"at_ms": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                P.validate_receipt(self.receipt(req, **change), req)

    def test_duplicate_receipt_preserves_original_decision(self):
        req = request()
        value = self.receipt(req, status="duplicate", reason="replayed_receipt", original_status="busy")
        P.validate_receipt(value, req)
        with self.assertRaisesRegex(ValueError, "receipt"):
            P.validate_receipt(self.receipt(req, status="duplicate", reason="replayed_receipt"), req)

    def test_uncorrelated_base_fact_is_not_an_execution_receipt(self):
        value = {"schema": P.SCHEMA, "message_type": "base_fact", "target_id": "deskbot-studio-simulator",
                 "boot_id": "sim-boot-1", "session_id": "sim-session-1", "sequence": 1,
                 "fact": "base.action.completed", "request_id": None, "correlated": False,
                 "source": "simulator", "at_ms": 0}
        Draft202012Validator(CONTROL_SCHEMA).validate(value)
        value["source"] = "device"
        Draft202012Validator(CONTROL_SCHEMA).validate(value)
        value["request_id"] = "e2c-test-1"
        with self.assertRaises(ValidationError):
            Draft202012Validator(CONTROL_SCHEMA).validate(value)


if __name__ == "__main__":
    unittest.main()
