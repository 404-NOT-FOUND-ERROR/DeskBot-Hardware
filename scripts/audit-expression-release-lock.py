#!/usr/bin/env python3
"""Validate an offline expression release lock against pinned project evidence."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_LOCK = ROOT / "protocol/expression-release-lock-e1-4c.json"
DEFAULT_OUTPUT = ROOT / "tmp/expression-e1-4c-20261021/release-audit.json"
LOCK_SCHEMA = "deskbot.expression_release_lock/v1"
PROFILE_SCHEMA = "deskbot.role_profile.v1"
BINDING_SCHEMA = "deskbot.behavior_bindings.v1"
NODE_SCHEMA = "deskbot.expression_node_graph/v1"
LIBRARY_SCHEMA = "deskbot.expression_master_library/v1"
EAF_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
COMMIT = "46d427255756fe2a148777ea3f256b615b52e160"
APP_PREFIX = "examples/xiaozhi-esp32/"
BINDING_VERSION = "deskbot.expression_bindings/v1"
_GIT_BLOB_CACHE: dict[tuple[str, str, str], bytes] = {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} cannot be read as JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def safe_project_path(root: Path, value: Any, label: str, *, directory: bool = False) -> Path:
    if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
            or ":" in value):
        raise ValueError(f"{label} must be a non-empty project-relative POSIX path")
    relative = PurePosixPath(value)
    if (relative.is_absolute() or relative.as_posix() != value
            or any(part in ("", ".", "..") for part in relative.parts)):
        raise ValueError(f"{label} must not be absolute or traverse directories")
    target = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse symlinks")
    resolved_root = root.resolve()
    resolved_target = target.resolve(strict=True)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the project directory") from exc
    if directory and not resolved_target.is_dir():
        raise ValueError(f"{label} is not a directory")
    if not directory and not resolved_target.is_file():
        raise ValueError(f"{label} is not a file")
    return resolved_target


def check_hashed_file(root: Path, ref: Any, label: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise ValueError(f"{label} must contain exactly path and sha256")
    expected = ref["sha256"]
    if not isinstance(expected, str) or not EAF_SHA_RE.fullmatch(expected):
        raise ValueError(f"{label} has an invalid SHA-256")
    path = safe_project_path(root, ref["path"], label)
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch")
    return path, load_json(path, label)


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location("deskbot_release_" + path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load project validator: " + name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_capability_validator():
    return load_script("export-expression-capabilities.py")


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def project_semantic_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = report.get("eaf_assets")
    semantics = report.get("semantics")
    if not isinstance(assets, dict) or not isinstance(semantics, list):
        raise ValueError("behavior report has no complete semantic/resource inventory")
    result = {}
    for semantic in semantics:
        if not isinstance(semantic, dict):
            raise ValueError("behavior report contains a malformed semantic")
        ident, src = semantic.get("id"), semantic.get("src")
        asset = assets.get(src)
        if (not isinstance(ident, str) or ident in result or not isinstance(src, str)
                or not isinstance(asset, dict)):
            raise ValueError("behavior report has an invalid or ambiguous semantic resource")
        result[ident] = {
            "resource_id": src,
            "sha256": asset.get("sha256"),
            "bytes": asset.get("bytes"),
            "fps": semantic.get("fps"),
            "loop": semantic.get("loop"),
            "action_id": semantic.get("set_emotion_action"),
        }
    return result


def apply_single_replacement(
    original: dict[str, dict[str, Any]], semantic_id: str,
    candidate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build a new map only after validation; never mutate the pinned original."""
    if semantic_id not in original:
        raise ValueError("replacement semantic is absent from the original map")
    effective = copy.deepcopy(original)
    previous = effective[semantic_id]
    effective[semantic_id] = {
        "resource_id": candidate["src"],
        "sha256": candidate["sha256"],
        "bytes": candidate["size"],
        "fps": candidate["fps"],
        "loop": candidate["loop"],
        "action_id": previous["action_id"],
    }
    return effective


def resolve_profile_expression_map(
    profile: dict[str, Any], semantic_map: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    resolved = {}
    for intent, semantic_id in profile["expression_map"].items():
        if semantic_id not in semantic_map:
            raise ValueError(
                f"role profile expression_map.{intent} targets unknown semantic: {semantic_id}"
            )
        resolved[intent] = {
            "semantic_id": semantic_id,
            **semantic_map[semantic_id],
        }
    return resolved


def verify_official_inputs(root: Path, baseline: dict, report: dict, graph: dict) -> dict:
    """Read only pinned Git objects; never scan or reset a generated source tree."""
    checkout = safe_project_path(root, baseline["source_checkout"], "source_checkout", directory=True)
    builder = load_script("build-expression-node-workbench.py")
    builder.validate_report(report)
    if builder.enrich_graph(report) != graph:
        raise ValueError("node graph does not correspond to the behavior binding report")

    def blob(path: str) -> bytes:
        relative = PurePosixPath(path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in path
                or ":" in path or relative.as_posix() != path):
            raise ValueError("official source object path is invalid")
        cache_key = (str(checkout), COMMIT, path)
        if cache_key in _GIT_BLOB_CACHE:
            return _GIT_BLOB_CACHE[cache_key]
        try:
            raw = subprocess.check_output(
                ["git", "-C", str(checkout), "show", COMMIT + ":" + APP_PREFIX + path],
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError("pinned official source object is unavailable: " + path) from exc
        _GIT_BLOB_CACHE[cache_key] = raw
        return raw

    checked = {}
    for item in report["source"]["inputs"]:
        if item.get("origin") != "official_git_blob":
            continue
        raw = blob(item["path"])
        if (item.get("commit") != COMMIT
                or hashlib.sha256(raw).hexdigest() != item.get("sha256")):
            raise ValueError("official source input hash differs: " + item["path"])
        checked[item["path"]] = raw
    assets = {}
    for name, item in report["eaf_assets"].items():
        raw = blob(item["path"])
        if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("official EAF source hash differs: " + name)
        assets[name] = {"sha256": item["sha256"], "bytes": len(raw), "path": item["path"]}
    emote_path = "main/boards/esp_vocat/assets/360_360/emote.json"
    layout_path = "main/boards/esp_vocat/assets/360_360/layout.json"
    if emote_path not in checked or layout_path not in checked:
        raise ValueError("official emote/layout source inputs were not verified")
    official = json.loads(checked[emote_path])
    semantics = {item["id"]: item for item in report["semantics"]}
    if set(semantics) != {item["emote"] for item in official} | {"listen"}:
        raise ValueError("semantic inventory differs from the pinned emote manifest")
    for item in official:
        semantic = semantics[item["emote"]]
        if any(semantic[key] != item[key] for key in ("src", "fps", "loop")):
            raise ValueError("semantic binding differs from the pinned manifest: " + item["emote"])
    layouts = json.loads(checked[layout_path])
    if {item["name"]: item for item in layouts if item["type"] == "anim"} != report["layouts"]:
        raise ValueError("layout definitions differ from the pinned source")
    validator = load_capability_validator()
    if report["emotion_action_map"] != validator.EMOTION_ACTION:
        raise ValueError("official emotion action mapping drifted")
    for ident, semantic in semantics.items():
        if semantic.get("set_emotion_action") != validator.EMOTION_ACTION.get(ident):
            raise ValueError("semantic action differs from the pinned action map: " + ident)
    return {"source_object_count": len(checked), "eaf_object_count": len(assets), "assets": assets}


def validate_lock(lock: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    required = {
        "schema", "release_id", "binding_version", "asset_version", "source_baseline",
        "role_profile", "resource_manifest", "candidate_root", "candidate_asset",
        "replacement", "fallback", "safety",
    }
    if not isinstance(lock, dict) or set(lock) != required:
        raise ValueError("release lock fields do not match v1 contract")
    if lock["schema"] != LOCK_SCHEMA:
        raise ValueError("unsupported release lock schema/version")
    if not isinstance(lock["release_id"], str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.-]{0,63}", lock["release_id"]
    ):
        raise ValueError("release_id is invalid")
    if lock["binding_version"] != BINDING_VERSION:
        raise ValueError("unsupported binding_version for the current release-lock implementation")
    if (not isinstance(lock["asset_version"], str) or not lock["asset_version"].strip()
            or len(lock["asset_version"]) > 96):
        raise ValueError("asset_version must be non-empty and at most 96 characters")
    safety = lock["safety"]
    expected_safety = {
        "device_accessed", "firmware_changed", "nvs_changed", "deployment_eligible",
    }
    if (not isinstance(safety, dict) or set(safety) != expected_safety
            or any(value is not False for value in safety.values())):
        raise ValueError("release lock must keep all device/deployment safety flags false")

    baseline = lock["source_baseline"]
    expected_baseline_keys = {
        "repository", "commit", "source_checkout", "behavior_bindings", "node_graph", "master_library",
    }
    if not isinstance(baseline, dict) or set(baseline) != expected_baseline_keys:
        raise ValueError("source_baseline fields do not match v1 contract")
    if baseline["repository"] != "https://gitee.com/esp-friends/esp-vocat":
        raise ValueError("source repository differs from the pinned official source")
    if baseline["commit"] != "46d427255756fe2a148777ea3f256b615b52e160":
        raise ValueError("source commit differs from the pinned official baseline")
    _, binding_report = check_hashed_file(root, baseline["behavior_bindings"], "behavior_bindings")
    _, node_graph = check_hashed_file(root, baseline["node_graph"], "node_graph")
    _, master_library = check_hashed_file(root, baseline["master_library"], "master_library")

    if (binding_report.get("schema") != BINDING_SCHEMA
            or binding_report.get("source", {}).get("official_commit") != baseline["commit"]
            or binding_report.get("result") != "source_only_binding_audit"):
        raise ValueError("behavior binding report does not match the pinned source baseline")
    report_safety = binding_report.get("safety", {})
    if any(report_safety.get(key) is not False for key in (
        "hardware_accessed", "firmware_changed", "runtime_observed", "device_playback_verified",
    )):
        raise ValueError("behavior binding evidence is not explicitly offline-only")
    if (node_graph.get("schema") != NODE_SCHEMA
            or node_graph.get("source_commit") != baseline["commit"]
            or node_graph.get("runtime_observation") != "not_collected"
            or node_graph.get("editable_bindings") is not False):
        raise ValueError("node graph is not the pinned, read-only source graph")
    official_inputs = verify_official_inputs(root, baseline, binding_report, node_graph)
    if master_library.get("schema") != LIBRARY_SCHEMA:
        raise ValueError("expression master library schema differs")
    official_collections = [
        item for item in master_library.get("collections", [])
        if item.get("id") == "official-vocat"
    ]
    if (len(official_collections) != 1
            or official_collections[0].get("source", {}).get("commit") != baseline["commit"]
            or official_collections[0].get("format") != "eaf"):
        raise ValueError("master library official collection differs from the pinned source")

    profile_path, profile = check_hashed_file(root, lock["role_profile"], "role_profile")
    from protocol.deskbot_role_profile import validate_role_profile

    validate_role_profile(profile)
    if profile.get("schema") != PROFILE_SCHEMA:
        raise ValueError("role profile schema differs")
    if profile.get("asset_version") != lock["asset_version"]:
        raise ValueError("role profile asset_version does not match the release lock")
    if profile.get("binding_version") != lock["binding_version"]:
        raise ValueError("role profile binding_version does not match the release lock")

    manifest_path, manifest = check_hashed_file(root, lock["resource_manifest"], "resource_manifest")
    candidate_root = safe_project_path(root, lock["candidate_root"], "candidate_root", directory=True)
    candidate_asset = lock["candidate_asset"]
    if (not isinstance(candidate_asset, dict)
            or set(candidate_asset) != {"id", "src", "sha256"}
            or not isinstance(candidate_asset.get("id"), str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,30}", candidate_asset["id"])
            or not isinstance(candidate_asset.get("src"), str)
            or not isinstance(candidate_asset.get("sha256"), str)
            or not EAF_SHA_RE.fullmatch(candidate_asset["sha256"])):
        raise ValueError("candidate_asset fields or SHA-256 are invalid")
    replacement = lock["replacement"]
    if (not isinstance(replacement, dict)
            or set(replacement) != {"semantic_id", "candidate_expression_id", "mode"}
            or not isinstance(replacement.get("semantic_id"), str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", replacement["semantic_id"])
            or not isinstance(replacement.get("candidate_expression_id"), str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,30}", replacement["candidate_expression_id"])
            or replacement.get("mode") != "replace"):
        raise ValueError("replacement must describe exactly one semantic replace operation")
    fallback = lock["fallback"]
    if (not isinstance(fallback, dict)
            or set(fallback) != {"resource_id", "sha256", "retained_action_id"}
            or not isinstance(fallback.get("resource_id"), str)
            or (fallback.get("retained_action_id") is not None
                and not isinstance(fallback["retained_action_id"], str))
            or not isinstance(fallback.get("sha256"), str)
            or not EAF_SHA_RE.fullmatch(fallback["sha256"])):
        raise ValueError("fallback fields or SHA-256 are invalid")

    semantics = [
        item for item in binding_report.get("semantics", [])
        if item.get("id") == replacement.get("semantic_id")
    ]
    if len(semantics) != 1:
        raise ValueError("replacement semantic is absent or ambiguous in official bindings")
    original = semantics[0]
    if original.get("id") in load_capability_validator().PROTECTED:
        raise ValueError("protected semantics cannot be replaced by this offline contract")
    if (fallback.get("resource_id") != original.get("src")
            or fallback.get("retained_action_id") != original.get("set_emotion_action")):
        raise ValueError("fallback resource or action differs from the original semantic binding")
    original_asset = binding_report.get("eaf_assets", {}).get(original["src"])
    if not isinstance(original_asset, dict) or original_asset.get("sha256") != fallback["sha256"]:
        raise ValueError("fallback asset hash differs from the pinned official binding report")
    original_map = project_semantic_map(binding_report)
    original_profile_map = resolve_profile_expression_map(profile, original_map)
    profile_intents = [
        intent for intent, semantic in profile["expression_map"].items()
        if semantic == replacement["semantic_id"]
    ]
    if not profile_intents:
        raise ValueError("role profile does not expose the semantic being replaced")

    if manifest.get("schema") != "deskbot.expression_capabilities/v1":
        raise ValueError("candidate manifest schema differs")
    if (set(manifest) != {"schema", "expressions", "bindings", "actions"}
            or not isinstance(manifest.get("expressions"), list)
            or not isinstance(manifest.get("bindings"), list)
            or len(manifest["expressions"]) != 1
            or len(manifest["bindings"]) != 1
            or manifest.get("actions", []) != []):
        raise ValueError("E1.4c accepts one expression, one replacement binding, and no action changes")
    expression = manifest["expressions"][0]
    binding = manifest["bindings"][0]
    if (not isinstance(expression, dict)
            or set(expression) != {"id", "src", "loop", "fps", "layout", "segments"}
            or not isinstance(binding, dict)
            or expression.get("id") != candidate_asset["id"]
            or expression.get("src") != candidate_asset["src"]
            or binding != {
                "id": replacement["semantic_id"],
                "target": replacement["candidate_expression_id"],
                "mode": "replace",
            }
            or replacement["candidate_expression_id"] != candidate_asset["id"]):
        raise ValueError("candidate manifest mapping does not match the release lock")

    validator = load_capability_validator()
    validated = validator.validate_manifest(
        manifest_path, candidate_root, allow_protected=False,
        max_package_bytes=None, package_bin=None,
    )
    if validated.get("result") != "offline_validated" or len(validated.get("expressions", [])) != 1:
        raise ValueError("candidate manifest or EAF failed the existing offline validator")
    checked_asset = validated["expressions"][0]
    if checked_asset.get("sha256") != candidate_asset["sha256"]:
        raise ValueError("candidate EAF hash differs from the release lock")
    if checked_asset["sha256"] == fallback["sha256"]:
        raise ValueError("candidate bytes unexpectedly equal the pinned fallback asset")
    if (checked_asset["src"] in binding_report["eaf_assets"]
            or checked_asset["id"] in original_map):
        raise ValueError("candidate resource/identity collides with the pinned official inventory")

    effective_map = apply_single_replacement(
        original_map, replacement["semantic_id"], checked_asset,
    )
    effective_profile_map = resolve_profile_expression_map(profile, effective_map)
    changed_semantics = [
        semantic_id for semantic_id in original_map
        if original_map[semantic_id] != effective_map[semantic_id]
    ]
    rollback_map = copy.deepcopy(effective_map)
    rollback_map[replacement["semantic_id"]] = copy.deepcopy(
        original_map[replacement["semantic_id"]]
    )
    rollback_exact = rollback_map == original_map
    if changed_semantics != [replacement["semantic_id"]] or not rollback_exact:
        raise ValueError("single replacement/rollback did not preserve the complete original map")
    actions_unchanged = all(
        effective_map[semantic_id]["action_id"] == original_map[semantic_id]["action_id"]
        for semantic_id in original_map
    )
    if not actions_unchanged:
        raise ValueError("replacement changed the official action mapping")
    action_map = binding_report["emotion_action_map"]
    if not isinstance(action_map, dict):
        raise ValueError("official action map is malformed")

    return {
        "schema": "deskbot.expression_release_audit/v1",
        "result": "offline_validated",
        "release_id": lock["release_id"],
        "binding_version": lock["binding_version"],
        "asset_version": lock["asset_version"],
        "replacement": {
            "semantic_id": replacement["semantic_id"],
            "profile_intents": profile_intents,
            "candidate_expression_id": checked_asset["id"],
            "candidate_src": checked_asset["src"],
            "candidate_sha256": checked_asset["sha256"],
            "candidate_bytes": checked_asset["size"],
            "retained_action_id": original.get("set_emotion_action"),
        },
        "mapping_comparison": {
            "original_semantics": original_map,
            "effective_semantics": effective_map,
            "changed_semantics": changed_semantics,
            "unchanged_semantic_count": len(original_map) - len(changed_semantics),
            "shared_resource_semantics_preserved": [
                semantic_id for semantic_id, item in original_map.items()
                if item["resource_id"] == original["src"]
                and semantic_id != replacement["semantic_id"]
                and item == effective_map[semantic_id]
            ],
            "official_emotion_action_map": copy.deepcopy(action_map),
            "official_emotion_action_map_sha256": canonical_sha(action_map),
            "action_map_unchanged": actions_unchanged,
            "role_action_map": copy.deepcopy(profile["action_map"]),
            "profile_expression_map_before": original_profile_map,
            "profile_expression_map_after": effective_profile_map,
            "rollback_exact": rollback_exact,
        },
        "candidate_render_contract": {
            "layout": checked_asset.get("layout"),
            "fps": checked_asset["fps"],
            "loop": checked_asset["loop"],
            "segments": checked_asset.get("segments"),
            "eaf": checked_asset["eaf"],
            "note": "offline metadata only; circular mask, target timing, and device playback are unverified",
        },
        "rollback": {
            "available": True,
            "scope": "offline_mapping_only",
            "exact_map_restored": rollback_exact,
            "restored_map_sha256": canonical_sha(rollback_map),
            "original_map_sha256": canonical_sha(original_map),
            "device_rollback_performed": False,
            "resource_id": fallback["resource_id"],
            "sha256": fallback["sha256"],
            "reason": "restore the original semantic-to-EAF binding without changing the action map",
        },
        "package_capacity_verified": False,
        "activation_supported": False,
        "official_source_objects_verified": official_inputs["source_object_count"],
        "official_eaf_objects_verified": official_inputs["eaf_object_count"],
        "device_accessed": False,
        "firmware_changed": False,
        "nvs_changed": False,
        "device_playback_verified": False,
        "deployment_eligible": False,
        "inputs": {
            "role_profile": profile_path.relative_to(root.resolve()).as_posix(),
            "resource_manifest": manifest_path.relative_to(root.resolve()).as_posix(),
            "source_commit": baseline["commit"],
        },
    }


def write_report_atomic(path: Path, report: dict[str, Any]) -> None:
    """Publish a complete offline report; validation never changes active bindings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-report-", dir=path.parent) as temporary:
        staged = Path(temporary) / "report.json"
        staged.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(staged, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        lock_path = args.lock.resolve(strict=True)
        lock_path.relative_to(ROOT.resolve())
        output_path = args.output.resolve()
        output_relative = output_path.relative_to(ROOT.resolve())
        if output_relative.parts[0] not in {"tmp", "artifacts"} or output_path.suffix != ".json":
            raise ValueError("audit output must be a JSON report inside project tmp/ or artifacts/")
        lock = load_json(lock_path, "release_lock")
        result = validate_lock(lock)
        locked_directories = (
            safe_project_path(ROOT, lock["source_baseline"]["source_checkout"],
                              "source_checkout", directory=True),
            safe_project_path(ROOT, lock["candidate_root"], "candidate_root", directory=True),
        )
        if any(output_path.is_relative_to(directory) for directory in locked_directories):
            raise ValueError("audit report must not write inside a locked source or candidate directory")
        input_paths = {
            lock_path,
            *(safe_project_path(ROOT, lock[field]["path"], field)
              for field in ("role_profile", "resource_manifest")),
            *(safe_project_path(ROOT, lock["source_baseline"][field]["path"], field)
              for field in ("behavior_bindings", "node_graph", "master_library")),
        }
        if output_path in input_paths:
            raise ValueError("audit report must not overwrite a locked input")
        write_report_atomic(output_path, result)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result = {
            "schema": "deskbot.expression_release_audit/v1",
            "result": "rejected",
            "error": str(exc),
            "device_accessed": False,
            "firmware_changed": False,
            "nvs_changed": False,
            "deployment_eligible": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
