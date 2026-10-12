#!/usr/bin/env python3
"""Offline DeskBot expression/action fake-device workbench.

The workbench is deliberately deterministic and hardware-free.  It consumes
an exported capability JSON or an expression manifest and emits a structured
timeline that a future client can inspect.  ``visual_only_preview`` never
creates a device action; ``emotion_perform`` mirrors an expression's declared
official action; ``action_execute`` expands only allow-listed actions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA = "deskbot.fake_device_preview/v1"
CAPABILITY_SCHEMA = "deskbot.expression_capabilities/v1"
MODES = {"visual_only_preview", "emotion_perform", "action_execute"}
DEFAULT_EXPRESSION_MS = 1000
MAX_DURATION_MS = 120_000
# Explicit semantic allow-list, independent of untrusted manifest contents.
# These spellings match the existing capability exporter and official tools.
OFFICIAL_ACTIONS = {"shark_head", "shark_head_decay", "look_around", "beat_swing",
                    "cat_nuzzle", "calibrate", "go_home"}
RECIPE_ACTIONS = OFFICIAL_ACTIONS - {"calibrate", "go_home"}


class PreviewError(ValueError):
    """A user input or capability contract error."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreviewError(f"cannot read JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PreviewError("JSON root must be an object")
    return value


def _as_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreviewError(f"{label} must be a non-empty string")
    return value.strip()


def _duration_from_expression(item: dict[str, Any]) -> int:
    try:
        fps = int(item.get("fps", 20))
        frames = int(item.get("eaf", {}).get("frames", 0))
    except (AttributeError, TypeError, ValueError):
        fps, frames = 20, 0
    if fps <= 0 or frames <= 0:
        return DEFAULT_EXPRESSION_MS
    # A looping expression has no natural end. One second is a bounded
    # preview window; non-looping resources use their measured duration.
    if bool(item.get("loop")):
        return DEFAULT_EXPRESSION_MS
    return max(1, min(MAX_DURATION_MS, round(frames * 1000 / fps)))


def load_capabilities(path: Path) -> dict[str, Any]:
    """Load either exported capabilities or a candidate expression manifest."""
    data = _read_json(path)
    if data.get("schema") not in (None, CAPABILITY_SCHEMA):
        raise PreviewError("unsupported capability schema/version")
    if data.get("result") == "rejected":
        raise PreviewError("capability validation has rejected this manifest")
    expressions = data.get("expressions", [])
    if not isinstance(expressions, list):
        raise PreviewError("expressions must be an array")

    expression_map: dict[str, dict[str, Any]] = {}
    for item in expressions:
        if not isinstance(item, dict):
            raise PreviewError("each expression must be an object")
        ident = _as_nonempty_string(item.get("id"), "expression id")
        if ident in expression_map:
            raise PreviewError(f"duplicate expression id: {ident}")
        expression_map[ident] = item

    action_entries = data.get("actions", [])
    if not isinstance(action_entries, list):
        raise PreviewError("actions must be an array")
    actions: dict[str, dict[str, Any]] = {}
    for item in action_entries:
        if not isinstance(item, dict):
            raise PreviewError("each action must be an object")
        ident = _as_nonempty_string(item.get("id"), "action id")
        if ident in actions:
            raise PreviewError(f"duplicate action id: {ident}")
        if "steps" not in item and ident not in OFFICIAL_ACTIONS:
            raise PreviewError(f"unknown official action: {ident}")
        if "steps" in item and ident in OFFICIAL_ACTIONS:
            raise PreviewError(f"recipe shadows official action: {ident}")
        actions[ident] = item

    binding_entries = data.get("bindings", [])
    if not isinstance(binding_entries, list):
        raise PreviewError("bindings must be an array")
    bindings: dict[str, str] = {}
    for item in binding_entries:
        if not isinstance(item, dict):
            raise PreviewError("each binding must be an object")
        semantic = _as_nonempty_string(item.get("id"), "binding id")
        target = _as_nonempty_string(item.get("target"), "binding target")
        if semantic in bindings:
            raise PreviewError(f"duplicate binding id: {semantic}")
        if target not in expression_map:
            raise PreviewError(f"unknown binding target: {target}")
        bindings[semantic] = target

    emotion_action = data.get("emotion_action_map", {})
    if not isinstance(emotion_action, dict):
        raise PreviewError("emotion_action_map must be an object")
    return {
        "source": str(path),
        "expressions": expression_map,
        "actions": actions,
        "bindings": bindings,
        "emotion_action_map": {str(k): _as_nonempty_string(v, "emotion action")
                               for k, v in emotion_action.items()},
        "capability_schema": data.get("schema"),
    }


def _resolve_expression(caps: dict[str, Any], requested: str) -> tuple[str, dict[str, Any]]:
    expression = requested
    target = caps["bindings"].get(expression, expression)
    item = caps["expressions"].get(target)
    if item is None:
        raise PreviewError(f"unknown expression: {requested}")
    return target, item


def _resolve_action(caps: dict[str, Any], requested: str) -> tuple[str, list[dict[str, Any]]]:
    item = caps["actions"].get(requested)
    if item is None:
        raise PreviewError(f"unknown action: {requested}")
    steps = item.get("steps")
    if steps is None:
        return requested, [{"action": requested, "simulation_window_ms": 0}]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        raise PreviewError(f"action has no valid steps: {requested}")
    resolved: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise PreviewError(f"invalid step in action: {requested}")
        base = _as_nonempty_string(step.get("action"), "action step")
        # Recipes may use official ordinary motions, but not calibrate or
        # go_home. Presence in a manifest can never grant raw UART access.
        if base not in RECIPE_ACTIONS:
            raise PreviewError(f"unknown action step: {base}")
        try:
            duration = step.get("duration_ms", 0)
            if isinstance(duration, bool) or not isinstance(duration, int):
                raise ValueError("duration must be an integer")
        except (TypeError, ValueError) as exc:
            raise PreviewError(f"invalid duration in action: {requested}") from exc
        if duration < 0 or duration > MAX_DURATION_MS:
            raise PreviewError(f"action duration out of bounds: {requested}")
        resolved.append({"action": base, "simulation_window_ms": duration})
    if sum(step["simulation_window_ms"] for step in resolved) > MAX_DURATION_MS:
        raise PreviewError(f"action recipe simulation window exceeds limit: {requested}")
    return requested, resolved


def _event(seq: int, at_ms: int, channel: str, event_type: str,
           payload: dict[str, Any]) -> dict[str, Any]:
    return {"sequence": seq, "at_ms": at_ms, "channel": channel,
            "type": event_type, "payload": payload}


def build_preview(caps: dict[str, Any], *, mode: str, expression: str | None = None,
                  action: str | None = None, duration_ms: int | None = None,
                  correlation_id: str = "preview-1") -> dict[str, Any]:
    """Build a deterministic fake-device timeline without touching hardware."""
    if mode not in MODES:
        raise PreviewError(f"mode must be one of: {', '.join(sorted(MODES))}")
    if mode != "action_execute" and not expression:
        raise PreviewError(f"{mode} requires an expression")
    if mode == "action_execute" and not action:
        raise PreviewError("action_execute requires an action")
    if mode != "action_execute" and action is not None:
        raise PreviewError(f"{mode} does not accept a separate action")
    if mode == "action_execute" and (expression is not None or duration_ms is not None):
        raise PreviewError("action_execute does not accept an expression or display duration")
    if duration_ms is not None and (isinstance(duration_ms, bool)
                                   or not isinstance(duration_ms, int)
                                   or duration_ms < 1 or duration_ms > MAX_DURATION_MS):
        raise PreviewError("duration_ms is out of bounds")

    timeline: list[dict[str, Any]] = []
    sequence = 1
    cursor = 0
    resolved_expression = None
    expression_item: dict[str, Any] | None = None
    if expression:
        resolved_expression, expression_item = _resolve_expression(caps, expression)
        render_duration = duration_ms or _duration_from_expression(expression_item)
        timeline.append(_event(sequence, cursor, "render.expression",
                               "expression.preview" if mode == "visual_only_preview"
                               else "expression.perform",
                               {"requested": expression, "resolved": resolved_expression,
                                "src": expression_item.get("src"),
                                "loop": bool(expression_item.get("loop")),
                                "simulation_window_ms": render_duration,
                                "hardware": "none"}))
        sequence += 1
        cursor = render_duration

    selected_action = action
    steps: list[dict[str, Any]] = []
    if mode == "emotion_perform":
        selected_action = caps["emotion_action_map"].get(resolved_expression or expression or "")
        # Bindings can rename an expression, but maps may use the user-facing
        # semantic ID. Try both before reporting that no action is declared.
        if selected_action is None and expression:
            selected_action = caps["emotion_action_map"].get(expression)
        if selected_action:
            _, steps = _resolve_action(caps, selected_action)
    elif mode == "action_execute":
        _, steps = _resolve_action(caps, action or "")

    if mode == "visual_only_preview":
        # This is the critical safety property of the workbench: no action
        # event is emitted, even when the capability map contains a mapping.
        pass
    else:
        action_cursor = 0
        for step in steps:
            timeline.append(_event(sequence, action_cursor, "device.action",
                                   "action.intent",
                                   {"action": step["action"],
                                    "simulation_window_ms": step["simulation_window_ms"],
                                    "hardware": "none",
                                    "simulation": True,
                                    "physical_completion": "unobserved"}))
            sequence += 1
            action_cursor += step["simulation_window_ms"]
        cursor = max(cursor, action_cursor)

    return {
        "schema": SCHEMA,
        "mode": mode,
        "correlation_id": correlation_id,
        "result": "simulated",
        "hardware_accessed": False,
        "visual_pixels_rendered": False,
        "physical_action_timing_verified": False,
        "device_playback_verified": False,
        "simulation_window_ms": cursor,
        "timing_note": "Times schedule offline intents only; they do not imply motor duration or completion.",
        "timeline": timeline,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capabilities", required=True, type=Path,
                        help="exported capability JSON or expression manifest")
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--expression", help="semantic expression ID")
    parser.add_argument("--action", help="allow-listed action ID")
    parser.add_argument("--duration-ms", type=int,
                        help="bounded expression preview duration")
    parser.add_argument("--correlation-id", default="preview-1")
    parser.add_argument("--output", type=Path,
                        help="write JSON output; stdout is used by default")
    args = parser.parse_args(argv)
    try:
        caps = load_capabilities(args.capabilities.resolve())
        output = build_preview(caps, mode=args.mode, expression=args.expression,
                               action=args.action, duration_ms=args.duration_ms,
                               correlation_id=args.correlation_id)
    except (OSError, PreviewError) as exc:
        error = {"schema": SCHEMA, "result": "rejected", "error": str(exc),
                 "hardware_accessed": False}
        print(json.dumps(error, ensure_ascii=False, indent=2))
        return 2
    rendered = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
