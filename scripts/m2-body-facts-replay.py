#!/usr/bin/env python3
"""Export an approved M2 evaluation as offline body facts (no hardware I/O)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "body-facts-replay-20261018"
DEFAULT_MANIFEST = ROOT / "protocol" / "magnetic-validation-20261018.manifest.json"
MAX_BYTES, MAX_LINES = 64 * 1024 * 1024, 250_000
ALLOWED = {"no_data", "warming_up", "known", "unknown", "ambiguous", "unstable", "stale"}


def _json(raw: str, label: str) -> Any:
    def reject(value: str) -> Any:
        raise ValueError(f"non-finite JSON number in {label}: {value}")
    try:
        result = json.loads(raw, parse_constant=reject)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON in {label}: {exc}") from exc
    _finite(result, label)
    return result


def _finite(value: Any, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number in {label}")
    if isinstance(value, dict):
        for key, item in value.items(): _finite(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value): _finite(item, f"{label}[{index}]")


def _read(path: Path, label: str) -> bytes:
    if not path.is_file(): raise ValueError(f"{label} not found: {path}")
    if path.stat().st_size > MAX_BYTES: raise ValueError(f"{label} exceeds {MAX_BYTES} bytes: {path}")
    return path.read_bytes()


def _sha(path: Path, label: str) -> str: return hashlib.sha256(_read(path, label)).hexdigest()


def _path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip(): raise ValueError(f"{label} must be a non-empty path")
    path = (Path(value) if Path(value).is_absolute() else ROOT / value).resolve()
    try: path.relative_to(ROOT.resolve())
    except ValueError as exc: raise ValueError(f"{label} escapes project root: {path}") from exc
    return path


def _hash(path: Path, expected: Any, label: str) -> str:
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected.lower()):
        raise ValueError(f"{label} hash must be SHA-256 hex")
    actual = _sha(path, label)
    if actual != expected.lower(): raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def _load(path: Path, label: str) -> Any:
    try: raw = _read(path, label).decode("utf-8")
    except UnicodeDecodeError as exc: raise ValueError(f"{label} is not UTF-8") from exc
    return _json(raw, label)


def _iso(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise ValueError(f"{label} timestamp is required")
    try: parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc: raise ValueError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None: raise ValueError(f"{label} timestamp requires timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _int(value: Any, label: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and (not math.isfinite(value) or not value.is_integer())):
        raise ValueError(f"{label} must be integer")
    result = int(value)
    if minimum is not None and result < minimum: raise ValueError(f"{label} must be >= {minimum}")
    return result


def _num(value: Any, label: str) -> float | None:
    if value is None or value == "": return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite numeric or null")
    return float(value)


def _jsonl(path: Path, label: str) -> tuple[list[dict[str, Any]], str]:
    raw = _read(path, label)
    try: lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc: raise ValueError(f"{label} is not UTF-8") from exc
    if len(lines) > MAX_LINES: raise ValueError(f"{label} exceeds {MAX_LINES} lines")
    rows = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip(): continue
        value = _json(line, f"{label} line {line_no}")
        if not isinstance(value, dict): raise ValueError(f"{label} line {line_no} must be object")
        rows.append(value)
    if not rows: raise ValueError(f"{label} contains no rows")
    return rows, hashlib.sha256(raw).hexdigest()


def _output(value: str | None) -> Path:
    path = ((Path(value) if value else ARTIFACT_ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")) if value and Path(value).is_absolute() else (ROOT / value if value else ARTIFACT_ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))).resolve()
    try: path.relative_to(ARTIFACT_ROOT.resolve())
    except ValueError as exc: raise ValueError(f"Refusing output outside {ARTIFACT_ROOT}") from exc
    if path.exists():
        if not path.is_dir() or any(path.iterdir()): raise ValueError(f"Refusing to overwrite output: {path}")
    else: path.mkdir(parents=True, exist_ok=False)
    return path


def _segment(manifest_path: Path, segment_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _load(manifest_path, "validation manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != "deskbot.magnetic.validation.manifest.v1": raise ValueError("unsupported validation manifest")
    matches = [s for s in manifest.get("segments", []) if isinstance(s, dict) and s.get("id") == segment_id]
    if len(matches) != 1: raise ValueError(f"segment not found or duplicated: {segment_id}")
    segment = matches[0]
    if segment.get("role") != "evaluation" or segment.get("valid") is not True: raise ValueError(f"only valid evaluation segments may be exported: {segment_id}")
    if not isinstance(segment.get("m2"), dict): raise ValueError(f"segment has no M2 artifact: {segment_id}")
    return manifest, segment


def _children(directory: Path, specs: Any, label: str) -> dict[str, Path]:
    if not isinstance(specs, dict) or not specs: raise ValueError(f"{label} hashes are required")
    result = {}
    for name, expected in specs.items():
        if not isinstance(name, str) or Path(name).name != name: raise ValueError(f"{label} names must be direct children")
        path = (directory / name).resolve()
        try: path.relative_to(directory.resolve())
        except ValueError as exc: raise ValueError(f"{label} path escapes directory") from exc
        _hash(path, expected, f"{label} {name}"); result[name] = path
    return result


def _verify_manifest_files(manifest: dict[str, Any]) -> None:
    """Verify the frozen evidence list, including excluded recordings."""
    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("manifest segments must be a non-empty list")
    seen: set[str] = set()
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("id"), str):
            raise ValueError("manifest segment must contain a string id")
        segment_id = segment["id"]
        if segment_id in seen:
            raise ValueError(f"duplicated manifest segment: {segment_id}")
        seen.add(segment_id)
        raw = segment.get("raw")
        if not isinstance(raw, dict):
            raise ValueError(f"raw evidence is required: {segment_id}")
        _hash(_path(raw.get("path"), f"raw {segment_id}"), raw.get("sha256"), f"raw {segment_id}")
        for layer in ("m1", "m2"):
            spec = segment.get(layer)
            if spec is None:
                continue
            if not isinstance(spec, dict):
                raise ValueError(f"invalid {layer} specification: {segment_id}")
            _children(_path(spec.get("path"), f"{layer} {segment_id}"), spec.get("files"), f"{layer} {segment_id}")


def _verify(manifest: dict[str, Any], segment: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], Path, Path, Path, Path, str, str, str, list[dict[str, str]]]:
    raw_spec, m1_spec, m2_spec = segment.get("raw"), segment.get("m1"), segment.get("m2")
    if not all(isinstance(v, dict) for v in (raw_spec, m1_spec, m2_spec)): raise ValueError("raw, M1, and M2 specs are required")
    raw_path = _path(raw_spec.get("path"), "raw path"); raw_hash = _hash(raw_path, raw_spec.get("sha256"), "raw log")
    m1_dir, m2_dir = _path(m1_spec.get("path"), "M1 path"), _path(m2_spec.get("path"), "M2 path")
    m1, m2 = _children(m1_dir, m1_spec.get("files"), "M1"), _children(m2_dir, m2_spec.get("files"), "M2")
    if not {"samples.jsonl", "summary.json"} <= set(m1): raise ValueError("M1 samples.jsonl/summary.json required")
    if not {"recognition.jsonl", "summary.json", "profiles.json"} <= set(m2): raise ValueError("M2 recognition/summary/profiles required")
    m1s, m2s, profiles = _load(m1["summary.json"], "M1 summary"), _load(m2["summary.json"], "M2 summary"), _load(m2["profiles.json"], "M2 profiles")
    m1_rows, m1_hash = _jsonl(m1["samples.jsonl"], "M1 samples"); m2_rows, m2_hash = _jsonl(m2["recognition.jsonl"], "M2 recognition")
    if not isinstance(m1s, dict) or not isinstance(m2s, dict) or not isinstance(profiles, list): raise ValueError("M1/M2 summary or profiles type invalid")
    if m1_hash != _hash(m1["samples.jsonl"], m1_spec["files"]["samples.jsonl"], "M1 samples"): raise ValueError("M1 samples changed")
    evaluation = m2s.get("evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("input_sha256") != m1_hash or evaluation.get("input_format") != "m1_samples_jsonl": raise ValueError("M2 input does not reference manifest M1")
    if m2s.get("evaluation_input_valid") is not True or m2s.get("hardware_io") is not False or m2s.get("serial_opened") is not False or m2s.get("profiles_are_explicitly_registered") is not True or m2s.get("similarity_is_probability") is not False: raise ValueError("M2 provenance flags invalid")
    count = _int(evaluation.get("sample_count"), "M2 sample_count", 1)
    if len(m1_rows) != count or len(m2_rows) != count or _int(m1s.get("samples_written"), "M1 samples_written", 1) != count or _int(m1s.get("callback_reconstructed"), "M1 callback_reconstructed", 0) != count: raise ValueError("sample counts do not match")
    if m1s.get("device_sample_time_available") is not False: raise ValueError("M1 time source is not host-only")
    entries = [e.get("profile") for e in profiles if isinstance(e, dict) and isinstance(e.get("profile"), dict)]
    if len(entries) != len(profiles) or m2s.get("profiles") != entries: raise ValueError("M2 profiles.json/summary mismatch")
    ids = {}
    for p in entries:
        pid, label = p.get("profile_id"), p.get("label")
        if not isinstance(pid, str) or not pid or not isinstance(label, str) or not label or pid in ids: raise ValueError("profile IDs/labels invalid")
        ids[pid] = label
    training_index: dict[Path, tuple[str, str]] = {}
    for candidate in manifest.get("segments", []):
        if not isinstance(candidate, dict) or candidate.get("role") != "training" or candidate.get("valid") is not True:
            continue
        candidate_m1 = candidate.get("m1")
        if not isinstance(candidate_m1, dict):
            continue
        candidate_dir = _path(candidate_m1.get("path"), f"training M1 path {candidate.get('id')}")
        candidate_files = candidate_m1.get("files")
        if not isinstance(candidate_files, dict) or "samples.jsonl" not in candidate_files:
            continue
        candidate_samples = (candidate_dir / "samples.jsonl").resolve()
        expected = candidate_files["samples.jsonl"]
        if not isinstance(expected, str):
            raise ValueError(f"training M1 samples hash is invalid: {candidate.get('id')}")
        training_index[candidate_samples] = (str(candidate.get("id")), expected.lower())
    training_sources: list[dict[str, str]] = []
    for entry in entries:
        training = next((item.get("training") for item in profiles if isinstance(item, dict) and isinstance(item.get("profile"), dict) and item.get("profile", {}).get("profile_id") == entry.get("profile_id")), None)
        if not isinstance(training, dict):
            raise ValueError(f"training provenance is missing for profile: {entry.get('profile_id')}")
        training_path = _path(training.get("input_path"), f"training input {entry.get('profile_id')}")
        training_hash = training.get("input_sha256")
        if not isinstance(training_hash, str) or len(training_hash) != 64:
            raise ValueError(f"training input hash is invalid for profile: {entry.get('profile_id')}")
        actual_training_hash = _sha(training_path, f"training input {entry.get('profile_id')}")
        if actual_training_hash != training_hash.lower():
            raise ValueError(f"training input SHA-256 mismatch for profile: {entry.get('profile_id')}")
        if actual_training_hash == m1_hash:
            raise ValueError("training and evaluation must be independent recordings")
        source_info = training_index.get(training_path)
        if source_info is None or source_info[1] != actual_training_hash:
            raise ValueError(f"training input is not an approved valid manifest segment: {training_path}")
        training_sources.append({"profile_id": str(entry.get("profile_id")), "segment_id": source_info[0], "path": training_path.relative_to(ROOT).as_posix(), "sha256": actual_training_hash})
    previous = None
    for i, (a, b) in enumerate(zip(m1_rows, m2_rows), 1):
        if _int(a.get("sequence"), f"M1 row {i} sequence", 1) != i or _int(b.get("sequence"), f"M2 row {i} sequence", 1) != i: raise ValueError("sample sequence is not contiguous")
        at, bt = _iso(a.get("host_received_at"), f"M1 row {i}"), _iso(b.get("timestamp"), f"M2 row {i}")
        if at != bt: raise ValueError("M1/M2 timestamps are not aligned")
        if previous is not None and at <= previous: raise ValueError("timestamps are not strictly monotonic")
        previous = at
        for axis in ("x", "y", "z"): _int(a.get(axis), f"M1 row {i} {axis}")
        status, pid = b.get("status"), b.get("profile_id")
        if status not in ALLOWED: raise ValueError(f"unsupported status: {status!r}")
        if status == "known":
            if not isinstance(pid, str) or pid not in ids or b.get("candidate_id") != pid or b.get("label") != ids[pid]: raise ValueError("known row profile mismatch")
        elif pid is not None: raise ValueError("non-known row contains profile_id")
        _num(b.get("similarity"), f"M2 row {i} similarity"); _num(b.get("margin"), f"M2 row {i} margin")
    return m2s, m1_rows, m2_rows, raw_path, m1["samples.jsonl"], m2["recognition.jsonl"], m2["profiles.json"], raw_hash, m1_hash, m2_hash, training_sources


def convert(segment_id: str, output_value: str | None, manifest_value: str | None = None) -> Path:
    manifest_path = _path(manifest_value or str(DEFAULT_MANIFEST), "validation manifest")
    _manifest, segment = _segment(manifest_path, segment_id)
    _verify_manifest_files(_manifest)
    m2s, m1_rows, m2_rows, raw_path, m1_samples, m2_recognition, profiles_path, raw_hash, m1_hash, m2_hash, training_sources = _verify(_manifest, segment)
    namespace = f"offline:{_sha(profiles_path, 'M2 profiles')[:16]}"; replay = f"{segment_id}-{m2_hash[:16]}"
    device, boot, session = f"offline-replay:{replay}", f"offline-unknown-boot:{replay}", f"offline-session:{replay}"
    first = _iso(m2_rows[0]["timestamp"], "first M2 row"); final_a, final_b = m1_rows[-1], m2_rows[-1]
    kind = final_a.get("kind_name")
    if not isinstance(kind, str) or not kind: raise ValueError("final M1 sample has no sensor kind")
    status, pid = final_b["status"], final_b.get("profile_id"); out_pid = f"{namespace}:{pid}" if status == "known" else None
    occurred, sample_at = _iso(final_b["timestamp"], "final M2 row"), _iso(final_a["host_received_at"], "final M1 sample")
    coords = [_int(final_a.get(axis), f"final magnetic {axis}") for axis in ("x", "y", "z")]
    base = {"link": "unknown", "mechanical_status": "unknown", "last_event_sequence": 0}
    magnetic = {"state": "fresh", "sensor_kind": kind, "x": coords[0], "y": coords[1], "z": coords[2], "sample_at": sample_at, "age_ms": 0}
    shell = {"status": status, "profile_id": out_pid, "profile_revision": 1 if out_pid else 0, "sensor_kind": kind, "similarity": _num(final_b.get("similarity"), "final similarity"), "margin": _num(final_b.get("margin"), "final margin"), "sample_count": _int(final_b.get("window_count"), "final window_count", 0), "observed_at": occurred}
    def message(kind_name: str, msg_id: str, sequence: int, when: str, payload: dict[str, Any]) -> dict[str, Any]: return {"schema": "deskbot.body.v1", "message_type": kind_name, "message_id": msg_id, "device_id": device, "boot_id": boot, "session_id": session, "sequence": sequence, "occurred_at": when, "payload": payload}
    hello = message("hello", f"{session}:hello", 0, first, {"kind": "hello", "firmware_id": "offline-replay:m2-recognition-v1", "capabilities": ["magnetic_sample", "shell_observation"], "max_event_backlog": 0})
    snapshot = message("snapshot", f"{session}:snapshot:1", 1, occurred, {"kind": "snapshot", "base": base, "magnetic": magnetic, "shell_observation": shell, "event_cursor": {"oldest_available_sequence": None, "high_watermark_sequence": 0, "gap_detected": False}})
    target = _output(output_value); messages = [hello, snapshot]
    message_bytes = "".join(json.dumps(m, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n" for m in messages).encode()
    (target / "messages.jsonl").write_bytes(message_bytes)
    replay_manifest = {"schema": "deskbot.body.facts.replay.v1", "source_mode": "offline_replay", "segment_id": segment_id, "hardware_io": False, "physical_boot_id": None, "validation_manifest": manifest_path.relative_to(ROOT).as_posix(), "validation_manifest_sha256": _sha(manifest_path, "validation manifest"), "raw_path": raw_path.relative_to(ROOT).as_posix(), "raw_sha256": raw_hash, "input_path": m2_recognition.relative_to(ROOT).as_posix(), "input_sha256": m2_hash, "m1_samples_path": m1_samples.relative_to(ROOT).as_posix(), "m1_samples_sha256": m1_hash, "profiles_path": profiles_path.relative_to(ROOT).as_posix(), "profiles_sha256": _sha(profiles_path, "M2 profiles"), "training_sources": training_sources, "profile_namespace": namespace, "device_id": device, "boot_id": boot, "session_id": session, "firmware_id": "offline-replay:m2-recognition-v1", "message_count": 2, "snapshot_count": 1, "event_count": 0, "messages_sha256": hashlib.sha256(message_bytes).hexdigest(), "time_basis": "recorded_host_receive_time", "sample_provenance": "m1_latest_sample; callback_reconstructed possible", "profiles_are_experiment_labels": True, "note": "Offline facts only; no physical device boot, base state, touch, motion, expression, role, personality, or probability is inferred."}
    (target / "manifest.json").write_text(json.dumps(replay_manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--segment", required=True); parser.add_argument("--manifest"); parser.add_argument("--output")
    args = parser.parse_args(argv)
    try: target = convert(args.segment, args.output, args.manifest)
    except (OSError, ValueError, KeyError, TypeError) as exc: print(f"error: {exc}", file=sys.stderr); return 2
    print(json.dumps({"output": str(target), "hardware_io": False, "event_count": 0}, ensure_ascii=False, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
