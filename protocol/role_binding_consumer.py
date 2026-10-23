"""Offline role resolution layered on the existing snapshot-first F2 consumer.

No transport, device activation, role creation, expression playback or action
execution lives here. Registry loading verifies existing E1.4c release locks;
resolution is a host-side, expiring view, never an installed-device claim.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
import importlib.util
from functools import lru_cache
import math
from pathlib import Path
import sys
from typing import Any

from protocol.body_facts_consumer import BodyFactsConsumer, ConsumeOutcome, MAX_SAFE_INTEGER
from protocol.deskbot_role_profile import ROLE_ID_RE, validate_role_profile


REGISTRY_SCHEMA = "deskbot.role_binding_registry/v1"
RESOLUTION_SCHEMA = "deskbot.role_resolution/v1"
BINDING_VERSION = "deskbot.expression_bindings/v1"


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_SAFE_INTEGER:
        raise ValueError(f"{name} must be a JSON-safe integer >= {minimum}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty string of at most 256 characters")
    return value


@lru_cache(maxsize=4)
def _audit_module(root: Path):
    # Reuse its immutable Git-blob cache, not verified registries or file hashes.
    # Editable project inputs are still read and validated on every load.
    name = "deskbot_role_binding_release_audit"
    spec = importlib.util.spec_from_file_location(name, root / "scripts/audit-expression-release-lock.py")
    if spec is None or spec.loader is None:
        raise ValueError("existing release-lock validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class VerifiedBinding:
    """Loader-created evidence, not a security credential or activation token."""

    binding_id: str
    device_id: str
    magnet_profile_id: str
    magnet_profile_revision: int
    profile: dict[str, Any]
    release_audit: dict[str, Any]


def load_binding_registry(path: Path, *, root: Path) -> tuple[VerifiedBinding, ...]:
    """Read a bounded registry and verify every referenced profile/lock atomically.

    Fail the whole configuration rather than silently dropping a broken entry.
    The existing release validator verifies pinned source objects and EAF hashes.
    """
    root = root.resolve(strict=True)
    path = path.resolve(strict=True)
    relative = path.relative_to(root).as_posix()
    audit = _audit_module(root)
    path = audit.safe_project_path(root, relative, "registry")
    registry = audit.load_json(path, "registry")
    if set(registry) != {"schema", "bindings"} or registry.get("schema") != REGISTRY_SCHEMA:
        raise ValueError("registry fields/schema do not match v1")
    entries = registry["bindings"]
    if not isinstance(entries, list) or len(entries) > 128:
        raise ValueError("registry.bindings must contain at most 128 entries")
    required = {
        "binding_id", "device_id", "magnet_profile_id", "magnet_profile_revision",
        "role_profile", "release_lock",
    }
    result, identities, releases, observation_keys = [], set(), {}, set()
    checked_entries = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError("registry binding fields do not match v1")
        ident = entry["binding_id"]
        if not isinstance(ident, str) or not ROLE_ID_RE.fullmatch(ident) or ident in identities:
            raise ValueError("binding_id must be a unique role-style identifier")
        identities.add(ident)
        device = _text(entry["device_id"], "device_id")
        magnet = _text(entry["magnet_profile_id"], "magnet_profile_id")
        if len(magnet) > 128:
            raise ValueError("magnet_profile_id exceeds the role-profile identity limit")
        revision = _integer(entry["magnet_profile_revision"], "magnet_profile_revision", minimum=1)
        observation_key = (device, magnet, revision)
        if observation_key in observation_keys:
            raise ValueError("registry contains duplicate device/magnetic-profile/revision bindings")
        observation_keys.add(observation_key)
        checked_entries.append((entry, ident, device, magnet, revision))
    # Structural conflicts fail before expensive pinned-object/resource auditing.
    for entry, ident, device, magnet, revision in checked_entries:
        _, profile = audit.check_hashed_file(root, entry["role_profile"], "role_profile")
        validate_role_profile(profile)
        if profile["magnet_profile_id"] != magnet:
            raise ValueError("registry and role profile magnetic identities differ")
        lock_path, lock = audit.check_hashed_file(root, entry["release_lock"], "release_lock")
        key = (str(lock_path), entry["release_lock"]["sha256"])
        if key not in releases:
            releases[key] = audit.validate_lock(lock, root)
        release = releases[key]
        if lock["role_profile"] != entry["role_profile"]:
            raise ValueError("registry profile is not the exact profile pinned by the release lock")
        if (profile.get("binding_version") != BINDING_VERSION
                or profile["asset_version"] != release["asset_version"]
                or profile["binding_version"] != release["binding_version"]):
            raise ValueError("profile and verified release versions differ")
        result.append(VerifiedBinding(ident, device, magnet, revision, deepcopy(profile), deepcopy(release)))
    return tuple(result)


class RoleBindingConsumer:
    """Resolve only fresh snapshots against unique, explicitly confirmed bindings.

    ``now_ms`` is an injected monotonic host/replay clock, not device uptime or
    historic UTC. The caller must call tick while idle. Every accepted event
    requires a new snapshot: v1 event.facts is untyped and is not a shell delta.
    """

    def __init__(self, bindings: tuple[VerifiedBinding, ...], *, freshness_limit_ms: int) -> None:
        if len(bindings) > 128 or any(not isinstance(item, VerifiedBinding) for item in bindings):
            raise ValueError("bindings must contain at most 128 loader-created VerifiedBinding entries")
        for entry in bindings:
            if not isinstance(entry.binding_id, str) or not ROLE_ID_RE.fullmatch(entry.binding_id):
                raise ValueError("binding_id must be a role-style identifier")
            _text(entry.device_id, "device_id")
            if len(_text(entry.magnet_profile_id, "magnet_profile_id")) > 128:
                raise ValueError("magnet_profile_id exceeds the role-profile identity limit")
            _integer(entry.magnet_profile_revision, "magnet_profile_revision", minimum=1)
            validate_role_profile(entry.profile)
            release = entry.release_audit
            if not isinstance(release, dict):
                raise ValueError("release audit must be a verified local report")
            if (entry.profile["magnet_profile_id"] != entry.magnet_profile_id
                    or release.get("result") != "offline_validated"
                    or entry.profile.get("binding_version") != BINDING_VERSION
                    or release.get("binding_version") != entry.profile.get("binding_version")
                    or release.get("asset_version") != entry.profile["asset_version"]):
                raise ValueError("binding profile and verified release identities/versions differ")
        self._bindings = deepcopy(tuple(bindings))
        self._freshness_limit_ms = _integer(freshness_limit_ms, "freshness_limit_ms", minimum=1)
        self._body = BodyFactsConsumer()
        self._now_ms = 0
        self._deadline_ms: int | None = None
        self._snapshot_evidence: dict[str, Any] | None = None
        self._snapshot_payload: dict[str, Any] | None = None
        self._status = "waiting_for_snapshot"
        self._reason = "hello_required"
        self._selection: dict[str, Any] | None = None
        self._disconnected = False
        self._freshness_anchor: tuple[str, str, datetime, datetime, int, int] | None = None

    @property
    def state(self) -> dict[str, Any]:
        body = self._body.state
        return deepcopy({
            "schema": RESOLUTION_SCHEMA,
            "status": self._status, "reason": self._reason,
            "clock_ms": self._now_ms,
            "freshness_limit_ms": self._freshness_limit_ms,
            "valid_until_ms": self._deadline_ms,
            "evidence": {
                "device_id": body.device_id, "boot_id": body.boot_id,
                "session_id": body.session_id, "generation": body.generation,
                "last_event_sequence": body.last_event_sequence,
                "snapshot": self._snapshot_evidence,
            },
            "shell_observation": (
                self._snapshot_payload["shell_observation"] if self._snapshot_payload else None
            ),
            "selection": self._selection,
            "safety": {
                "mode": "offline_resolution_only", "activation_supported": False,
                "deployment_eligible": False, "hardware_io": False,
                "network_io": False, "expression_executed": False, "action_executed": False,
            },
        })

    def _clear(self, status: str, reason: str) -> None:
        self._status, self._reason, self._selection = status, reason, None
        self._deadline_ms = None

    def replace_bindings(self, bindings: tuple[VerifiedBinding, ...]) -> dict[str, Any]:
        """Replace verified host configuration; never reuse an old snapshot.

        Preserve transport deduplication, retired sessions and sensor freshness
        anchors. A configuration change must not make stale facts new again.
        Validation completes before changing this consumer.
        """
        checked = RoleBindingConsumer(bindings, freshness_limit_ms=self._freshness_limit_ms)
        self._bindings = checked._bindings
        self._snapshot_payload = self._snapshot_evidence = None
        self._clear("waiting_for_snapshot", "bindings_changed")
        return self.state

    def disconnect(self, *, now_ms: int) -> dict[str, Any]:
        """Invalidate selection; require hello without discarding F2 history."""
        now = _integer(now_ms, "now_ms")
        if now < self._now_ms:
            raise ValueError("host/replay clock must be monotonic")
        self._now_ms = now
        # Keep F2 duplicate and retired-session caches across transport loss.
        self._disconnected = True
        self._snapshot_payload = self._snapshot_evidence = None
        self._clear("disconnected", "transport_disconnected")
        return self.state

    def tick(self, now_ms: int) -> dict[str, Any]:
        now = _integer(now_ms, "now_ms")
        if now < self._now_ms:
            raise ValueError("host/replay clock must be monotonic")
        self._now_ms = now
        if self._deadline_ms is not None and now >= self._deadline_ms:
            self._clear("expired", "observation_expired")
        return self.state

    def consume(self, message: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
        self.tick(now_ms)
        if self._disconnected and (not isinstance(message, dict) or message.get("message_type") != "hello"):
            outcome = ConsumeOutcome("needs_snapshot", reason="hello_required_after_disconnect")
            return {"body_outcome": asdict(outcome), "resolution": self.state}
        outcome = self._body.consume(message)
        if outcome.status == "needs_snapshot":
            self._snapshot_payload = self._snapshot_evidence = None
            self._clear("waiting_for_snapshot", outcome.reason)
        elif outcome.accepted:
            if outcome.message_type == "hello":
                self._disconnected = False
                if outcome.boot_changed:
                    self._freshness_anchor = None
                self._snapshot_payload = self._snapshot_evidence = None
                self._clear("waiting_for_snapshot", "snapshot_required")
            elif outcome.message_type == "snapshot":
                self._snapshot_payload = deepcopy(self._body.state.latest_snapshot)
                self._snapshot_evidence = {
                    "message_id": message["message_id"],
                    "occurred_at": message["occurred_at"],
                    "transport_sequence": message["sequence"],
                }
                self._resolve(message)
            elif outcome.message_type == "event":
                # v1 events have untyped facts; none is authoritative shell state.
                self._snapshot_payload = self._snapshot_evidence = None
                self._clear("waiting_for_snapshot", "event_requires_snapshot")
        # Rejected, duplicate and ACK messages cannot refresh stale observations.
        return {"body_outcome": asdict(outcome), "resolution": self.state}

    def _resolve(self, message: dict[str, Any]) -> None:
        self._clear("unresolved", "binding_not_found")
        body, payload = self._body.state, self._snapshot_payload
        assert payload is not None
        shell, magnetic = payload["shell_observation"], payload["magnetic"]
        if not {"shell_observation", "magnetic_sample"} <= set(body.capabilities):
            self._clear("blocked", "shell_capabilities_missing")
            return
        if payload["base"]["link"] != "online":
            self._clear("blocked", "base_not_online")
            return
        if payload["base"]["mechanical_status"] != "ready":
            self._clear("blocked", "base_not_ready")
            return
        if magnetic["state"] != "fresh":
            self._clear("unresolved", "magnetic_" + magnetic["state"])
            return
        if shell["status"] != "known":
            self._clear("unresolved", "shell_" + shell["status"])
            return
        if not magnetic.get("sample_at") or not shell.get("observed_at") or magnetic.get("age_ms") is None:
            self._clear("blocked", "freshness_evidence_missing")
            return
        occurred = datetime.fromisoformat(message["occurred_at"].replace("Z", "+00:00"))
        stamps = [datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                  for stamp in (magnetic["sample_at"], shell["observed_at"])]
        ages = []
        for stamp in stamps:
            age = (occurred - stamp).total_seconds() * 1000
            if age < 0:
                self._clear("blocked", "future_observation_timestamp")
                return
            ages.append(age)
        sample_age = math.ceil(max(ages[0], magnetic["age_ms"]))
        shell_age = math.ceil(ages[1])
        if max(sample_age, shell_age) >= self._freshness_limit_ms:
            self._clear("expired", "observation_expired")
            return
        deadlines = [min(MAX_SAFE_INTEGER, self._now_ms + self._freshness_limit_ms - age)
                     for age in (sample_age, shell_age)]
        anchor = self._freshness_anchor
        identity = (body.device_id, body.boot_id)
        if anchor is not None and anchor[:2] == identity:
            if stamps[0] < anchor[2] or stamps[1] < anchor[3]:
                self._clear("blocked", "observation_timestamp_regressed")
                return
            for index in range(2):
                if stamps[index] == anchor[index + 2]:
                    deadlines[index] = min(deadlines[index], anchor[index + 4])
        self._freshness_anchor = (*identity, *stamps, *deadlines)
        deadline = min(deadlines)
        if deadline <= self._now_ms:
            self._clear("expired", "observation_expired")
            return
        candidates = [entry for entry in self._bindings
                      if entry.device_id == body.device_id
                      and entry.magnet_profile_id == shell["profile_id"]]
        if not candidates:
            return
        candidates = [entry for entry in candidates
                      if entry.magnet_profile_revision == shell["profile_revision"]]
        if not candidates:
            self._clear("blocked", "magnetic_revision_mismatch")
            return
        if len(candidates) != 1:
            self._clear("blocked", "binding_conflict")
            return
        entry = candidates[0]
        if entry.profile["binding_state"] == "draft":
            self._clear("unresolved", "binding_not_confirmed")
            return
        if entry.profile["binding_state"] == "disabled":
            self._clear("unresolved", "binding_disabled")
            return
        profile, release = entry.profile, entry.release_audit
        self._selection = {
            "binding_id": entry.binding_id,
            "shell_id": profile["shell_id"], "role_id": profile["role_id"],
            "magnet_profile_id": entry.magnet_profile_id,
            "magnet_profile_revision": entry.magnet_profile_revision,
            "profile_revision": profile["profile_revision"],
            "display_name": profile["display_name"], "voice_id": profile["voice_id"],
            "catchphrases": deepcopy(profile["catchphrases"]),
            "release_id": release["release_id"],
            "asset_version": release["asset_version"],
            "binding_version": release["binding_version"],
            "expression_map": deepcopy(release["mapping_comparison"]["profile_expression_map_after"]),
            "action_map": deepcopy(profile["action_map"]),
        }
        self._status, self._reason = "resolved_offline", "confirmed_unique_binding"
        self._deadline_ms = deadline
