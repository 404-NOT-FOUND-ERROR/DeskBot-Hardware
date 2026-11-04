from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from protocol import expression_control as C
from protocol import studio_expression_receiver as R

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "artifacts/deskbot-studio-e2c-20261104/expression-catalog.json").read_bytes())
SCHEMA = Draft202012Validator(json.loads((ROOT / "protocol/deskbot-expression-control-v1.schema.json").read_bytes()))


def request(**changes):
    result = {
        "schema": C.SCHEMA, "message_type": "request", "request_id": "offline-r1",
        "target_id": "offline-device", "boot_id": "offline-boot-1", "session_id": "offline-session-1",
        "sequence": 1, "catalog_id": CATALOG["catalog_id"], "role_id": CATALOG["role_id"],
        "issued_at": "2026-11-04T00:00:00.000Z", "expires_after_ms": 3000,
        "command": dict(R.COMMAND),
    }
    result.update(changes)
    return result


def wire(value=None):
    return C.canonical(request() if value is None else value)


class FramingTests(unittest.TestCase):
    def test_complete_lf_crlf_and_empty_lines(self):
        frames = list(R.RequestFrames().feed(b"\n \r\n" + wire() + b"\r\n" + wire() + b"\n"))
        self.assertEqual([x.request for x in frames], [request(), request()])

    def test_every_single_split_position(self):
        raw = wire() + b"\n"
        for split in range(len(raw)):
            decoder = R.RequestFrames()
            with self.subTest(split=split):
                frames = list(decoder.feed(raw[:split])) + list(decoder.feed(raw[split:]))
                self.assertEqual([x.request for x in frames], [request()])
                self.assertEqual(decoder.buffered_bytes, 0)

    def test_bytewise_input_and_concatenated_requests(self):
        decoder, frames = R.RequestFrames(), []
        for byte in (wire() + b"\n") * 3:
            frames.extend(decoder.feed(bytes([byte])))
        self.assertEqual([x.request for x in frames], [request()] * 3)

    def test_exact_limit_is_accepted(self):
        raw = wire()
        padded = raw + b" " * (R.MAX_FRAME_BYTES - len(raw))
        self.assertEqual(list(R.RequestFrames().feed(padded + b"\n"))[0].request, request())
        self.assertEqual(R.decode_request(padded), request())

    def test_oversized_frame_discards_through_delimiter_and_recovers(self):
        decoder = R.RequestFrames()
        self.assertEqual(list(decoder.feed(b"x" * 100_000)), [])
        self.assertLessEqual(decoder.buffered_bytes, R.MAX_FRAME_BYTES)
        frames = list(decoder.feed(wire() + b"\n" + wire() + b"\n"))
        self.assertEqual(frames[0].error, "frame_too_large")
        self.assertEqual(frames[1].request, request())

    def test_disconnect_reset_drops_partial_and_overflow(self):
        for partial in (wire()[:40], b"x" * (R.MAX_FRAME_BYTES + 1)):
            decoder = R.RequestFrames()
            list(decoder.feed(partial))
            decoder.reset()
            self.assertEqual([x.request for x in decoder.feed(wire() + b"\n")], [request()])

    def test_invalid_json_utf8_logs_and_non_objects_recover(self):
        invalid = [b'{"unfinished":', b'\xff', b'ordinary console output', b'null', b'[]', b'1', b'"text"']
        for raw in invalid:
            with self.subTest(raw=raw):
                frames = list(R.RequestFrames().feed(raw + b"\n" + wire() + b"\n"))
                self.assertEqual(frames[0].error, "invalid_request")
                self.assertEqual(frames[1].request, request())

    def test_duplicate_keys_at_each_envelope_depth(self):
        raw = wire()
        for bad in (raw.replace(b'"sequence":1', b'"sequence":1,"sequence":2'),
                    raw.replace(b'"semantic_id":"neutral"', b'"semantic_id":"neutral","semantic_id":"happy"')):
            with self.subTest(raw=bad), self.assertRaisesRegex(ValueError, "duplicate_key"):
                R.decode_request(bad)

    def test_depth_bound_and_brackets_inside_quoted_strings(self):
        with self.assertRaisesRegex(ValueError, "json_too_deep"):
            R.decode_request(b'[' * 9 + b'0' + b']' * 9)
        with self.assertRaises(ValueError):
            R.decode_request(b'[' * 1000)
        self.assertEqual(R.decode_request(wire()), request())
        R._depth_check(json.dumps({"value": '\\"' + '[{]' * 200}).encode())

    def test_nonfinite_float_boolean_and_bad_date_rejected(self):
        for bad in (wire().replace(b'"sequence":1', b'"sequence":NaN'),
                    wire().replace(b'"sequence":1', b'"sequence":1e999'),
                    wire().replace(b'"sequence":1', b'"sequence":1.0'),
                    wire(request(sequence=True)), wire(request(issued_at="2026-02-30T00:00:00.000Z"))):
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                R.decode_request(bad)

    def test_empty_large_and_wrong_types_rejected(self):
        for bad in (b'', b' ' * 2049, '{}', None):
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                R.decode_request(bad)
        with self.assertRaises(TypeError):
            list(R.RequestFrames().feed('text'))

    def test_extra_keys_and_arbitrary_resource_paths_rejected(self):
        for change in ({"extra": True}, {"command": {**R.COMMAND, "resource_id": "../other.eaf"}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                R.decode_request(wire(request(**change)))


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.receiver = R.Receiver(CATALOG, target_id="offline-device", boot_id="offline-boot-1",
                                   clock=lambda: self.now)
        self.receiver.connect()

    def submit(self, value=None, *, ready=True):
        return self.receiver.submit(request() if value is None else value, owner_ready=ready)

    def test_receive_accepts_once_and_owner_dispatches_explicitly(self):
        accepted = self.submit()
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(self.receiver.snapshot()["dispatch_count"], 0)
        dispatched = self.receiver.dispatch(owner_ready=True)
        self.assertEqual(dispatched["status"], "dispatched")
        self.assertIsNone(self.receiver.dispatch(owner_ready=True))
        self.assertEqual(self.receiver.snapshot()["dispatch_count"], 1)

    def test_all_receipts_validate_and_are_simulated(self):
        records = [self.submit(), self.submit(), self.receiver.dispatch(owner_ready=True), self.submit()]
        for record in records:
            C.validate_receipt(record, request(), receiver=self.receiver.snapshot())
            SCHEMA.validate(record)
            self.assertEqual(record["source"], "simulator")
        self.assertFalse(self.receiver.snapshot()["hardware_accessed"])

    def test_duplicate_while_pending_and_after_dispatch(self):
        self.submit()
        self.assertEqual(self.submit()["original_status"], "accepted")
        self.receiver.dispatch(owner_ready=True)
        for _ in range(100):
            self.assertEqual(self.submit()["original_status"], "dispatched")
        self.assertEqual(self.receiver.snapshot()["dispatch_count"], 1)

    def test_same_id_changed_payload_rejected_and_original_preserved(self):
        self.submit()
        for change in ({"sequence": 2}, {"expires_after_ms": 500}, {"command": {"kind": "play_expression", "semantic_id": "happy"}}):
            self.assertEqual(self.submit(request(**change))["reason"], "request_id_conflict")
        self.assertEqual(self.submit()["original_status"], "accepted")

    def test_full_slot_returns_busy_without_dispatching_or_replacing(self):
        self.submit()
        result = self.submit(request(request_id="offline-r2", sequence=2))
        self.assertEqual(result["status"], "busy")
        state = self.receiver.snapshot()
        self.assertEqual(state["pending"]["request"]["request_id"], "offline-r1")
        self.assertEqual(state["dispatch_count"], 0)

    def test_owner_busy_is_remembered_without_later_autoexecution(self):
        self.assertEqual(self.submit(ready=False)["status"], "busy")
        self.assertEqual(self.submit()["original_status"], "busy")
        self.assertIsNone(self.receiver.dispatch(owner_ready=True))

    def test_owner_change_before_dispatch_interrupts(self):
        self.submit()
        receipt = self.receiver.dispatch(owner_ready=False)
        self.assertEqual(receipt["status"], "interrupted")
        self.assertEqual(receipt["reason"], "owner_changed_before_dispatch")
        self.assertEqual(self.receiver.snapshot()["dispatch_count"], 0)
        self.assertEqual(self.submit()["original_status"], "interrupted")

    def test_exact_monotonic_deadline_expires_before_owner_dispatch(self):
        self.submit()
        self.now = 3000
        self.assertEqual(self.receiver.dispatch(owner_ready=True)["status"], "expired")
        self.assertEqual(self.submit()["original_status"], "expired")
        self.assertEqual(self.receiver.snapshot()["dispatch_count"], 0)

    def test_dispatch_before_deadline_and_utc_only_for_display(self):
        value = request(issued_at="2000-01-01T00:00:00.000Z")
        self.now = 150
        self.submit(value)
        self.now = 3149
        self.assertEqual(self.receiver.dispatch(owner_ready=True)["status"], "dispatched")

    def test_owner_turn_uses_one_time_snapshot_for_decision_and_receipt(self):
        times, reads = iter((0, 2999)), []

        def clock():
            value = next(times)
            reads.append(value)
            return value

        receiver = R.Receiver(CATALOG, target_id="offline-device", boot_id="offline-boot-1", clock=clock)
        receiver.connect()
        accepted = receiver.submit(request(), owner_ready=True)
        dispatched = receiver.dispatch(owner_ready=True)
        self.assertEqual((accepted["status"], accepted["at_ms"]), ("accepted", 0))
        self.assertEqual((dispatched["status"], dispatched["at_ms"]), ("dispatched", 2999))
        self.assertEqual(reads, [0, 2999])

    def test_expiry_releases_slot_for_next_request(self):
        self.submit()
        self.now = 3000
        self.assertEqual(self.submit(request(request_id="offline-r2", sequence=2))["status"], "accepted")
        self.assertEqual(self.receiver.snapshot()["receipts"][-2]["status"], "expired")

    def test_wrong_target_boot_session_catalog_role_and_sequence(self):
        for field, value, reason in (("target_id", "other-device", "target_mismatch"),
                                     ("boot_id", "old-boot", "boot_changed"),
                                     ("session_id", "old-session", "session_changed"),
                                     ("catalog_id", "0" * 64, "catalog_or_role_changed"),
                                     ("role_id", "other-role", "catalog_or_role_changed")):
            with self.subTest(field=field):
                self.assertEqual(self.submit(request(**{field: value}))["reason"], reason)
        self.assertEqual(self.receiver.snapshot()["retained_decisions"], 0)
        self.submit()
        result = self.submit(request(request_id="offline-r2"))
        self.assertEqual(result["reason"], "sequence_not_increasing")

    def test_only_neutral_is_available_no_actions_or_other_semantics(self):
        for seq, command in enumerate((
            {"kind": "play_expression", "semantic_id": "happy"},
            {"kind": "play_expression", "semantic_id": "winking"},
            {"kind": "play_expression", "semantic_id": "listen"},
            {"kind": "play_expression", "semantic_id": "missing"},
            {"kind": "play_action", "action_id": "look_around"},
        ), 1):
            self.assertEqual(self.submit(request(request_id=f"offline-r{seq}", sequence=seq, command=command))["reason"],
                             "unsupported_expression_or_action")
        self.assertIsNone(self.receiver.snapshot()["pending"])

    def test_reconnect_preserves_decision_but_rejects_new_old_session_request(self):
        self.submit()
        self.receiver.dispatch(owner_ready=True)
        self.receiver.disconnect()
        self.assertIsNone(self.submit(request(request_id="offline-r2", sequence=2)))
        self.receiver.connect()
        replay = self.submit()
        self.assertEqual(replay["original_status"], "dispatched")
        self.assertEqual(replay["session_id"], "offline-session-2")
        self.assertEqual(self.submit(request(request_id="offline-r2", sequence=2))["reason"], "session_changed")
        fresh = request(request_id="offline-r2", sequence=2, session_id="offline-session-2")
        self.assertEqual(self.submit(fresh)["status"], "accepted")

    def test_already_accepted_request_can_complete_while_disconnected(self):
        self.submit()
        self.receiver.disconnect()
        self.assertEqual(self.receiver.dispatch(owner_ready=True)["status"], "dispatched")
        self.receiver.connect()
        self.assertEqual(self.submit()["original_status"], "dispatched")

    def test_reboot_discards_pending_and_rejects_previous_boot(self):
        self.submit()
        self.receiver.reboot("offline-boot-2")
        self.now = 0
        self.receiver.connect()
        self.assertIsNone(self.receiver.snapshot()["pending"])
        self.assertEqual(self.receiver.snapshot()["retained_decisions"], 0)
        self.assertEqual(self.submit()["reason"], "boot_changed")
        self.assertEqual(self.submit(request(boot_id="offline-boot-2"))["status"], "accepted")

    def test_decision_and_history_bounds_match_e2c(self):
        for sequence in range(1, 65):
            value = request(request_id=f"offline-r{sequence}", sequence=sequence)
            self.assertEqual(self.submit(value)["status"], "accepted")
            self.receiver.dispatch(owner_ready=True)
        snapshot = self.receiver.snapshot()
        self.assertEqual(snapshot["retained_decisions"], 64)
        self.assertEqual(len(snapshot["receipts"]), 24)
        self.assertEqual(self.submit(request(request_id="offline-r65", sequence=65))["reason"], "idempotency_window_full")
        self.assertEqual(self.submit()["original_status"], "dispatched")
        self.assertEqual(self.receiver.snapshot()["dispatch_count"], 64)

    def test_invalid_clock_and_counter_bounds_leave_request_undispatched(self):
        self.now = 10
        self.submit()
        for invalid in (9, -1, True, 11.5, R.MAX_COUNTER + 1):
            self.now = invalid
            with self.subTest(now=invalid), self.assertRaises(ValueError):
                self.receiver.dispatch(owner_ready=True)
        self.now = 11
        self.assertEqual(self.receiver.snapshot()["dispatch_count"], 0)
        self.assertEqual(self.receiver.dispatch(owner_ready=True)["status"], "dispatched")

    def test_deadline_overflow_rejected(self):
        self.now = R.MAX_COUNTER - 20
        self.assertEqual(self.submit()["reason"], "deadline_out_of_range")

    def test_inputs_snapshots_and_receipts_are_copied(self):
        value = request()
        receipt = self.submit(value)
        value["command"]["semantic_id"] = "happy"
        receipt["status"] = "dispatched"
        snapshot = self.receiver.snapshot()
        snapshot["pending"]["request"]["command"]["semantic_id"] = "happy"
        self.assertEqual(self.receiver.snapshot()["pending"]["request"]["command"], R.COMMAND)
        self.assertEqual(self.submit()["original_status"], "accepted")

    def test_explicit_typed_owner_snapshot_and_new_boot_required(self):
        for value in (None, 1, "idle"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.receiver.submit(request(), owner_ready=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.receiver.dispatch(owner_ready=value)
        with self.assertRaises(ValueError):
            self.receiver.reboot("offline-boot-1")

    def test_mapping_side_effect_and_integrity_changes_rejected(self):
        for field, value in (("resource_id", "Happy.eaf"), ("implicit_action_id", "cat_nuzzle")):
            changed = deepcopy(CATALOG)
            next(row for row in changed["expressions"] if row["semantic_id"] == "neutral")[field] = value
            unsigned = {k: v for k, v in changed.items() if k != "catalog_id"}
            changed["catalog_id"] = hashlib.sha256(C.canonical(unsigned)).hexdigest()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "neutral_mapping_changed"):
                R.Receiver(changed, target_id="offline-device", boot_id="offline-boot-1", clock=lambda: 0)
        changed = deepcopy(CATALOG)
        changed["catalog_id"] = "0" * 64
        with self.assertRaises(ValueError):
            R.Receiver(changed, target_id="offline-device", boot_id="offline-boot-1", clock=lambda: 0)

    def test_fragmented_receive_to_explicit_owner_turn_end_to_end(self):
        decoder, frames = R.RequestFrames(), []
        raw = wire() + b"\n"
        for start in range(0, len(raw), 7):
            frames.extend(decoder.feed(raw[start:start + 7]))
        self.assertEqual(len(frames), 1)
        self.assertEqual(self.submit(frames[0].request)["status"], "accepted")
        self.assertEqual(self.receiver.dispatch(owner_ready=True)["status"], "dispatched")
        self.assertEqual(self.submit(frames[0].request)["original_status"], "dispatched")
        self.assertEqual(self.receiver.snapshot()["dispatch_count"], 1)
