#!/usr/bin/env python3
"""Run an offline F2 body-facts consumer demonstration.

The demo reads one already-exported F1 ``hello + snapshot`` and then feeds
synthetic events to exercise duplicate suppression, an event gap, and snapshot
resynchronization.  It never opens a serial port, connects to a network, or
writes a device.  Synthetic events are explicitly marked in the report.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_INPUT = ROOT / "artifacts" / "body-facts-replay-20261018" / "runs" / "f1-final3-eval-shell-b-01" / "messages.jsonl"
DEFAULT_OUTPUT = ROOT / "artifacts" / "body-facts-consumer-20261018" / "demo"


def _load_messages(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise ValueError(f"input not found: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if [row.get("message_type") for row in rows] != ["hello", "snapshot"]:
        raise ValueError("F1 input must contain hello followed by snapshot")
    return rows


def _project_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a project-relative path")
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes project root") from exc
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_f1_run(input_path: Path, messages: list[dict[str, object]]) -> dict[str, object]:
    try:
        input_path.resolve().relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("F1 input must be inside the project") from exc
    run_manifest_path = input_path.parent / "manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(run_manifest, dict) or run_manifest.get("schema") != "deskbot.body.facts.replay.v1":
        raise ValueError("unsupported F1 replay manifest")
    if run_manifest.get("source_mode") != "offline_replay" or run_manifest.get("hardware_io") is not False or run_manifest.get("physical_boot_id") is not None:
        raise ValueError("F1 manifest is not an offline replay")
    if run_manifest.get("message_count") != 2 or run_manifest.get("snapshot_count") != 1 or run_manifest.get("event_count") != 0:
        raise ValueError("F1 run must contain exactly hello + snapshot and no events")
    if _sha(input_path) != run_manifest.get("messages_sha256"):
        raise ValueError("F1 messages SHA-256 mismatch")
    validation_path = _project_path(run_manifest.get("validation_manifest"), "validation_manifest")
    if _sha(validation_path) != run_manifest.get("validation_manifest_sha256"):
        raise ValueError("validation manifest SHA-256 mismatch")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    segments = validation.get("segments") if isinstance(validation, dict) else None
    if not isinstance(segments, list):
        raise ValueError("validation manifest has no segments")
    segment_id = run_manifest.get("segment_id")
    matches = [segment for segment in segments if isinstance(segment, dict) and segment.get("id") == segment_id]
    if len(matches) != 1 or matches[0].get("role") != "evaluation" or matches[0].get("valid") is not True:
        raise ValueError("F1 source is not one approved valid evaluation segment")
    segment = matches[0]
    raw = segment.get("raw")
    m1 = segment.get("m1")
    m2 = segment.get("m2")
    if not all(isinstance(item, dict) for item in (raw, m1, m2)):
        raise ValueError("F1 source evidence is incomplete")
    required = (
        (run_manifest.get("raw_path"), run_manifest.get("raw_sha256"), raw.get("path"), raw.get("sha256"), "raw"),
        (run_manifest.get("m1_samples_path"), run_manifest.get("m1_samples_sha256"), m1.get("path", "").rstrip("/") + "/samples.jsonl", m1.get("files", {}).get("samples.jsonl"), "M1 samples"),
        (run_manifest.get("input_path"), run_manifest.get("input_sha256"), m2.get("path", "").rstrip("/") + "/recognition.jsonl", m2.get("files", {}).get("recognition.jsonl"), "M2 recognition"),
        (run_manifest.get("profiles_path"), run_manifest.get("profiles_sha256"), m2.get("path", "").rstrip("/") + "/profiles.json", m2.get("files", {}).get("profiles.json"), "M2 profiles"),
    )
    for run_path, run_hash, source_path, source_hash, label in required:
        if run_path != source_path or run_hash != source_hash:
            raise ValueError(f"F1 {label} reference does not match validation manifest")
        if _sha(_project_path(run_path, f"{label} path")) != run_hash:
            raise ValueError(f"F1 {label} SHA-256 mismatch")
    ids = {key: run_manifest.get(key) for key in ("device_id", "boot_id", "session_id")}
    for message in messages:
        if message.get("schema") != "deskbot.body.v1" or any(message.get(key) != value for key, value in ids.items()):
            raise ValueError("F1 message identity does not match replay manifest")
    return {"segment_id": segment_id, "messages_sha256": run_manifest["messages_sha256"], "validation_manifest_sha256": run_manifest["validation_manifest_sha256"]}


def _event(common: dict[str, object], *, message_id: str, transport_sequence: int, event_sequence: int, event_type: str = "base.action.completed") -> dict[str, object]:
    return {
        **common,
        "message_type": "event",
        "message_id": message_id,
        "sequence": transport_sequence,
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "payload": {"kind": "event", "event_type": event_type, "event_sequence": event_sequence, "facts": {"source": "synthetic_demo"}},
    }


def _resync_snapshot(base: dict[str, object], *, message_id: str, transport_sequence: int, high: int) -> dict[str, object]:
    payload = json.loads(json.dumps(base["payload"]))
    payload["event_cursor"] = {"oldest_available_sequence": max(1, high - 2), "high_watermark_sequence": high, "gap_detected": True}
    payload["base"]["last_event_sequence"] = high
    return {**base, "message_type": "snapshot", "message_id": message_id, "sequence": transport_sequence, "occurred_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "payload": payload}


def run(input_path: Path, output: Path) -> Path:
    from protocol.body_facts_consumer import BodyFactsConsumer

    messages = _load_messages(input_path)
    f1_evidence = _verify_f1_run(input_path, messages)
    root_out = (ROOT / "artifacts" / "body-facts-consumer-20261018").resolve()
    try:
        output.relative_to(root_out)
    except ValueError as exc:
        raise ValueError(f"output must be inside {root_out}") from exc

    real_consumer = BodyFactsConsumer()
    real_outcomes = [real_consumer.consume(message) for message in messages]
    if any(outcome.status != "accepted" for outcome in real_outcomes):
        raise ValueError("real F1 hello/snapshot were not both accepted")

    synthetic_consumer = BodyFactsConsumer()
    synthetic_steps: list[dict[str, object]] = []

    def feed(message: dict[str, object]) -> None:
        outcome = synthetic_consumer.consume(message)
        synthetic_steps.append({"message_type": message.get("message_type"), "message_id": message.get("message_id"), "event_sequence": message.get("payload", {}).get("event_sequence") if isinstance(message.get("payload"), dict) else None, "status": outcome.status, "reason": outcome.reason})

    real_hello, real_snapshot = messages
    synthetic_identity = {"schema": "deskbot.body.v1", "device_id": "synthetic:f2-device", "boot_id": "synthetic:f2-boot", "session_id": "synthetic:f2-session"}
    syn_hello = {**synthetic_identity, "message_type": "hello", "message_id": "synthetic:hello", "sequence": 0, "occurred_at": real_hello["occurred_at"], "payload": {**real_hello["payload"], "firmware_id": "synthetic:f2-demo"}}
    feed(syn_hello)
    syn_snapshot = {**real_snapshot, **synthetic_identity, "message_type": "snapshot", "message_id": "synthetic:snapshot:initial", "sequence": 1}
    feed(syn_snapshot)
    first = _event(synthetic_identity, message_id="synthetic:event:1", transport_sequence=2, event_sequence=1)
    feed(first)
    feed(first)
    feed(_event(synthetic_identity, message_id="synthetic:event:3", transport_sequence=3, event_sequence=3))
    feed(_resync_snapshot(syn_snapshot, message_id="synthetic:snapshot:resync", transport_sequence=4, high=3))
    feed(_event(synthetic_identity, message_id="synthetic:event:4", transport_sequence=5, event_sequence=4))

    expected_statuses = ["accepted", "accepted", "accepted", "duplicate", "needs_snapshot", "accepted", "accepted"]
    actual_statuses = [step["status"] for step in synthetic_steps]
    if actual_statuses != expected_statuses:
        raise ValueError(f"synthetic consumer outcomes differ from contract: {actual_statuses}")
    if synthetic_consumer.state.needs_snapshot or synthetic_consumer.state.last_event_sequence != 4:
        raise ValueError("synthetic gap recovery did not converge to event sequence 4")

    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise ValueError(f"refusing to overwrite non-empty output: {output}")
    else:
        output.mkdir(parents=True, exist_ok=False)
    state = synthetic_consumer.state
    report = {
        "schema": "deskbot.body.facts.consumer.demo.v1",
        "synthetic": True,
        "hardware_io": False,
        "network_io": False,
        "input": input_path.relative_to(ROOT).as_posix(),
        "f1_evidence": f1_evidence,
        "real_messages": 2,
        "synthetic_messages": len(synthetic_steps),
        "real_f1_consumption": {
            "source": "approved_f1_recording",
            "outcomes": [{"message_type": message["message_type"], "status": outcome.status, "reason": outcome.reason} for message, outcome in zip(messages, real_outcomes)],
            "state": {"device_id": real_consumer.state.device_id, "boot_id": real_consumer.state.boot_id, "session_id": real_consumer.state.session_id, "last_event_sequence": real_consumer.state.last_event_sequence, "shell_observation": real_consumer.state.latest_snapshot.get("shell_observation") if real_consumer.state.latest_snapshot else None},
        },
        "synthetic_protocol_test": {
            "source": "synthetic_messages_only",
            "identity": {"device_id": state.device_id, "boot_id": state.boot_id, "session_id": state.session_id},
            "steps": synthetic_steps,
            "final_state": {
                "snapshot_seen": state.snapshot_seen,
                "needs_snapshot": state.needs_snapshot,
                "last_event_sequence": state.last_event_sequence,
                "high_watermark_sequence": state.high_watermark_sequence,
                "gap_detected": state.gap_detected,
                "event_count": len(state.events),
            },
        },
        "note": "The real F1 hello/snapshot is separate from synthetic protocol tests. Synthetic identity and events are not device facts.",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        input_path = (args.input if args.input.is_absolute() else ROOT / args.input).resolve()
        output_path = (args.output if args.output.is_absolute() else ROOT / args.output).resolve()
        path = run(input_path, output_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(path), "synthetic": True, "hardware_io": False}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
