#!/usr/bin/env python3
"""Build the offline Blender-style expression/binding node workbench."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DEFAULT = ROOT / "tmp/expression-bindings-e1-4a-20261021/bindings.json"
PREVIEW_DEFAULT = ROOT / "artifacts/expression-workbench-e1-2-20261020/index.html"
OUTPUT_DEFAULT = ROOT / "artifacts/expression-node-workbench-e1-4b-20261021"
LAYERED_OUTPUT_DEFAULT = ROOT / "artifacts/expression-node-workbench-e1-4d-20261021"
LOCK_DEFAULT = ROOT / "protocol/expression-release-lock-e1-4c.json"
COMMIT = "46d427255756fe2a148777ea3f256b615b52e160"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_binding_builder():
    path = ROOT / "scripts/build-behavior-binding-viewer.py"
    spec = importlib.util.spec_from_file_location("deskbot_binding_viewer_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the accepted binding viewer builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def script_json(value: object) -> str:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def load_preview(report: dict, path: Path) -> dict:
    raw = path.read_bytes()
    match = re.search(r'<script\b(?=[^>]*\bid="bundle-data")(?=[^>]*\btype="application/json")[^>]*>(.*?)</script>',
                      raw.decode("utf-8"), re.S)
    if match is None:
        raise ValueError("accepted E1 preview has no embedded bundle")
    bundle = json.loads(match[1])
    if bundle.get("schema") != "deskbot.expression_workbench_bundle.v1":
        raise ValueError("unsupported E1 preview bundle")
    if bundle.get("source", {}).get("commit") != COMMIT:
        raise ValueError("preview bundle source commit differs from the pinned audit")
    assets = {}
    expressions = {}
    for semantic in report["semantics"]:
        expression = bundle["expressions"].get(semantic["id"])
        if not expression or expression.get("origin") != "official":
            raise ValueError("official preview missing: " + semantic["id"])
        asset = bundle["assets"][expression["asset_key"]]
        expected = report["eaf_assets"][semantic["src"]]
        if asset.get("origin") != "official" or asset.get("sha256") != expected["sha256"]:
            raise ValueError("preview mapping drift: " + semantic["id"])
        header, encoded = asset["atlas"].split(",", 1)
        if header != "data:image/png;base64":
            raise ValueError("unexpected atlas format")
        atlas = base64.b64decode(encoded, validate=True)
        if not atlas.startswith(b"\x89PNG\r\n\x1a\n") or sha256(atlas) != asset.get("atlas_sha256"):
            raise ValueError("preview atlas checksum mismatch: " + semantic["src"])
        assets[asset["asset_key"]] = asset
        expressions[semantic["id"]] = expression
    return {"expressions": expressions, "assets": assets,
            "source_commit": COMMIT, "device_playback_verified": False,
            "html_sha256": sha256(raw)}


def validate_report(report: dict) -> None:
    load_binding_builder().validate_report(report)
    if report.get("schema") != "deskbot.behavior_bindings.v1":
        raise ValueError("unsupported behavior binding report")
    if report.get("source", {}).get("official_commit") != COMMIT:
        raise ValueError("source commit differs from pinned audit")
    if any(report.get("safety", {}).get(k) is not False for k in
           ("hardware_accessed", "firmware_changed", "runtime_observed", "device_playback_verified")):
        raise ValueError("node workbench requires offline-only evidence")
    node_ids = {node["id"] for node in report["graph"]["nodes"]}
    route_ids = {route["id"] for route in report["routes"]}
    if len(node_ids) != len(report["graph"]["nodes"]):
        raise ValueError("duplicate graph node id")
    for node in report["graph"]["nodes"]:
        if node["route_id"] not in route_ids or not node.get("evidence"):
            raise ValueError("graph node lacks route or evidence")
    for edge in report["graph"]["edges"]:
        if edge["from"] not in node_ids or edge["to"] not in node_ids:
            raise ValueError("dangling graph edge")


def load_release_lock_validator():
    path = ROOT / "scripts/audit-expression-release-lock.py"
    spec = importlib.util.spec_from_file_location("deskbot_expression_release_lock", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the pinned release-lock validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_layer_model(report_path: Path, report: dict, graph: dict,
                     preview_path: Path, lock_path: Path) -> tuple[dict, dict, dict, dict]:
    """Revalidate the locked maps and candidate pixels before exposing three offline layers."""
    lock_path = lock_path.resolve(strict=True)
    lock_path.relative_to(ROOT.resolve())
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    audit = load_release_lock_validator().validate_lock(lock)
    if audit.get("result") != "offline_validated" or audit.get("deployment_eligible") is not False:
        raise ValueError("release lock is not an offline-only validated candidate")

    binding_ref = lock["source_baseline"]["behavior_bindings"]
    if sha256(report_path.resolve(strict=True).read_bytes()) != binding_ref["sha256"]:
        raise ValueError("behavior binding report differs from the pinned release lock")
    graph_ref = lock["source_baseline"]["node_graph"]
    graph_bytes = (json.dumps(graph, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if sha256(graph_bytes) != graph_ref["sha256"]:
        raise ValueError("node graph differs from the pinned release lock")

    html = preview_path.read_bytes().decode("utf-8")
    match = re.search(r'<script\b(?=[^>]*\bid="bundle-data")(?=[^>]*\btype="application/json")[^>]*>(.*?)</script>',
                      html, re.S)
    if match is None:
        raise ValueError("accepted E1 preview has no embedded bundle")
    bundle = json.loads(match[1])
    if bundle.get("schema") != "deskbot.expression_workbench_bundle.v1":
        raise ValueError("unsupported E1 preview bundle")
    if bundle.get("source", {}).get("commit") != COMMIT:
        raise ValueError("preview bundle source commit differs from the pinned audit")

    replacement = audit["replacement"]
    candidate_id = replacement["candidate_expression_id"]
    candidate = bundle.get("expressions", {}).get(candidate_id)
    if not candidate or candidate.get("origin") != "candidate":
        raise ValueError("candidate expression is missing from the accepted preview bundle")
    candidate_asset = bundle.get("assets", {}).get(candidate.get("asset_key"))
    contract = audit["candidate_render_contract"]
    expected_asset = lock["candidate_asset"]
    if (candidate.get("src") != expected_asset["src"]
            or candidate.get("fps") != contract["fps"]
            or candidate.get("loop") is not contract["loop"]
            or candidate.get("layout") != contract["layout"]):
        raise ValueError("candidate preview metadata differs from the locked render contract")
    if (not candidate_asset or candidate_asset.get("origin") != "candidate"
            or candidate_asset.get("sha256") != expected_asset["sha256"]
            or candidate_asset.get("size_bytes") != replacement["candidate_bytes"]):
        raise ValueError("candidate preview asset differs from the locked EAF identity")
    header, encoded = candidate_asset["atlas"].split(",", 1)
    if header != "data:image/png;base64":
        raise ValueError("candidate preview atlas has an unsupported encoding")
    atlas = base64.b64decode(encoded, validate=True)
    atlas_hash = sha256(atlas)
    if not atlas.startswith(b"\x89PNG\r\n\x1a\n") or atlas_hash != candidate_asset.get("atlas_sha256"):
        raise ValueError("candidate preview atlas checksum mismatch")
    if len(atlas) < 24:
        raise ValueError("candidate preview atlas is truncated")
    atlas_width, atlas_height = struct.unpack(">II", atlas[16:24])
    if (atlas_width != candidate_asset.get("atlas_width")
            or atlas_height != candidate_asset.get("atlas_height")
            or candidate_asset.get("frame_width") != 360
            or candidate_asset.get("frame_height") != 360
            or candidate_asset.get("frame_count") != contract["eaf"]["image_frames"]
            or candidate.get("segments") != contract.get("segments")):
        raise ValueError("candidate preview atlas geometry differs from the locked render contract")
    asset_relative = Path(candidate_asset["atlas_file"])
    if asset_relative.is_absolute() or ".." in asset_relative.parts:
        raise ValueError("candidate preview atlas path escapes its bundle")
    atlas_file = (preview_path.resolve().parent / asset_relative).resolve()
    atlas_file.relative_to(preview_path.resolve().parent)
    if sha256(atlas_file.read_bytes()) != atlas_hash:
        raise ValueError("candidate preview atlas file differs from its embedded pixels")

    original = audit["mapping_comparison"]["original_semantics"]
    effective = audit["mapping_comparison"]["effective_semantics"]
    if set(original) != set(effective) or audit["mapping_comparison"]["changed_semantics"] != [replacement["semantic_id"]]:
        raise ValueError("candidate must change exactly one semantic in the complete mapping")
    action_map = audit["mapping_comparison"]["official_emotion_action_map"]
    if not audit["mapping_comparison"]["action_map_unchanged"]:
        raise ValueError("candidate changed the official emotion-to-action mapping")

    def project_mapping(source: dict, candidate_layer: bool) -> dict:
        projected = {}
        for semantic, item in source.items():
            expression_id = candidate_id if candidate_layer and semantic == replacement["semantic_id"] else semantic
            expression = bundle.get("expressions", {}).get(expression_id)
            if not expression:
                raise ValueError("preview expression missing for semantic: " + semantic)
            asset_key = expression.get("asset_key")
            asset = bundle.get("assets", {}).get(asset_key)
            if not asset:
                raise ValueError("preview asset missing for semantic: " + semantic)
            if item["resource_id"] != asset.get("src") or item["sha256"] != asset.get("sha256"):
                raise ValueError("preview EAF identity differs from locked semantic: " + semantic)
            projected[semantic] = {
                "resource_id": item["resource_id"], "sha256": item["sha256"],
                "bytes": item["bytes"], "fps": item["fps"], "loop": item["loop"],
                "action_id": item["action_id"], "asset_key": asset_key,
                "expression_id": expression_id,
                "layout_override": contract["layout"] if candidate_layer and semantic == replacement["semantic_id"] else None,
                "segments": expression.get("segments"),
            }
        return projected

    official_map = project_mapping(original, False)
    candidate_map = project_mapping(effective, True)
    fallback_map = project_mapping(original, False)
    changed = [semantic for semantic in original if candidate_map[semantic] != official_map[semantic]]
    if changed != [replacement["semantic_id"]] or fallback_map != official_map:
        raise ValueError("three-layer projection changed more than the locked semantic or failed exact rollback")
    if any(candidate_map[key]["action_id"] != action_map.get(key) for key in candidate_map):
        raise ValueError("candidate layer does not preserve the official action map")

    layer_model = {
        "schema": "deskbot.expression_mapping_layers/v1",
        "default_layer": "official",
        "binding_version": audit["binding_version"],
        "asset_version": audit["asset_version"],
        "release_id": audit["release_id"],
        "replacement_semantic": replacement["semantic_id"],
        "profile_intents": replacement["profile_intents"],
        "profile_expression_map_before": audit["mapping_comparison"]["profile_expression_map_before"],
        "profile_expression_map_after": audit["mapping_comparison"]["profile_expression_map_after"],
        "comparison": {
            "changed_semantics": changed,
            "unchanged_semantic_count": len(official_map) - len(changed),
            "official_emotion_action_map_sha256": audit["mapping_comparison"]["official_emotion_action_map_sha256"],
            "action_map_unchanged": True,
            "fallback_exact": fallback_map == official_map,
            "candidate_atlas_sha256": atlas_hash,
        },
        "layers": {
            "official": {"label": "官方", "kind": "official_baseline", "read_only": True,
                          "description": "固定官方源码映射；只读基线。", "semantic_map": official_map},
            "candidate": {"label": "候选", "kind": "offline_candidate", "read_only": True,
                           "description": "仅在本机预览；只替换 embarrassed，保留官方动作请求。", "semantic_map": candidate_map},
            "fallback": {"label": "精确回退", "kind": "locked_fallback", "read_only": True,
                         "description": "逐项恢复官方资源映射；动作映射不变。", "semantic_map": fallback_map},
        },
        "evidence": {
            "lock_path": lock_path.resolve().relative_to(ROOT.resolve()).as_posix(),
            "lock_sha256": sha256(lock_path.read_bytes()),
            "binding_report_sha256": binding_ref["sha256"],
            "node_graph_sha256": graph_ref["sha256"],
            "candidate_eaf_sha256": expected_asset["sha256"],
            "candidate_atlas_sha256": atlas_hash,
            "device_accessed": False, "firmware_changed": False, "nvs_changed": False,
            "deployment_eligible": False,
        },
    }
    return layer_model, candidate, candidate_asset, audit


def enrich_graph(report: dict) -> dict:
    """Separate lookup, display layout and motor branches without runtime claims."""
    nodes, edges = [], []
    def add_edge(a, b, kind, refs):
        edges.append({"from": a, "to": b, "kind": kind,
                      "evidence": list(dict.fromkeys(refs)), "runtime_observation": "not_collected"})
    for route in report["routes"]:
        rid = route["id"]
        for i, step in enumerate(route["steps"]):
            node = dict(step, id=f"{rid}:{i}", route_id=rid, rank=i, lane=0,
                        runtime_observation="not_collected")
            nodes.append(node)
            if i:
                add_edge(f"{rid}:{i - 1}", node["id"], "source_order_not_completion", step["evidence"])
        branch_count = 0
        for i, output in enumerate(route["outputs"]):
            # Use the matching API call, not the last unrelated action in a branch.
            matches = [j for j, step in enumerate(route["steps"])
                       if step["kind"] == "call" and output["api"] in step["label"]]
            anchor_index = matches[-1] if matches else len(route["steps"]) - 1
            anchor = f"{rid}:{anchor_index}"
            resource_id = f"{rid}:output:{i}"
            lane = i * 2
            rank = anchor_index + 1
            nodes.append({"id": resource_id, "route_id": rid, "kind": "resource", "label": output["src"],
                          "detail": f"{output['semantic']} · {output['fps']} FPS · " + ("循环" if output["loop"] else "单次"),
                          "evidence": list(dict.fromkeys(output["evidence"])), "rank": rank, "lane": lane,
                          "output_index": i, "semantic": output["semantic"], "src": output["src"],
                          "runtime_observation": "not_collected"})
            add_edge(anchor, resource_id, "conditional_resource_binding", output["evidence"])
            layout = report["layouts"][output["layout"]]
            layout_id = f"{rid}:layout:{i}"
            nodes.append({"id": layout_id, "route_id": rid, "kind": "layout", "label": output["layout"],
                          "detail": f"{layout['align']} · ({layout['x']}, {layout['y']}) · mirror={layout['anim']['mirror']}",
                          "evidence": list(dict.fromkeys(output["evidence"])), "rank": rank + 1, "lane": lane,
                          "output_index": i, "runtime_observation": "not_collected"})
            add_edge(resource_id, layout_id, "display_layout", output["evidence"])
            if output.get("action_id"):
                semantic = next(s for s in report["semantics"] if s["id"] == output["semantic"])
                action = next(a for a in report["actions"] if a["id"] == output["action_id"])
                action_id = f"{rid}:motor:{i}"
                refs = list(dict.fromkeys(semantic.get("action_evidence", []) + output["evidence"]))
                nodes.append({"id": action_id, "route_id": rid, "kind": "action", "label": action["label"],
                              "detail": f"{action['id']} · cmd={action['wire_command']} / value={action['wire_value']} · 仅请求",
                              "action_id": action["id"], "evidence": refs, "rank": rank, "lane": lane + 1,
                              "runtime_observation": "not_collected"})
                add_edge(anchor, action_id, "parallel_motor_request_not_completion", refs)
            branch_count = max(branch_count, lane + (2 if output.get("action_id") else 1))
        # Keep action-only routes as source steps; do not invent EAF bindings for DOA.
    ids = {n["id"] for n in nodes}
    if len(ids) != len(nodes) or any(e["from"] not in ids or e["to"] not in ids for e in edges):
        raise ValueError("enriched graph has duplicate or dangling node IDs")
    if any(not n["evidence"] or any(ref not in report["evidence"] for ref in n["evidence"]) for n in nodes):
        raise ValueError("enriched graph has unresolved evidence")
    return {"schema": "deskbot.expression_node_graph/v1", "source_commit": COMMIT,
            "runtime_observation": "not_collected", "editable_bindings": False,
            "nodes": nodes, "edges": edges}


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';script-src 'unsafe-inline';style-src 'unsafe-inline';img-src data:;connect-src 'none';base-uri 'none';form-action 'none'>
<title>DeskBot · 表情绑定节点工作台 E1.4b</title>
<style>
:root{color-scheme:light;--bg:#e9efec;--paper:#fbfdfb;--ink:#203a36;--muted:#5c716b;--line:#c9d8d0;--accent:#17675c;--accent2:#8b4c2d;--soft:#e1efe8;--warn:#f6e8dd;--node:#dcece5;--node2:#f4e4d9;--shadow:0 7px 18px rgba(24,64,54,.12)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif}button,input,select{font:inherit;color:inherit}button,input,select{min-height:36px;border:1px solid var(--line);border-radius:6px;background:var(--paper)}button{padding:6px 10px;cursor:pointer}button:hover{background:var(--soft)}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #8abdaf;outline-offset:2px}code{font:12px Consolas,monospace;overflow-wrap:anywhere}.sub,.small{font-size:12px;color:var(--muted)}
header{display:flex;justify-content:space-between;gap:16px;align-items:center;padding:16px 22px;background:var(--paper);border-bottom:1px solid var(--line)}h1{font-size:20px;line-height:1.3;margin:0;font-weight:600}h2{font-size:14px;margin:0 0 9px;font-weight:600}.status{text-align:right;color:var(--accent);font-size:12px}.toolbar{display:flex;gap:9px;align-items:end;flex-wrap:wrap;padding:12px 22px;background:var(--paper);border-bottom:1px solid var(--line)}.field{display:grid;gap:4px;min-width:180px}.field label{font-size:11px;font-weight:600}.field input,.field select{width:100%;padding:7px}.toolbar .grow{flex:1;min-width:220px}.toolbar button{min-height:36px}.legend{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-left:auto;font-size:11px;color:var(--muted)}.legend span{display:inline-flex;align-items:center;gap:4px}.swatch{width:12px;height:12px;border-radius:3px;background:var(--node);border:1px solid var(--line)}.swatch.external,.swatch.action{background:var(--node2)}
.summary{display:flex;gap:18px;flex-wrap:wrap;padding:12px 22px;background:var(--paper);border-bottom:1px solid var(--line)}.summary strong{font-size:18px;margin-right:4px;font-variant-numeric:tabular-nums}.summary span{font-size:12px}
main{display:grid;grid-template-columns:270px minmax(500px,1fr) 330px;max-width:1800px;margin:0 auto;background:var(--paper);min-height:700px}.panel{padding:17px;min-width:0}.left,.center{border-right:1px solid var(--line)}.route-list{display:grid;gap:6px;max-height:680px;overflow:auto;margin-top:12px;padding-right:4px}.route{width:100%;text-align:left;padding:8px 9px}.route[aria-pressed=true]{background:var(--soft);border-color:var(--accent)}.route b{display:block;font-size:12px}.route small{display:block;color:var(--muted);font-size:11px}.route .tags{font:10px Consolas,monospace;color:var(--accent);overflow-wrap:anywhere;margin-top:3px}.notice{padding:9px 11px;background:var(--soft);border-left:3px solid var(--accent);font-size:12px;margin-bottom:10px}.notice.warn{background:var(--warn);border-color:var(--accent2)}
#graph-shell{position:relative;height:660px;overflow:hidden;background:radial-gradient(circle at 1px 1px,#cedbd5 1px,transparent 1px);background-size:22px 22px;border:1px solid var(--line);border-radius:8px;touch-action:none}.graph-help{display:flex;justify-content:space-between;gap:10px;margin:0 0 8px}.graph-help .small{max-width:75%}#graph{width:100%;height:100%;display:block}.edge{fill:none;stroke:#8aa9a0;stroke-width:2;opacity:.76}.edge.dim{opacity:.1}.edge.active{stroke:var(--accent);stroke-width:3;opacity:1}.node rect{fill:var(--node);stroke:#8eb8aa;stroke-width:1.2;rx:8}.node[data-kind=external] rect,.node[data-kind=action] rect{fill:var(--node2);stroke:#d19a78}.node[data-kind=resource] rect{fill:#e6e8d8;stroke:#9eaa77}.node.dim{opacity:.18}.node.active rect{stroke:var(--accent);stroke-width:2.4;filter:drop-shadow(0 3px 5px rgba(20,95,80,.22))}.node text{pointer-events:none}.node .title{font-size:13px;font-weight:600;fill:var(--ink)}.node .detail{font-size:10px;fill:var(--muted)}.node .kind{font-size:9px;fill:var(--accent);letter-spacing:.03em}.node .port{fill:var(--accent);opacity:.8}.empty-graph{display:grid;place-items:center;height:100%;color:var(--muted);font-size:13px}
.right{display:grid;gap:16px;align-content:start}.detail{border-top:1px solid var(--line);padding-top:12px}.detail p{margin:6px 0}.evidence{display:flex;gap:5px;flex-wrap:wrap}.evidence button{min-height:27px;padding:2px 6px;font-size:11px;color:var(--accent)}.screen{width:240px;aspect-ratio:1;border-radius:50%;overflow:hidden;background:#000;border:4px solid #b9cbc2;margin:10px auto}.screen canvas{display:block;width:100%;height:100%}.transport{display:flex;justify-content:center;gap:5px}.transport button{font-size:11px;min-height:30px;padding:3px 7px}.frame-row{display:flex;justify-content:space-between;color:var(--muted);font-size:11px}input[type=range]{width:100%;min-height:24px;accent-color:var(--accent);padding:0}pre{background:#edf3ef;padding:8px;border-radius:5px;white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.55 Consolas,monospace;max-height:250px;overflow:auto}footer{padding:12px 22px;border-top:1px solid var(--line);background:var(--paper);color:var(--muted);font-size:11px}
@media(max-width:1150px){main{grid-template-columns:235px minmax(430px,1fr)}.right{grid-column:1/-1;border-top:1px solid var(--line);grid-template-columns:1fr 1fr}.left,.center{border-right:1px solid var(--line)}}@media(max-width:760px){header{align-items:flex-start;padding:14px}h1{font-size:18px}.status{max-width:120px}.toolbar,.summary{padding-left:14px;padding-right:14px}.toolbar .grow{min-width:180px}.legend{margin-left:0}main{display:block}.panel{padding:14px}.left,.center{border:0;border-bottom:1px solid var(--line)}.right{display:block;border:0}.route-list{max-height:280px}#graph-shell{height:560px}.graph-help{display:block}.graph-help .small{max-width:none;margin-bottom:4px}}
</style></head><body>
<header><div><h1>表情 · 行为绑定节点工作台</h1><div class="sub">E1.4b · 只读静态证据；用节点、端口和连线审阅“输入 → 调用 → 语义 → EAF → 动作”</div></div><div class="status">官方源码链路<br>运行记录未采集</div></header>
<div class="toolbar"><div class="field grow"><label for="search">搜索节点 / 路由 / EAF</label><input id="search" type="search" placeholder="例如：摸头、Happy.eaf、DOA"></div><div class="field"><label for="route">显示路由</label><select id="route"></select></div><button id="reset-view" type="button">重置视图</button><button id="all-routes" type="button">全部路由</button><div class="legend"><span><i class="swatch"></i>事件/调用/语义</span><span><i class="swatch external"></i>外部决定/动作</span><span><i class="swatch" style="background:#e6e8d8"></i>EAF 资源</span></div></div>
<div class="summary" id="summary"></div>
<main><aside class="panel left"><div class="notice">拖动节点调整布局；拖动空白处平移，滚轮缩放。点击节点后会高亮上下游链路。选择 EAF 节点可在右侧预览。</div><p class="small" id="route-count" aria-live="polite"></p><div class="route-list" id="route-list"></div></aside>
<section class="panel center"><div class="graph-help"><div><h2 id="graph-title">路由节点图</h2><div class="small" id="graph-caption"></div></div><div class="small">当前视图仅展示静态源码证据，不表示实机发生顺序。</div></div><div id="graph-shell"><svg id="graph" role="img" aria-label="表情与行为绑定节点图"><g id="graph-layer"><g id="edges"></g><g id="nodes"></g></g></svg><div id="graph-empty" class="empty-graph" hidden>没有匹配节点。</div></div></section>
<aside class="panel right"><div><h2>选中节点</h2><div id="node-detail" class="detail"><p class="small">点击图中的节点查看来源、类型和证据。</p></div></div><div><h2>EAF 预览</h2><div class="screen"><canvas id="screen-canvas" width="360" height="360" aria-label="EAF 电脑预览"></canvas></div><div class="transport"><button id="play-toggle" type="button">播放</button><button id="next-frame" type="button">下一帧</button><button id="reset-frame" type="button">首帧</button></div><input id="frame-range" type="range" min="0" max="0" value="0" aria-label="动画帧"><div class="frame-row"><span id="frame-label"></span><span id="frame-time"></span></div><p class="small" id="preview-caption">请选择一个资源节点。</p></div><div id="evidence-detail" class="detail"><h2>源码证据</h2><p class="small">点击证据按钮查看固定文件、行号、摘录和 SHA-256。</p></div></aside></main>
<footer id="footer"></footer><script id="workbench-data" type="application/json">__DATA_JSON__</script><script>__WORKBENCH_JS__</script></body></html>'''


JS = r'''(() => {
"use strict";
const data=JSON.parse(document.getElementById("workbench-data").textContent), report=data.report, preview=data.preview;
const $=id=>document.getElementById(id), esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const nodes=report.graph.nodes, edges=report.graph.edges, routes=report.routes, evidence=report.evidence;
const kindLabel={event:"事件",call:"源码调用",external:"外部决定",semantic:"语义",action:"动作",resource:"EAF资源"};
let selectedRoute="__all__", selectedNode=null, positions=new Map(), scale=1, pan={x:0,y:0}, dragging=null, playing=false, timer=null, frame=0, activeOutput=null, drawToken=0;
const graph=$('graph'), layer=$('graph-layer'), edgeLayer=$('edges'), nodeLayer=$('nodes'), shell=$('graph-shell'), ctx=$('screen-canvas').getContext('2d'), scratch=document.createElement('canvas'), scratchCtx=scratch.getContext('2d'), images=new Map();
function routeFor(id){return routes.find(r=>r.id===id)}
function nodeRoute(n){return n.route_id}
function visibleNodes(){const q=$('search').value.trim().toLowerCase();return nodes.filter(n=>(selectedRoute==="__all__"||nodeRoute(n)===selectedRoute)&&(!q||[n.label,n.detail,n.kind,n.route_id].join(" ").toLowerCase().includes(q)))}
function visibleEdges(set){return edges.filter(e=>set.has(e.from)&&set.has(e.to))}
function setTransform(){layer.setAttribute("transform",`translate(${pan.x} ${pan.y}) scale(${scale})`)}
function layout(list){const next=new Map(), routeIndex=new Map(routes.map((r,i)=>[r.id,i]));const byRoute=new Map();list.forEach(n=>{if(!byRoute.has(n.route_id))byRoute.set(n.route_id,[]);byRoute.get(n.route_id).push(n)}); for(const [rid,ns] of byRoute){ns.sort((a,b)=>nodes.indexOf(a)-nodes.indexOf(b));ns.forEach((n,i)=>{const old=positions.get(n.id);next.set(n.id,old||{x:40+i*245,y:40+(routeIndex.get(rid)||0)*150})})} positions=next}
function nodeSize(n){return {w:n.kind==="resource"?190:210,h:78}}
function point(n){const p=positions.get(n.id),s=nodeSize(n);return {x:p.x,y:p.y,w:s.w,h:s.h,cx:p.x+s.w/2,cy:p.y+s.h/2}}
function render(){const list=visibleNodes(),set=new Set(list.map(n=>n.id));layout(list);$('graph-empty').hidden=list.length!==0;$('route-count').textContent=`${list.length} 个节点 · ${visibleEdges(set).length} 条连线`;$('graph-title').textContent=selectedRoute==="__all__"?"全部路由节点图":(routeFor(selectedRoute)?.title||"路由节点图");$('graph-caption').textContent=selectedRoute==="__all__"?"按路由分层；拖拽可整理视图":(routeFor(selectedRoute)?.condition||"");
 edgeLayer.innerHTML=""; for(const e of visibleEdges(set)){const a=point(nodes.find(n=>n.id===e.from)),b=point(nodes.find(n=>n.id===e.to)),x1=a.x+a.w,x2=b.x,c1=x1+(x2-x1)*.45,c2=x2-(x2-x1)*.45;const path=document.createElementNS("http://www.w3.org/2000/svg","path");path.setAttribute("d",`M ${x1} ${a.cy} C ${c1} ${a.cy}, ${c2} ${b.cy}, ${x2} ${b.cy}`);path.setAttribute("class","edge");path.dataset.from=e.from;path.dataset.to=e.to;edgeLayer.append(path)}
 nodeLayer.innerHTML="";for(const n of list){const p=point(n),g=document.createElementNS("http://www.w3.org/2000/svg","g");g.classList.add("node");g.dataset.id=n.id;g.dataset.kind=n.kind;g.setAttribute("transform",`translate(${p.x} ${p.y})`);const r=document.createElementNS("http://www.w3.org/2000/svg","rect");r.setAttribute("width",p.w);r.setAttribute("height",p.h);g.append(r);const k=document.createElementNS("http://www.w3.org/2000/svg","text");k.setAttribute("x",12);k.setAttribute("y",17);k.setAttribute("class","kind");k.textContent=kindLabel[n.kind]||n.kind;g.append(k);const t=document.createElementNS("http://www.w3.org/2000/svg","text");t.setAttribute("x",12);t.setAttribute("y",38);t.setAttribute("class","title");t.textContent=n.label.length>27?n.label.slice(0,26)+"…":n.label;g.append(t);const d=document.createElementNS("http://www.w3.org/2000/svg","text");d.setAttribute("x",12);d.setAttribute("y",58);d.setAttribute("class","detail");d.textContent=n.detail.length>34?n.detail.slice(0,33)+"…":n.detail;g.append(d);if(n.kind!=="event"){const port=document.createElementNS("http://www.w3.org/2000/svg","circle");port.setAttribute("class","port");port.setAttribute("cx",0);port.setAttribute("cy",p.h/2);port.setAttribute("r",4);g.append(port)}if(n.kind!=="resource"){const port=document.createElementNS("http://www.w3.org/2000/svg","circle");port.setAttribute("class","port");port.setAttribute("cx",p.w);port.setAttribute("cy",p.h/2);port.setAttribute("r",4);g.append(port)}g.addEventListener("pointerdown",ev=>startNodeDrag(ev,n.id));g.addEventListener("click",ev=>{ev.stopPropagation();selectNode(n.id)});nodeLayer.append(g)};applySelection();setTransform()}
function connected(id){const up=new Set(),down=new Set(),todo=[id];while(todo.length){const x=todo.pop();edges.filter(e=>e.to===x).forEach(e=>{if(!up.has(e.from)){up.add(e.from);todo.push(e.from)}})}todo=[id];while(todo.length){const x=todo.pop();edges.filter(e=>e.from===x).forEach(e=>{if(!down.has(e.to)){down.add(e.to);todo.push(e.to)}})}return {up,down}}
function applySelection(){const linked=selectedNode?connected(selectedNode):null;nodeLayer.querySelectorAll(".node").forEach(g=>{const id=g.dataset.id;g.classList.toggle("active",id===selectedNode);g.classList.toggle("dim",!!linked&&!linked.up.has(id)&&!linked.down.has(id)&&id!==selectedNode)});edgeLayer.querySelectorAll(".edge").forEach(p=>{const active=!!linked&&(p.dataset.from===selectedNode||p.dataset.to===selectedNode||linked.up.has(p.dataset.from)&&linked.down.has(p.dataset.to));p.classList.toggle("active",active);p.classList.toggle("dim",!!linked&&!active)})}
function selectNode(id){selectedNode=id;const n=nodes.find(x=>x.id===id);if(!n)return;const route=routeFor(n.route_id);$('node-detail').innerHTML=`<p><b>${esc(n.label)}</b></p><p class="small">类型：${esc(kindLabel[n.kind]||n.kind)} · 路由：${esc(route?.title||n.route_id)}</p><p>${esc(n.detail)}</p><p class="small">运行观察：${esc((edges.find(e=>e.from===id)||{}).runtime_observation||n.runtime_observation||"not_collected")}</p><div class="evidence">${[...new Set(n.evidence||[])].map(x=>`<button type="button" data-evidence="${esc(x)}">${esc(evidence[x]?.path?.split("/").pop()||x)}:${evidence[x]?.line||"?"}</button>`).join("")}</div>`;$('node-detail').querySelectorAll("[data-evidence]").forEach(b=>b.onclick=()=>showEvidence(b.dataset.evidence));setPreviewForNode(n);applySelection()}
function showEvidence(id){const e=evidence[id];if(!e)return;const input=report.source.inputs.find(x=>x.path===e.path);$('evidence-detail').innerHTML=`<h2>源码证据</h2><p><code>${esc(e.path)}:${e.line}${e.end_line===e.line?"":"–"+e.end_line}</code></p><p class="small">SHA-256 <code>${esc(e.source_sha256)}</code><br>${input?.current_o2f2_matches===true?"O2f2 文件一致（忽略 CRLF）":input?.current_o2f2_matches===false?"O2f2 文件有差异；这里仍只展示固定官方证据":"未取得 O2f2 对照文件"}</p><pre>${esc(e.excerpt)}</pre>`}
function outputForNode(n){const m=n.id.match(/:output:(\d+)$/);if(m){const r=routeFor(n.route_id);return r?.outputs?.[Number(m[1])]||null}if(n.kind!=="resource")return routeFor(n.route_id)?.outputs?.[0]||null;return routeFor(n.route_id)?.outputs?.find(o=>n.label.includes(o.src))||routeFor(n.route_id)?.outputs?.[0]||null}
function setPreviewForNode(n){activeOutput=outputForNode(n);frame=0;stop();if(activeOutput)draw(0);else{ctx.clearRect(0,0,360,360);$('preview-caption').textContent="该节点没有固定 EAF 输出；可能是外部决定、动作或 DOA 角度。";$('frame-range').max="0";$('frame-label').textContent="";$('frame-time').textContent=""}}
function getAsset(){if(!activeOutput)return null;const ex=preview.expressions[activeOutput.semantic],a=ex&&preview.assets[ex.asset_key];return a||null}
function image(a){if(!images.has(a.asset_key))images.set(a.asset_key,new Promise((res,rej)=>{const im=new Image();im.onload=()=>res(im);im.onerror=()=>rej(Error("预览图集加载失败"));im.src=a.atlas}));return images.get(a.asset_key)}
async function draw(v){const token=++drawToken,a=getAsset();if(!a)return;frame=Math.max(0,Math.min(a.frame_count-1,v));try{const im=await image(a);if(token!==drawToken)return;scratch.width=a.frame_width;scratch.height=a.frame_height;scratchCtx.drawImage(im,(frame%a.columns)*a.frame_width,Math.floor(frame/a.columns)*a.frame_height,a.frame_width,a.frame_height,0,0,a.frame_width,a.frame_height);ctx.clearRect(0,0,360,360);ctx.drawImage(scratch,0,0,360,360);$('frame-range').max=String(a.frame_count-1);$('frame-range').value=String(frame);$('frame-label').textContent=`第 ${frame+1} / ${a.frame_count} 帧`;$('frame-time').textContent=`${Math.round(frame*1000/activeOutput.fps)} ms`;$('preview-caption').textContent=`${activeOutput.semantic} → ${activeOutput.src} · ${activeOutput.layout} · ${a.frame_width}×${a.frame_height}`}catch(err){$('preview-caption').textContent=err.message}}
function stop(){playing=false;clearTimeout(timer);timer=null;$('play-toggle').textContent="播放"}
async function play(){const a=getAsset();if(!a||playing)return;playing=true;$('play-toggle').textContent="暂停";const tick=async()=>{if(!playing)return;await draw(frame);if(!playing)return;if(frame>=a.frame_count-1){if(activeOutput.loop)frame=0;else return stop()}else frame++;timer=setTimeout(tick,Math.max(1,1000/activeOutput.fps))};tick()}
function startNodeDrag(ev,id){ev.preventDefault();const p=positions.get(id);dragging={id,startX:ev.clientX,startY:ev.clientY,x:p.x,y:p.y};ev.currentTarget.setPointerCapture(ev.pointerId)}
nodeLayer.addEventListener("pointermove",ev=>{if(!dragging)return;const p=positions.get(dragging.id);p.x=dragging.x+(ev.clientX-dragging.startX)/scale;p.y=dragging.y+(ev.clientY-dragging.startY)/scale;render()});nodeLayer.addEventListener("pointerup",()=>{dragging=null});
let panDrag=null;shell.addEventListener("pointerdown",ev=>{if(ev.target.closest(".node"))return;panDrag={x:ev.clientX,y:ev.clientY,px:pan.x,py:pan.y};shell.setPointerCapture(ev.pointerId)});shell.addEventListener("pointermove",ev=>{if(!panDrag)return;pan.x=panDrag.px+ev.clientX-panDrag.x;pan.y=panDrag.py+ev.clientY-panDrag.y;setTransform()});shell.addEventListener("pointerup",()=>panDrag=null);shell.addEventListener("wheel",ev=>{ev.preventDefault();const before=scale, factor=ev.deltaY<0?1.1:.9;scale=Math.min(2.4,Math.max(.45,scale*factor));const rect=shell.getBoundingClientRect(),mx=ev.clientX-rect.left,my=ev.clientY-rect.top;pan.x=mx-(mx-pan.x)*(scale/before);pan.y=my-(my-pan.y)*(scale/before);setTransform()},{passive:false});shell.addEventListener("click",()=>{selectedNode=null;applySelection()});
function fillRoutes(){const opts=[['__all__','全部路由']].concat(routes.map(r=>[r.id,r.title]));$('route').innerHTML=opts.map(x=>`<option value="${esc(x[0])}">${esc(x[1])}</option>`).join("");$('route-list').innerHTML=routes.map(r=>`<button class="route" type="button" aria-pressed="${selectedRoute===r.id}" data-route="${esc(r.id)}"><b>${esc(r.title)}</b><small>${esc(r.group)} · ${esc(r.resolution||"")}</small><div class="tags">${esc((r.outputs||[]).map(o=>o.src).concat(r.action_ids||[]).join(" · "))}</div></button>`).join("");$('route-list').querySelectorAll("[data-route]").forEach(b=>b.onclick=()=>{selectedRoute=b.dataset.route;$('route').value=selectedRoute;selectedNode=null;fillRoutes();render()})}
$('route').onchange=()=>{selectedRoute=$('route').value;selectedNode=null;fillRoutes();render()};$('search').oninput=()=>{selectedNode=null;render()};$('reset-view').onclick=()=>{scale=1;pan={x:0,y:0};render()};$('all-routes').onclick=()=>{$('route').value="__all__";selectedRoute="__all__";selectedNode=null;fillRoutes();render()};$('play-toggle').onclick=()=>playing?stop():play();$('next-frame').onclick=()=>{stop();draw(frame+1)};$('reset-frame').onclick=()=>{stop();draw(0)};$('frame-range').oninput=()=>{stop();draw(Number($('frame-range').value))};
const s=report.summary;$('summary').innerHTML=[[s.routes,"路由"],[nodes.length,"图节点"],[edges.length,"图连线"],[s.unique_eaf_files,"唯一 EAF"],[s.shared_asset_count,"共享资源"]].map(x=>`<span><strong>${x[0]}</strong>${esc(x[1])}</span>`).join("");$('footer').textContent=`官方提交 ${report.source.official_commit} · 静态源码证据 ${report.routes.length} 条路由 · 不连接设备、不修改固件、不写 NVS。EAF 预览来自已验收 E1.2 主机图集。`;
fillRoutes();render();
window.DESKBOT_NODE_WORKBENCH_TEST={state:()=>({selectedRoute,selectedNode,scale,pan,visibleNodes:visibleNodes().length}),selectNode,render};
})();'''


def build(report_path: Path = REPORT_DEFAULT, preview_path: Path = PREVIEW_DEFAULT,
          output: Path = OUTPUT_DEFAULT, release_lock_path: Path | None = None) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validate_report(report)
    preview = load_preview(report, preview_path)
    graph = enrich_graph(report)
    layer_model = None
    release_audit = None
    if release_lock_path is not None:
        layer_model, candidate, candidate_asset, release_audit = load_layer_model(
            report_path, report, graph, preview_path, release_lock_path)
        preview["expressions"][candidate["id"]] = candidate
        preview["assets"][candidate_asset["asset_key"]] = candidate_asset
    data = script_json({"report": report, "preview": preview, "graph": graph, "layers": layer_model})
    template = (ROOT / "scripts/templates/expression-node-workbench.html").read_text(encoding="utf-8")
    viewer = (ROOT / "scripts/templates/expression-node-workbench.js").read_text(encoding="utf-8")
    core = (ROOT / "scripts/templates/expression-workbench-core.js").read_text(encoding="utf-8")
    page = template.replace("__DATA_JSON__", data).replace("__WORKBENCH_JS__", core + "\n" + viewer)
    if "__DATA_JSON__" in page or "__WORKBENCH_JS__" in page:
        raise ValueError("unexpanded workbench token")
    output = output.resolve()
    output.relative_to(ROOT.resolve())
    output.mkdir(parents=True, exist_ok=True)
    raw = page.encode("utf-8")
    (output / "index.html").write_bytes(raw)
    (output / "node-graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    result = {"schema": "deskbot.expression_node_workbench_layers/v1" if layer_model else "deskbot.expression_node_workbench/v1",
              "result": "offline_layered_node_workbench_created" if layer_model else "offline_node_workbench_created",
              "hardware_accessed": False, "firmware_changed": False, "device_playback_verified": False,
              "source_commit": COMMIT, "route_count": len(report["routes"]),
              "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]),
              "files": {"index.html": {"bytes": len(raw), "sha256": sha256(raw)}},
              "interaction_contract": ["route selection", "search", "node drag", "pan", "zoom", "upstream/downstream highlight", "source evidence", "EAF preview"],
              "limitations": ["static source evidence; no runtime observation", "no firmware or device writes", "preview is host atlas, not proof of device timing or circular LCD masking"]}
    if layer_model:
        mapping_bytes = (json.dumps(layer_model, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (output / "mapping-layers.json").write_bytes(mapping_bytes)
        result["layer_count"] = len(layer_model["layers"])
        result["mapping_semantic_count"] = len(layer_model["layers"]["official"]["semantic_map"])
        result["files"]["mapping-layers.json"] = {"bytes": len(mapping_bytes), "sha256": sha256(mapping_bytes)}
        result["release_lock"] = layer_model["evidence"]
        result["interaction_contract"].extend(["official/candidate/exact-fallback layer selection", "visual-only animation preview"])
        result["limitations"].extend(["candidate layer is read-only and offline-only", "no package capacity or device playback claim"])
        (output / "release-audit.json").write_text(
            json.dumps(release_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        result["files"]["release-audit.json"] = {
            "bytes": (output / "release-audit.json").stat().st_size,
            "sha256": sha256((output / "release-audit.json").read_bytes()),
        }
        result["schema_path"] = "protocol/expression-mapping-layers-v1.schema.json"
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    edition = "E1.4d three-layer offline node workbench" if layer_model else "E1.4b offline node workbench"
    (output / "README.txt").write_text(
        f"DeskBot {edition}. Open index.html locally. Static source evidence only; no device, firmware or NVS access.\n",
        encoding="utf-8", newline="\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=REPORT_DEFAULT)
    parser.add_argument("--preview", type=Path, default=PREVIEW_DEFAULT)
    parser.add_argument("--release-lock", type=Path,
                        help="validate and display official/candidate/fallback offline mapping layers")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        output = args.output or (LAYERED_OUTPUT_DEFAULT if args.release_lock else OUTPUT_DEFAULT)
        result = build(args.report, args.preview, output, args.release_lock)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "rejected", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
