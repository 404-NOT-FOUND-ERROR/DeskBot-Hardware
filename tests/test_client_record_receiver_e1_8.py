from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from protocol.client_record_receiver import CLOCK_DOMAIN, ClientRecordReceiver
from protocol.client_transport_contract import make_record, review_request_to_command, review_result_to_ack
from protocol.shell_binding_workflow import ShellBindingWorkflow


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "protocol/deskbot-role-binding-registry-e1-5.example.json"
WHEN = "2026-10-22T01:00:00Z"


def body(message_type: str, sequence: int, *, high: int = 0, **identity) -> dict:
    message = {
        "schema": "deskbot.body.v1", "message_type": message_type,
        "message_id": f"body-{message_type}-{sequence}", "device_id": "offline-replay:demo-device-a",
        "boot_id": "boot-e1-8", "session_id": "session-e1-8", "sequence": sequence,
        "occurred_at": WHEN, **identity,
    }
    if message_type == "hello":
        message["payload"] = {"kind": "hello", "firmware_id": "synthetic-e1-8",
                              "capabilities": ["base_events", "magnetic_sample", "shell_observation"]}
    elif message_type == "snapshot":
        message["payload"] = {
            "kind": "snapshot",
            "base": {"link": "online", "mechanical_status": "ready", "last_event_sequence": high},
            "event_cursor": {"oldest_available_sequence": 1 if high else None,
                             "high_watermark_sequence": high, "gap_detected": False},
            "magnetic": {"state": "fresh", "sensor_kind": "BMM150", "x": 190, "y": -540,
                         "z": -940, "sample_at": WHEN, "age_ms": 0},
            "shell_observation": {"status": "known", "profile_id": "offline:demo-layout-a:profile-0001",
                                  "profile_revision": 1, "sensor_kind": "BMM150", "sample_count": 8,
                                  "similarity": 0.1, "margin": 0.3, "observed_at": WHEN},
        }
    else:
        message["payload"] = {"kind": "event", "event_type": "base.action.completed",
                              "event_sequence": high, "facts": {}}
    return message


class ClientRecordReceiverE18Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = "adapter-a"
        self.workflow = ShellBindingWorkflow(REGISTRY, choices={"shell_a": REGISTRY}, root=ROOT)
        self.receiver = ClientRecordReceiver(clock_domain=CLOCK_DOMAIN)
        self.receiver.connect(self.adapter, now_ms=0)
        self.outer_sequence = 0

    def record(self, kind: str, payload: dict, *, sequence: int | None = None,
               record_id: str | None = None, stream: str | None = None) -> dict:
        if sequence is None:
            self.outer_sequence += 1
            sequence = self.outer_sequence
        return make_record(record_id=record_id or f"record-{sequence}",
                           stream_id=stream or self.adapter, sequence=sequence,
                           created_at=WHEN, record_type=kind, payload=payload)

    def send(self, kind: str, payload: dict, now: int, **args):
        return self.receiver.consume(self.record(kind, payload, **args), now_ms=now)

    def send_body(self, message: dict, now: int, **args):
        self.workflow.consume(message, now_ms=now)
        return self.send("body_fact", message, now, **args)

    def publish_states(self, now: int, *, order=("role_state", "review_state")) -> None:
        self.workflow.tick(now)
        for kind in order:
            payload = (self.workflow.state["resolution"] if kind == "role_state" else
                       self.workflow.client_review_state(adapter_instance_id=self.adapter))
            self.assertEqual(self.send(kind, payload, now).status, "accepted")

    def activate(self) -> None:
        self.assertEqual(self.send_body(body("hello", 10), 0).status, "accepted")
        self.assertEqual(self.send_body(body("snapshot", 20), 100).status, "accepted")
        self.publish_states(100)
        self.assertIsNotNone(self.receiver.state["selection"])

    def test_full_state_pair_is_required_and_all_three_sequences_are_independent(self) -> None:
        self.send_body(body("hello", 10), 0)
        self.send_body(body("snapshot", 20, high=70), 100)
        self.send("role_state", self.workflow.state["resolution"], 100)
        self.assertIsNone(self.receiver.state["selection"])
        self.assertTrue(self.receiver.state["needs_review_state"])
        self.send("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter), 100)
        state = self.receiver.state
        self.assertEqual(state["last_seen_sequence"], 4)
        self.assertEqual(state["body"]["last_transport_sequence"], 20)
        self.assertEqual(state["body"]["last_event_sequence"], 70)
        self.assertIsNotNone(state["selection"])
        self.assertFalse(state["needs_resync"])

    def test_review_first_pair_also_works(self) -> None:
        self.send_body(body("hello", 10), 0)
        self.send_body(body("snapshot", 20), 100)
        self.publish_states(100, order=("review_state", "role_state"))
        self.assertIsNotNone(self.receiver.state["selection"])

    def test_exact_duplicate_is_idempotent_and_does_not_extend_lease(self) -> None:
        self.activate()
        record = self.record("role_state", self.workflow.state["resolution"])
        self.assertTrue(self.receiver.consume(record, now_ms=200).applied)
        deadline = self.receiver.state["role_state"]["valid_until_ms"]
        self.assertEqual(self.receiver.consume(deepcopy(record), now_ms=500).status, "duplicate")
        self.assertEqual(self.receiver.state["role_state"]["valid_until_ms"], deadline)
        self.assertEqual(self.receiver.consume(record, now_ms=deadline).status, "duplicate")
        self.assertIsNone(self.receiver.state["selection"])

    def test_same_record_id_different_content_clears_selection(self) -> None:
        self.activate()
        original = self.record("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter))
        self.receiver.consume(original, now_ms=100)
        conflict = deepcopy(original)
        conflict["payload"]["audit_entry_count"] += 1
        result = self.receiver.consume(conflict, now_ms=100)
        self.assertEqual(result.reason, "record_id_conflict")
        self.assertIsNone(self.receiver.state["selection"])
        self.assertTrue(self.receiver.state["needs_body_snapshot"])

    def test_older_or_reused_sequence_with_new_id_is_rejected(self) -> None:
        self.activate()
        before = self.receiver.state
        stale = self.record("role_state", self.workflow.state["resolution"],
                            sequence=2, record_id="late-record")
        self.assertEqual(self.receiver.consume(stale, now_ms=100).reason, "sequence_not_increasing")
        self.assertEqual(before, self.receiver.state)

    def test_gap_clears_immediately_and_late_fill_cannot_restore(self) -> None:
        self.activate()
        lost = self.record("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter))
        later = self.record("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter))
        result = self.receiver.consume(later, now_ms=100)
        self.assertEqual(result.status, "needs_resync")
        self.assertIsNone(self.receiver.state["selection"])
        self.assertEqual(self.receiver.resync_request()["reason"], "stream_gap")
        self.assertEqual(self.receiver.consume(lost, now_ms=100).status, "rejected")
        self.assertIsNone(self.receiver.state["selection"])

    def test_gap_requires_new_body_snapshot_before_resolved_role(self) -> None:
        self.activate()
        self.outer_sequence += 1
        self.send("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter), 100)
        old = self.send("role_state", self.workflow.state["resolution"], 100)
        self.assertEqual(old.reason, "body_snapshot_required_for_role")
        self.send_body(body("snapshot", 21), 200)
        self.publish_states(200)
        self.assertIsNotNone(self.receiver.state["selection"])

    def test_gap_can_resync_to_full_unresolved_state_without_body(self) -> None:
        self.activate()
        self.outer_sequence += 1
        self.workflow.disconnect(now_ms=200)
        self.send("role_state", self.workflow.state["resolution"], 200)
        self.send("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter), 200)
        self.assertFalse(self.receiver.state["needs_resync"])
        self.assertTrue(self.receiver.state["needs_body_snapshot"])
        self.assertIsNone(self.receiver.state["selection"])

    def test_event_revokes_role_until_new_snapshot_not_only_new_role_record(self) -> None:
        self.activate()
        selected = self.workflow.state["resolution"]
        self.send_body(body("event", 30, high=1), 200)
        self.assertIsNone(self.receiver.state["selection"])
        self.assertEqual(self.send("role_state", selected, 200).reason, "body_snapshot_required_for_role")
        self.send_body(body("snapshot", 40, high=1), 300)
        self.publish_states(300)
        self.assertEqual(self.receiver.state["body"]["last_event_sequence"], 1)
        self.assertIsNotNone(self.receiver.state["selection"])

    def test_inner_event_gap_uses_existing_body_consumer(self) -> None:
        self.activate()
        result = self.send_body(body("event", 30, high=3), 200)
        self.assertEqual(result.reason, "body_event_sequence_gap")
        self.assertTrue(self.receiver.state["needs_body_snapshot"])
        self.assertIsNone(self.receiver.state["selection"])

    def test_inner_duplicate_does_not_reapply_snapshot(self) -> None:
        self.activate()
        before = self.receiver.state["selection"]
        result = self.send("body_fact", body("snapshot", 20), 200)
        self.assertEqual(result.body_outcome["status"], "duplicate")
        self.assertEqual(before, self.receiver.state["selection"])

    def test_disconnect_clears_and_reconnect_preserves_watermark(self) -> None:
        self.activate()
        watermark = self.receiver.state["last_seen_sequence"]
        self.receiver.disconnect(now_ms=200)
        self.assertIsNone(self.receiver.state["selection"])
        self.assertIsNone(self.receiver.resync_request())
        record = self.record("role_state", self.workflow.state["resolution"])
        self.assertEqual(self.receiver.consume(record, now_ms=200).reason, "connection_required")
        self.receiver.connect(self.adapter, now_ms=300)
        self.assertEqual(self.receiver.state["last_seen_sequence"], watermark)
        self.assertEqual(self.receiver.resync_request()["reason"], "client_reconnect")
        # Reconnect requires a NEW hello, not a duplicate or cached snapshot.
        self.assertEqual(self.send("body_fact", body("snapshot", 20), 300).reason, "body_hello_required")
        self.send_body(body("hello", 50, session_id="session-new"), 300)
        self.send_body(body("snapshot", 60, session_id="session-new"), 400)
        self.publish_states(400)
        self.assertIsNotNone(self.receiver.state["selection"])

    def test_duplicate_hello_cannot_satisfy_reconnect(self) -> None:
        self.activate()
        self.receiver.disconnect(now_ms=200)
        self.receiver.connect(self.adapter, now_ms=200)
        duplicate = self.send("body_fact", body("hello", 10), 200)
        self.assertEqual(duplicate.body_outcome["status"], "duplicate")
        self.assertTrue(self.receiver.state["needs_body_hello"])

    def test_explicit_instance_change_resets_stream_and_rejects_retired_packets(self) -> None:
        self.activate()
        self.receiver.connect("adapter-b", now_ms=200)
        self.assertIsNone(self.receiver.state["selection"])
        self.assertEqual(self.receiver.state["last_seen_sequence"], 0)
        self.assertEqual(self.receiver.resync_request()["reason"], "adapter_restart")
        prior = self.receiver.state
        record = self.record("role_state", self.workflow.state["resolution"])
        self.assertEqual(self.receiver.consume(record, now_ms=200).reason, "stream_id_mismatch")
        self.assertEqual(self.receiver.state, prior)
        with self.assertRaisesRegex(ValueError, "retired"):
            self.receiver.connect("adapter-a", now_ms=200)
        self.adapter, self.outer_sequence = "adapter-b", 0
        self.workflow = ShellBindingWorkflow(REGISTRY, choices={}, root=ROOT)
        self.send_body(body("hello", 10), 200)
        self.send_body(body("snapshot", 20), 300)
        self.publish_states(300)
        self.assertIsNotNone(self.receiver.state["selection"])

    def test_inbound_packet_cannot_select_or_switch_adapter(self) -> None:
        self.activate()
        record = self.record("role_state", self.workflow.state["resolution"], stream="adapter-forged")
        prior = self.receiver.state
        self.assertEqual(self.receiver.consume(record, now_ms=100).reason, "stream_id_mismatch")
        self.assertEqual(self.receiver.state, prior)

    def test_review_payload_instance_must_match_stream(self) -> None:
        self.activate()
        review = self.workflow.client_review_state(adapter_instance_id="adapter-wrong")
        self.assertEqual(self.send("review_state", review, 100).reason, "review_adapter_instance_mismatch")
        self.assertIsNone(self.receiver.state["selection"])

    def test_new_boot_clears_and_does_not_accept_delayed_previous_boot(self) -> None:
        self.activate()
        self.send_body(body("hello", 0, boot_id="boot-new"), 200)
        self.assertIsNone(self.receiver.state["selection"])
        self.send_body(body("snapshot", 1, boot_id="boot-new"), 300)
        self.publish_states(300)
        self.assertIsNotNone(self.receiver.state["selection"])
        result = self.send("body_fact", body("hello", 100, message_id="late-old-boot"), 300)
        self.assertEqual(result.reason, "body_retired_boot_id")
        self.assertIsNone(self.receiver.state["selection"])

    def test_same_clock_domain_is_explicit_and_clocks_never_rewind(self) -> None:
        for domain in ("utc", "client_monotonic", None):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                ClientRecordReceiver(clock_domain=domain)
        self.activate()
        prior = self.receiver.state
        with self.assertRaises(ValueError):
            self.receiver.tick(99)
        self.assertEqual(prior, self.receiver.state)
        for value in (True, 0.5, -1, 9_007_199_254_740_992):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.receiver.tick(value)

    def test_future_or_regressing_producer_clock_is_rejected(self) -> None:
        self.activate()
        for clock in (99, 101):
            role = self.workflow.state["resolution"]
            role["clock_ms"] = clock
            self.assertEqual(self.send("role_state", role, 100).reason,
                             "producer_clock_outside_shared_domain")
            self.assertIsNone(self.receiver.state["selection"])

    def test_expired_role_and_duplicate_cannot_resurrect(self) -> None:
        self.activate()
        role = self.workflow.state["resolution"]
        record = self.record("role_state", role)
        self.receiver.consume(record, now_ms=100)
        self.receiver.tick(5100)
        self.assertIsNone(self.receiver.state["selection"])
        self.assertEqual(self.receiver.consume(record, now_ms=5100).status, "duplicate")
        self.assertEqual(self.send("role_state", role, 5100).reason, "role_expired_or_invalid_lease")

    def test_old_observation_cannot_extend_its_original_lease(self) -> None:
        self.activate()
        role = self.workflow.state["resolution"]
        role.update(clock_ms=200, valid_until_ms=5200)
        result = self.send("role_state", role, 200)
        self.assertEqual(result.reason, "observation_lease_extended")
        self.assertIsNone(self.receiver.state["selection"])

    def test_role_must_match_snapshot_identity_sequence_shell_and_readiness(self) -> None:
        mutators = (
            lambda row: row["evidence"].update(device_id="wrong-device"),
            lambda row: row["evidence"].update(last_event_sequence=5),
            lambda row: row["evidence"]["snapshot"].update(transport_sequence=19),
            lambda row: row["selection"].update(magnet_profile_id="wrong-profile"),
            lambda row: row["shell_observation"].update(profile_revision=2),
        )
        for mutate in mutators:
            with self.subTest(mutate=mutate):
                self.setUp()
                self.activate()
                role = self.workflow.state["resolution"]
                mutate(role)
                self.assertEqual(self.send("role_state", role, 100).status, "rejected")
                self.assertIsNone(self.receiver.state["selection"])

    def test_body_offline_or_unknown_never_yields_a_selection(self) -> None:
        for component, field, value in (("base", "link", "offline"),
                                        ("base", "mechanical_status", "homing"),
                                        ("shell_observation", "status", "unknown")):
            with self.subTest(component=component, field=field):
                self.setUp()
                self.activate()
                snapshot = body("snapshot", 21)
                snapshot["payload"][component][field] = value
                if component == "shell_observation":
                    snapshot["payload"][component]["profile_id"] = None
                self.send_body(snapshot, 200)
                self.publish_states(200)
                self.assertIsNone(self.receiver.state["selection"])

    def test_schema_invalid_partial_and_nonfinite_records_fail_closed(self) -> None:
        mutations = (
            lambda row: row.update(schema="deskbot.client_transport_record/v2"),
            lambda row: row["payload"].pop("evidence"),
            lambda row: row["payload"]["safety"].update(hardware_io=True),
            lambda row: row.update(created_at="not-a-date"),
            lambda row: row["payload"]["shell_observation"].update(similarity=float("nan")),
            lambda row: row["payload"]["shell_observation"].update(similarity=float("inf")),
            lambda row: row.update(sequence=True),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.setUp()
                self.activate()
                invalid = self.record("role_state", self.workflow.state["resolution"])
                mutate(invalid)
                self.assertEqual(self.receiver.consume(invalid, now_ms=100).reason, "invalid_record")
                self.assertIsNone(self.receiver.state["selection"])

    def test_full_cleared_role_cannot_keep_selection(self) -> None:
        self.activate()
        role = self.workflow.state["resolution"]
        role.update(status="unresolved", valid_until_ms=None)
        self.assertEqual(self.send("role_state", role, 100).reason, "invalid_record")
        self.assertIsNone(self.receiver.state["selection"])

    def test_timestamp_formats_are_enforced_without_optional_format_packages(self) -> None:
        with patch.dict("jsonschema.FormatChecker.checkers", {}, clear=True):
            self.receiver = ClientRecordReceiver(clock_domain=CLOCK_DOMAIN)
        self.receiver.connect(self.adapter, now_ms=0)
        self.activate()
        for stamp in (WHEN, "2026-10-22t01:00:00z", "2026-10-22T09:00:00.001+08:00"):
            with self.subTest(stamp=stamp):
                valid = self.record("review_state", self.workflow.client_review_state(
                    adapter_instance_id=self.adapter))
                valid["created_at"] = stamp
                self.assertEqual(self.receiver.consume(valid, now_ms=100).status, "accepted")
        for stamp in ("not-a-date", "2026-02-30T01:00:00Z", "2026-10-22T01:00:00",
                      "2026-10-22 01:00:00Z", "2026-10-22T01:00:00+00:99"):
            with self.subTest(stamp=stamp):
                invalid = self.record("review_state", self.workflow.client_review_state(
                    adapter_instance_id=self.adapter))
                invalid["created_at"] = stamp
                self.assertEqual(self.receiver.consume(invalid, now_ms=100).reason, "invalid_record")

    def test_nested_timestamps_are_also_checked_without_network_access(self) -> None:
        self.activate()
        for key in ("occurred_at", "sample_at", "observed_at"):
            with self.subTest(key=key):
                invalid = body("snapshot", 21)
                if key == "occurred_at":
                    invalid[key] = "not-a-date"
                else:
                    component = "magnetic" if key == "sample_at" else "shell_observation"
                    invalid["payload"][component][key] = "not-a-date"
                with patch.object(socket, "socket", side_effect=AssertionError("network prohibited")):
                    self.assertEqual(self.send("body_fact", invalid, 100).reason, "invalid_record")

    def test_wrong_direction_requests_are_never_applied(self) -> None:
        self.activate()
        for kind, payload in (("review_request", {
                "schema": "deskbot.client_review_request/v1", "request_id": "request-a",
                "target_adapter_instance_id": self.adapter, "operation": "begin_rebind",
                "expected_registry_revision": 0}), ("resync_request", {
                "schema": "deskbot.client_resync_request/v1", "target_adapter_instance_id": self.adapter,
                "known_stream_id": self.adapter, "last_seen_sequence": 4, "reason": "manual"})):
            prior = self.receiver.state
            self.assertEqual(self.send(kind, payload, 100).reason, "wrong_direction")
            self.assertEqual(prior, self.receiver.state)

    def test_ack_cannot_select_role_or_install_revision(self) -> None:
        self.activate()
        ack = {"schema": "deskbot.client_review_ack/v1", "request_id": "unbind-1",
               "result": "accepted", "reason": "unbind", "registry_revision": 1}
        self.send("review_ack", ack, 200)
        self.assertEqual(self.receiver.state["registry_revision"], 0)
        self.assertIsNone(self.receiver.state["selection"])
        duplicate = {**ack, "result": "duplicate", "original_result": "accepted"}
        self.send("review_ack", duplicate, 200)
        self.assertEqual(self.receiver.state["review_acks"][-1]["original_result"], "accepted")
        self.assertIsNone(self.receiver.state["selection"])

    def test_duplicate_rejected_ack_keeps_rejected_decision_and_old_revision(self) -> None:
        self.activate()
        rejected = {"schema": "deskbot.client_review_ack/v1", "request_id": "reject-1",
                    "result": "rejected", "reason": "registry_revision_conflict", "registry_revision": 0}
        self.send("review_ack", rejected, 100)
        self.send("review_ack", {**rejected, "result": "duplicate", "original_result": "rejected"}, 100)
        self.assertEqual(self.receiver.state["review_acks"][-1]["original_result"], "rejected")
        self.assertIsNotNone(self.receiver.state["selection"])
        conflict = {**rejected, "result": "duplicate", "original_result": "accepted"}
        self.assertEqual(self.send("review_ack", conflict, 100).reason, "review_ack_conflict")
        self.assertIsNone(self.receiver.state["selection"])

    def test_newer_ack_prevents_old_registry_from_resurrecting_a_role(self) -> None:
        self.activate()
        self.send("review_ack", {"schema": "deskbot.client_review_ack/v1", "request_id": "newer",
                                "result": "accepted", "reason": "unbind", "registry_revision": 1}, 200)
        self.send_body(body("snapshot", 21), 300)
        self.send("role_state", self.workflow.state["resolution"], 300)
        result = self.send("review_state", self.workflow.client_review_state(
            adapter_instance_id=self.adapter), 300)
        self.assertEqual(result.reason, "registry_revision_regressed")
        self.assertIsNone(self.receiver.state["selection"])
        self.assertEqual(self.receiver.state["registry_revision"], 0)

    def test_ack_before_initial_state_sets_a_revision_floor_not_a_registry(self) -> None:
        self.send("review_ack", {"schema": "deskbot.client_review_ack/v1", "request_id": "newer",
                                "result": "rejected", "reason": "revision_conflict",
                                "registry_revision": 5}, 0)
        self.assertIsNone(self.receiver.state["registry_revision"])
        result = self.send("review_state", self.workflow.client_review_state(
            adapter_instance_id=self.adapter), 0)
        self.assertEqual(result.reason, "registry_revision_regressed")
        self.assertIsNone(self.receiver.state["selection"])

    def test_registry_change_requires_new_snapshot_and_current_complete_states(self) -> None:
        self.activate()
        self.workflow.apply({"schema": "deskbot.shell_review_command/v1", "command_id": "unbind-1",
                             "operation": "unbind", "actor": {"kind": "human", "id": "reviewer-a"},
                             "binding_id": "demo-shell-a-frog-chef", "expected_registry_revision": 0}, now_ms=200)
        self.send("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter), 200)
        self.assertIsNone(self.receiver.state["selection"])
        self.assertTrue(self.receiver.state["needs_body_snapshot"])
        self.send_body(body("snapshot", 21), 300)
        self.publish_states(300)
        self.assertEqual(self.receiver.state["registry_revision"], 1)
        self.assertIsNone(self.receiver.state["selection"])
        review = self.workflow.client_review_state(adapter_instance_id=self.adapter)
        review["registry_revision"] = 0
        self.assertEqual(self.send("review_state", review, 300).reason, "registry_revision_regressed")

    def test_expired_pending_review_is_not_displayed_as_actionable(self) -> None:
        self.send_body(body("hello", 10), 0)
        snapshot = body("snapshot", 20)
        snapshot["payload"]["shell_observation"].update(status="unknown", profile_id=None, profile_revision=0)
        self.send_body(snapshot, 100)
        self.publish_states(100)
        review = self.receiver.state["review_state"]
        self.assertIsNotNone(review["pending_case"])
        self.receiver.tick(review["pending_case"]["valid_until_ms"])
        self.assertIsNone(self.receiver.state["review_state"])
        self.assertTrue(self.receiver.state["needs_resync"])

    def test_resync_payload_matches_contract_and_has_no_hardware_command(self) -> None:
        payload = self.receiver.resync_request()
        record = make_record(record_id="resync-a", stream_id="client-a", sequence=1,
                             created_at=WHEN, record_type="resync_request", payload=payload)
        self.receiver._validate.validate(record)
        self.assertEqual(payload["last_seen_sequence"], 0)
        self.assertEqual(set(payload), {"schema", "target_adapter_instance_id", "known_stream_id",
                                       "last_seen_sequence", "reason"})

    def test_memory_is_bounded_and_evicted_duplicate_cannot_reapply(self) -> None:
        self.receiver = ClientRecordReceiver(clock_domain=CLOCK_DOMAIN, max_record_ids=2, max_acks=2)
        self.receiver.connect(self.adapter, now_ms=0)
        self.activate()
        old = self.record("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter))
        self.receiver.consume(old, now_ms=100)
        for index in range(5):
            self.send("review_ack", {"schema": "deskbot.client_review_ack/v1", "request_id": f"request-{index}",
                                     "result": "rejected", "reason": "no_case", "registry_revision": 0}, 100)
        self.assertEqual(self.receiver.state["cache_counts"]["record_ids"], 2)
        self.assertEqual(self.receiver.state["cache_counts"]["acks"], 2)
        self.assertEqual(self.receiver.consume(old, now_ms=100).reason, "sequence_not_increasing")

    def test_retired_stream_capacity_fails_without_forgetting_old_streams(self) -> None:
        receiver = ClientRecordReceiver(clock_domain=CLOCK_DOMAIN, max_retired_streams=1)
        receiver.connect("adapter-a", now_ms=0)
        receiver.connect("adapter-b", now_ms=0)
        before = receiver.state
        with self.assertRaisesRegex(ValueError, "capacity"):
            receiver.connect("adapter-c", now_ms=0)
        self.assertEqual(before, receiver.state)

    def test_input_and_public_state_are_defensive_copies(self) -> None:
        self.activate()
        record = self.record("review_state", self.workflow.client_review_state(adapter_instance_id=self.adapter))
        self.receiver.consume(record, now_ms=100)
        record["payload"]["choices"].clear()
        state = self.receiver.state
        state["selection"]["display_name"] = "edited"
        state["role_state"]["selection"]["expression_map"].clear()
        state["body"]["latest_snapshot"]["base"]["link"] = "offline"
        self.assertNotEqual(self.receiver.state["selection"]["display_name"], "edited")
        self.assertTrue(self.receiver.state["selection"]["expression_map"])
        self.assertTrue(self.receiver.state["review_state"]["choices"])
        self.assertEqual(self.receiver.state["body"]["latest_snapshot"]["base"]["link"], "online")

    def test_unknown_case_manual_request_ack_and_new_snapshot_use_real_workflow(self) -> None:
        self.workflow = ShellBindingWorkflow(REGISTRY, choices={"shell_a": REGISTRY}, root=ROOT)
        self.send_body(body("hello", 10), 0)
        unbind = {"schema": "deskbot.client_review_request/v1", "request_id": "request-unbind",
                  "target_adapter_instance_id": self.adapter, "operation": "unbind",
                  "binding_id": "demo-shell-a-frog-chef", "expected_registry_revision": 0}
        command = review_request_to_command(unbind, adapter_instance_id=self.adapter,
                                            authenticated_actor_id="test-human")
        self.workflow.apply(command, now_ms=50)
        self.send_body(body("snapshot", 20), 100)
        self.publish_states(100)
        pending = self.receiver.state["review_state"]["pending_case"]
        request = {"schema": "deskbot.client_review_request/v1", "request_id": "request-confirm",
                   "target_adapter_instance_id": self.adapter, "operation": "confirm",
                   "case_id": pending["case_id"], "choice_id": "shell_a", "expected_registry_revision": 1}
        result = self.workflow.apply(review_request_to_command(request, adapter_instance_id=self.adapter,
                                                               authenticated_actor_id="test-human"), now_ms=200)
        self.assertEqual(result["status"], "accepted")
        self.send("review_ack", review_result_to_ack(request["request_id"], result), 200)
        self.publish_states(200)
        self.assertIsNone(self.receiver.state["selection"])
        self.send_body(body("snapshot", 21), 300)
        self.publish_states(300)
        self.assertEqual(self.receiver.state["registry_revision"], 2)
        self.assertIsNotNone(self.receiver.state["selection"])

    def test_real_f1_fixture_stays_blocked_with_no_base_online_evidence(self) -> None:
        source = ROOT / "artifacts/body-facts-replay-20261018/runs/f1-final3-eval-shell-b-01/messages.jsonl"
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        for index, row in enumerate(rows):
            self.send_body(row, index * 100)
        self.publish_states(100)
        self.assertEqual(self.receiver.state["role_state"]["reason"], "base_not_online")
        self.assertIsNone(self.receiver.state["selection"])

    def test_receiver_never_opens_network_and_exports_offline_safety_flags(self) -> None:
        with patch.object(socket, "socket", side_effect=AssertionError("network prohibited")):
            self.activate()
            self.receiver.tick(5100)
        for key, value in self.receiver.state["safety"].items():
            if key != "mode":
                self.assertIs(value, False)
        self.assertEqual(asdict(self.send("review_ack", {
            "schema": "deskbot.client_review_ack/v1", "request_id": "last-ack",
            "result": "rejected", "reason": "not_confirmed", "registry_revision": 0}, 5100))["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
