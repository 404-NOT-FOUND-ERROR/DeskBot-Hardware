from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol.role_binding_consumer import RoleBindingConsumer, _audit_module, load_binding_registry


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "artifacts/body-facts-replay-20261018/runs/f1-final3-eval-shell-b-01/messages.jsonl"
REGISTRY = ROOT / "protocol/deskbot-role-binding-registry-e1-5.example.json"


def _stamp(offset_ms: int = 0) -> str:
    return (datetime.fromisoformat("2026-10-18T08:36:56.253+00:00")
            + timedelta(milliseconds=offset_ms)).isoformat(timespec="milliseconds")


def _replay_module():
    spec = importlib.util.spec_from_file_location("role_resolution_replay_tests", ROOT / "scripts/resolve-role-binding-replay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _messages() -> tuple[dict, dict]:
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    hello, snapshot = deepcopy(rows[0]), deepcopy(rows[1])
    identity = {"device_id": "offline-replay:demo-device-a", "boot_id": "boot-e1-5", "session_id": "session-e1-5"}
    for row in (hello, snapshot):
        row.update(identity)
    hello["payload"]["capabilities"] = ["magnetic_sample", "shell_observation", "base_events"]
    snapshot["payload"]["base"] = {"link": "online", "mechanical_status": "ready", "last_event_sequence": 0}
    observation = snapshot["payload"]["shell_observation"]
    observation.update({
        "status": "known", "profile_id": "offline:demo-layout-a:profile-0001",
        "profile_revision": 1, "observed_at": snapshot["occurred_at"],
    })
    snapshot["payload"]["magnetic"].update({"state": "fresh", "sample_at": snapshot["occurred_at"], "age_ms": 0})
    return hello, snapshot


class RoleBindingConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bindings = load_binding_registry(REGISTRY, root=ROOT)

    def setUp(self) -> None:
        self.consumer = RoleBindingConsumer(self.bindings, freshness_limit_ms=5000)
        self.hello, self.snapshot = _messages()

    def test_registry_is_verified_against_release_lock(self) -> None:
        self.assertEqual(len(self.bindings), 1)
        self.assertEqual(self.bindings[0].release_audit["result"], "offline_validated")
        self.assertEqual(self.bindings[0].profile["binding_state"], "confirmed")

    def test_reused_audit_module_still_rehashes_editable_inputs(self) -> None:
        self.assertIs(_audit_module(ROOT), _audit_module(ROOT))
        self.assertEqual(_audit_module.cache_info().maxsize, 4)
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="e1-8-audit-cache-", dir=ROOT / "tmp") as folder:
            path = Path(folder) / "registry.json"
            profile = Path(folder) / "profile.json"
            source = ROOT / registry["bindings"][0]["role_profile"]["path"]
            profile.write_bytes(source.read_bytes())
            registry["bindings"][0]["role_profile"]["path"] = profile.relative_to(ROOT).as_posix()
            path.write_text(json.dumps(registry), encoding="utf-8")
            # The valid hash passes before the release lock rejects the new path.
            with self.assertRaisesRegex(ValueError, "exact profile pinned"):
                load_binding_registry(path, root=ROOT)
            profile.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "role_profile SHA-256 mismatch"):
                load_binding_registry(path, root=ROOT)

    def test_snapshot_resolves_unique_confirmed_role_and_exposes_no_activation(self) -> None:
        self.consumer.consume(self.hello, now_ms=0)
        result = self.consumer.consume(self.snapshot, now_ms=100)
        self.assertEqual(result["resolution"]["status"], "resolved_offline")
        selection = result["resolution"]["selection"]
        self.assertEqual(selection["role_id"], "frog-chef")
        self.assertEqual(selection["asset_version"], "deskbot.expression_assets/e1-4c-shy-legacy-v1")
        self.assertFalse(result["resolution"]["safety"]["activation_supported"])
        self.assertFalse(result["resolution"]["safety"]["expression_executed"])

    def start(self) -> dict:
        self.consumer.consume(self.hello, now_ms=0)
        return self.consumer.consume(self.snapshot, now_ms=100)["resolution"]

    def next_snapshot(self, sequence: int = 2, **changes) -> dict:
        value = deepcopy(self.snapshot)
        value.update({"message_id": "snapshot-" + str(sequence), "sequence": sequence, **changes})
        return value

    def test_all_nonknown_states_revoke_previous_selection(self) -> None:
        for status in ("no_data", "warming_up", "unknown", "ambiguous", "unstable", "stale"):
            with self.subTest(status=status):
                self.consumer = RoleBindingConsumer(self.bindings, freshness_limit_ms=5000)
                self.start()
                value = self.next_snapshot()
                value["payload"]["shell_observation"].update(status=status, profile_id=None)
                state = self.consumer.consume(value, now_ms=200)["resolution"]
                self.assertEqual(state["reason"], "shell_" + status)
                self.assertIsNone(state["selection"])

    def test_exact_device_and_full_namespace_are_required(self) -> None:
        for change in ("device", "profile"):
            with self.subTest(change=change):
                consumer = RoleBindingConsumer(self.bindings, freshness_limit_ms=5000)
                hello, snapshot = _messages()
                if change == "device":
                    hello["device_id"] = snapshot["device_id"] = "other-device"
                else:
                    snapshot["payload"]["shell_observation"]["profile_id"] = "profile-0001"
                consumer.consume(hello, now_ms=0)
                state = consumer.consume(snapshot, now_ms=100)["resolution"]
                self.assertEqual(state["reason"], "binding_not_found")
                self.assertIsNone(state["selection"])

    def test_magnetic_revision_is_not_role_profile_revision(self) -> None:
        entry = deepcopy(self.bindings[0])
        entry.profile["profile_revision"] = 7
        self.consumer = RoleBindingConsumer((entry,), freshness_limit_ms=5000)
        self.assertEqual(self.start()["selection"]["profile_revision"], 7)
        value = self.next_snapshot()
        value["payload"]["shell_observation"]["profile_revision"] = 7
        state = self.consumer.consume(value, now_ms=200)["resolution"]
        self.assertEqual(state["reason"], "magnetic_revision_mismatch")
        self.assertIsNone(state["selection"])

    def test_draft_disabled_and_conflicts_never_resolve(self) -> None:
        for binding_state in ("draft", "disabled"):
            entry = deepcopy(self.bindings[0])
            entry.profile["binding_state"] = binding_state
            self.consumer = RoleBindingConsumer((entry,), freshness_limit_ms=5000)
            self.assertIsNone(self.start()["selection"])
        self.consumer = RoleBindingConsumer(self.bindings + self.bindings, freshness_limit_ms=5000)
        self.assertEqual(self.start()["reason"], "binding_conflict")

    def test_snapshot_before_hello_does_not_resolve(self) -> None:
        result = self.consumer.consume(self.snapshot, now_ms=0)
        self.assertEqual(result["body_outcome"]["status"], "needs_snapshot")
        self.assertIsNone(result["resolution"]["selection"])

    def test_reconnect_and_boot_change_require_new_snapshot(self) -> None:
        self.start()
        hello = deepcopy(self.hello)
        hello.update(session_id="session-b", message_id="hello-session-b", sequence=2)
        state = self.consumer.consume(hello, now_ms=200)["resolution"]
        self.assertEqual(state["reason"], "snapshot_required")
        self.assertIsNone(state["selection"])
        value = self.next_snapshot(sequence=3, session_id="session-b")
        self.assertEqual(self.consumer.consume(value, now_ms=300)["resolution"]["status"], "resolved_offline")
        hello.update(boot_id="boot-b", session_id="session-c", message_id="hello-boot-b", sequence=0)
        state = self.consumer.consume(hello, now_ms=400)["resolution"]
        self.assertEqual(state["evidence"]["generation"], 2)
        self.assertIsNone(state["selection"])

    def test_gap_requires_resynchronization_not_inferred_facts(self) -> None:
        self.start()
        value = self.next_snapshot()
        value.update(message_type="event", message_id="event-gap")
        value["payload"] = {"kind": "event", "event_type": "future.shell.known", "event_sequence": 3,
                            "facts": {"role_id": "malicious-inference"}}
        result = self.consumer.consume(value, now_ms=200)
        self.assertEqual(result["body_outcome"]["reason"], "event_sequence_gap")
        self.assertIsNone(result["resolution"]["shell_observation"])
        resync = self.next_snapshot(sequence=3)
        resync["payload"]["event_cursor"].update(high_watermark_sequence=3, oldest_available_sequence=1, gap_detected=True)
        self.assertEqual(self.consumer.consume(resync, now_ms=300)["resolution"]["status"], "resolved_offline")

    def test_disconnect_rejects_late_snapshot_and_duplicate_hello(self) -> None:
        self.start()
        self.consumer.disconnect(now_ms=200)
        result = self.consumer.consume(self.next_snapshot(), now_ms=300)
        self.assertEqual(result["body_outcome"]["reason"], "hello_required_after_disconnect")
        result = self.consumer.consume(deepcopy(self.hello), now_ms=400)
        self.assertEqual(result["body_outcome"]["status"], "duplicate")
        self.assertEqual(result["resolution"]["status"], "disconnected")

    def test_stale_magnetic_offline_or_homing_revokes_selection(self) -> None:
        cases = (("magnetic", "state", "stale", "magnetic_stale"),
                 ("magnetic", "state", "no_data", "magnetic_no_data"),
                 ("base", "link", "offline", "base_not_online"),
                 ("base", "link", "unknown", "base_not_online"),
                 ("base", "mechanical_status", "homing", "base_not_ready"))
        for section, key, value, reason in cases:
            with self.subTest(reason=reason):
                self.consumer = RoleBindingConsumer(self.bindings, freshness_limit_ms=5000)
                self.start()
                snap = self.next_snapshot()
                snap["payload"][section][key] = value
                state = self.consumer.consume(snap, now_ms=200)["resolution"]
                self.assertEqual(state["reason"], reason)
                self.assertIsNone(state["selection"])

    def test_missing_or_future_timestamps_block_resolution(self) -> None:
        cases = (("magnetic", "age_ms", None), ("magnetic", "sample_at", None),
                 ("shell_observation", "observed_at", None),
                 ("shell_observation", "observed_at", "2026-10-19T00:00:00Z"))
        for section, key, value in cases:
            with self.subTest(section=section, key=key, value=value):
                self.consumer = RoleBindingConsumer(self.bindings, freshness_limit_ms=5000)
                self.start()
                snap = self.next_snapshot()
                snap["payload"][section][key] = value
                self.assertEqual(self.consumer.consume(snap, now_ms=200)["resolution"]["status"], "blocked")

    def test_declared_age_and_recorded_time_both_limit_freshness(self) -> None:
        self.snapshot["payload"]["magnetic"]["age_ms"] = 4999
        self.start()
        self.assertEqual(self.consumer.tick(100)["valid_until_ms"], 101)
        self.assertEqual(self.consumer.tick(101)["status"], "expired")
        for section, key in (("magnetic", "sample_at"), ("shell_observation", "observed_at")):
            with self.subTest(section=section):
                self.consumer = RoleBindingConsumer(self.bindings, freshness_limit_ms=5000)
                self.hello, self.snapshot = _messages()
                self.snapshot["payload"][section][key] = _stamp(-4999)
                state = self.start()
                self.assertEqual(state["status"], "resolved_offline")
                self.assertEqual(state["valid_until_ms"], 101)
                self.assertEqual(self.consumer.tick(101)["status"], "expired")

    def test_independently_advancing_timestamps_can_renew_their_own_deadlines(self) -> None:
        self.assertEqual(self.start()["valid_until_ms"], 5100)
        for sequence, elapsed, sample, shell, expected in (
            (2, 1000, 1000, 0, 5100),
            (3, 2000, 1000, 2000, 6100),
            (4, 3000, 3000, 2000, 7100),
        ):
            snap = self.next_snapshot(sequence=sequence, occurred_at=_stamp(elapsed))
            snap["payload"]["magnetic"]["sample_at"] = _stamp(sample)
            snap["payload"]["shell_observation"]["observed_at"] = _stamp(shell)
            state = self.consumer.consume(snap, now_ms=100 + elapsed)["resolution"]
            self.assertEqual(state["status"], "resolved_offline")
            self.assertEqual(state["valid_until_ms"], expected)

    def test_constructor_rejects_identity_and_version_mismatches(self) -> None:
        changed_release = deepcopy(self.bindings[0].release_audit)
        changed_release["asset_version"] = "wrong-version"
        changed_binding = deepcopy(self.bindings[0].release_audit)
        changed_binding["binding_version"] = "wrong-binding-version"
        for entry in (
            replace(self.bindings[0], release_audit=changed_release),
            replace(self.bindings[0], release_audit=changed_binding),
            replace(self.bindings[0], magnet_profile_id="different-identity"),
            replace(self.bindings[0], magnet_profile_revision=True),
        ):
            with self.subTest(entry=entry.binding_id):
                with self.assertRaises(ValueError):
                    RoleBindingConsumer((entry,), freshness_limit_ms=5000)

    def test_duplicate_ack_and_bad_message_do_not_extend_deadline(self) -> None:
        self.assertEqual(self.start()["valid_until_ms"], 5100)
        self.assertEqual(self.consumer.consume(deepcopy(self.snapshot), now_ms=5000)["resolution"]["valid_until_ms"], 5100)
        ack = self.next_snapshot()
        ack.update(message_type="ack", message_id="ack")
        ack["payload"] = {"kind": "ack", "ack_for": "x", "status": "accepted"}
        self.assertEqual(self.consumer.consume(ack, now_ms=5099)["resolution"]["valid_until_ms"], 5100)
        bad = self.next_snapshot(sequence=3)
        bad["payload"]["shell_observation"]["similarity"] = float("nan")
        self.assertEqual(self.consumer.consume(bad, now_ms=5099)["body_outcome"]["status"], "rejected")
        self.assertEqual(self.consumer.tick(5100)["status"], "expired")

    def test_missing_capabilities_block_resolution(self) -> None:
        self.hello["payload"]["capabilities"] = ["base_events"]
        self.assertEqual(self.start()["reason"], "shell_capabilities_missing")

    def test_new_message_id_cannot_refresh_old_sensor_timestamps(self) -> None:
        self.start()
        result = self.consumer.consume(self.next_snapshot(), now_ms=5000)
        self.assertEqual(result["resolution"]["valid_until_ms"], 5100)
        self.consumer.tick(5100)
        result = self.consumer.consume(self.next_snapshot(sequence=3), now_ms=5200)
        self.assertEqual(result["resolution"]["status"], "expired")
        self.assertIsNone(result["resolution"]["selection"])

    def test_regressed_timestamp_is_blocked_with_increasing_transport_sequence(self) -> None:
        self.start()
        value = self.next_snapshot()
        value["payload"]["magnetic"]["sample_at"] = "2026-10-18T08:36:56.252+00:00"
        state = self.consumer.consume(value, now_ms=200)["resolution"]
        self.assertEqual(state["reason"], "observation_timestamp_regressed")

    def test_host_clock_cannot_regress(self) -> None:
        self.start()
        with self.assertRaisesRegex(ValueError, "monotonic"):
            self.consumer.tick(99)
        for value in (True, -1, 0, 1.5):
            with self.assertRaises(ValueError):
                RoleBindingConsumer(self.bindings, freshness_limit_ms=value)

    def test_outputs_and_catalog_are_defensively_copied(self) -> None:
        self.start()
        state = self.consumer.state
        state["selection"]["expression_map"]["bashful"]["resource_id"] = "wrong.eaf"
        self.assertEqual(self.consumer.state["selection"]["expression_map"]["bashful"]["resource_id"], "shy_legacy.eaf")
        self.assertEqual(self.consumer.state["selection"]["expression_map"]["bashful"]["action_id"], "cat_nuzzle")
        self.assertFalse(self.consumer.state["safety"]["action_executed"])

    def test_real_f1_keeps_original_namespace_and_has_no_role_binding(self) -> None:
        rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
        for index, row in enumerate(rows):
            result = self.consumer.consume(row, now_ms=index * 100)
        self.assertEqual(result["resolution"]["reason"], "base_not_online")
        self.assertEqual(result["resolution"]["shell_observation"]["profile_id"], "offline:72b0eac6ea3ccb64:profile-0002")
        self.assertIsNone(result["resolution"]["selection"])

    def test_registry_rejects_hash_tampering_traversal_duplicates_and_secrets(self) -> None:
        source = json.loads(REGISTRY.read_text(encoding="utf-8"))
        cases = []
        value = deepcopy(source)
        value["bindings"][0]["role_profile"]["sha256"] = "0" * 64
        cases.append(value)
        value = deepcopy(source)
        value["bindings"][0]["release_lock"]["path"] = "../outside.json"
        cases.append(value)
        value = deepcopy(source)
        value["bindings"].append(deepcopy(value["bindings"][0]))
        cases.append(value)
        value = deepcopy(source)
        value["bindings"][0]["api_key"] = "not-allowed"
        cases.append(value)
        value = deepcopy(source)
        value["bindings"][0]["magnet_profile_revision"] = True
        cases.append(value)
        with tempfile.TemporaryDirectory(prefix="e1-5-registry-", dir=ROOT / "tmp") as folder:
            path = Path(folder) / "registry.json"
            for value in cases:
                with self.subTest(case=value):
                    path.write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_binding_registry(path, root=ROOT)

    def test_registry_duplicate_observation_key_fails_before_resource_audit(self) -> None:
        value = json.loads(REGISTRY.read_text(encoding="utf-8"))
        duplicate = deepcopy(value["bindings"][0])
        duplicate["binding_id"] = "different-binding-id"
        value["bindings"].append(duplicate)
        with tempfile.TemporaryDirectory(prefix="e1-5-registry-", dir=ROOT / "tmp") as folder:
            path = Path(folder) / "registry.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with patch("protocol.role_binding_consumer._audit_module") as loader:
                audit = loader.return_value
                audit.safe_project_path.return_value = path
                audit.load_json.return_value = value
                with self.assertRaisesRegex(ValueError, "duplicate device/magnetic-profile/revision"):
                    load_binding_registry(path, root=ROOT)
                audit.check_hashed_file.assert_not_called()

    def test_schemas_validate_outputs_and_reject_side_effect_claims(self) -> None:
        from jsonschema import Draft202012Validator, FormatChecker

        registry_schema = json.loads((ROOT / "protocol/deskbot-role-binding-registry-v1.schema.json").read_text(encoding="utf-8"))
        resolution_schema = json.loads((ROOT / "protocol/deskbot-role-resolution-v1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(registry_schema)
        Draft202012Validator.check_schema(resolution_schema)
        Draft202012Validator(registry_schema).validate(json.loads(REGISTRY.read_text(encoding="utf-8")))
        validator = Draft202012Validator(resolution_schema, format_checker=FormatChecker())
        validator.validate(self.consumer.state)
        state = self.start()
        validator.validate(state)
        malformed = deepcopy(state)
        malformed["evidence"]["snapshot"]["unexpected_field"] = True
        self.assertFalse(validator.is_valid(malformed))
        malformed = deepcopy(state)
        malformed["shell_observation"]["profile_id"] = None
        self.assertFalse(validator.is_valid(malformed))
        state["safety"]["action_executed"] = True
        self.assertFalse(validator.is_valid(state))

    def test_unknown_or_unready_snapshot_never_selects_role(self) -> None:
        self.consumer.consume(self.hello, now_ms=0)
        unknown = deepcopy(self.snapshot)
        unknown["payload"]["shell_observation"].update({"status": "unknown", "profile_id": None})
        result = self.consumer.consume(unknown, now_ms=100)
        self.assertEqual(result["resolution"]["status"], "unresolved")
        self.assertIsNone(result["resolution"]["selection"])
        unready = deepcopy(self.snapshot)
        unready["message_id"] = "snapshot-unready"
        unready["sequence"] = 2
        unready["payload"]["base"]["mechanical_status"] = "homing"
        result = self.consumer.consume(unready, now_ms=200)
        self.assertEqual(result["resolution"]["status"], "blocked")
        self.assertIsNone(result["resolution"]["selection"])

    def test_event_gap_or_event_after_snapshot_clears_selection(self) -> None:
        self.consumer.consume(self.hello, now_ms=0)
        self.consumer.consume(self.snapshot, now_ms=100)
        event = deepcopy(self.snapshot)
        event.update({"message_type": "event", "message_id": "event-1", "sequence": 2})
        event["payload"] = {"kind": "event", "event_type": "base.action.completed", "event_sequence": 1, "facts": {}}
        result = self.consumer.consume(event, now_ms=150)
        self.assertEqual(result["resolution"]["status"], "waiting_for_snapshot")
        self.assertIsNone(result["resolution"]["selection"])

    def test_expiry_disconnect_and_new_boot_invalidate_role(self) -> None:
        self.consumer.consume(self.hello, now_ms=0)
        self.consumer.consume(self.snapshot, now_ms=100)
        self.assertEqual(self.consumer.tick(5100)["status"], "expired")
        self.assertEqual(self.consumer.disconnect(now_ms=5200)["status"], "disconnected")
        hello2 = deepcopy(self.hello)
        hello2.update({"boot_id": "boot-e1-5-b", "session_id": "session-e1-5-b", "message_id": "hello-b", "sequence": 3})
        result = self.consumer.consume(hello2, now_ms=5300)
        self.assertEqual(result["resolution"]["status"], "waiting_for_snapshot")
        self.assertIsNone(result["resolution"]["selection"])


class RoleBindingReplayTests(unittest.TestCase):
    def test_recorded_replay_keeps_source_provenance_without_inventing_a_role(self) -> None:
        module = _replay_module()
        parent = ROOT / "tmp/body-facts-role-resolution-e1-5"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="recorded-", dir=parent) as folder:
            path = Path(folder) / "report.json"
            report = module.run(FIXTURE, REGISTRY, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
            self.assertFalse(report["synthetic_messages"])
            self.assertEqual(report["clock_basis"], "recorded_host_receive_elapsed_ms")
            self.assertEqual(report["input_sha256"], "fbc335ef4d30b0988d5360c490b5665cb2c7f3a4978aaa5aff791419cd1b015b")
            self.assertEqual([step["body_outcome"]["status"] for step in report["steps"]], ["accepted", "accepted"])
            state = report["final_resolution"]
            self.assertEqual(state["reason"], "base_not_online")
            self.assertEqual(state["clock_ms"], 14947)
            self.assertEqual(state["shell_observation"]["profile_id"], "offline:72b0eac6ea3ccb64:profile-0002")
            self.assertIsNone(state["selection"])

    def test_synthetic_report_exercises_resolution_without_hardware_claims(self) -> None:
        from jsonschema import Draft202012Validator, FormatChecker

        module = _replay_module()
        parent = ROOT / "tmp/body-facts-role-resolution-e1-5"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="synthetic-", dir=parent) as folder:
            path = Path(folder) / "report.json"
            report = module.run_synthetic_demo(REGISTRY, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
            self.assertTrue(report["synthetic_messages"])
            self.assertFalse(report["hardware_io"])
            self.assertFalse(report["network_io"])
            self.assertEqual(report["steps"][1]["selected_role_id"], "frog-chef")
            self.assertIsNone(report["steps"][4]["selected_role_id"])
            self.assertEqual(report["final_resolution"]["status"], "expired")
            schema = json.loads((ROOT / "protocol/deskbot-role-resolution-v1.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(report["final_resolution"])

    def test_report_publication_never_replaces_existing_file(self) -> None:
        module = _replay_module()
        with tempfile.TemporaryDirectory(prefix="e1-5-publish-", dir=ROOT / "tmp") as folder:
            path = Path(folder) / "report.json"
            module.publish_report(path, {"first": True})
            initial = path.read_bytes()
            with self.assertRaises(FileExistsError):
                module.publish_report(path, {"first": False})
            self.assertEqual(path.read_bytes(), initial)

    def test_replay_rejects_wrong_output_scope_before_reading_source(self) -> None:
        module = _replay_module()
        for path in (ROOT / "report.json", ROOT / "protocol/no-output.json", ROOT / "artifacts/wrong-stage/report.json"):
            with self.subTest(path=path):
                with patch.object(module, "_load_f1_helpers") as helpers:
                    with self.assertRaisesRegex(ValueError, "output must be inside"):
                        module.run(FIXTURE, REGISTRY, path)
                    helpers.assert_not_called()

    def test_replay_rejects_existing_output_before_reading_source(self) -> None:
        module = _replay_module()
        parent = ROOT / "tmp/body-facts-role-resolution-e1-5"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="existing-", dir=parent) as folder:
            path = Path(folder) / "report.json"
            path.write_text("preserve", encoding="utf-8")
            with patch.object(module, "_load_f1_helpers") as helpers:
                with self.assertRaisesRegex(ValueError, "never overwritten"):
                    module.run(FIXTURE, REGISTRY, path)
                helpers.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
