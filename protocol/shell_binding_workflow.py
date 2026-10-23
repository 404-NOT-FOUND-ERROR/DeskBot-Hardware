"""E1.6 offline, human-reviewed shell/role binding transactions.

Unknown observations are review cases, not learned magnetic identities. Only
an already identified, fresh profile can be bound to an audited local choice.
This module performs local reads only. It has no transport, hardware controls,
learning API, expression player, AI integration, or persistence side effects.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from protocol.body_facts_consumer import MAX_SAFE_INTEGER
from protocol.deskbot_role_profile import ROLE_ID_RE
from protocol.role_binding_consumer import RoleBindingConsumer, load_binding_registry


REVISION_SCHEMA = "deskbot.role_binding_revision/v1"
COMMAND_SCHEMA = "deskbot.shell_review_command/v1"
STATE_SCHEMA = "deskbot.shell_review_state/v1"


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _number(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_SAFE_INTEGER:
        raise ValueError(f"{label} must be a JSON-safe integer >= {minimum}")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not ROLE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a role-style identifier")
    return value


def _key(row: dict[str, Any]) -> tuple[str, str, int]:
    return row["device_id"], row["magnet_profile_id"], row["magnet_profile_revision"]


def _load(path: Path, root: Path) -> tuple[dict[str, Any], tuple, dict[str, str]]:
    """Hash and verify the exact bytes, detecting changes during validation."""
    resolved = path.resolve(strict=True)
    relative = resolved.relative_to(root).as_posix()
    before = resolved.read_bytes()
    verified = load_binding_registry(resolved, root=root)
    if resolved.read_bytes() != before:
        raise ValueError("registry changed while being verified")
    return json.loads(before), verified, {"path": relative, "sha256": hashlib.sha256(before).hexdigest()}


class ShellBindingWorkflow:
    """One bounded offline review session, on top of the E1.5 consumer.

    All decisions must be explicit manual inputs. ``actor.kind=human`` is an
    audit label, NOT authentication: a future UI/adapter must enforce identity
    and must never let an Agent self-assert it. State lasts only for this local
    session; export does not automatically install a registry anywhere.
    """

    def __init__(self, registry_path: Path, *, choices: dict[str, Path], root: Path,
                 freshness_limit_ms: int = 5000, max_cases: int = 32, max_commands: int = 128):
        self._root = root.resolve(strict=True)
        self._registry, self._verified, self._source = _load(registry_path, self._root)
        self._consumer = RoleBindingConsumer(self._verified, freshness_limit_ms=freshness_limit_ms)
        if not isinstance(choices, dict) or len(choices) > 16:
            raise ValueError("choices must contain at most 16 audited local registries")
        self._choices = {}
        for choice_id, path in choices.items():
            _identifier(choice_id, "choice_id")
            registry, verified, source = _load(path, self._root)
            if len(verified) != 1 or verified[0].profile["binding_state"] != "confirmed":
                raise ValueError("each choice must contain exactly one confirmed audited binding")
            self._choices[choice_id] = (registry["bindings"][0], verified[0], source)
        for value, name, limit in ((max_cases, "max_cases", 128), (max_commands, "max_commands", 512)):
            if not 1 <= _number(value, name, minimum=1) <= limit:
                raise ValueError(f"{name} exceeds {limit}")
        self._max_cases, self._max_commands = max_cases, max_commands
        self._cases: list[dict[str, Any]] = []
        self._pending_id: str | None = None
        self._episode = None
        self._dismissed_episode = None
        self._latest: dict[str, Any] | None = None
        self._deadline: int | None = None
        self._anchor = None
        self._now = 0
        self._revision = 0
        self._history = [self._revision_document(None, None)]
        self._journal: list[dict[str, Any]] = []
        self._commands: dict[str, tuple[str, dict[str, Any]]] = {}
        self._review_reason = "snapshot_required"

    def _revision_document(self, parent: str | None, command_id: str | None) -> dict[str, Any]:
        return {"schema": REVISION_SCHEMA, "revision": self._revision,
                "parent_sha256": parent, "command_id": command_id,
                "source_registry": deepcopy(self._source),
                "registry_sha256": canonical_sha(self._registry),
                "registry": deepcopy(self._registry), "offline_only": True}

    @property
    def state(self) -> dict[str, Any]:
        return deepcopy({
            "schema": STATE_SCHEMA, "clock_ms": self._now,
            "registry_revision": self._revision,
            "revision_sha256": canonical_sha(self._history[-1]),
            "registry": self._registry,
            "pending_case_id": self._pending_id, "review_reason": self._review_reason,
            "cases": self._cases, "resolution": self._consumer.state,
            "limits": {"cases": self._max_cases, "commands": self._max_commands},
            "safety": {"mode": "offline_manual_review_only", "hardware_io": False,
                       "network_io": False, "learning_executed": False, "device_activated": False,
                       "expression_executed": False, "action_executed": False, "nvs_written": False},
        })

    def export(self) -> dict[str, Any]:
        """A local audit bundle, never a client or device activation command."""
        return deepcopy({"state": self.state, "revision_history": self._history,
                         "journal": self._journal,
                         "choices": {name: {"source": source, "binding": row}
                                     for name, (row, _, source) in self._choices.items()}})

    def client_review_state(self, *, adapter_instance_id: str) -> dict[str, Any]:
        """Return a read-only review summary without local paths or audit snapshots."""
        _identifier(adapter_instance_id, "adapter_instance_id")
        pending = next((item for item in self._cases if item["case_id"] == self._pending_id), None)
        pending_case = None
        if pending is not None:
            pending_case = {key: pending[key] for key in (
                "case_id", "kind", "status", "device_id", "magnet_profile_id",
                "magnet_profile_revision", "valid_until_ms", "previous_binding_id",
            )}
        choices = []
        for choice_id, (row, verified, _) in sorted(self._choices.items()):
            profile = verified.profile
            choices.append({
                "choice_id": choice_id,
                "binding_id": row["binding_id"],
                "device_id": row["device_id"],
                "shell_id": profile["shell_id"],
                "magnet_profile_id": row["magnet_profile_id"],
                "magnet_profile_revision": row["magnet_profile_revision"],
                "role_id": profile["role_id"],
                "display_name": profile["display_name"],
                "voice_id": profile["voice_id"],
                "profile_revision": profile["profile_revision"],
                "asset_version": profile["asset_version"],
                "binding_version": profile["binding_version"],
            })
        statuses = ("pending", "rejected", "registration_requested", "confirmed", "expired", "superseded")
        return deepcopy({
            "schema": "deskbot.client_review_state/v1",
            "adapter_instance_id": adapter_instance_id,
            "registry_revision": self._revision,
            "clock_ms": self._now,
            "pending_case": pending_case,
            "choices": choices,
            "case_counts": {status: sum(item["status"] == status for item in self._cases)
                            for status in statuses},
            "audit_entry_count": len(self._journal),
            "safety": {"mode": "read_only_review_projection", "hardware_io": False,
                       "network_io": False, "device_activated": False,
                       "expression_executed": False, "action_executed": False,
                       "nvs_written": False},
        })

    def _finish_case(self, status: str, reason: str) -> None:
        if self._pending_id is not None:
            case = next(item for item in self._cases if item["case_id"] == self._pending_id)
            case.update(status=status, closed_at_ms=self._now, close_reason=reason)
        self._pending_id = None
        self._review_reason = reason

    def tick(self, now_ms: int) -> dict[str, Any]:
        now = _number(now_ms, "now_ms")
        if now < self._now:
            raise ValueError("host/replay clock must be monotonic")
        self._consumer.tick(now)
        self._now = now
        if self._deadline is not None and now >= self._deadline:
            self._latest = None
            self._deadline = None
            self._finish_case("expired", "observation_expired")
        return self.state

    def disconnect(self, *, now_ms: int) -> dict[str, Any]:
        self.tick(now_ms)
        self._consumer.disconnect(now_ms=now_ms)
        self._latest = None
        self._deadline = None
        self._episode = self._dismissed_episode = None
        self._finish_case("superseded", "transport_disconnected")
        return self.state

    def consume(self, message: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
        self.tick(now_ms)
        result = self._consumer.consume(message, now_ms=now_ms)
        outcome = result["body_outcome"]
        if outcome["status"] == "needs_snapshot" or (
            outcome["status"] == "accepted" and outcome["message_type"] in {"hello", "event"}
        ):
            if outcome.get("boot_changed"):
                self._anchor = None
                self._episode = self._dismissed_episode = None
            self._latest = None
            self._deadline = None
            self._finish_case("superseded", "snapshot_required")
        elif outcome["status"] == "accepted" and outcome["message_type"] == "snapshot":
            self._accept_snapshot(message)
        return {"body_outcome": outcome, "review": self.state}

    def _review_deadline(self, message: dict[str, Any]) -> tuple[int | None, str]:
        """Additional freshness gate for unknowns, which E1.5 never resolves."""
        body = self._consumer.state
        payload = message["payload"]
        magnetic, shell = payload["magnetic"], payload["shell_observation"]
        if body["reason"] == "shell_capabilities_missing":
            return None, "shell_capabilities_missing"
        if payload["base"]["link"] != "online" or payload["base"]["mechanical_status"] != "ready":
            return None, "base_not_ready"
        if magnetic["state"] != "fresh" or shell["status"] not in {"known", "unknown"}:
            return None, "observation_not_reviewable"
        if shell["status"] == "known" and shell["profile_revision"] < 1:
            return None, "magnetic_profile_revision_required"
        if not magnetic.get("sample_at") or not shell.get("observed_at") or magnetic.get("age_ms") is None:
            return None, "freshness_evidence_missing"
        occurred = datetime.fromisoformat(message["occurred_at"].replace("Z", "+00:00"))
        stamps = [datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                  for stamp in (magnetic["sample_at"], shell["observed_at"])]
        ages = [(occurred - stamp).total_seconds() * 1000 for stamp in stamps]
        if min(ages) < 0:
            return None, "future_observation_timestamp"
        ages[0] = max(ages[0], magnetic["age_ms"])
        limit = body["freshness_limit_ms"]
        deadlines = [min(MAX_SAFE_INTEGER, self._now + limit - math.ceil(age)) for age in ages]
        identity = (message["device_id"], message["boot_id"])
        if self._anchor is not None and self._anchor[:2] == identity:
            if stamps[0] < self._anchor[2] or stamps[1] < self._anchor[3]:
                return None, "observation_timestamp_regressed"
            for i in range(2):
                if stamps[i] == self._anchor[i + 2]:
                    deadlines[i] = min(deadlines[i], self._anchor[i + 4])
        self._anchor = (*identity, *stamps, *deadlines)
        if min(deadlines) <= self._now:
            return None, "observation_expired"
        return min(deadlines), "reviewable"

    def _accept_snapshot(self, message: dict[str, Any]) -> None:
        deadline, reason = self._review_deadline(message)
        if deadline is None:
            self._latest = None
            self._deadline = None
            self._episode = self._dismissed_episode = None
            self._finish_case("superseded", reason)
            return
        self._latest, self._deadline = deepcopy(message), deadline
        shell = message["payload"]["shell_observation"]
        episode = (message["device_id"], message["boot_id"], message["session_id"],
                   shell["status"], shell["profile_id"], shell["profile_revision"])
        if episode != self._episode:
            self._finish_case("superseded", "observation_changed")
            self._dismissed_episode = None
            self._episode = episode
        if self._consumer.state["status"] == "resolved_offline" and self._pending_id is None:
            self._review_reason = "already_confirmed"
        elif self._pending_id is not None:
            case = next(item for item in self._cases if item["case_id"] == self._pending_id)
            case["valid_until_ms"] = deadline
            self._review_reason = "pending_confirmation"
        elif self._dismissed_episode == episode:
            self._review_reason = "dismissed_for_current_episode"
        else:
            self._open_case("unknown_observation" if shell["status"] == "unknown" else "unbound_profile")

    def _open_case(self, kind: str) -> None:
        if len(self._cases) >= self._max_cases:
            self._review_reason = "case_capacity_full"
            return
        assert self._latest is not None
        shell = self._latest["payload"]["shell_observation"]
        current = [row for row in self._registry["bindings"]
                   if _key(row) == (self._latest["device_id"], shell["profile_id"], shell["profile_revision"])]
        if current:
            kind = "rebind"
        case = {"case_id": f"review-{len(self._cases) + 1:04d}", "kind": kind, "status": "pending",
                "device_id": self._latest["device_id"], "boot_id": self._latest["boot_id"],
                "session_id": self._latest["session_id"],
                "magnet_profile_id": shell["profile_id"], "magnet_profile_revision": shell["profile_revision"],
                "opened_at_ms": self._now, "valid_until_ms": self._deadline,
                "closed_at_ms": None, "close_reason": None,
                "initial_snapshot_sha256": canonical_sha(self._latest),
                "initial_snapshot": deepcopy(self._latest),
                "previous_binding_id": current[0]["binding_id"] if len(current) == 1 else None}
        self._cases.append(case)
        self._pending_id = case["case_id"]
        self._review_reason = "pending_confirmation"

    @staticmethod
    def _validate_command(command: Any) -> None:
        if not isinstance(command, dict):
            raise ValueError("command must be an object")
        common = {"schema", "command_id", "operation", "actor", "expected_registry_revision"}
        specific = {"confirm": {"case_id", "choice_id"}, "reject": {"case_id"},
                    "request_registration": {"case_id"},
                    "begin_rebind": set(), "rebind": {"case_id", "choice_id", "previous_binding_id"},
                    "unbind": {"binding_id"}}
        operation = command.get("operation")
        if not isinstance(operation, str) or operation not in specific or set(command) != common | specific[operation]:
            raise ValueError("unsupported command fields or operation")
        if command["schema"] != COMMAND_SCHEMA:
            raise ValueError("unsupported command schema")
        _identifier(command["command_id"], "command_id")
        _number(command["expected_registry_revision"], "expected_registry_revision")
        actor = command["actor"]
        if not isinstance(actor, dict) or set(actor) != {"kind", "id"} or actor["kind"] != "human":
            raise ValueError("explicit human review is required; Agent decisions are not accepted")
        _identifier(actor["id"], "actor.id")
        for field in specific[operation]:
            _identifier(command[field], field)

    def apply(self, command: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
        """Apply one manual decision with idempotency and revision preconditions."""
        self.tick(now_ms)
        try:
            self._validate_command(command)
        except ValueError as exc:
            return {"status": "rejected", "reason": str(exc), "registry_revision": self._revision}
        digest = canonical_sha(command)
        previous = self._commands.get(command["command_id"])
        if previous:
            if previous[0] != digest:
                return {"status": "rejected", "reason": "command_id_conflict", "registry_revision": self._revision}
            return {**deepcopy(previous[1]), "status": "duplicate", "original_status": previous[1]["status"]}
        if len(self._commands) >= self._max_commands:
            return {"status": "rejected", "reason": "command_capacity_full", "registry_revision": self._revision}
        before = self._revision
        snapshot = deepcopy(self._latest)
        evidence = canonical_sha(self._latest) if self._latest else None
        try:
            if command["expected_registry_revision"] != self._revision:
                raise ValueError("registry_revision_conflict")
            self._decide(command)
            result = {"status": "accepted", "reason": command["operation"], "registry_revision": self._revision}
        except ValueError as exc:
            result = {"status": "rejected", "reason": str(exc), "registry_revision": self._revision}
        record = {"sequence": len(self._journal) + 1, "clock_ms": self._now,
                  "command": deepcopy(command), "command_sha256": digest,
                  "snapshot": snapshot, "snapshot_sha256": evidence,
                  "before_revision": before, "after_revision": self._revision,
                  "result": deepcopy(result),
                  "previous_sha256": self._journal[-1]["sha256"] if self._journal else None}
        record["sha256"] = canonical_sha(record)
        self._journal.append(record)
        self._commands[command["command_id"]] = digest, deepcopy(result)
        return result

    def _decide(self, command: dict[str, Any]) -> None:
        operation = command["operation"]
        if operation == "unbind":
            rows = self._registry["bindings"]
            index = next((i for i, row in enumerate(rows) if row["binding_id"] == command["binding_id"]), None)
            if index is None:
                raise ValueError("binding_not_found")
            updated = deepcopy(self._registry)
            updated["bindings"].pop(index)
            verified = tuple(entry for entry in self._verified if entry.binding_id != command["binding_id"])
            self._commit(updated, verified, command["command_id"])
            self._finish_case("superseded", "binding_revoked")
            return
        if operation == "begin_rebind":
            if self._latest is None or self._consumer.state["status"] != "resolved_offline":
                raise ValueError("fresh_confirmed_binding_required")
            if self._pending_id is not None:
                raise ValueError("review_already_pending")
            if len(self._cases) >= self._max_cases:
                raise ValueError("case_capacity_full")
            self._open_case("rebind")
            return
        if command["case_id"] != self._pending_id:
            raise ValueError("case_not_pending")
        case = next(item for item in self._cases if item["case_id"] == self._pending_id)
        if operation == "request_registration":
            if case["kind"] != "unknown_observation":
                raise ValueError("unknown_observation_required")
            # This records a human intent only. An independent registration
            # workflow must later provide a real profile and a fresh known
            # snapshot. No ID, training sample or role is inferred here.
            self._finish_case("registration_requested", "human_requested_registration")
            self._dismissed_episode = self._episode
            self._consumer.replace_bindings(self._verified)
            self._latest = None
            self._deadline = None
            return
        if operation == "reject":
            self._finish_case("rejected", "human_rejected")
            self._dismissed_episode = self._episode
            self._consumer.replace_bindings(self._verified)
            self._latest = None
            self._deadline = None
            return
        if self._latest is None or self._deadline is None or self._now >= self._deadline:
            raise ValueError("fresh_observation_required")
        shell = self._latest["payload"]["shell_observation"]
        if shell["status"] != "known":
            raise ValueError("magnetic_profile_registration_required")
        choice = self._choices.get(command["choice_id"])
        if choice is None:
            raise ValueError("choice_not_found")
        row, verified, _ = choice
        if _key(row) != (self._latest["device_id"], shell["profile_id"], shell["profile_revision"]):
            raise ValueError("choice_identity_or_revision_mismatch")
        existing = [i for i, item in enumerate(self._registry["bindings"]) if _key(item) == _key(row)]
        if operation == "confirm" and existing:
            raise ValueError("binding_exists_use_explicit_rebind")
        if operation == "rebind":
            if len(existing) != 1 or case["kind"] != "rebind":
                raise ValueError("explicit_rebind_review_required")
            if self._registry["bindings"][existing[0]]["binding_id"] != command["previous_binding_id"]:
                raise ValueError("previous_binding_mismatch")
        if any(item["binding_id"] == row["binding_id"] and i not in existing
               for i, item in enumerate(self._registry["bindings"])):
            raise ValueError("binding_id_conflict")
        if existing and self._registry["bindings"][existing[0]] == row:
            raise ValueError("binding_unchanged")
        if not existing and len(self._registry["bindings"]) >= 128:
            raise ValueError("registry_capacity_full")
        # Re-read and audit the choice at decision time; do not trust a stale
        # on-disk profile/release just because it was valid when the UI opened.
        try:
            _, checked, source = _load(self._root / choice[2]["path"], self._root)
        except (OSError, ValueError) as exc:
            raise ValueError("choice_failed_revalidation") from exc
        if source != choice[2] or len(checked) != 1:
            raise ValueError("choice_changed_after_review")
        verified = checked[0]
        updated = deepcopy(self._registry)
        remaining = [entry for entry in self._verified
                     if (entry.device_id, entry.magnet_profile_id, entry.magnet_profile_revision) != _key(row)]
        if existing:
            updated["bindings"][existing[0]] = deepcopy(row)
        else:
            updated["bindings"].append(deepcopy(row))
        self._commit(updated, tuple(remaining) + (verified,), command["command_id"])
        self._finish_case("confirmed", "human_" + operation)

    def _commit(self, registry: dict[str, Any], verified: tuple, command_id: str) -> None:
        if self._revision == MAX_SAFE_INTEGER:
            raise ValueError("registry_revision_exhausted")
        self._consumer.replace_bindings(verified)
        parent = canonical_sha(self._history[-1])
        self._registry, self._verified = registry, verified
        self._revision += 1
        self._history.append(self._revision_document(parent, command_id))
        self._latest = None
        self._deadline = None


def verify_audit_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Check exported hash chains and cross references, without activating them.

    Hashes detect inconsistent edits; they are NOT signatures/authentication.
    Someone who can replace the entire bundle can also recompute its hashes.
    This verifier does not re-audit external resources or import the registry.
    JSON shape validation is a separate, offline JSON Schema check.
    """
    if not isinstance(bundle, dict) or set(bundle) != {"state", "revision_history", "journal", "choices"}:
        raise ValueError("audit bundle fields do not match v1")
    state, history, journal = bundle["state"], bundle["revision_history"], bundle["journal"]
    if not isinstance(history, list) or not 1 <= len(history) <= 513:
        raise ValueError("invalid revision history size")
    if not isinstance(journal, list) or len(journal) > 512:
        raise ValueError("invalid command journal size")
    prior, initial_source = None, history[0]["source_registry"]
    revision_commands = {}
    for index, revision in enumerate(history):
        if (revision["schema"] != REVISION_SCHEMA or revision["offline_only"] is not True
                or type(revision["revision"]) is not int or revision["revision"] != index
                or revision["parent_sha256"] != prior or revision["source_registry"] != initial_source
                or revision["registry_sha256"] != canonical_sha(revision["registry"])):
            raise ValueError("revision chain mismatch")
        command_id = revision["command_id"]
        if index == 0:
            if command_id is not None:
                raise ValueError("initial revision cannot reference a command")
        elif command_id is None or command_id in revision_commands:
            raise ValueError("revision command is missing or reused")
        else:
            revision_commands[command_id] = index
        prior = canonical_sha(revision)
    if (state["schema"] != STATE_SCHEMA or state["registry_revision"] != len(history) - 1
            or state["revision_sha256"] != prior or state["registry"] != history[-1]["registry"]):
        raise ValueError("current registry does not match revision history")
    prior, seen, current_revision, clock = None, set(), 0, 0
    for index, record in enumerate(journal, 1):
        command, result = record["command"], record["result"]
        ShellBindingWorkflow._validate_command(command)
        digest = canonical_sha({key: value for key, value in record.items() if key != "sha256"})
        command_id = command["command_id"]
        if (record["sequence"] != index or record["previous_sha256"] != prior
                or record["sha256"] != digest or record["command_sha256"] != canonical_sha(command)
                or command_id in seen or record["clock_ms"] < clock
                or record["before_revision"] != current_revision):
            raise ValueError("command journal chain mismatch")
        snapshot = record["snapshot"]
        if record["snapshot_sha256"] != (canonical_sha(snapshot) if snapshot is not None else None):
            raise ValueError("command snapshot hash mismatch")
        mutates = result["status"] == "accepted" and command["operation"] in {"confirm", "rebind", "unbind"}
        expected = current_revision + int(mutates)
        if (result["status"] not in {"accepted", "rejected"}
                or record["after_revision"] != expected or result["registry_revision"] != expected):
            raise ValueError("command revision transition mismatch")
        if result["status"] == "accepted" and command["expected_registry_revision"] != current_revision:
            raise ValueError("accepted command has a revision conflict")
        if mutates and revision_commands.pop(command_id, None) != expected:
            raise ValueError("registry revision has no matching accepted command")
        if not mutates and command_id in revision_commands:
            raise ValueError("non-mutating command created a revision")
        prior, current_revision, clock = digest, expected, record["clock_ms"]
        seen.add(command_id)
    if revision_commands or current_revision != state["registry_revision"] or clock > state["clock_ms"]:
        raise ValueError("incomplete command/revision history")
    if len(journal) > state["limits"]["commands"] or len(state["cases"]) > state["limits"]["cases"]:
        raise ValueError("session limits exceeded")
    pending = []
    for case in state["cases"]:
        if case["initial_snapshot_sha256"] != canonical_sha(case["initial_snapshot"]):
            raise ValueError("review case snapshot hash mismatch")
        if case["status"] == "pending":
            pending.append(case["case_id"])
    if pending != ([state["pending_case_id"]] if state["pending_case_id"] else []):
        raise ValueError("pending case pointer mismatch")
    for safety, mode in ((state["safety"], "offline_manual_review_only"),
                         (state["resolution"]["safety"], "offline_resolution_only")):
        if safety.get("mode") != mode or any(value is not False for key, value in safety.items() if key != "mode"):
            raise ValueError("offline safety boundary mismatch")
    return {"result": "hash_chains_consistent", "registry_revisions": len(history),
            "journal_entries": len(journal), "authenticated": False,
            "resources_reaudited": False, "activation_performed": False}
