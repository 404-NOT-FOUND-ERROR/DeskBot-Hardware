from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from protocol.role_binding_consumer import load_binding_registry
from protocol.shell_binding_workflow import (
    COMMAND_SCHEMA,
    ShellBindingWorkflow,
    verify_audit_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "protocol/deskbot-role-binding-registry-e1-5.example.json"
F1 = ROOT / "artifacts/body-facts-replay-20261018/runs/f1-final3-eval-shell-b-01/messages.jsonl"
SCHEMA = ROOT / "protocol/deskbot-shell-binding-workflow-e1-6.schema.json"


def _stamp(offset_ms: int = 0) -> str:
    base = datetime.fromisoformat("2026-10-18T08:36:56.253+00:00")
    return (base + timedelta(milliseconds=offset_ms)).isoformat(timespec="milliseconds")


def _messages(*, device_id: str = "offline-replay:demo-device-a", status: str = "known",
              profile_id: str | None = "offline:demo-layout-a:profile-0001") -> tuple[dict, dict]:
    rows = [json.loads(line) for line in F1.read_text(encoding="utf-8").splitlines() if line.strip()]
    hello, snapshot = deepcopy(rows[0]), deepcopy(rows[1])
    identity = {"device_id": device_id, "boot_id": "boot-e1-6", "session_id": "session-e1-6"}
    for row in (hello, snapshot):
        row.update(identity)
    hello["payload"]["capabilities"] = ["magnetic_sample", "shell_observation", "base_events"]
    snapshot["payload"]["base"] = {"link": "online", "mechanical_status": "ready", "last_event_sequence": 0}
    snapshot["payload"]["shell_observation"].update({
        "status": status, "profile_id": profile_id,
        "profile_revision": 1 if profile_id else 0, "observed_at": snapshot["occurred_at"],
    })
    snapshot["payload"]["magnetic"].update({
        "state": "fresh", "sample_at": snapshot["occurred_at"], "age_ms": 0,
    })
    return hello, snapshot


def _next_snapshot(snapshot: dict, *, sequence: int, status: str,
                   profile_id: str | None, time_offset_ms: int) -> dict:
    result = deepcopy(snapshot)
    occurred = _stamp(time_offset_ms)
    result.update(message_id=f"snapshot-{sequence}", sequence=sequence, occurred_at=occurred)
    result["payload"]["shell_observation"].update({
        "status": status, "profile_id": profile_id,
        "profile_revision": 1 if profile_id else 0, "observed_at": occurred,
    })
    result["payload"]["magnetic"].update({"state": "fresh", "sample_at": occurred, "age_ms": 0})
    return result


def _write_choice(folder: Path, *, device_id: str, magnet_profile_id: str,
                  binding_id: str, role_id: str) -> Path:
    source_registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    source_row = source_registry["bindings"][0]
    profile = json.loads((ROOT / source_row["role_profile"]["path"]).read_text(encoding="utf-8"))
    profile.update({"shell_id": f"shell-{role_id}", "magnet_profile_id": magnet_profile_id,
                    "role_id": role_id, "display_name": f"测试角色 {role_id}"})
    profile_path = folder / f"{binding_id}-profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    lock = json.loads((ROOT / source_row["release_lock"]["path"]).read_text(encoding="utf-8"))
    profile_hash = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    lock["role_profile"] = {"path": profile_path.resolve().relative_to(ROOT).as_posix(),
                            "sha256": profile_hash}
    lock_path = folder / f"{binding_id}-lock.json"
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")

    row = deepcopy(source_row)
    row.update({"binding_id": binding_id, "device_id": device_id,
                "magnet_profile_id": magnet_profile_id, "magnet_profile_revision": 1,
                "role_profile": {"path": profile_path.resolve().relative_to(ROOT).as_posix(),
                                 "sha256": profile_hash},
                "release_lock": {"path": lock_path.resolve().relative_to(ROOT).as_posix(),
                                 "sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest()}})
    choice_path = folder / f"{binding_id}-registry.json"
    choice_path.write_text(json.dumps({"schema": source_registry["schema"], "bindings": [row]},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    return choice_path


def _command(operation: str, command_id: str, revision: int = 0, **fields) -> dict:
    return {"schema": COMMAND_SCHEMA, "command_id": command_id, "operation": operation,
            "actor": {"kind": "human", "id": "reviewer-1"},
            "expected_registry_revision": revision, **fields}


class ShellBindingWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="e1-6-", dir=ROOT / "tmp")
        self.folder = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def workflow(self, choices: dict[str, Path]) -> ShellBindingWorkflow:
        return ShellBindingWorkflow(REGISTRY, choices=choices, root=ROOT)

    def start(self, workflow: ShellBindingWorkflow, *, status="known", profile_id="offline:demo-layout-a:profile-0001",
              device_id="offline-replay:demo-device-a") -> tuple[dict, dict]:
        hello, snapshot = _messages(device_id=device_id, status=status, profile_id=profile_id)
        workflow.consume(hello, now_ms=0)
        workflow.consume(snapshot, now_ms=100)
        return hello, snapshot

    def test_unknown_only_opens_review_and_registration_request_never_invents_identity(self) -> None:
        device = "offline-replay:demo-device-a"
        profile_b = "offline:demo-layout-b:profile-0001"
        choice_b = _write_choice(self.folder, device_id=device, magnet_profile_id=profile_b,
                                 binding_id="binding-b", role_id="robot-b")
        workflow = self.workflow({"shell_b": choice_b})
        hello, unknown = self.start(workflow, status="unknown", profile_id=None)
        case = workflow.state["cases"][0]
        self.assertEqual(case["kind"], "unknown_observation")
        self.assertIsNone(case["magnet_profile_id"])
        self.assertEqual(case["magnet_profile_revision"], 0)

        blocked = workflow.apply(_command("confirm", "confirm-unknown", case_id=case["case_id"],
                                          choice_id="shell_b"), now_ms=150)
        self.assertEqual(blocked["reason"], "magnetic_profile_registration_required")
        self.assertEqual(workflow.state["registry_revision"], 0)

        requested = workflow.apply(_command("request_registration", "register-unknown",
                                             case_id=case["case_id"]), now_ms=200)
        self.assertEqual(requested["status"], "accepted")
        self.assertEqual(workflow.state["cases"][0]["status"], "registration_requested")
        self.assertEqual(workflow.state["registry"]["bindings"],
                         json.loads(REGISTRY.read_text(encoding="utf-8"))["bindings"])
        self.assertEqual(workflow.state["registry_revision"], 0)

        known_b = _next_snapshot(unknown, sequence=2, status="known", profile_id=profile_b,
                                 time_offset_ms=1000)
        workflow.consume(known_b, now_ms=300)
        new_case = workflow.state["cases"][-1]
        self.assertEqual(new_case["kind"], "unbound_profile")
        self.assertEqual(new_case["magnet_profile_id"], profile_b)
        confirmed = workflow.apply(_command("confirm", "confirm-b", case_id=new_case["case_id"],
                                            choice_id="shell_b"), now_ms=350)
        self.assertEqual(confirmed["status"], "accepted")
        self.assertEqual(confirmed["registry_revision"], 1)
        self.assertEqual(workflow.state["resolution"]["status"], "waiting_for_snapshot")
        self.assertIsNone(workflow.state["resolution"]["selection"])

        next_known = _next_snapshot(known_b, sequence=3, status="known", profile_id=profile_b,
                                    time_offset_ms=1500)
        resolved = workflow.consume(next_known, now_ms=500)["review"]["resolution"]
        self.assertEqual(resolved["status"], "resolved_offline")
        self.assertEqual(resolved["selection"]["role_id"], "robot-b")
        self.assertFalse(resolved["safety"]["activation_supported"])

    def test_known_unbound_confirm_requires_new_snapshot_after_registry_change(self) -> None:
        device = "offline-replay:new-device"
        profile_a = "offline:demo-layout-a:profile-0001"
        choice = _write_choice(self.folder, device_id=device, magnet_profile_id=profile_a,
                               binding_id="new-binding", role_id="new-role")
        workflow = self.workflow({"choice_a": choice})
        self.start(workflow, device_id=device)
        case = workflow.state["cases"][0]
        result = workflow.apply(_command("confirm", "confirm-known", case_id=case["case_id"],
                                         choice_id="choice_a"), now_ms=200)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(workflow.state["registry_revision"], 1)
        self.assertIsNone(workflow.state["resolution"]["selection"])

    def test_rebind_is_explicit_and_requires_matching_previous_binding(self) -> None:
        profile_a = "offline:demo-layout-a:profile-0001"
        choice = _write_choice(self.folder, device_id="offline-replay:demo-device-a",
                               magnet_profile_id=profile_a, binding_id="replacement-binding",
                               role_id="replacement-role")
        workflow = self.workflow({"replacement": choice})
        _, snapshot = self.start(workflow)
        self.assertEqual(workflow.state["review_reason"], "already_confirmed")
        blocked = workflow.apply(_command("rebind", "early-rebind", case_id="review-0001",
                                          choice_id="replacement", previous_binding_id="demo-shell-a-frog-chef"),
                                 now_ms=150)
        self.assertEqual(blocked["reason"], "case_not_pending")
        self.assertEqual(workflow.state["registry_revision"], 0)

        started = workflow.apply(_command("begin_rebind", "begin-rebind"), now_ms=200)
        self.assertEqual(started["status"], "accepted")
        case = workflow.state["cases"][-1]
        wrong_previous = workflow.apply(_command("rebind", "wrong-previous", case_id=case["case_id"],
                                                  choice_id="replacement", previous_binding_id="wrong-binding"),
                                        now_ms=250)
        self.assertEqual(wrong_previous["reason"], "previous_binding_mismatch")
        result = workflow.apply(_command("rebind", "rebind-ok", case_id=case["case_id"],
                                         choice_id="replacement", previous_binding_id="demo-shell-a-frog-chef"),
                                now_ms=300)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(workflow.state["registry"]["bindings"][0]["binding_id"], "replacement-binding")
        self.assertEqual(workflow.state["resolution"]["reason"], "bindings_changed")

    def test_reject_dismisses_same_episode_but_new_session_can_reopen(self) -> None:
        workflow = self.workflow({"shell_a": REGISTRY})
        hello, snapshot = self.start(workflow, status="unknown", profile_id=None)
        case_id = workflow.state["pending_case_id"]
        result = workflow.apply(_command("reject", "reject-1", case_id=case_id), now_ms=150)
        self.assertEqual(result["status"], "accepted")
        same = _next_snapshot(snapshot, sequence=2, status="unknown", profile_id=None,
                              time_offset_ms=0)
        state = workflow.consume(same, now_ms=200)["review"]
        self.assertIsNone(state["pending_case_id"])
        self.assertEqual(state["review_reason"], "dismissed_for_current_episode")
        new_hello, new_session = _messages(status="unknown", profile_id=None)
        new_hello.update(session_id="session-e1-6-new", message_id="hello-new-session", sequence=1)
        new_session = _next_snapshot(same, sequence=2, status="unknown", profile_id=None,
                                     time_offset_ms=0)
        new_session.update(session_id="session-e1-6-new", message_id="snapshot-new-session")
        workflow.consume(new_hello, now_ms=225)
        state = workflow.consume(new_session, now_ms=250)["review"]
        self.assertIsNotNone(state["pending_case_id"])
        self.assertEqual(state["cases"][-1]["case_id"], "review-0002")

    def test_stale_expired_disconnected_and_superseded_cases_cannot_commit(self) -> None:
        for mode in ("expired", "disconnect", "superseded"):
            with self.subTest(mode=mode):
                workflow = self.workflow({"shell_a": REGISTRY})
                self.start(workflow, status="unknown", profile_id=None)
                case_id = workflow.state["pending_case_id"]
                if mode == "expired":
                    workflow.tick(5100)
                elif mode == "disconnect":
                    workflow.disconnect(now_ms=200)
                else:
                    hello, _ = _messages(status="unknown", profile_id=None)
                    event = {"schema": "deskbot.body.v1", "message_type": "event", "message_id": "event-2",
                             "device_id": hello["device_id"], "boot_id": hello["boot_id"],
                             "session_id": hello["session_id"], "sequence": 2, "occurred_at": _stamp(100),
                             "payload": {"kind": "event", "event_type": "base.action.completed",
                                         "event_sequence": 1, "facts": {}}}
                    workflow.consume(event, now_ms=200)
                result = workflow.apply(_command("request_registration", f"cannot-{mode}", case_id=case_id),
                                        now_ms=5200 if mode == "expired" else 250)
                self.assertNotEqual(result["status"], "accepted")
                self.assertEqual(workflow.state["registry_revision"], 0)

    def test_revision_conflict_command_idempotency_and_conflict(self) -> None:
        workflow = self.workflow({"shell_a": REGISTRY})
        self.start(workflow, status="unknown", profile_id=None)
        case_id = workflow.state["pending_case_id"]
        command = _command("reject", "stable-command", case_id=case_id)
        first = workflow.apply(command, now_ms=150)
        duplicate = workflow.apply(command, now_ms=160)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["original_status"], "accepted")
        self.assertEqual(len(workflow.export()["journal"]), 1)
        conflict = deepcopy(command)
        conflict["actor"]["id"] = "different-reviewer"
        self.assertEqual(workflow.apply(conflict, now_ms=170)["reason"], "command_id_conflict")

        known_workflow = self.workflow({"shell_a": REGISTRY})
        self.start(known_workflow, device_id="offline-replay:new-device")
        case = known_workflow.state["pending_case_id"]
        revision_conflict = known_workflow.apply(_command("confirm", "stale-revision", 1,
                                                          case_id=case, choice_id="shell_a"), now_ms=150)
        self.assertEqual(revision_conflict["reason"], "registry_revision_conflict")
        self.assertEqual(known_workflow.state["registry_revision"], 0)

    def test_choice_is_reaudited_at_decision_time(self) -> None:
        choice = _write_choice(self.folder, device_id="offline-replay:new-device",
                               magnet_profile_id="offline:demo-layout-a:profile-0001",
                               binding_id="choice-binding", role_id="choice-role")
        workflow = self.workflow({"choice": choice})
        self.start(workflow, device_id="offline-replay:new-device")
        case_id = workflow.state["pending_case_id"]
        row = json.loads(choice.read_text(encoding="utf-8"))
        profile_path = ROOT / row["bindings"][0]["role_profile"]["path"]
        profile_path.write_text(profile_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        result = workflow.apply(_command("confirm", "choice-mutated", case_id=case_id, choice_id="choice"),
                                now_ms=150)
        self.assertEqual(result["reason"], "choice_failed_revalidation")
        self.assertEqual(workflow.state["registry_revision"], 0)

    def test_unbind_revokes_binding_and_selection_without_deleting_audit(self) -> None:
        workflow = self.workflow({"shell_a": REGISTRY})
        self.start(workflow)
        result = workflow.apply(_command("unbind", "unbind-a", binding_id="demo-shell-a-frog-chef"),
                                now_ms=150)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(workflow.state["registry"]["bindings"], [])
        self.assertIsNone(workflow.state["resolution"]["selection"])
        self.assertEqual(len(workflow.state["cases"]), 0)

    def test_audit_bundle_hash_chains_tamper_and_schema(self) -> None:
        from jsonschema import Draft202012Validator, FormatChecker

        workflow = self.workflow({"shell_a": REGISTRY})
        self.start(workflow, status="unknown", profile_id=None)
        case_id = workflow.state["pending_case_id"]
        workflow.apply(_command("request_registration", "audit-register", case_id=case_id), now_ms=150)
        bundle = workflow.export()
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(bundle)
        report = verify_audit_bundle(bundle)
        self.assertEqual(report["result"], "hash_chains_consistent")
        self.assertFalse(report["authenticated"])
        self.assertFalse(report["activation_performed"])

        tampered = deepcopy(bundle)
        tampered["journal"][0]["snapshot"]["device_id"] = "edited-device"
        with self.assertRaisesRegex(ValueError, "command journal chain mismatch|command snapshot hash mismatch"):
            verify_audit_bundle(tampered)
        self.assertTrue(all(value is False for key, value in bundle["state"]["safety"].items()
                            if key != "mode"))

    def test_actor_must_be_explicit_human_and_agent_cannot_decide(self) -> None:
        workflow = self.workflow({"shell_a": REGISTRY})
        self.start(workflow, status="unknown", profile_id=None)
        command = _command("reject", "agent-reject", case_id=workflow.state["pending_case_id"])
        command["actor"] = {"kind": "agent", "id": "assistant"}
        result = workflow.apply(command, now_ms=150)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(workflow.state["registry_revision"], 0)
        self.assertEqual(workflow.export()["journal"], [])


if __name__ == "__main__":
    unittest.main()
