from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from protocol.client_transport_contract import make_record, review_request_to_command, review_result_to_ack
from protocol.role_binding_consumer import RoleBindingConsumer, load_binding_registry
from protocol.shell_binding_workflow import ShellBindingWorkflow


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocol"
RECORD_SCHEMA = PROTOCOL / "deskbot-client-transport-e1-7.schema.json"
BODY_SCHEMA = PROTOCOL / "deskbot-body-facts-v1.schema.json"
RESOLUTION_SCHEMA = PROTOCOL / "deskbot-role-resolution-v1.schema.json"
REVIEW_SCHEMA = PROTOCOL / "deskbot-shell-binding-workflow-e1-6.schema.json"
BODY_SAMPLE = PROTOCOL / "deskbot-body-facts-v1.example.json"
REGISTRY = PROTOCOL / "deskbot-role-binding-registry-e1-5.example.json"
F1_MESSAGES = ROOT / "artifacts/body-facts-replay-20261018/runs/f1-final3-eval-shell-b-01/messages.jsonl"


def _unresolved() -> dict:
    return {
        "schema": "deskbot.role_resolution/v1", "status": "waiting_for_snapshot",
        "reason": "snapshot_required", "clock_ms": 0, "freshness_limit_ms": 5000,
        "valid_until_ms": None,
        "evidence": {"device_id": None, "boot_id": None, "session_id": None,
                     "generation": 0, "last_event_sequence": 0, "snapshot": None},
        "shell_observation": None, "selection": None,
        "safety": {"mode": "offline_resolution_only", "activation_supported": False,
                   "deployment_eligible": False, "hardware_io": False, "network_io": False,
                   "expression_executed": False, "action_executed": False},
    }


def _review_request(operation: str, **args) -> dict:
    return {"schema": "deskbot.client_review_request/v1", "request_id": "request-1",
            "target_adapter_instance_id": "adapter-a", "operation": operation,
            "expected_registry_revision": 0, **args}


class ClientTransportContractE17Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
        resources = []
        for path in (BODY_SCHEMA, RESOLUTION_SCHEMA, REVIEW_SCHEMA):
            document = json.loads(path.read_text(encoding="utf-8"))
            resources.append((document["$id"], Resource.from_contents(document)))
        cls.validator = Draft202012Validator(
            cls.schema,
            registry=Registry().with_resources(resources),
            format_checker=FormatChecker(),
        )
        cls.body = json.loads(BODY_SAMPLE.read_text(encoding="utf-8"))

    def assert_record_valid(self, record: dict) -> None:
        self.validator.validate(record)

    def test_schema_is_valid_and_external_protocol_refs_resolve(self) -> None:
        Draft202012Validator.check_schema(self.schema)
        record = make_record(record_id="record-1", stream_id="adapter-a", sequence=1,
                             created_at="2026-10-22T01:00:00Z", record_type="role_state",
                             payload=_unresolved())
        self.assert_record_valid(record)

    def test_body_fact_is_preserved_and_has_independent_transport_sequence(self) -> None:
        source = deepcopy(self.body)
        record = make_record(record_id="record-body-1", stream_id="adapter-a", sequence=1,
                             created_at="2026-10-22T01:00:00Z", record_type="body_fact",
                             payload=source)
        self.assert_record_valid(record)
        self.assertEqual(record["payload"], source)
        self.assertEqual(record["sequence"], 1)
        self.assertEqual(record["payload"]["sequence"], source["sequence"])
        self.assertNotIn("role_id", record["payload"]["payload"])

    def test_role_state_schema_allows_full_resolved_or_cleared_snapshot(self) -> None:
        cleared = make_record(record_id="state-1", stream_id="adapter-a", sequence=2,
                              created_at="2026-10-22T01:00:01Z", record_type="role_state",
                              payload=_unresolved())
        self.assert_record_valid(cleared)

        resolved = _unresolved()
        resolved.update(status="resolved_offline", reason="confirmed_unique_binding", valid_until_ms=5100)
        resolved["evidence"].update({
            "device_id": "offline-replay:demo-device-a", "boot_id": "boot-e1-7",
            "session_id": "session-e1-7", "generation": 1,
            "snapshot": {"message_id": "snapshot-1", "occurred_at": "2026-10-22T01:00:00Z",
                         "transport_sequence": 2},
        })
        resolved["shell_observation"] = {
            "status": "known", "profile_id": "offline:demo-layout-a:profile-0001",
            "profile_revision": 1, "sensor_kind": "BMM150", "similarity": 0.1,
            "margin": 0.3, "sample_count": 8, "observed_at": "2026-10-22T01:00:00Z",
        }
        resolved["selection"] = {
            "binding_id": "demo-shell-a-frog-chef", "shell_id": "shell-a", "role_id": "frog-chef",
            "magnet_profile_id": "offline:demo-layout-a:profile-0001", "magnet_profile_revision": 1,
            "profile_revision": 1, "display_name": "青蛙厨师", "voice_id": "voice-01",
            "catchphrases": ["呱"], "release_id": "release-e1-4c-shy-legacy",
            "asset_version": "deskbot.expression_assets/e1-4c-shy-legacy-v1",
            "binding_version": "deskbot.expression_bindings/v1", "expression_map": {}, "action_map": {},
        }
        selected = make_record(record_id="state-2", stream_id="adapter-a", sequence=3,
                               created_at="2026-10-22T01:00:02Z", record_type="role_state",
                               payload=resolved)
        self.assert_record_valid(selected)

        revoked = deepcopy(resolved)
        revoked.update(status="waiting_for_snapshot", reason="event_requires_snapshot", valid_until_ms=None)
        revoked["selection"] = None
        invalidation = make_record(record_id="state-3", stream_id="adapter-a", sequence=4,
                                   created_at="2026-10-22T01:00:03Z", record_type="role_state",
                                   payload=revoked)
        self.assert_record_valid(invalidation)

    def test_all_manual_review_requests_map_to_e1_6_commands_without_trusting_actor_input(self) -> None:
        cases = [
            ("confirm", {"case_id": "case-1", "choice_id": "shell-a"}),
            ("reject", {"case_id": "case-1"}),
            ("request_registration", {"case_id": "case-1"}),
            ("begin_rebind", {}),
            ("rebind", {"case_id": "case-1", "choice_id": "shell-a",
                         "previous_binding_id": "binding-old"}),
            ("unbind", {"binding_id": "binding-old"}),
        ]
        for index, (operation, fields) in enumerate(cases, 1):
            with self.subTest(operation=operation):
                request = _review_request(operation, **fields)
                envelope = make_record(record_id=f"request-record-{index}", stream_id="client-a",
                                       sequence=index, created_at="2026-10-22T01:00:00Z",
                                       record_type="review_request", payload=request)
                self.assert_record_valid(envelope)
                command = review_request_to_command(
                    request, adapter_instance_id="adapter-a", authenticated_actor_id="reviewer-7")
                self.assertEqual(command["schema"], "deskbot.shell_review_command/v1")
                self.assertEqual(command["command_id"], request["request_id"])
                self.assertEqual(command["expected_registry_revision"], 0)
                self.assertEqual(command["actor"], {"kind": "human", "id": "reviewer-7"})
                self.assertEqual(command["operation"], operation)

    def test_review_request_rejects_forged_actor_stale_adapter_and_boolean_revision(self) -> None:
        forged = _review_request("reject", case_id="case-1", actor={"kind": "human", "id": "owner"})
        with self.assertRaisesRegex(ValueError, "unsupported operation or fields"):
            review_request_to_command(forged, adapter_instance_id="adapter-a", authenticated_actor_id="reviewer-7")

        with self.assertRaisesRegex(ValueError, "adapter_instance_mismatch"):
            review_request_to_command(_review_request("reject", case_id="case-1"),
                                      adapter_instance_id="adapter-b", authenticated_actor_id="reviewer-7")

        invalid = _review_request("reject", case_id="case-1")
        invalid["expected_registry_revision"] = True
        with self.assertRaisesRegex(ValueError, "JSON-safe integer"):
            review_request_to_command(invalid, adapter_instance_id="adapter-a", authenticated_actor_id="reviewer-7")

    def test_malformed_operation_and_record_fields_fail_explicitly(self) -> None:
        for operation in ([], {}, None, "activate_role", "learn_magnet"):
            with self.subTest(operation=operation):
                request = _review_request(operation)
                with self.assertRaisesRegex(ValueError, "unsupported operation or fields"):
                    review_request_to_command(request, adapter_instance_id="adapter-a",
                                              authenticated_actor_id="reviewer-7")
        for changes in ({"sequence": True}, {"sequence": 0}, {"sequence": 9007199254740992},
                        {"record_id": "../escape"}, {"record_type": []}, {"payload": []}):
            args = dict(record_id="record-1", stream_id="adapter-a", sequence=1,
                        created_at="2026-10-22T01:00:00Z", record_type="body_fact", payload=self.body)
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                make_record(**args)

    def test_mapped_request_round_trip_uses_existing_workflow_idempotency(self) -> None:
        workflow = ShellBindingWorkflow(REGISTRY, choices={"shell_a": REGISTRY}, root=ROOT)
        review = workflow.client_review_state(adapter_instance_id="adapter-a")
        self.assertEqual(review["schema"], "deskbot.client_review_state/v1")
        self.assertNotIn("journal", review)
        self.assert_record_valid(make_record(record_id="review-state-1", stream_id="adapter-a", sequence=1,
                                             created_at="2026-10-22T01:00:00Z", record_type="review_state",
                                             payload=review))
        self.assertEqual(review["registry_revision"], 0)
        self.assertIsNone(review["pending_case"])
        self.assertEqual(review["choices"][0]["role_id"], "frog-chef")
        serialized = json.dumps(review, ensure_ascii=False)
        for private_field in ("source", "sha256", "journal", "initial_snapshot", "release_lock"):
            self.assertNotIn(private_field, serialized)
        review["choices"][0]["role_id"] = "edited-output"
        self.assertEqual(workflow.client_review_state(adapter_instance_id="adapter-a")["choices"][0]["role_id"],
                         "frog-chef")
        # Preserve the existing profile limits and vendor voice identifiers.
        compatible = workflow.client_review_state(adapter_instance_id="adapter-a")
        compatible["choices"][0].update(display_name="名" * 80, voice_id="vendor:voice/中文-01")
        self.assert_record_valid(make_record(record_id="review-compatible", stream_id="adapter-a", sequence=1,
                                             created_at="2026-10-22T01:00:00Z", record_type="review_state",
                                             payload=compatible))
        request = _review_request("unbind", binding_id="demo-shell-a-frog-chef")
        command = review_request_to_command(request, adapter_instance_id="adapter-a",
                                            authenticated_actor_id="reviewer-7")
        first = workflow.apply(command, now_ms=0)
        duplicate = workflow.apply(deepcopy(command), now_ms=1)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(first["registry_revision"], 1)
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(len(workflow.export()["journal"]), 1)
        self.assert_record_valid(make_record(record_id="review-state-2", stream_id="adapter-a", sequence=2,
                                             created_at="2026-10-22T01:00:01Z", record_type="review_state",
                                             payload=workflow.client_review_state(adapter_instance_id="adapter-a")))
        unsafe = workflow.client_review_state(adapter_instance_id="adapter-a")
        unsafe["safety"]["action_executed"] = True
        with self.assertRaises(ValidationError):
            self.assert_record_valid(make_record(record_id="unsafe-review", stream_id="adapter-a", sequence=3,
                                                 created_at="2026-10-22T01:00:02Z", record_type="review_state",
                                                 payload=unsafe))
        for index, result in enumerate((first, duplicate), 1):
            ack = review_result_to_ack(request["request_id"], result)
            self.assert_record_valid(make_record(record_id=f"ack-{index}", stream_id="adapter-a",
                                                 sequence=index, created_at="2026-10-22T01:00:00Z",
                                                 record_type="review_ack", payload=ack))

        stale = _review_request("unbind", binding_id="demo-shell-a-frog-chef")
        stale["request_id"] = "request-2"
        stale_command = review_request_to_command(stale, adapter_instance_id="adapter-a",
                                                  authenticated_actor_id="reviewer-7")
        result = workflow.apply(stale_command, now_ms=2)
        self.assertEqual(result["reason"], "registry_revision_conflict")
        self.assertEqual(result["status"], "rejected")
        repeated_rejection = workflow.apply(stale_command, now_ms=3)
        ack = review_result_to_ack(stale["request_id"], repeated_rejection)
        self.assertEqual(ack["original_result"], "rejected")
        self.assert_record_valid(make_record(record_id="rejected-ack", stream_id="adapter-a", sequence=5,
                                             created_at="2026-10-22T01:00:04Z", record_type="review_ack", payload=ack))

    def test_duplicate_ack_requires_original_decision(self) -> None:
        result = {"status": "duplicate", "reason": "confirm", "registry_revision": 1}
        with self.assertRaisesRegex(ValueError, "original_status"):
            review_result_to_ack("request-1", result)
        for original in ("accepted", "rejected"):
            with self.subTest(original=original):
                result["original_status"] = original
                ack = review_result_to_ack("request-1", result)
                self.assertEqual(ack["original_result"], original)
                record = make_record(record_id="duplicate-1", stream_id="adapter-a", sequence=1,
                                     created_at="2026-10-22T01:00:00Z", record_type="review_ack", payload=ack)
                self.assert_record_valid(record)
                del record["payload"]["original_result"]
                with self.assertRaises(ValidationError):
                    self.assert_record_valid(record)

    def test_pending_review_projection_omits_raw_unknown_sample(self) -> None:
        snapshot = deepcopy(self.body)
        identity = {"device_id": "offline-replay:demo-device-a", "boot_id": "boot-e1-7",
                    "session_id": "session-e1-7"}
        snapshot.update(identity, message_id="snapshot-unknown", sequence=2)
        timestamp = snapshot["occurred_at"]
        hello = {
            "schema": "deskbot.body.v1", "message_type": "hello", "message_id": "hello-unknown",
            **identity, "sequence": 1, "occurred_at": timestamp,
            "payload": {"kind": "hello", "firmware_id": "offline-e1-7",
                        "capabilities": ["magnetic_sample", "shell_observation", "base_events"]},
        }
        snapshot["payload"]["magnetic"].update({"state": "fresh", "sample_at": timestamp, "age_ms": 0})
        snapshot["payload"]["shell_observation"].update({
            "status": "unknown", "profile_id": None, "profile_revision": 0, "observed_at": timestamp,
        })
        workflow = ShellBindingWorkflow(REGISTRY, choices={"shell_a": REGISTRY}, root=ROOT)
        workflow.consume(hello, now_ms=0)
        workflow.consume(snapshot, now_ms=100)
        review = workflow.client_review_state(adapter_instance_id="adapter-a")
        self.assertEqual(review["pending_case"]["kind"], "unknown_observation")
        self.assertIsNone(review["pending_case"]["magnet_profile_id"])
        self.assertEqual(review["pending_case"]["magnet_profile_revision"], 0)
        serialized = json.dumps(review, ensure_ascii=False)
        for private_field in ("x", "y", "z", "initial_snapshot", "sample_at", "sha256", "source"):
            self.assertNotIn(f'"{private_field}"', serialized)
        self.assert_record_valid(make_record(record_id="review-pending-1", stream_id="adapter-a", sequence=1,
                                             created_at="2026-10-22T01:00:00Z", record_type="review_state",
                                             payload=review))
        review["pending_case"]["magnet_profile_id"] = "edited-output"
        self.assertIsNone(workflow.client_review_state(adapter_instance_id="adapter-a")["pending_case"]["magnet_profile_id"])
        workflow.tick(5100)
        self.assertIsNone(workflow.client_review_state(adapter_instance_id="adapter-a")["pending_case"])

    def test_ack_mapper_rejects_malformed_status_reason_and_revision(self) -> None:
        for result in ({"status": "activated"}, {"status": []},
                       {"status": "accepted", "reason": "ok", "registry_revision": True},
                       {"status": "accepted", "reason": "", "registry_revision": 1}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                review_result_to_ack("request-1", result)

    def test_resync_request_has_no_device_control_fields(self) -> None:
        payload = {"schema": "deskbot.client_resync_request/v1",
                   "target_adapter_instance_id": "adapter-a", "known_stream_id": "adapter-stream-1",
                   "last_seen_sequence": 8, "reason": "stream_gap"}
        record = make_record(record_id="resync-1", stream_id="client-a", sequence=3,
                             created_at="2026-10-22T01:00:03Z", record_type="resync_request", payload=payload)
        self.assert_record_valid(record)
        payload["motor_command"] = "look_around"
        invalid = make_record(record_id="resync-2", stream_id="client-a", sequence=4,
                              created_at="2026-10-22T01:00:04Z", record_type="resync_request", payload=payload)
        with self.assertRaises(ValidationError):
            self.assert_record_valid(invalid)

    def test_schema_rejects_mixed_payload_wrong_version_and_extra_fields(self) -> None:
        record = make_record(record_id="body-2", stream_id="adapter-a", sequence=2,
                             created_at="2026-10-22T01:00:00Z", record_type="body_fact", payload=self.body)
        for mutate in (
            lambda row: row.update(schema="deskbot.client_transport_record/v2"),
            lambda row: row.update(record_type="role_state"),
            lambda row: row.update(unreviewed_role="frog-chef"),
        ):
            invalid = deepcopy(record)
            mutate(invalid)
            with self.subTest(record=invalid):
                with self.assertRaises(ValidationError):
                    self.assert_record_valid(invalid)

    def test_cleared_role_cannot_keep_selection_or_claim_activation(self) -> None:
        for field in ("activation_supported", "deployment_eligible", "network_io", "hardware_io",
                      "expression_executed", "action_executed"):
            state = _unresolved()
            state["safety"][field] = True
            record = make_record(record_id="unsafe-1", stream_id="adapter-a", sequence=1,
                                 created_at="2026-10-22T01:00:00Z", record_type="role_state", payload=state)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.assert_record_valid(record)
        state = _unresolved()
        state["selection"] = {"role_id": "old-role"}
        record = make_record(record_id="old-role-1", stream_id="adapter-a", sequence=1,
                             created_at="2026-10-22T01:00:00Z", record_type="role_state", payload=state)
        with self.assertRaises(ValidationError):
            self.assert_record_valid(record)

    def test_disconnect_and_event_invalidate_selection_before_client_replay(self) -> None:
        rows = [json.loads(line) for line in F1_MESSAGES.read_text(encoding="utf-8").splitlines() if line.strip()]
        hello, snapshot = deepcopy(rows[0]), deepcopy(rows[1])
        identity = {"device_id": "offline-replay:demo-device-a", "boot_id": "boot-e1-7",
                    "session_id": "session-e1-7"}
        for message in (hello, snapshot):
            message.update(identity)
        hello["payload"]["capabilities"] = ["magnetic_sample", "shell_observation", "base_events"]
        snapshot["payload"]["base"] = {"link": "online", "mechanical_status": "ready", "last_event_sequence": 0}
        snapshot["payload"]["magnetic"].update({"state": "fresh", "sample_at": snapshot["occurred_at"], "age_ms": 0})
        snapshot["payload"]["shell_observation"].update({
            "status": "known", "profile_id": "offline:demo-layout-a:profile-0001",
            "profile_revision": 1, "observed_at": snapshot["occurred_at"],
        })
        consumer = RoleBindingConsumer(load_binding_registry(REGISTRY, root=ROOT), freshness_limit_ms=5000)
        consumer.consume(hello, now_ms=0)
        selected = consumer.consume(snapshot, now_ms=100)["resolution"]
        self.assertEqual(selected["status"], "resolved_offline")
        self.assertIsNotNone(selected["selection"])
        self.assert_record_valid(make_record(record_id="selected-1", stream_id="adapter-a", sequence=2,
                                             created_at="2026-10-22T01:00:00Z", record_type="role_state",
                                             payload=selected))

        event = deepcopy(snapshot)
        event.update(message_type="event", message_id="event-1", sequence=2)
        event["payload"] = {"kind": "event", "event_type": "base.action.completed",
                             "event_sequence": 1, "facts": {}}
        cleared = consumer.consume(event, now_ms=200)["resolution"]
        self.assertIsNone(cleared["selection"])
        self.assertEqual(cleared["reason"], "event_requires_snapshot")
        self.assert_record_valid(make_record(record_id="revoked-1", stream_id="adapter-a", sequence=3,
                                             created_at="2026-10-22T01:00:01Z", record_type="role_state",
                                             payload=cleared))

        disconnected = consumer.disconnect(now_ms=300)
        self.assertIsNone(disconnected["selection"])
        self.assert_record_valid(make_record(record_id="disconnected-1", stream_id="adapter-a", sequence=4,
                                             created_at="2026-10-22T01:00:02Z", record_type="role_state",
                                             payload=disconnected))


if __name__ == "__main__":
    unittest.main()
