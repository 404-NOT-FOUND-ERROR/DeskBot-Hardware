#!/usr/bin/env python3
"""Inspect an offline ``deskbot.body.v1`` replay without hardware I/O.

The input is a directory containing ``messages.jsonl`` and its adjacent
``manifest.json``, or a direct path to ``messages.jsonl``.  The command only
reads those files and writes a self-contained ``report.json`` and
``report.html`` below ``artifacts/body-facts-replay-20261018``.  It never opens
a serial port, contacts a network, writes NVS, or changes a device.

The HTML is intentionally a diagnostic view, not a confidence dashboard:
``similarity`` and ``margin`` remain raw experiment indicators and are never
converted into a probability.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "body-facts-replay-20261018"
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_INPUT_LINES = 100_000
MAX_OUTPUT_BYTES = 4 * 1024 * 1024

TOP_LEVEL_REQUIRED = (
    "schema", "message_type", "message_id", "device_id", "boot_id",
    "session_id", "sequence", "occurred_at", "payload",
)
TOP_LEVEL_ALLOWED = set(TOP_LEVEL_REQUIRED)
MESSAGE_TYPES = {"hello", "snapshot", "event", "ack"}
SHELL_STATUSES = {"no_data", "warming_up", "known", "unknown", "ambiguous", "unstable", "stale"}
MAGNETIC_STATES = {"no_data", "fresh", "stale"}
BASE_LINKS = {"unknown", "online", "offline"}
MECHANICAL_STATES = {"unknown", "homing", "ready", "failed"}
CAPABILITIES = {"base_events", "magnetic_sample", "shell_observation", "touch", "motion", "expression", "audio_doa"}
INT16_MIN, INT16_MAX = -32768, 32767


def _unknown_keys(value: object, allowed: set[str], name: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        unknown = sorted(set(value) - allowed)
        if unknown:
            errors.append(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number or null")
    return float(value)


def _nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _load_input(value: str) -> tuple[Path, Path, list[dict[str, object]], bytes, dict[str, object] | None]:
    requested = Path(value).resolve()
    messages_path = requested / "messages.jsonl" if requested.is_dir() else requested
    manifest_path = messages_path.parent / "manifest.json"
    if not messages_path.is_file():
        raise ValueError(f"messages.jsonl not found: {messages_path}")
    raw = messages_path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError(f"messages.jsonl exceeds {MAX_INPUT_BYTES} bytes")
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("messages.jsonl is not valid UTF-8") from exc
    if len(lines) > MAX_INPUT_LINES:
        raise ValueError(f"messages.jsonl exceeds {MAX_INPUT_LINES} lines")
    messages: list[dict[str, object]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = _json(line, f"messages.jsonl line {number}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"messages.jsonl line {number} is invalid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"messages.jsonl line {number} must be a JSON object")
        messages.append(value)
    if not messages:
        raise ValueError("messages.jsonl contains no messages")
    manifest: dict[str, object] | None = None
    if manifest_path.is_file():
        value = _json(manifest_path.read_text(encoding="utf-8"), "manifest.json")
        if not isinstance(value, dict):
            raise ValueError("manifest.json must be a JSON object")
        manifest = value
    return messages_path, manifest_path, messages, raw, manifest


def _json(raw: str, label: str) -> object:
    def reject_constant(token: str) -> object:
        raise ValueError(f"non-finite JSON number in {label}: {token}")
    def finite(value: object) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"non-finite JSON number in {label}")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)
    try:
        value = json.loads(raw, parse_constant=reject_constant)
        finite(value)
        return value
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {label}: {exc.msg}") from exc


def _manifest_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty project-relative path")
    path = (Path(value) if Path(value).is_absolute() else ROOT / value).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes project root") from exc
    return path


def _verify_manifest_file(manifest: dict[str, object], path_key: str, hash_key: str, errors: list[str], warnings: list[str]) -> None:
    path_value = manifest.get(path_key)
    hash_value = manifest.get(hash_key)
    if path_value is None and hash_value is None:
        warnings.append(f"manifest provenance field {path_key}/{hash_key} is absent")
        return
    if not isinstance(path_value, str) or not path_value:
        errors.append(f"manifest.{path_key} must be a non-empty project-relative path")
        return
    if not isinstance(hash_value, str) or len(hash_value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in hash_value):
        errors.append(f"manifest.{hash_key} must be a SHA-256 hex string")
        return
    try:
        path = _manifest_path(path_value, f"manifest.{path_key}")
        if not path.is_file():
            errors.append(f"manifest.{path_key} not found: {path}")
            return
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual.lower() != hash_value.lower():
            errors.append(f"manifest.{hash_key} does not match {path_value}")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))


def _schema_validate(messages: list[dict[str, object]], errors: list[str], warnings: list[str]) -> str:
    """Use the frozen protocol schema when the optional validator is installed."""
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        warnings.append("jsonschema is unavailable; strict F1 field and provenance checks ran without optional full JSON Schema validation")
        return "f1_contract_without_optional_jsonschema"
    try:
        schema = _json((ROOT / "protocol" / "deskbot-body-facts-v1.schema.json").read_text(encoding="utf-8"), "body facts schema")
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for index, message in enumerate(messages, 1):
            for error in validator.iter_errors(message):
                location = ".".join(str(part) for part in error.absolute_path) or "message"
                errors.append(f"message {index} schema {location}: {error.message}")
    except (OSError, ValueError) as exc:
        errors.append(f"cannot load body facts JSON Schema: {exc}")
    return "jsonschema_and_f1_provenance"


def _provenance_snapshots(manifest: dict[str, object], errors: list[str]) -> tuple[dict[str, object] | None, dict[str, object] | None, dict[str, str], str | None]:
    """Bind the final snapshot to the files frozen in the evaluation manifest."""
    try:
        validation_path = _manifest_path(manifest.get("validation_manifest"), "manifest.validation_manifest")
        validation = _json(validation_path.read_text(encoding="utf-8"), "validation manifest")
        if not isinstance(validation, dict) or validation.get("schema") != "deskbot.magnetic.validation.manifest.v1":
            raise ValueError("unsupported validation manifest schema")
        segments = validation.get("segments")
        if not isinstance(segments, list):
            raise ValueError("validation manifest segments must be a list")
        matches = [segment for segment in segments if isinstance(segment, dict) and segment.get("id") == manifest.get("segment_id")]
        if len(matches) != 1 or matches[0].get("role") != "evaluation" or matches[0].get("valid") is not True:
            raise ValueError("manifest.segment_id must identify exactly one valid evaluation segment")
        segment = matches[0]
        paths = (
            ("raw_path", "raw_sha256", "raw", None),
            ("m1_samples_path", "m1_samples_sha256", "m1", "samples.jsonl"),
            ("input_path", "input_sha256", "m2", "recognition.jsonl"),
            ("profiles_path", "profiles_sha256", "m2", "profiles.json"),
        )
        for path_key, hash_key, layer, child in paths:
            source = segment.get(layer)
            if not isinstance(source, dict):
                raise ValueError(f"validation segment has no {layer} provenance")
            source_path = _manifest_path(source.get("path"), f"validation {layer} path")
            expected_path = source_path / child if child else source_path
            files = source.get("files")
            expected_hash = files.get(child) if child and isinstance(files, dict) else source.get("sha256")
            if _manifest_path(manifest.get(path_key), f"manifest.{path_key}") != expected_path.resolve() or manifest.get(hash_key) != expected_hash:
                raise ValueError(f"manifest.{path_key}/{hash_key} disagrees with frozen evaluation segment")
        def rows(path_key: str) -> list[dict[str, object]]:
            path = _manifest_path(manifest.get(path_key), f"manifest.{path_key}")
            if path.stat().st_size > MAX_INPUT_BYTES:
                raise ValueError(f"manifest.{path_key} exceeds input safety limit")
            values = [_json(line, path_key) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if not values or any(not isinstance(item, dict) for item in values):
                raise ValueError(f"manifest.{path_key} must contain JSONL objects")
            return values
        m1, m2 = rows("m1_samples_path"), rows("input_path")
        if len(m1) != len(m2) or segment.get("sample_count") != len(m1):
            raise ValueError("frozen evaluation sample counts disagree")
        for index, (sample, recognition) in enumerate(zip(m1, m2), 1):
            if sample.get("sequence") != index or recognition.get("sequence") != index:
                raise ValueError("source M1/M2 sequence is not contiguous")
            if _timestamp(sample.get("host_received_at"), "M1 host_received_at") != _timestamp(recognition.get("timestamp"), "M2 timestamp"):
                raise ValueError("source M1/M2 timestamps disagree")
        profiles_path = _manifest_path(manifest.get("profiles_path"), "manifest.profiles_path")
        profile_value = _json(profiles_path.read_text(encoding="utf-8"), "profiles.json")
        if not isinstance(profile_value, list):
            raise ValueError("profiles.json must contain a list")
        profile_ids = set()
        for entry in profile_value:
            profile = entry.get("profile") if isinstance(entry, dict) else None
            if not isinstance(profile, dict) or not isinstance(profile.get("profile_id"), str) or not profile["profile_id"]:
                raise ValueError("profiles.json contains an invalid profile")
            profile_ids.add(profile["profile_id"])
        expected_namespace = "offline:" + hashlib.sha256(profiles_path.read_bytes()).hexdigest()[:16]
        if manifest.get("profile_namespace") != expected_namespace:
            raise ValueError("manifest.profile_namespace disagrees with profiles SHA-256")
        labels = {}
        for entry in profile_value:
            profile = entry["profile"]
            labels[profile["profile_id"]] = profile.get("label")
        return m1[-1], m2[-1], labels, expected_namespace
    except (OSError, ValueError, TypeError) as exc:
        errors.append(str(exc))
        return None, None, {}, None


def _check_snapshot(payload: object, index: int, occurred_at: datetime | None, errors: list[str]) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        errors.append(f"message {index}: snapshot payload must be an object")
        return None
    _unknown_keys(payload, {"kind", "base", "magnetic", "shell_observation", "event_cursor", "touch", "motion", "expression"}, f"message {index} snapshot payload", errors)
    if payload.get("kind") != "snapshot":
        errors.append(f"message {index}: snapshot payload.kind must be 'snapshot'")
    base = payload.get("base")
    magnetic = payload.get("magnetic")
    shell = payload.get("shell_observation")
    cursor = payload.get("event_cursor")
    if not isinstance(base, dict):
        errors.append(f"message {index}: snapshot.base must be an object")
    else:
        _unknown_keys(base, {"link", "mechanical_status", "last_event_sequence"}, f"message {index} snapshot.base", errors)
        for name in ("link", "mechanical_status", "last_event_sequence"):
            if name not in base:
                errors.append(f"message {index}: snapshot.base.{name} is required")
        if base.get("link") not in BASE_LINKS:
            errors.append(f"message {index}: invalid base.link")
        if base.get("mechanical_status") not in MECHANICAL_STATES:
            errors.append(f"message {index}: invalid base.mechanical_status")
        try:
            _nonnegative_integer(base.get("last_event_sequence"), "base.last_event_sequence")
        except ValueError as exc:
            errors.append(f"message {index}: {exc}")
    if not isinstance(magnetic, dict):
        errors.append(f"message {index}: snapshot.magnetic must be an object")
    else:
        _unknown_keys(magnetic, {"state", "sensor_kind", "x", "y", "z", "sample_at", "age_ms"}, f"message {index} snapshot.magnetic", errors)
        if "state" not in magnetic:
            errors.append(f"message {index}: snapshot.magnetic.state is required")
        if magnetic.get("state") not in MAGNETIC_STATES:
            errors.append(f"message {index}: invalid magnetic.state")
        if "sensor_kind" in magnetic and magnetic["sensor_kind"] is not None and (not isinstance(magnetic["sensor_kind"], str) or not magnetic["sensor_kind"]):
            errors.append(f"message {index}: magnetic.sensor_kind must be a non-empty string or null")
        for axis in ("x", "y", "z"):
            value = magnetic.get(axis)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                errors.append(f"message {index}: magnetic.{axis} must be an integer or null")
            elif isinstance(value, int) and not INT16_MIN <= value <= INT16_MAX:
                errors.append(f"message {index}: magnetic.{axis} must fit signed int16")
        sample_at = magnetic.get("sample_at")
        if sample_at is not None:
            try:
                sample_time = _timestamp(sample_at, f"magnetic.sample_at (message {index})")
                if occurred_at is not None and sample_time > occurred_at:
                    errors.append(f"message {index}: magnetic.sample_at cannot be after occurred_at")
                age = magnetic.get("age_ms")
                if occurred_at is not None and age is not None:
                    expected_age = int(round((occurred_at - sample_time).total_seconds() * 1000))
                    if expected_age < 0 or age != expected_age:
                        errors.append(f"message {index}: magnetic.age_ms does not match occurred_at-sample_at ({expected_age} ms expected)")
            except ValueError as exc:
                errors.append(str(exc))
        try:
            age = magnetic.get("age_ms")
            if age is not None:
                _nonnegative_integer(age, "magnetic.age_ms")
        except ValueError as exc:
            errors.append(f"message {index}: {exc}")
    if not isinstance(shell, dict):
        errors.append(f"message {index}: snapshot.shell_observation must be an object")
    else:
        _unknown_keys(shell, {"status", "profile_id", "profile_revision", "sensor_kind", "similarity", "margin", "sample_count", "observed_at"}, f"message {index} snapshot.shell_observation", errors)
        for name in ("status", "profile_id", "profile_revision", "sample_count"):
            if name not in shell:
                errors.append(f"message {index}: snapshot.shell_observation.{name} is required")
        status = shell.get("status")
        if status not in SHELL_STATUSES:
            errors.append(f"message {index}: invalid shell_observation.status")
        profile_id = shell.get("profile_id")
        if status == "known" and (not isinstance(profile_id, str) or not profile_id):
            errors.append(f"message {index}: known shell must have profile_id")
        if status != "known" and profile_id is not None:
            errors.append(f"message {index}: non-known shell must have profile_id=null")
        try:
            _nonnegative_integer(shell.get("profile_revision"), "shell_observation.profile_revision")
            _nonnegative_integer(shell.get("sample_count"), "shell_observation.sample_count")
        except ValueError as exc:
            errors.append(f"message {index}: {exc}")
        for name in ("similarity", "margin"):
            try:
                _finite_number(shell.get(name), f"shell_observation.{name}")
            except ValueError as exc:
                errors.append(f"message {index}: {exc}")
        for name in ("sensor_kind",):
            if name in shell and shell[name] is not None and (not isinstance(shell[name], str) or not shell[name]):
                errors.append(f"message {index}: shell_observation.{name} must be a non-empty string or null")
        observed_at = shell.get("observed_at")
        if observed_at is not None:
            try:
                observed_time = _timestamp(observed_at, f"shell_observation.observed_at (message {index})")
                if occurred_at is not None and observed_time != occurred_at:
                    errors.append(f"message {index}: shell_observation.observed_at must match occurred_at in offline replay")
            except ValueError as exc:
                errors.append(str(exc))
    if not isinstance(cursor, dict):
        errors.append(f"message {index}: snapshot.event_cursor must be an object")
    else:
        _unknown_keys(cursor, {"oldest_available_sequence", "high_watermark_sequence", "gap_detected"}, f"message {index} snapshot.event_cursor", errors)
        for name in ("oldest_available_sequence", "high_watermark_sequence", "gap_detected"):
            if name not in cursor:
                errors.append(f"message {index}: snapshot.event_cursor.{name} is required")
        for name in ("oldest_available_sequence", "high_watermark_sequence"):
            try:
                value = cursor.get(name)
                if value is not None:
                    _nonnegative_integer(value, f"event_cursor.{name}")
            except ValueError as exc:
                errors.append(f"message {index}: {exc}")
        if not isinstance(cursor.get("gap_detected"), bool):
            errors.append(f"message {index}: event_cursor.gap_detected must be boolean")
    for name, allowed in (
        ("touch", {"state", "count"}),
        ("motion", {"state", "action", "angle"}),
        ("expression", {"state", "asset_id", "asset_version"}),
    ):
        optional = payload.get(name)
        if optional is not None:
            if not isinstance(optional, dict):
                errors.append(f"message {index}: snapshot.{name} must be an object")
            else:
                _unknown_keys(optional, allowed, f"message {index} snapshot.{name}", errors)
                if name == "touch":
                    if "state" in optional and optional["state"] not in {"unknown", "idle", "contact", "petting"}:
                        errors.append(f"message {index}: invalid snapshot.touch.state")
                    if "count" in optional:
                        try: _nonnegative_integer(optional["count"], "snapshot.touch.count")
                        except ValueError as exc: errors.append(f"message {index}: {exc}")
                elif name == "motion":
                    if "state" in optional and optional["state"] not in {"unknown", "idle", "moving", "complete"}:
                        errors.append(f"message {index}: invalid snapshot.motion.state")
                    if "action" in optional and optional["action"] is not None and not isinstance(optional["action"], str):
                        errors.append(f"message {index}: snapshot.motion.action must be string or null")
                    if "angle" in optional:
                        try: _finite_number(optional["angle"], "snapshot.motion.angle")
                        except ValueError as exc: errors.append(f"message {index}: {exc}")
                else:
                    if "state" in optional and optional["state"] not in {"unknown", "idle", "playing"}:
                        errors.append(f"message {index}: invalid snapshot.expression.state")
                    for key in ("asset_id", "asset_version"):
                        if key in optional and optional[key] is not None and not isinstance(optional[key], str):
                            errors.append(f"message {index}: snapshot.expression.{key} must be string or null")
    if isinstance(base, dict) and isinstance(magnetic, dict) and isinstance(shell, dict):
        return {
            "sequence": None,
            "occurred_at": None,
            "base": {"link": base.get("link"), "mechanical_status": base.get("mechanical_status")},
            "magnetic": {name: magnetic.get(name) for name in ("state", "sensor_kind", "x", "y", "z", "sample_at", "age_ms")},
            "shell_observation": {name: shell.get(name) for name in ("status", "profile_id", "sensor_kind", "similarity", "margin", "sample_count", "observed_at")},
        }
    return None


def _validate(messages: list[dict[str, object]], raw: bytes, manifest: dict[str, object] | None, messages_path: Path, manifest_path: Path) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, str]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "status": "pass" if passed else "fail", "detail": detail})

    if manifest is None:
        errors.append(f"offline replay manifest not found: {manifest_path}")
        check("offline_manifest", False, "manifest.json is required for an offline replay report")
    else:
        if manifest.get("schema") != "deskbot.body.facts.replay.v1":
            errors.append("manifest.schema must be deskbot.body.facts.replay.v1")
        source_mode = manifest.get("source_mode")
        hardware_io = manifest.get("hardware_io")
        if source_mode != "offline_replay":
            errors.append(f"manifest.source_mode must be offline_replay, got {source_mode!r}")
        if hardware_io is not False:
            errors.append("manifest.hardware_io must be false")
        if manifest.get("event_count") not in (0, None):
            errors.append("offline replay manifest must declare event_count=0")
        if manifest.get("profiles_are_experiment_labels") is not True:
            errors.append("manifest.profiles_are_experiment_labels must be true")
        check("offline_manifest", source_mode == "offline_replay" and hardware_io is False, "source_mode=offline_replay and hardware_io=false")
        messages_sha = manifest.get("messages_sha256")
        if not isinstance(messages_sha, str) or len(messages_sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in messages_sha):
            errors.append("manifest.messages_sha256 is required and must be a SHA-256 hex string")
        elif messages_sha.lower() != hashlib.sha256(raw).hexdigest():
            errors.append("manifest.messages_sha256 does not match messages.jsonl")
        # These provenance fields are produced by the current adapter.  A
        # missing field is reported as a warning for hand-made fixtures, while
        # an explicitly supplied path/hash pair is always verified.
        for path_key, hash_key in (("raw_path", "raw_sha256"), ("m1_samples_path", "m1_samples_sha256"), ("profiles_path", "profiles_sha256"), ("input_path", "input_sha256")):
            _verify_manifest_file(manifest, path_key, hash_key, errors, warnings)
        validation_path = manifest.get("validation_manifest")
        validation_hash = manifest.get("validation_manifest_sha256")
        if validation_path is None:
            warnings.append("manifest.validation_manifest is absent")
        else:
            if not isinstance(validation_path, str) or not validation_path:
                errors.append("manifest.validation_manifest must be a project-relative path")
            else:
                try:
                    validation_file = _manifest_path(validation_path, "manifest.validation_manifest")
                    if not validation_file.is_file():
                        errors.append(f"manifest.validation_manifest not found: {validation_file}")
                    elif validation_hash is not None:
                        if not isinstance(validation_hash, str) or len(validation_hash) != 64 or any(c not in "0123456789abcdefABCDEF" for c in validation_hash):
                            errors.append("manifest.validation_manifest_sha256 must be a SHA-256 hex string")
                        elif hashlib.sha256(validation_file.read_bytes()).hexdigest() != validation_hash.lower():
                            errors.append("manifest.validation_manifest_sha256 does not match validation_manifest")
                    else:
                        warnings.append("manifest.validation_manifest_sha256 is absent")
                except (OSError, ValueError) as exc:
                    errors.append(str(exc))
        if not isinstance(manifest.get("segment_id"), str) or not manifest.get("segment_id"):
            warnings.append("manifest.segment_id is absent")
        if manifest.get("physical_boot_id") is not None:
            errors.append("offline replay must not claim a physical_boot_id")
        boot_id = manifest.get("boot_id")
        if not isinstance(boot_id, str) or not boot_id.startswith("offline-unknown-boot:"):
            errors.append("manifest.boot_id must use the offline-unknown-boot: prefix")
        device_id = manifest.get("device_id")
        session_id = manifest.get("session_id")
        if not isinstance(device_id, str) or not device_id.startswith("offline-replay:"):
            errors.append("manifest.device_id must use the offline-replay: prefix")
        if not isinstance(session_id, str) or not session_id.startswith("offline-session:"):
            errors.append("manifest.session_id must use the offline-session: prefix")

    sequences: list[int] = []
    message_ids: set[str] = set()
    identity: tuple[str, str, str] | None = None
    timestamps: list[datetime] = []
    hello_count = 0
    snapshots: list[dict[str, object]] = []
    event_count = 0
    ack_count = 0
    for index, message in enumerate(messages, 1):
        _unknown_keys(message, TOP_LEVEL_ALLOWED, f"message {index}", errors)
        missing = [name for name in TOP_LEVEL_REQUIRED if name not in message]
        if missing:
            errors.append(f"message {index}: missing {', '.join(missing)}")
            continue
        if message.get("schema") != "deskbot.body.v1":
            errors.append(f"message {index}: schema must be deskbot.body.v1")
        message_type = message.get("message_type")
        if message_type not in MESSAGE_TYPES:
            errors.append(f"message {index}: invalid message_type {message_type!r}")
        try:
            message_id = _string(message.get("message_id"), "message_id")
            device_id = _string(message.get("device_id"), "device_id")
            boot_id = _string(message.get("boot_id"), "boot_id")
            session_id = _string(message.get("session_id"), "session_id")
            sequence = _nonnegative_integer(message.get("sequence"), "sequence")
            occurred_at = _timestamp(message.get("occurred_at"), f"occurred_at (message {index})")
            timestamps.append(occurred_at)
            sequences.append(sequence)
            if message_id in message_ids:
                errors.append(f"message {index}: duplicate message_id")
            message_ids.add(message_id)
            current_identity = (device_id, boot_id, session_id)
            if identity is None:
                identity = current_identity
            elif current_identity != identity:
                errors.append(f"message {index}: device_id/boot_id/session_id changed within stream")
        except ValueError as exc:
            errors.append(f"message {index}: {exc}")
            continue
        if message_type == "hello":
            hello_count += 1
            payload = message.get("payload")
            if not isinstance(payload, dict):
                errors.append(f"message {index}: hello payload must be an object")
            else:
                _unknown_keys(payload, {"kind", "firmware_id", "hardware_id", "capabilities", "max_event_backlog"}, f"message {index} hello payload", errors)
            if not isinstance(payload, dict) or payload.get("kind") != "hello":
                errors.append(f"message {index}: hello payload.kind must be 'hello'")
            elif not isinstance(payload.get("firmware_id"), str) or not payload.get("firmware_id"):
                errors.append(f"message {index}: hello payload.firmware_id is required")
            elif (not isinstance(payload.get("capabilities"), list)
                  or any(not isinstance(item, str) or item not in CAPABILITIES for item in payload.get("capabilities", []))
                  or len(set(payload.get("capabilities", []))) != len(payload.get("capabilities", []))):
                errors.append(f"message {index}: hello payload.capabilities is invalid")
            if isinstance(payload, dict) and "hardware_id" in payload and payload["hardware_id"] is not None and (not isinstance(payload["hardware_id"], str) or not payload["hardware_id"]):
                errors.append(f"message {index}: hello payload.hardware_id is invalid")
            if isinstance(payload, dict) and "max_event_backlog" in payload:
                try:
                    _nonnegative_integer(payload["max_event_backlog"], f"hello max_event_backlog (message {index})")
                except ValueError as exc:
                    errors.append(f"message {index}: {exc}")
        elif message_type == "snapshot":
            snapshot = _check_snapshot(message.get("payload"), index, occurred_at, errors)
            if snapshot is not None:
                snapshot["sequence"] = sequence
                snapshot["occurred_at"] = message.get("occurred_at")
                snapshots.append(snapshot)
        elif message_type == "event":
            event_count += 1
            errors.append(f"message {index}: event messages are not allowed in an offline replay")
        elif message_type == "ack":
            ack_count += 1
            errors.append(f"message {index}: ack messages are not allowed in an offline replay")
    if sequences and sequences != sorted(sequences) or (sequences and sequences != list(range(sequences[0], sequences[0] + len(sequences)))):
        errors.append("message sequence must be contiguous and increasing")
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        errors.append("message occurred_at timestamps must be strictly increasing")
    check("message_sequence", not any("message sequence" in error for error in errors), "contiguous message sequence")
    if hello_count != 1 or not messages or messages[0].get("message_type") != "hello":
        errors.append("offline replay must contain exactly one hello as the first message")
    check("hello_first", hello_count == 1 and bool(messages) and messages[0].get("message_type") == "hello", "one hello message at sequence 0")
    if messages and messages[0].get("sequence") != 0:
        errors.append("hello message must have sequence 0")
    if not snapshots:
        errors.append("offline replay must contain at least one snapshot")
    check("snapshot_present", bool(snapshots), "at least one validated snapshot")
    schema_mode = _schema_validate(messages, errors, warnings)

    status_counts = Counter(item["shell_observation"]["status"] for item in snapshots)
    latest = snapshots[-1] if snapshots else None
    if manifest is not None and isinstance(manifest.get("profiles_path"), str):
        try:
            profile_file = _manifest_path(manifest["profiles_path"], "manifest.profiles_path")
            if profile_file.is_file():
                profile_value = _json(profile_file.read_text(encoding="utf-8"), "profiles.json")
                profile_ids: set[str] = set()
                if isinstance(profile_value, list):
                    for entry in profile_value:
                        candidate = entry.get("profile") if isinstance(entry, dict) else None
                        candidate = candidate if isinstance(candidate, dict) else entry
                        if isinstance(candidate, dict) and isinstance(candidate.get("profile_id"), str):
                            profile_ids.add(candidate["profile_id"])
                for snapshot in snapshots:
                    profile_id = snapshot["shell_observation"].get("profile_id")
                    namespace = manifest.get("profile_namespace")
                    if isinstance(profile_id, str) and (not isinstance(namespace, str) or not namespace.startswith("offline:") or not profile_id.startswith(namespace + ":")):
                        errors.append(f"snapshot profile_id is outside manifest profile namespace: {profile_id}")
                    base_id = profile_id[len(namespace) + 1:] if isinstance(profile_id, str) and isinstance(namespace, str) and profile_id.startswith(namespace + ":") else None
                    if isinstance(profile_id, str) and profile_ids and base_id not in profile_ids:
                        errors.append(f"snapshot profile_id is absent from manifest profiles: {profile_id}")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    if manifest is not None and identity is not None:
        for name, actual in zip(("device_id", "boot_id", "session_id"), identity):
            declared = manifest.get(name)
            if declared is not None and declared != actual:
                errors.append(f"manifest.{name} does not match messages.jsonl")
        first_message = messages[0]
        first_payload = first_message.get("payload") if isinstance(first_message.get("payload"), dict) else {}
        if manifest.get("firmware_id") is not None and manifest.get("firmware_id") != first_payload.get("firmware_id"):
            errors.append("manifest.firmware_id does not match hello payload")
    if manifest is not None and manifest.get("snapshot_count") is not None and manifest.get("snapshot_count") != len(snapshots):
        errors.append(f"manifest.snapshot_count={manifest.get('snapshot_count')} does not match {len(snapshots)} snapshots")
    if manifest is not None:
        namespace = manifest.get("profile_namespace")
        for snapshot in snapshots:
            profile_id = snapshot["shell_observation"].get("profile_id")
            if isinstance(profile_id, str) and isinstance(namespace, str) and (not namespace.startswith("offline:") or not profile_id.startswith(namespace + ":")):
                errors.append(f"snapshot profile_id is outside manifest profile namespace: {profile_id}")
        if manifest.get("message_count") is not None and manifest.get("message_count") != len(messages):
            errors.append("manifest.message_count does not match messages.jsonl")
        if manifest.get("validation_manifest") is not None:
            source_m1, source_m2, profile_labels, expected_namespace = _provenance_snapshots(manifest, errors)
            if source_m1 is not None and source_m2 is not None and latest is not None:
                magnetic, shell = latest["magnetic"], latest["shell_observation"]
                expected_time = _timestamp(source_m1.get("host_received_at"), "source M1 host_received_at")
                if _timestamp(latest.get("occurred_at"), "latest occurred_at") != expected_time:
                    errors.append("latest snapshot occurred_at disagrees with final source sample time")
                if any(magnetic.get(axis) != source_m1.get(axis) for axis in ("x", "y", "z")) or magnetic.get("sensor_kind") != source_m1.get("kind_name"):
                    errors.append("latest snapshot XYZ/sensor_kind disagrees with final source M1 sample")
                if magnetic.get("sample_at") is None or _timestamp(magnetic.get("sample_at"), "latest sample_at") != expected_time or magnetic.get("age_ms") != 0:
                    errors.append("latest snapshot sample_at/age_ms disagrees with final source M1 sample")
                expected_pid = f"{expected_namespace}:{source_m2.get('profile_id')}" if source_m2.get("status") == "known" else None
                expected_shell = {"status": source_m2.get("status"), "profile_id": expected_pid, "similarity": source_m2.get("similarity"), "margin": source_m2.get("margin"), "sample_count": source_m2.get("window_count"), "sensor_kind": source_m1.get("kind_name")}
                if any(shell.get(name) != value for name, value in expected_shell.items()):
                    errors.append("latest snapshot shell observation disagrees with final source M2 result")
                if shell.get("observed_at") is None or _timestamp(shell.get("observed_at"), "latest observed_at") != expected_time:
                    errors.append("latest snapshot observed_at disagrees with final source M2 result")
                if source_m2.get("status") == "known" and (source_m2.get("profile_id") not in profile_labels or source_m2.get("candidate_id") != source_m2.get("profile_id") or source_m2.get("label") != profile_labels.get(source_m2.get("profile_id"))):
                    errors.append("final source M2 identity disagrees with declared profiles")
    summary = {
        "message_count": len(messages),
        "snapshot_count": len(snapshots),
        "event_count": event_count,
        "ack_count": ack_count,
        "status_counts": dict(status_counts),
        "latest_occurred_at": latest.get("occurred_at") if latest else None,
    }
    return {
        "schema": "deskbot.body.facts.viewer.v1",
        "source": {
            "mode": manifest.get("source_mode") if manifest else "unknown",
            "hardware_io": manifest.get("hardware_io") if manifest else None,
            "offline_only": manifest is not None and manifest.get("source_mode") == "offline_replay" and manifest.get("hardware_io") is False,
            "messages_path": str(messages_path),
            "manifest_path": str(manifest_path),
            "messages_sha256": hashlib.sha256(raw).hexdigest(),
            "input_sha256": manifest.get("input_sha256") if manifest else None,
            "segment_id": manifest.get("segment_id") if manifest else None,
            "validation_manifest": manifest.get("validation_manifest") if manifest else None,
            "validation_manifest_sha256": manifest.get("validation_manifest_sha256") if manifest else None,
            "raw_path": manifest.get("raw_path") if manifest else None,
            "raw_sha256": manifest.get("raw_sha256") if manifest else None,
            "m1_samples_path": manifest.get("m1_samples_path") if manifest else None,
            "m1_samples_sha256": manifest.get("m1_samples_sha256") if manifest else None,
            "profiles_path": manifest.get("profiles_path") if manifest else None,
            "profiles_sha256": manifest.get("profiles_sha256") if manifest else None,
            "time_basis": manifest.get("time_basis") if manifest else None,
            "sample_provenance": manifest.get("sample_provenance") if manifest else None,
            "schema_validation": schema_mode,
        },
        "integrity": {
            "status": "pass" if not errors else "fail",
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
        },
        "identity": {
            "device_id": identity[0] if identity else None,
            "boot_id": identity[1] if identity else None,
            "session_id": identity[2] if identity else None,
            "physical_device_claimed": bool(identity and not str(identity[0]).startswith("offline-")),
        },
        "summary": summary,
        "latest_snapshot": latest,
        "snapshots": snapshots,
        "provenance_note": "离线回放；不代表当前物理设备状态。similarity 和 margin 是原始实验指标，不是概率或识别正确率。",
    }


def _output_dir(value: str | None) -> Path:
    if value:
        requested = Path(value)
        target = (requested if requested.is_absolute() else ROOT / requested).resolve()
    else:
        target = ARTIFACT_ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        target.relative_to(ARTIFACT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Refusing output outside {ARTIFACT_ROOT}") from exc
    if target.exists():
        raise ValueError(f"Refusing to overwrite existing output directory: {target}; choose a new report directory")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _html(report: dict[str, object]) -> str:
    payload = json.dumps(report, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DeskBot 身体事实离线检查</title><style>
:root{color-scheme:dark}body{font:15px system-ui,-apple-system,"Microsoft Yahei",sans-serif;background:#0f1724;color:#edf3fb;max-width:1120px;margin:auto;padding:24px}h1{font-size:25px;margin:0 0 5px}h2{font-size:17px;margin:0 0 12px}p{line-height:1.6;color:#b9c8dc}.banner{padding:14px 16px;border-radius:12px;background:#17304b;border:1px solid #2f5a7d;margin:18px 0}.banner.fail{background:#4a2025;border-color:#a74e58}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card,.panel{background:#182538;border:1px solid #263b55;border-radius:12px;padding:14px;margin:12px 0}.card small{color:#9cb0c8;display:block}.value{font-size:20px;margin-top:5px;word-break:break-word}.mono{font:12px ui-monospace,Consolas,monospace;word-break:break-all}.axis{display:grid;grid-template-columns:38px 1fr 100px;gap:8px;align-items:center;margin:8px 0}.bar{height:10px;background:#273c55;border-radius:99px;overflow:hidden}.fill{height:100%;width:50%;background:#64d5bf}.x{background:#64d5bf}.y{background:#ffd080}.z{background:#9aaeff}.known{color:#64e0b1}.unknown,.ambiguous{color:#ffd080}.warming_up{color:#9aaeff}.unstable,.stale,.no_data{color:#ffab9e}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:8px;border-bottom:1px solid #2b4059;vertical-align:top}th{color:#adc1d8}pre{white-space:pre-wrap;overflow:auto;background:#101b2a;padding:12px;border-radius:8px}.tag{display:inline-block;padding:3px 8px;border-radius:99px;background:#26445d}.bad{color:#ff9fa5}.warn{color:#ffd080}.good{color:#64e0b1}</style>
<body><h1>DeskBot 身体事实离线检查</h1><p>这是一份只读的电脑端回放报告：不连接串口、不联网、不写入设备。磁场坐标为记录中的 XYZ 传输计数值。</p><div id="app"></div><script id="report" type="application/json">__REPORT__</script><script>
const r=JSON.parse(document.querySelector('#report').textContent),app=document.querySelector('#app');
const labels={known:'已知匹配',unknown:'未知',warming_up:'积累中',no_data:'无数据',ambiguous:'难以区分',unstable:'不稳定',stale:'过期'};
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])), fmt=v=>typeof v==='number'?Number(v.toFixed(6)):esc(v);
const integrity=r.integrity||{}, src=r.source||{}, latest=r.latest_snapshot||{}, m=latest.magnetic||{}, s=latest.shell_observation||{}, base=latest.base||{};
const status=Object.hasOwn(labels,s.status)?s.status:'no_data', statusLabel=labels[status];
let html=`<div class="banner ${integrity.status==='pass'?'':'fail'}"><strong>${integrity.status==='pass'?'完整性通过':'完整性未通过'}</strong> · 来源：<span class="tag">${esc(src.mode)}</span> · hardware_io=${esc(src.hardware_io)} · ${src.offline_only?'明确离线':'请先修复来源声明'}</div>`;
html+=`<div class="grid"><div class="card"><small>外壳状态</small><div class="value ${status}">${statusLabel}</div></div><div class="card"><small>profile_id</small><div class="value mono">${esc(s.profile_id)}</div></div><div class="card"><small>similarity（原始指标）</small><div class="value">${fmt(s.similarity)}</div></div><div class="card"><small>margin（原始指标）</small><div class="value">${fmt(s.margin)}</div></div><div class="card"><small>底座状态</small><div class="value">${esc(base.link)} / ${esc(base.mechanical_status)}</div></div><div class="card"><small>样本数</small><div class="value">${esc(s.sample_count)}</div></div></div>`;
html+=`<section class="panel"><h2>最新磁场样本</h2><p>传感器：${esc(m.sensor_kind)} · 状态：${esc(m.state)} · 采样时间：${esc(m.sample_at)} · age_ms：${esc(m.age_ms)}</p>`;
for(const [axis,color] of [['x','x'],['y','y'],['z','z']]){const value=m[axis];const width=typeof value==='number'?Math.min(100,Math.max(0,50+value/2000*50)):0;html+=`<div class="axis"><strong class="${color}">${axis.toUpperCase()}</strong><div class="bar"><div class="fill ${color}" style="width:${width}%"></div></div><span class="mono">${fmt(value)}</span></div>`}html+='</section>';
html+=`<section class="panel"><h2>识别时间线（${(r.snapshots||[]).length} 条快照）</h2><table><thead><tr><th>序号</th><th>时间</th><th>状态</th><th>profile_id</th><th>XYZ</th><th>similarity</th><th>margin</th></tr></thead><tbody>`;
for(const row of (r.snapshots||[])){const sm=row.shell_observation||{},mm=row.magnetic||{};html+=`<tr><td>${esc(row.sequence)}</td><td>${esc(row.occurred_at)}</td><td class="${esc(sm.status)}">${labels[sm.status]||esc(sm.status)}</td><td class="mono">${esc(sm.profile_id)}</td><td class="mono">${esc(mm.x)}, ${esc(mm.y)}, ${esc(mm.z)}</td><td>${fmt(sm.similarity)}</td><td>${fmt(sm.margin)}</td></tr>`}html+='</tbody></table></section>';
html+=`<section class="panel"><h2>身份与完整性</h2><p class="mono">device_id=${esc((r.identity||{}).device_id)}<br>boot_id=${esc((r.identity||{}).boot_id)}<br>session_id=${esc((r.identity||{}).session_id)}<br>messages_sha256=${esc(src.messages_sha256)}</p>`;
if(integrity.errors&&integrity.errors.length)html+=`<p class="bad">错误：${integrity.errors.map(esc).join('<br>')}</p>`;if(integrity.warnings&&integrity.warnings.length)html+=`<p class="warn">警告：${integrity.warnings.map(esc).join('<br>')}</p>`;html+=`<details><summary>查看原始报告 JSON</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details></section>`;app.innerHTML=html;
</script></body></html>'''.replace('__REPORT__', payload)


def inspect(input_value: str, output_value: str | None) -> tuple[Path, dict[str, object]]:
    messages_path, manifest_path, messages, raw, manifest = _load_input(input_value)
    report = _validate(messages, raw, manifest, messages_path, manifest_path)
    target = _output_dir(output_value)
    (target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html = _html(report)
    if len(html.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError(f"HTML report exceeds {MAX_OUTPUT_BYTES} byte safety limit")
    (target / "report.html").write_text(html, encoding="utf-8")
    return target, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="offline replay directory or messages.jsonl")
    parser.add_argument("--output", help="directory below artifacts/body-facts-replay-20261018")
    args = parser.parse_args(argv)
    try:
        target, report = inspect(args.input, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"inspect-body-facts-replay: {exc}", file=sys.stderr)
        return 2
    result = {"output": str(target), "integrity": report["integrity"]["status"], "offline_only": report["source"]["offline_only"], "summary": report["summary"]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if report["integrity"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
