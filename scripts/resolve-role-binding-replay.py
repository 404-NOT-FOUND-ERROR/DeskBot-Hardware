#!/usr/bin/env python3
"""Resolve an approved offline body-facts F1 recording against a pinned registry."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_INPUT = ROOT / "artifacts/body-facts-replay-20261018/runs/f1-final3-eval-shell-b-01/messages.jsonl"
DEFAULT_REGISTRY = ROOT / "protocol/deskbot-role-binding-registry-e1-5.example.json"
DEFAULT_OUTPUT = ROOT / "artifacts/body-facts-role-resolution-e1-5/replay-report.json"


def _load_f1_helpers():
    path = ROOT / "scripts/consume-body-facts-demo.py"
    spec = importlib.util.spec_from_file_location("deskbot_f1_replay_helpers", path)
    if spec is None or spec.loader is None:
        raise ValueError("F1 replay verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _checked_output(output_path: Path) -> Path:
    output_path = output_path.resolve()
    try:
        relative_output = output_path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("output must be inside the project") from exc
    if (len(relative_output.parts) < 3
            or relative_output.parts[0] not in {"tmp", "artifacts"}
            or relative_output.parts[1] != "body-facts-role-resolution-e1-5"):
        raise ValueError("output must be inside project tmp/ or artifacts/body-facts-role-resolution-e1-5/")
    if output_path.suffix.lower() != ".json" or output_path.exists():
        raise ValueError("output must be a new .json path; existing files are never overwritten")
    return output_path


def run(input_path: Path, registry_path: Path, output_path: Path) -> dict:
    from protocol.role_binding_consumer import RoleBindingConsumer, load_binding_registry

    input_path = input_path.resolve(strict=True)
    registry_path = registry_path.resolve(strict=True)
    output_path = _checked_output(output_path)
    for path, label in ((input_path, "input"), (registry_path, "registry")):
        try:
            path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise ValueError(f"{label} must be inside the project") from exc

    helpers = _load_f1_helpers()
    messages = helpers._load_messages(input_path)
    source_evidence = helpers._verify_f1_run(input_path, messages)
    bindings = load_binding_registry(registry_path, root=ROOT)
    consumer = RoleBindingConsumer(bindings, freshness_limit_ms=5000)
    steps = []
    start = datetime.fromisoformat(messages[0]["occurred_at"].replace("Z", "+00:00"))
    for message in messages:
        stamp = datetime.fromisoformat(message["occurred_at"].replace("Z", "+00:00"))
        elapsed_ms = int((stamp - start).total_seconds() * 1000)
        result = consumer.consume(message, now_ms=elapsed_ms)
        steps.append({
            "message_type": message["message_type"],
            "message_id": message["message_id"],
            "body_outcome": result["body_outcome"],
            "resolution_status": result["resolution"]["status"],
            "resolution_reason": result["resolution"]["reason"],
        })
    if [step["body_outcome"]["status"] for step in steps] != ["accepted", "accepted"]:
        raise ValueError("verified F1 hello/snapshot were not accepted by the F2 consumer")
    manifest_path = input_path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = {
        "schema": "deskbot.role_resolution_replay_report/v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_mode": "offline_replay",
        "clock_basis": "recorded_host_receive_elapsed_ms",
        "synthetic_messages": False,
        "hardware_io": False,
        "network_io": False,
        "input": input_path.relative_to(ROOT).as_posix(),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "f1_source_evidence": source_evidence,
        "f1_manifest": {
            "segment_id": manifest["segment_id"],
            "profile_namespace": manifest["profile_namespace"],
            "sample_provenance": manifest["sample_provenance"],
            "physical_boot_id": manifest["physical_boot_id"],
            "profiles_are_experiment_labels": manifest["profiles_are_experiment_labels"],
        },
        "registry": registry_path.relative_to(ROOT).as_posix(),
        "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        "verified_binding_count": len(bindings),
        "steps": steps,
        "final_resolution": consumer.state,
        "note": "The F1 recording contains offline experimental labels, not an online device connection or a user-confirmed shell-to-role mapping.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    publish_report(output_path, report)
    return report


def run_synthetic_demo(registry_path: Path, output_path: Path) -> dict:
    """Exercise resolution and conservative invalidation with explicitly synthetic facts."""
    from protocol.role_binding_consumer import RoleBindingConsumer, load_binding_registry

    registry_path = registry_path.resolve(strict=True)
    output_path = _checked_output(output_path)
    try:
        registry_path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("registry must be inside the project") from exc
    bindings = load_binding_registry(registry_path, root=ROOT)
    if len(bindings) != 1:
        raise ValueError("synthetic demo requires exactly one verified binding")
    binding = bindings[0]
    consumer = RoleBindingConsumer(bindings, freshness_limit_ms=5000)
    start = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    identity = {
        "device_id": binding.device_id,
        "boot_id": "synthetic-demo-boot",
        "session_id": "synthetic-demo-session",
    }

    def stamp(clock_ms: int) -> str:
        from datetime import timedelta

        return (start + timedelta(milliseconds=clock_ms)).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def envelope(message_type: str, message_id: str, sequence: int, clock_ms: int, payload: dict) -> dict:
        return {
            "schema": "deskbot.body.v1", "message_type": message_type,
            "message_id": message_id, **identity, "sequence": sequence,
            "occurred_at": stamp(clock_ms), "payload": payload,
        }

    def snapshot(message_id: str, sequence: int, clock_ms: int, *, shell_status: str = "known", event_cursor: int = 0) -> dict:
        profile_id = binding.magnet_profile_id if shell_status == "known" else None
        return envelope("snapshot", message_id, sequence, clock_ms, {
            "kind": "snapshot",
            "base": {"link": "online", "mechanical_status": "ready", "last_event_sequence": event_cursor},
            "event_cursor": {
                "oldest_available_sequence": 1 if event_cursor else None,
                "high_watermark_sequence": event_cursor, "gap_detected": False,
            },
            "magnetic": {
                "state": "fresh", "sensor_kind": "BMM150", "x": 190, "y": -540, "z": -940,
                "sample_at": stamp(clock_ms), "age_ms": 0,
            },
            "shell_observation": {
                "status": shell_status, "profile_id": profile_id,
                "profile_revision": binding.magnet_profile_revision,
                "sensor_kind": "BMM150", "similarity": 0.12, "margin": 0.31,
                "sample_count": 8, "observed_at": stamp(clock_ms),
            },
        })

    messages = [
        envelope("hello", "demo-hello", 0, 0, {
            "kind": "hello", "firmware_id": "synthetic-contract-demo",
            "capabilities": ["magnetic_sample", "shell_observation", "base_events"],
        }),
        snapshot("demo-known-1", 1, 100),
        envelope("event", "demo-event-1", 2, 200, {
            "kind": "event", "event_type": "base.action.completed", "event_sequence": 1, "facts": {},
        }),
        snapshot("demo-known-2", 3, 300, event_cursor=1),
        snapshot("demo-unknown", 4, 400, shell_status="unknown", event_cursor=1),
        snapshot("demo-known-3", 5, 500, event_cursor=1),
    ]
    steps = []
    for message in messages:
        clock_ms = int((datetime.fromisoformat(message["occurred_at"].replace("Z", "+00:00")) - start).total_seconds() * 1000)
        result = consumer.consume(message, now_ms=clock_ms)
        steps.append({
            "message_type": message["message_type"], "message_id": message["message_id"],
            "body_outcome": result["body_outcome"],
            "resolution_status": result["resolution"]["status"],
            "resolution_reason": result["resolution"]["reason"],
            "selected_role_id": (result["resolution"]["selection"] or {}).get("role_id"),
        })
    expired = consumer.tick(5500)
    steps.append({
        "message_type": "host_tick", "clock_ms": 5500,
        "resolution_status": expired["status"], "resolution_reason": expired["reason"],
        "selected_role_id": (expired["selection"] or {}).get("role_id"),
    })
    if [step.get("resolution_status") for step in steps] != [
        "waiting_for_snapshot", "resolved_offline", "waiting_for_snapshot",
        "resolved_offline", "unresolved", "resolved_offline", "expired",
    ]:
        raise ValueError("synthetic role-resolution demo did not follow the expected state sequence")
    report = {
        "schema": "deskbot.role_resolution_replay_report/v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_mode": "synthetic_contract_demo",
        "clock_basis": "deterministic_synthetic_monotonic_ms",
        "synthetic_messages": True, "hardware_io": False, "network_io": False,
        "registry": registry_path.relative_to(ROOT).as_posix(),
        "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        "verified_binding_count": len(bindings), "freshness_limit_ms": 5000,
        "steps": steps, "final_resolution": expired,
        "note": "All body messages and known/unknown shell observations in this report are synthetic contract fixtures; no device was observed and no role was activated.",
    }
    publish_report(output_path, report)
    return report


def publish_report(path: Path, report: dict) -> None:
    """Publish a complete local report without ever replacing an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".role-resolution-", dir=path.parent) as folder:
        staged = Path(folder) / "report.json"
        staged.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        # Both paths share a filesystem. A hard-link publication is atomic and
        # fails if the destination exists, including a check/publish race.
        os.link(staged, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--synthetic-demo", action="store_true",
                        help="run a clearly synthetic positive/invalidation/expiry demonstration instead of the recorded F1 input")
    args = parser.parse_args(argv)
    try:
        report = (run_synthetic_demo(args.registry, args.output) if args.synthetic_demo
                  else run(args.input, args.registry, args.output))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"role-binding replay failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "result": report["final_resolution"]["status"],
        "reason": report["final_resolution"]["reason"],
        "selection": report["final_resolution"]["selection"],
        "report": args.output.resolve().relative_to(ROOT.resolve()).as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
