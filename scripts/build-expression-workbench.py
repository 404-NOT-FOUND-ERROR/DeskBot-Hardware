#!/usr/bin/env python3
"""Build a static, hardware-free DeskBot expression workbench."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.eaf_preview import decode_eaf, write_sprite_atlas  # noqa: E402

SCHEMA = "deskbot.expression_workbench_bundle.v1"
CAP_SCHEMA = "deskbot.expression_capabilities/v1"
CAPABILITIES_DEFAULT = ROOT / "tmp/expression-e1-20261019/capabilities.json"
OUTPUT_DEFAULT = ROOT / "artifacts/expression-workbench-e1-20261019"


def load_exporter() -> Any:
    path = ROOT / "scripts/export-expression-capabilities.py"
    spec = importlib.util.spec_from_file_location("deskbot_expression_exporter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the existing manifest validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_resource_audit(source_root: Path) -> dict[str, Any]:
    path = ROOT / "scripts/audit-expression-resources.py"
    spec = importlib.util.spec_from_file_location("deskbot_expression_auditor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the expression resource auditor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit(SimpleNamespace(
        source_root=source_root, official_package=module.OFFICIAL_PACKAGE_DEFAULT,
        research_bundle=module.RESEARCH_BUNDLE_DEFAULT, library_manifest=module.MANIFEST_DEFAULT,
        partition_bytes=module.PARTITION_BYTES_DEFAULT,
    ), write_output=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def safe_source_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or path.drive or ".." in path.parts:
        raise ValueError(f"unsafe source asset path: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"asset escaped source root: {relative}") from exc
    if not resolved.is_file():
        raise ValueError(f"missing EAF asset: {relative}")
    return resolved


def render_template(data: dict[str, Any], output: Path) -> None:
    template = (ROOT / "scripts/templates/expression-workbench-e1.html").read_text(encoding="utf-8")
    core = (ROOT / "scripts/templates/expression-workbench-core.js").read_text(encoding="utf-8")
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    serialized = (serialized.replace("<", "\\u003c").replace(">", "\\u003e")
                  .replace("&", "\\u0026"))
    edition = data.get("edition", "E1.1")
    html = template.replace("__CORE_JS__", core).replace("__DATA_JSON__", serialized).replace("__EDITION__", edition)
    if "__CORE_JS__" in html or "__DATA_JSON__" in html or "__EDITION__" in html:
        raise ValueError("workbench template contains an unexpanded placeholder")
    output.write_text(html, encoding="utf-8", newline="\n")


def verify_resource_provenance(root: Path, commit: str | None, paths: list[str]) -> dict[str, Any]:
    """Compare only these resource inputs with HEAD; never enumerate build trees."""
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8").strip()
    actual_commit = git("rev-parse", "HEAD")
    if commit != actual_commit:
        raise ValueError("capability source commit differs from the selected source tree")
    prefix = git("rev-parse", "--show-prefix")
    tree = git("ls-tree", "-r", "--full-name", "HEAD", "--", *paths)
    expected: dict[str, str] = {}
    for line in tree.splitlines():
        fields, path = line.split("\t", 1)
        expected[path] = fields.split()[2]
    checks = []
    for relative in sorted(set(paths)):
        path = safe_source_file(root, relative)
        raw = path.read_bytes()
        blob_hash = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        normalized = raw.replace(b"\r\n", b"\n") if path.suffix == ".json" else raw
        normalized_hash = hashlib.sha1(b"blob " + str(len(normalized)).encode("ascii") + b"\0" + normalized).hexdigest()
        expected_hash = expected.get(prefix + relative)
        checks.append({"path":relative, "sha256":hashlib.sha256(raw).hexdigest(),
                       "matches_commit":expected_hash in {blob_hash, normalized_hash},
                       "text_eol_normalized":expected_hash != blob_hash and expected_hash == normalized_hash})
    if not all(item["matches_commit"] for item in checks):
        raise ValueError("official resource inputs differ from the fixed source commit: " +
                         ", ".join(item["path"] for item in checks if not item["matches_commit"]))
    return {"commit":actual_commit, "resource_inputs_match_commit":True, "resource_inputs":checks}


def build(args: argparse.Namespace) -> dict[str, Any]:
    caps_path = args.capabilities.resolve()
    caps = read_json(caps_path)
    if caps.get("schema") != CAP_SCHEMA:
        raise ValueError("unsupported expression capability schema")
    if caps.get("runtime", {}).get("activation_supported") is not False:
        raise ValueError("capabilities must not claim runtime replacement support")
    source_root = (args.source_root or Path(caps.get("source_root", ""))).resolve()
    if not source_root.is_dir():
        raise ValueError("explicit official source root does not exist")
    official = caps.get("expressions")
    if not isinstance(official, list) or not official:
        raise ValueError("capability report has no expression entries")
    resource_paths = [item["path"] for item in official]
    listening = caps.get("listening")
    if isinstance(listening, dict) and listening.get("path"):
        resource_paths.append(listening["path"])
    resource_paths.extend(["main/boards/esp_vocat/assets/360_360/emote.json",
                           "main/boards/esp_vocat/assets/360_360/layout.json"])
    resource_provenance = verify_resource_provenance(source_root, caps.get("source_commit"), resource_paths)
    actual_emote = json.loads((source_root / "main/boards/esp_vocat/assets/360_360/emote.json").read_text(encoding="utf-8"))
    expected_semantics = {item["emote"]:(item["src"],item["loop"],item["fps"]) for item in actual_emote}
    if ({item["id"]:(item["src"],item["loop"],item["fps"]) for item in official}
            != expected_semantics or len(official) != len(actual_emote)):
        raise ValueError("capability semantic bindings differ from the fixed official emote.json")
    exporter = load_exporter()
    if caps.get("emotion_action_map") != exporter.EMOTION_ACTION:
        raise ValueError("emotion/action mapping differs from the audited official capability contract")
    resource_audit = load_resource_audit(source_root)

    output = args.output.resolve()
    assets_dir = output / "assets"
    output.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    assets: dict[str, dict[str, Any]] = {}
    expression_map: dict[str, dict[str, Any]] = {}
    aliases_by_src: dict[str, list[str]] = {}

    def add_asset(item: dict[str, Any], file_path: Path, origin: str) -> str:
        actual_sha = sha256(file_path)
        key = f"eaf-{actual_sha[:16]}"
        if key not in assets:
            animation = decode_eaf(file_path, fps=20)
            atlas_name = f"{key}.png"
            atlas = write_sprite_atlas(assets_dir / atlas_name, animation, columns=8)
            assets[key] = {
                "asset_key": key, "src": item["src"], "origin": origin,
                "sha256": actual_sha, "size_bytes": file_path.stat().st_size,
                "atlas": "data:image/png;base64," + base64.b64encode((assets_dir / atlas_name).read_bytes()).decode("ascii"),
                "atlas_file": f"assets/{atlas_name}", "atlas_sha256": sha256(assets_dir / atlas_name),
                "atlas_width": atlas["width"], "atlas_height": atlas["height"],
                "columns": atlas["columns"], "frame_width": atlas["frame_width"],
                "frame_height": atlas["frame_height"], "frame_count": atlas["frame_count"],
                "stored_frame_count": animation.stored_frame_count,
                "metadata_frame_count": animation.metadata_frame_count,
                "object_kind": (item.get("layout") if item.get("layout") == "full_screen"
                                else "listen" if item["id"] == "listen" else "eye"),
            }
            if item.get("layout") is not None:
                assets[key]["layout"] = item["layout"]
            if item.get("segments") is not None:
                assets[key]["segments"] = item["segments"]
        elif item["id"] == "listen":
            assets[key]["object_kind"] = "listen"
        return key

    official_ids: set[str] = set()
    for item in official:
        ident = item.get("id")
        if not isinstance(ident, str) or ident in official_ids:
            raise ValueError("official capability report contains an invalid or duplicate expression id")
        official_ids.add(ident)
        rel = item.get("path")
        path = safe_source_file(source_root, rel)
        actual_sha = sha256(path)
        if item.get("sha256") and item["sha256"] != actual_sha:
            raise ValueError(f"official asset hash differs from capability report: {ident}")
        asset_key = add_asset({"id": ident, "src": item.get("src", path.name)}, path, "official")
        asset = assets[asset_key]
        if item.get("eaf", {}).get("frames") != asset["stored_frame_count"]:
            raise ValueError(f"decoded stored frame count differs from capability report: {ident}")
        expression_map[ident] = {
            "id": ident, "src": item["src"], "loop": bool(item.get("loop")),
            "fps": int(item.get("fps", 20)), "asset_key": asset_key, "origin": "official",
        }
        aliases_by_src.setdefault(item["src"], []).append(ident)

    listening = caps.get("listening")
    if isinstance(listening, dict) and listening.get("path"):
        listen_path = safe_source_file(source_root, listening["path"])
        if listening.get("sha256") != sha256(listen_path):
            raise ValueError("listening asset hash differs from capability report")
        key = add_asset({"id": "listen", "src": listening.get("src", listen_path.name)}, listen_path, "official")
        asset = assets[key]
        expression_map["listen"] = {"id":"listen", "src":listening.get("src","listen.eaf"),
                                     "loop":True,"fps":15,"asset_key":key,"origin":"official"}
        aliases_by_src.setdefault(expression_map["listen"]["src"], []).append("listen")

    actions = {item["id"]: {"id":item["id"], "protocol_value":item.get("protocol_value"), "origin":"official"}
               for item in caps.get("actions", [])}
    preloaded: list[dict[str, Any]] = []
    candidate_manifest: dict[str, Any] | None = None
    candidate_validation: dict[str, Any] | None = None
    if args.manifest:
        if not args.candidate_root:
            raise ValueError("--manifest requires --candidate-root")
        candidate_validation = exporter.validate_manifest(
            args.manifest.resolve(), args.candidate_root.resolve(), args.allow_protected
        )
        if candidate_validation.get("result") != "offline_validated":
            raise ValueError("candidate manifest rejected: " + json.dumps(candidate_validation.get("errors", []), ensure_ascii=False))
        candidate_manifest = read_json(args.manifest.resolve())
        for item in candidate_validation["expressions"]:
            if item["id"] in official_ids:
                raise ValueError(f"candidate expression ID collides with official semantic: {item['id']}")
            path = safe_source_file(args.candidate_root.resolve(), item["src"])
            key = add_asset(item, path, "candidate")
            animation = assets[key]
            expression = {
                "id":item["id"], "src":item["src"], "loop":item["loop"],
                "fps":item["fps"], "asset_key":key, "origin":"candidate",
            }
            if "layout" in item:
                expression["layout"] = item["layout"]
            if "segments" in item:
                expression["segments"] = item["segments"]
            expression_map[item["id"]] = expression
            preloaded.append({"id":item["id"], "src":item["src"], "loop":item["loop"],
                              "fps":item["fps"], "asset_key":key, "frame_count":animation["frame_count"],
                              "layout":item.get("layout"), "segments":item.get("segments")})
        for action in candidate_validation["actions"]:
            actions[action["id"]] = {"id":action["id"], "steps":action["steps"], "origin":"candidate"}

    for semantic, target in caps.get("emotion_action_map", {}).items():
        if target not in actions:
            raise ValueError(f"official expression action is missing from action catalog: {semantic}->{target}")

    provenance = caps.get("provenance", {})
    data = {
        "schema": SCHEMA,
        "edition": "E1.2" if candidate_manifest and any("segments" in item for item in candidate_manifest.get("expressions", [])) else "E1.1",
        "source": {"commit":caps.get("source_commit"), "source_dirty":provenance.get("source_dirty"),
                   "tracked_inputs_clean":provenance.get("tracked_inputs_clean"),
                   "resource_inputs_match_commit":resource_provenance["resource_inputs_match_commit"],
                   "emote_manifest":provenance.get("emote_manifest"), "hardware_accessed":False},
        "display": {"width":360,"height":360,"shape":"circle"},
        "expressions": expression_map, "assets": assets,
        "shared_assets": {src: ids for src,ids in aliases_by_src.items() if len(ids)>1},
        "bindings": {}, "emotion_action_map": caps.get("emotion_action_map", {}),
        "protected_semantics": caps.get("protected_semantics", []),
        "actions": actions, "preloaded": preloaded,
        "candidate_manifest": candidate_manifest,
        "resource_audit": resource_audit,
        "candidate_validation": ({"result":candidate_validation["result"],
                                  "measured_bytes":candidate_validation["package"]["measured_bytes"],
                                  "errors":candidate_validation["errors"]}
                                 if candidate_validation else None),
        "demo_profiles": [
            {"schema":"deskbot.role_profile.v1","shell_id":"shell-a","magnet_profile_id":"offline:demo-layout-a:profile-0001","role_id":"frog-chef","display_name":"青蛙厨师","voice_id":"voice-demo-01","catchphrases":["今天要不要试试新菜？","呱，闻起来很香。"],"expression_map":{"greeting":"happy","thinking":"confused","sleep":"sleepy","unknown_shell":"question"},"action_map":{"curious":"look_around","celebrate":"beat_swing"},"asset_version":"official-e1-preview","profile_revision":1,"binding_state":"confirmed","notes":"合成示例：不可用于真实设备身份。"},
            {"schema":"deskbot.role_profile.v1","shell_id":"shell-b","magnet_profile_id":"offline:demo-layout-b:profile-0001","role_id":"astronaut-cat","display_name":"宇航猫","voice_id":"voice-demo-02","catchphrases":["导航完成，喵。"],"expression_map":{"greeting":"surprised","thinking":"question","sleep":"sleepy","unknown_shell":"question"},"action_map":{"curious":"look_around"},"asset_version":"official-e1-preview","profile_revision":1,"binding_state":"confirmed","notes":"合成示例：磁铁布局与角色映射均为演示数据。"}
        ],
        "demo_observations": [
            {"label":"合成外壳 A","status":"known","profile_id":"offline:demo-layout-a:profile-0001"},
            {"label":"合成外壳 B","status":"known","profile_id":"offline:demo-layout-b:profile-0001"},
            {"label":"未知外壳","status":"unknown","profile_id":None}
        ],
    }
    html_path = output / "index.html"
    render_template(data, html_path)
    report = {
        "schema":SCHEMA,"result":"offline_preview_bundle_created",
        "hardware_accessed":False,"firmware_changed":False,"device_playback_verified":False,
        "source_commit":caps.get("source_commit"),"source_dirty":provenance.get("source_dirty"),
        "tracked_inputs_clean":provenance.get("tracked_inputs_clean"),
        "resource_provenance":resource_provenance,
        "official_semantic_count":len(official_ids),
        "official_unique_asset_count":sum(1 for asset in assets.values() if asset["origin"] == "official"),
        "candidate_expression_count":len(preloaded),
        "candidate_validation":data["candidate_validation"],
        "resource_audit": {"file":"resource-audit.json", "capacity":resource_audit["capacity"],
                           "summary":resource_audit["summary"]},
        "assets":[{key:value for key,value in asset.items() if key != "atlas"}
                  for asset in assets.values()],
        "files":{"index.html":{"sha256":sha256(html_path),"size_bytes":html_path.stat().st_size}},
    }
    (output / "report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    (output / "resource-audit.json").write_text(json.dumps(resource_audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    (output / "README.txt").write_text(
        "DeskBot E1 offline expression workbench\n\n"
        "Open index.html in a browser. All previews are embedded; no server or network is needed.\n"
        "This package is a local visual/simulation tool. It never talks to COM ports, firmware, or the device.\n"
        "Expression preview does not prove device playback or support runtime hot replacement.\n"
        "Candidate EAF files must be validated and bundled by scripts/build-expression-workbench.py.\n",
        encoding="utf-8",newline="\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capabilities", type=Path, default=CAPABILITIES_DEFAULT)
    parser.add_argument("--source-root", type=Path, help="explicit fixed source tree for the official EAF files")
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--manifest", type=Path, help="candidate EAF manifest to validate and preload")
    parser.add_argument("--candidate-root", type=Path, help="candidate EAF asset directory")
    parser.add_argument("--allow-protected", action="store_true", help="explicitly allow protected semantics for offline experiments")
    args = parser.parse_args(argv)
    try:
        report = build(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"result":"rejected","hardware_accessed":False,"error":str(exc)},ensure_ascii=False,indent=2))
        return 2
    print(json.dumps({key:value for key,value in report.items() if key not in {"assets","resource_provenance"}}
                     | {"resource_inputs_match_commit":report["resource_provenance"]["resource_inputs_match_commit"]},
                     ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
