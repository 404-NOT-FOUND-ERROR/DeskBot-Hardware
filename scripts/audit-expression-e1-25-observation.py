#!/usr/bin/env python3
"""Audit the E1.25 passive expression-observation design, offline only."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = "artifacts/expression-e1-25-runtime-observation-20261026/report.json"
MAX_JSON_SEQUENCE = 9_007_199_254_740_991
E125_BASELINE_COMMIT = "747b0d918538eedaf936fda0bb6c9340fc7658cc"


@dataclass(frozen=True)
class InputFile:
    """A checked input read either from the worktree or a fixed git commit."""

    relative: str
    path: Path | None = None
    commit: str | None = None

    def read_bytes(self) -> bytes:
        if self.path is not None:
            return self.path.read_bytes()
        if self.commit is None:
            raise ValueError(f"input has no source: {self.relative}")
        result = subprocess.run(
            ["git", "show", f"{self.commit}:{self.relative}"],
            cwd=ROOT, check=True, capture_output=True,
        )
        return result.stdout

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding)

    def sha256(self) -> str:
        return hashlib.sha256(self.read_bytes()).hexdigest()


def checked_file(relative: str) -> InputFile:
    path = (ROOT / relative).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"input escapes repository: {relative}") from exc
    if not path.is_file():
        raise ValueError(f"required input is missing: {relative}")
    return InputFile(relative=relative, path=path)


def checked_baseline_file(relative: str) -> InputFile:
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"{E125_BASELINE_COMMIT}:{relative}"],
            cwd=ROOT, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"E1.25 baseline input is missing: {E125_BASELINE_COMMIT}:{relative}"
        ) from exc
    return InputFile(relative=relative, commit=E125_BASELINE_COMMIT)


def validate_inputs() -> dict[str, InputFile]:
    paths = {
        # E1.25 is a historical static audit.  Pin firmware inputs to the
        # pre-observer commit so later E1.26 work cannot invalidate its result.
        "display": checked_baseline_file("firmware/body/main/display/emote_display.cc"),
        "application": checked_baseline_file("firmware/body/main/application.cc"),
        "base_control": checked_baseline_file("firmware/body/main/boards/esp_vocat/base_control.cc"),
        "schema": checked_file("protocol/expression-runtime-observation-e1-25.schema.json"),
        "reference_model": checked_file("protocol/expression_runtime_observation.py"),
        "tests": checked_file("tests/test-expression-e1-25-observation.py"),
        "design": checked_file("docs/expression-runtime-observation-e1-25-20261026.md"),
    }
    display = paths["display"].read_text(encoding="utf-8")
    application = paths["application"].read_text(encoding="utf-8")
    base_control = paths["base_control"].read_text(encoding="utf-8")
    if "void EmoteDisplay::SetEmotion(const char* const emotion)" not in display:
        raise ValueError("SetEmotion observation seam changed")
    if "emote_set_anim_emoji(emote_handle_, emotion);" not in display:
        raise ValueError("official player call is no longer present at audited seam")
    if "emotion_to_action_map" not in display or "vocat_base_control_set_action(it->second);" not in display:
        raise ValueError("official action branch anchor changed")
    if "Schedule([this, display, emotion_str = std::string(emotion->valuestring)]()" not in application:
        raise ValueError("application emotion scheduling path changed")
    if "void BaseControl::HandleCommand(uint8_t cmd, uint8_t *data, int data_len)" not in base_control:
        raise ValueError("base UART callback anchor changed")
    if 'emote_display->SetEmotion("happy");' not in base_control:
        raise ValueError("base UART expression call site changed")

    return paths


def build_report(paths: dict[str, InputFile]) -> dict[str, Any]:
    schema = json.loads(paths["schema"].read_text(encoding="utf-8"))
    model = paths["reference_model"].read_text(encoding="utf-8")
    required_constants = {
        "CAPACITY = 24": "record capacity",
        "COALESCE_WINDOW_MS = 250": "coalescing window",
        "MAX_INPUT_BYTES = 24": "bounded semantic input",
        "OTHER_SEMANTIC = \"other\"": "unknown-name redaction",
    }
    for marker, label in required_constants.items():
        if marker not in model:
            raise ValueError(f"reference model changed its {label}")
    definitions = schema.get("$defs", {})
    record = definitions.get("record", {})
    properties = schema.get("properties", {})
    if (properties.get("records", {}).get("maxItems") != 24
            or record.get("properties", {}).get("repeat_count", {}).get("maximum") != 65535
            or record.get("properties", {}).get("sequence", {}).get("maximum") != MAX_JSON_SEQUENCE):
        raise ValueError("schema bounds drifted from the E1.25 contract")
    inputs = {
        name: {"path": path.relative, "sha256": path.sha256()}
        for name, path in paths.items()
    }
    return {
        "schema": "deskbot.expression_e1_25_observation_audit/v1",
        "edition": "E1.25",
        "date": "2026-10-26",
        "firmware_baseline_commit": E125_BASELINE_COMMIT,
        "result": "offline_passive_observation_design_audit_passed",
        "audit_pass": True,
        "design_reviewed": True,
        "observer_implemented": False,
        "runtime_data_collected": False,
        "deployment_eligible": False,
        "flash_authorized": False,
        "safety": {
            "hardware_accessed": False,
            "serial_accessed": False,
            "firmware_built": False,
            "firmware_changed": False,
            "device_written": False,
            "nvs_changed": False,
        },
        "observation": {
            "point": "EmoteDisplay::SetEmotion",
            "claim_scope": "set_emotion_call_only",
            "resource_resolved": "not_observed",
            "player_rendered": "not_observed",
            "action_completed": "not_observed",
            "static_call_contexts": ["application_scheduled_emotion", "base_uart_callback"],
            "concurrency_overlap_proven": False,
        },
        "bounds": {
            "default_enabled": False,
            "capacity_records": 24,
            "coalesce_window_ms": 250,
            "semantic_input_max_bytes": 24,
            "unknown_semantic": "other_without_original_value",
            "repeat_count_max": 65535,
            "sequence_max": MAX_JSON_SEQUENCE,
            "timestamp": "uint32_uptime_ms_wrap_safe_for_coalesce_window",
            "storage": "volatile_ram_only",
            "nvs_or_flash_persistence": False,
            "automatic_export": False,
        },
        "concurrency_contract": {
            "producer_model": "multiple_call_contexts_possible",
            "admission": "fixed_memory_single_bounded_nonblocking_attempt",
            "on_contention_or_full": "drop_and_count",
            "blocking": False,
            "allocation": False,
            "logging_or_ui_or_player_or_action_side_effect": False,
            "network_transmission": False,
            "host_concurrency_proof": False,
        },
        "host_reference_model": {
            "replay_semantics_tested": True,
            "hardware_or_freertos_behavior_tested": False,
            "schema": "protocol/expression-runtime-observation-e1-25.schema.json",
        },
        "explicit_non_goals": [
            "no speech text, prompts, replies, user or device identity, MAC, network data, audio, pixels, EAF bytes, or UART frames",
            "no claim that a resource resolved, the player rendered, or an action completed",
            "no changes to firmware, UI, player, action map, DOA, touch, audio, base, partitions, or expression mapping",
            "no MCP/UI/query/export/clear endpoint is implemented in E1.25",
            "no resource deletion, merge, or replacement is authorized",
        ],
        "sources": inputs,
        "next_gate": "review_design_then_separately_authorize_default_off_firmware_observer_build_without_flash",
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        output = output.resolve()
        relative = output.relative_to(ROOT.resolve())
        if not relative.parts or relative.parts[0] not in {"artifacts", "tmp"} or output.suffix.lower() != ".json":
            raise ValueError("output must be a JSON file inside artifacts/ or tmp/")
        paths = validate_inputs()
        report = build_report(paths)
        write_json(output, report)
        print(f"{report['result']}: {relative.as_posix()}")
        print("Design-only result; observer_implemented=false; deployment_eligible=false; flash_authorized=false.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"E1.25 audit failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
