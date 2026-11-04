#!/usr/bin/env python3
"""Build the E1.32 read-only role/expression contract audit.

The audit consumes the generated E1.31 draft workbench and independently
recomputes the accepted E1.18 baseline from E1.19's anchored payload.  It
checks identity isolation, expression/action branch separation, allow-lists,
layout compatibility, hash/version pins and shared-resource accounting.  The
HTML output is a self-contained review page; it never reads a device, opens a
serial port, contacts a network, writes firmware/NVS or creates a flashable
package.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
E131 = ROOT / "artifacts/role-expression-workbench-e1-31-20261029/index.html"
E131_REPORT = ROOT / "artifacts/role-expression-workbench-e1-31-20261029/report.json"
E119 = ROOT / "artifacts/expression-node-preview-e1-19-20261024/index.html"
OUT = ROOT / "artifacts/role-expression-audit-e1-32-20261029"

EDITION = "E1.32"
SCHEMA = "deskbot.role_expression_audit/v1"
E118_LOCK = {
    "package_sha256": "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1",
    "index_sha256": "ba853dbe8388cb9dbfd992431d9f972302035de5fe5f574f984542b5cf76fe69",
    "mapping_sha256": "d66fd16c07f7094e311daff1c3646b5b0b0587567668c0cebe0fccb3c11492dd",
    "catalog_sha256": "cd66be0e97e7fff326130d72074568bee7868b586c3a762719970535383ac184",
}


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha(value: Any) -> str:
    return sha256_bytes(canonical(value).encode("utf-8"))


def extract_payload(path: Path, marker: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="' + re.escape(marker) + r'" type="application/json">(.*?)</script>', text, re.S)
    if not match:
        raise ValueError(f"{marker} payload is missing")
    value = json.loads(match.group(1))
    if not isinstance(value, dict):
        raise ValueError(f"{marker} payload must be an object")
    return value


def project_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("input/output must stay inside the project") from exc
    return resolved


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    e131 = extract_payload(E131, "e131-data")
    e119 = extract_payload(E119, "e119-data")
    e131_report = json.loads(E131_REPORT.read_text(encoding="utf-8"))
    if e131.get("edition") != "E1.31" or e131.get("hardware_accessed") is not False:
        raise ValueError("E1.31 is not the expected offline workbench")
    if e119.get("edition") != "E1.19" or e119.get("hardware_accessed") is not False:
        raise ValueError("E1.19 is not the expected offline baseline")
    if e119.get("firmware_changed") is not False:
        raise ValueError("E1.19 claims a firmware change")
    if e131_report.get("edition") != "E1.31" or e131_report.get("deployment_eligible") is not False:
        raise ValueError("E1.31 report boundary drifted")
    return e131, e119, e131_report


def recompute_baseline(e119: dict[str, Any]) -> dict[str, Any]:
    deployed = e119.get("snapshots", {}).get("deployed")
    assets = e119.get("assets")
    if not isinstance(deployed, dict) or len(deployed) != 29:
        raise ValueError("E1.19 deployed semantic inventory must contain 29 entries")
    if not isinstance(assets, dict) or len(assets) != 18:
        raise ValueError("E1.19 official asset inventory must contain 18 entries")
    baseline = e119.get("baselines", {}).get("deployed", {})
    package_sha = baseline.get("package_sha256")
    index_sha = baseline.get("index_sha256")
    if not isinstance(package_sha, str) or not isinstance(index_sha, str):
        raise ValueError("E1.18 package/index hashes are missing from E1.19")
    catalog = {
        name: {"sha256": item.get("deployed_sha256"), "bytes": item.get("deployed_bytes")}
        for name, item in sorted(assets.items())
    }
    if any(not isinstance(v["sha256"], str) or not isinstance(v["bytes"], int) for v in catalog.values()):
        raise ValueError("E1.19 catalog contains incomplete asset hashes")
    return {
        "release_id": "E1.18",
        "package_sha256": package_sha,
        "index_sha256": index_sha,
        "mapping_sha256": canonical_sha(deployed),
        "catalog_sha256": canonical_sha(catalog),
    }


def audit_payloads(e131: dict[str, Any], e119: dict[str, Any], e131_report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Audit decoded inputs; kept separate so mutation tests exercise the gate."""
    sys.path.insert(0, str(ROOT))
    from protocol.deskbot_role_profile import SAFE_ACTIONS, validate_role_profile
    from protocol.role_expression_draft import validate_draft

    if e131.get("edition") != "E1.31" or e131.get("hardware_accessed") is not False:
        raise ValueError("E1.31 is not the expected offline workbench")
    if e119.get("edition") != "E1.19" or e119.get("hardware_accessed") is not False or e119.get("firmware_changed") is not False:
        raise ValueError("E1.19 is not the expected offline baseline")
    if e131_report.get("edition") != "E1.31" or e131_report.get("deployment_eligible") is not False:
        raise ValueError("E1.31 report boundary drifted")
    expected_baseline = recompute_baseline(e119)
    if expected_baseline != {"release_id": "E1.18", **E118_LOCK}:
        raise ValueError("E1.18 immutable release lock does not match the E1.19 payload")
    declared_baseline = e131.get("baseline")
    if declared_baseline != expected_baseline:
        raise ValueError("E1.31 declared baseline does not match the independently recomputed E1.18 baseline")
    if e131_report.get("baseline") != expected_baseline:
        raise ValueError("E1.31 report baseline does not match E1.18")

    semantics = e131.get("semantics", {})
    assets = e131.get("assets", {})
    initial = e131.get("initial", {})
    roles = initial.get("roles", [])
    deployed = e119.get("snapshots", {}).get("deployed", {})
    e119_assets = e119.get("assets", {})
    if len(semantics) != 29 or len(assets) != 18 or len(roles) != 2:
        raise ValueError("E1.31 inventory count drifted")
    if set(semantics) != set(deployed):
        raise ValueError("E1.31 semantic IDs differ from the E1.18 deployed snapshot")
    semantic_drift = []
    for semantic, row in deployed.items():
        projected = semantics[semantic]
        expected = {
            "resource_id": row["resource_id"], "fps": row["fps"], "loop": row["loop"],
            "action_id": row.get("action_id"), "layout_id": row["layout_id"],
        }
        mismatch = {key: {"e119": value, "e131": projected.get(key)}
                    for key, value in expected.items() if projected.get(key) != value}
        if mismatch:
            semantic_drift.append({"semantic": semantic, "mismatch": mismatch})
    if semantic_drift:
        raise ValueError("E1.18 official resource/action/layout/timing facts drifted: " + json.dumps(semantic_drift, ensure_ascii=False))
    if set(assets) != set(e119_assets):
        raise ValueError("E1.31 official EAF inventory differs from E1.19")
    asset_drift = []
    for resource, source in e119_assets.items():
        projected = assets[resource]
        expected_hash, expected_bytes = source.get("deployed_sha256"), source.get("deployed_bytes")
        if (projected.get("deployed_sha256") != expected_hash or projected.get("bytes") != expected_bytes
                or projected.get("object_kind") != source.get("object_kind")):
            asset_drift.append(resource)
    if asset_drift:
        raise ValueError("E1.18 official asset hash/size/layout drifted: " + ", ".join(sorted(asset_drift)))

    context = {
        "baseline": expected_baseline,
        "asset_version": e131["asset_version"],
        "binding_version": e131["binding_version"],
        "semantics": semantics,
    }
    validate_draft(initial, context)
    identity_rows = []
    for row in roles:
        profile = row["role_profile"]
        validate_role_profile(profile)
        identities = {
            "shell_id": profile["shell_id"],
            "magnet_profile_id": profile["magnet_profile_id"],
            "role_id": profile["role_id"],
            "all_distinct": len({profile["shell_id"], profile["magnet_profile_id"], profile["role_id"]}) == 3,
        }
        if not identities["all_distinct"]:
            raise ValueError(f"identity namespace collision in {profile['role_id']}")
        identity_rows.append(identities | {"display_name": profile["display_name"]})

    baseline_refs: dict[str, list[str]] = {}
    for semantic, info in semantics.items():
        baseline_refs.setdefault(info["resource_id"], []).append(semantic)
    role_refs: dict[str, list[str]] = {}
    for row in roles:
        for semantic, resource in row["semantic_resources"].items():
            if resource:
                role_refs.setdefault(resource, []).append(f"{row['role_profile']['role_id']}:{semantic}")
    shared_resources = []
    for resource in sorted(assets):
        shared_resources.append({
            "resource": resource, "bytes": assets[resource].get("bytes"),
            "baseline_semantic_count": len(baseline_refs.get(resource, [])),
            "baseline_semantics": sorted(baseline_refs.get(resource, [])),
            "draft_reference_count": len(role_refs.get(resource, [])),
            "draft_references": sorted(role_refs.get(resource, [])),
            "shared": len(baseline_refs.get(resource, [])) > 1 or len(role_refs.get(resource, [])) > 1,
        })

    branch_rows = []
    for index, row in enumerate(roles):
        profile = row["role_profile"]
        intent_semantics = sorted(set(profile["expression_map"].values()))
        official_branch = [{
            "semantic": semantic,
            "resource": semantics[semantic]["resource_id"],
            "action": semantics[semantic].get("action_id"),
            "layout": semantics[semantic]["layout_id"],
        } for semantic in intent_semantics]
        expression_branch = [{
            "intent": intent, "semantic": semantic,
            "resource": row["semantic_resources"].get(semantic),
            "baseline_resource": semantics[semantic]["resource_id"],
            "layout": semantics[semantic]["layout_id"],
        } for intent, semantic in sorted(profile["expression_map"].items())]
        action_branch = [{"intent": intent, "action": action} for intent, action in sorted(profile["action_map"].items())]
        branch_rows.append({
            "role_id": profile["role_id"], "display_name": profile["display_name"],
            "identity": identity_rows[index], "official_branch": official_branch,
            "expression_branch": expression_branch, "action_branch": action_branch,
            "binding_state": profile["binding_state"],
        })

    positive_checks = {
        "baseline_hashes_match": declared_baseline == expected_baseline,
        "official_E1.18_mapping_unchanged": all(
            semantics[semantic][key] == row.get(key)
            for semantic, row in deployed.items()
            for key in ("resource_id", "fps", "loop", "action_id", "layout_id")
        ),
        "official_EAF_hashes_sizes_unchanged": all(
            assets[name].get("deployed_sha256") == row.get("deployed_sha256")
            and assets[name].get("bytes") == row.get("deployed_bytes")
            and assets[name].get("object_kind") == row.get("object_kind")
            for name, row in e119_assets.items()
        ),
        "identity_namespaces_isolated": all(item["all_distinct"] for item in identity_rows),
        "expression_and_action_branches_are_separate_fields": all(
            isinstance(row["role_profile"].get("expression_map"), dict)
            and isinstance(row["role_profile"].get("action_map"), dict)
            and row["role_profile"]["expression_map"] is not row["role_profile"]["action_map"]
            for row in roles
        ),
        "action_allowlist": all(action in SAFE_ACTIONS for row in roles for action in row["role_profile"]["action_map"].values()),
        "known_semantics_and_resources": all(
            semantic in semantics and (resource is None or resource in semantics[semantic]["allowed_resources"])
            for row in roles for semantic, resource in row["semantic_resources"].items()
        ),
        "layout_compatibility": all(
            resource is None or resource in semantics[semantic]["allowed_resources"]
            for row in roles for semantic, resource in row["semantic_resources"].items()
        ),
        "version_and_hash_pins": e131["asset_version"] == "deskbot.expression_assets/e1-18" and e131["binding_version"] == "deskbot.expression_bindings/v1",
        "shared_resource_impact_tracked": len(shared_resources) == 18 and any(item["shared"] for item in shared_resources),
        "draft_boundary": e131.get("deployment_eligible") is False and e131.get("firmware_changed") is False and all(
            row["role_profile"].get("binding_state") == "draft" for row in roles
        ),
    }
    if not all(positive_checks.values()):
        raise ValueError("one or more positive contract checks failed")

    negative_cases = [
        reject_case("magnet_equals_shell", lambda d: d["roles"][0]["role_profile"].update(magnet_profile_id=d["roles"][0]["role_profile"]["shell_id"]), initial, context),
        reject_case("magnet_equals_role", lambda d: d["roles"][0]["role_profile"].update(magnet_profile_id=d["roles"][0]["role_profile"]["role_id"]), initial, context),
        reject_case("unknown_expression_semantic", lambda d: d["roles"][0]["role_profile"]["expression_map"].update(greeting="unknown_semantic"), initial, context),
        reject_case("unknown_resource", lambda d: d["roles"][0]["semantic_resources"].update(happy="unknown.eaf"), initial, context),
        reject_case("wrong_layout_resource", lambda d: d["roles"][0]["semantic_resources"].update(listen="Happy.eaf"), initial, context),
        reject_case("unsafe_action", lambda d: d["roles"][0]["role_profile"]["action_map"].update(curious="calibrate"), initial, context),
        reject_case("binding_version_mismatch", lambda d: d["roles"][0]["role_profile"].update(binding_version="deskbot.expression_bindings/v2"), initial, context),
        reject_case("asset_version_mismatch", lambda d: d["roles"][0]["role_profile"].update(asset_version="deskbot.expression_assets/e1-13"), initial, context),
        reject_case("release_version_mismatch", lambda d: d.update(edition="E1.30"), initial, context),
        reject_case("baseline_hash_mismatch", lambda d: d["baseline"].update(package_sha256="0" * 64), initial, context),
    ]
    if not all(case["passed"] for case in negative_cases):
        raise ValueError("a negative contract case was accepted")

    report = {
        "schema": SCHEMA, "edition": EDITION, "date": "2026-10-29",
        "result": "offline_role_expression_audit_passed", "audit_pass": True,
        "hardware_accessed": False, "serial_accessed": False, "firmware_changed": False,
        "nvs_changed": False, "deployment_eligible": False, "flash_authorized": False,
        "sources": {
            "e131_page": {"path": E131.relative_to(ROOT).as_posix(), "sha256": sha256_file(E131)},
            "e131_report": {"path": E131_REPORT.relative_to(ROOT).as_posix(), "sha256": sha256_file(E131_REPORT)},
            "e119_page": {"path": E119.relative_to(ROOT).as_posix(), "sha256": sha256_file(E119)},
            "role_profile_validator": {"path": "protocol/deskbot_role_profile.py", "sha256": sha256_file(ROOT / "protocol/deskbot_role_profile.py")},
            "draft_validator": {"path": "protocol/role_expression_draft.py", "sha256": sha256_file(ROOT / "protocol/role_expression_draft.py")},
        },
        "baseline": {"release_id": "E1.18", "recomputed": expected_baseline, "declared_by_e131": declared_baseline, "match": True},
        "inventory": {
            "semantic_count": len(semantics), "official_resource_count": len(assets), "role_count": len(roles),
            "action_allowlist": sorted(SAFE_ACTIONS), "shared_resources": shared_resources,
        },
        "checks": positive_checks, "identity_isolation": identity_rows, "branches": branch_rows,
        "negative_cases": negative_cases,
        "limits": [
            "角色表达映射与官方 E1.18 语义/动作映射是两套独立只读事实；审计不推断运行时仲裁、LCD 呈现或动作已执行。",
            "draft binding_state 不能表示外壳已登记、角色已激活或客户端已加载。",
            "E1.18 资源和哈希只作锚点；本轮不生成 EAF、资源分区、固件或 NVS。",
            "共享资源只报告影响范围，不因角色草稿引用变化删除或合并 EAF。",
            "审计没有访问设备、串口、MCP、DeepSeek/TTS 或网络。",
        ],
        "next_gate": "offline_asset_import_and_player_layout_audit",
    }
    page_data = {
        "edition": EDITION, "baseline": report["baseline"], "inventory": report["inventory"],
        "checks": report["checks"], "identity_isolation": report["identity_isolation"],
        "branches": report["branches"], "negative_cases": report["negative_cases"],
        "limits": report["limits"], "source_paths": {key: value["path"] for key, value in report["sources"].items()},
    }
    return report, page_data


def reject_case(case_id: str, mutate: Callable[[dict[str, Any]], None], draft: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(draft)
    try:
        mutate(candidate)
        from protocol.role_expression_draft import validate_draft
        validate_draft(candidate, context)
    except (ValueError, TypeError, KeyError, AssertionError) as exc:
        return {"id": case_id, "expected": "reject", "passed": True, "reason": str(exc)}
    return {"id": case_id, "expected": "reject", "passed": False, "reason": "mutated draft was accepted"}


# One public entry point for tests and callers.
def build_report() -> tuple[dict[str, Any], dict[str, Any]]:
    return audit_payloads(*load_inputs())


def page_html(page_data: dict[str, Any]) -> str:
    data = json.dumps(page_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    script = r"""
(function(){
  const D=JSON.parse(document.getElementById('e132-data').textContent);
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const yes=v=>v?'通过':'失败';
  $('baseline').innerHTML=`<div class="hash"><span>release</span><b>${esc(D.baseline.recomputed.release_id)}</b></div><div class="hash"><span>package SHA-256</span><code>${esc(D.baseline.recomputed.package_sha256)}</code></div><div class="hash"><span>index SHA-256</span><code>${esc(D.baseline.recomputed.index_sha256)}</code></div><div class="hash"><span>mapping SHA-256</span><code>${esc(D.baseline.recomputed.mapping_sha256)}</code></div><div class="hash"><span>catalog SHA-256</span><code>${esc(D.baseline.recomputed.catalog_sha256)}</code></div><p class="ok">独立重算结果与 E1.31 声明一致。</p>`;
  $('checks').innerHTML=Object.entries(D.checks).map(([k,v])=>`<div class="check ${v?'pass':'fail'}"><span>${v?'✓':'×'}</span><div><b>${esc(k)}</b><small>${esc(yes(v))}</small></div></div>`).join('');
  $('identities').innerHTML=D.identity_isolation.map(r=>`<tr><td>${esc(r.display_name)}</td><td class="mono">${esc(r.shell_id)}</td><td class="mono">${esc(r.magnet_profile_id)}</td><td class="mono">${esc(r.role_id)}</td><td class="ok">${r.all_distinct?'三者独立':'冲突'}</td></tr>`).join('');
  function rows(items, kind){return items.length?items.map(x=>kind==='expr'?`<div class="edge"><span class="intent">${esc(x.intent)}</span><span>→</span><span class="semantic">${esc(x.semantic)}</span><span>→</span><code>${esc(x.resource||'未接线')}</code><small>基线 ${esc(x.baseline_resource)} · ${esc(x.layout)}</small></div>`:`<div class="edge action"><span class="intent">${esc(x.intent)}</span><span>→</span><code>${esc(x.action)}</code><small>动作白名单；仅表达意图</small></div>`).join(''):'<div class="empty">无分支</div>';}
  $('branches').innerHTML=D.branches.map(r=>`<section class="role"><div class="role-head"><h3>${esc(r.display_name)} <code>${esc(r.role_id)}</code></h3><span class="badge">draft · 未激活</span></div><div class="identity-line">外壳 <code>${esc(r.identity.shell_id)}</code> · 磁场 <code>${esc(r.identity.magnet_profile_id)}</code> · 角色 <code>${esc(r.identity.role_id)}</code></div><div class="branch-grid"><div class="branch official"><h4>官方 E1.18 语义/动作事实</h4><p>语义 → EAF + 官方伴随动作；和角色草稿分离。</p>${r.official_branch.map(x=>`<div class="edge official-edge"><span class="semantic">${esc(x.semantic)}</span><span>→</span><code>${esc(x.resource)}</code><span>·</span><small>官方动作 ${esc(x.action||'无')} · ${esc(x.layout)}</small></div>`).join('')}</div><div class="branch role-expr"><h4>角色表情分支</h4><p>角色意图 → 语义 → 当前草稿资源。可独立改线。</p>${rows(r.expression_branch,'expr')}</div><div class="branch role-action"><h4>角色动作意图分支（独立）</h4><p>角色意图 → 普通动作白名单；不表示调用或完成。</p>${rows(r.action_branch,'action')}</div></div></section>`).join('');
  $('shared').innerHTML=D.inventory.shared_resources.filter(x=>x.shared).map(x=>`<tr><td><code>${esc(x.resource)}</code></td><td>${x.baseline_semantic_count}<br><small>${esc(x.baseline_semantics.join(', '))}</small></td><td>${x.draft_reference_count}<br><small>${esc(x.draft_references.join(', '))}</small></td><td>${x.bytes} B</td></tr>`).join('');
  $('negative').innerHTML=D.negative_cases.map(x=>`<li><span class="ok">${x.passed?'✓':'×'}</span><code>${esc(x.id)}</code><small>${esc(x.reason)}</small></li>`).join('');
})();
""";
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'><meta name="viewport" content="width=device-width,initial-scale=1"><title>E1.32 · 角色与表达契约审计</title>
<style>:root{{--bg:#0d1215;--panel:#171f23;--panel2:#202b30;--line:#35444b;--text:#eef5f3;--muted:#9aabb0;--green:#79d2a2;--amber:#efbd70;--red:#ef8985;--blue:#8cc8ff}}*{{box-sizing:border-box}}body{{margin:0;color:var(--text);background:radial-gradient(circle at 15% -10%,#29413f 0,#0d1215 44%);font:14px/1.45 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}header{{padding:22px 26px;border-bottom:1px solid var(--line);background:#11191ce8;position:sticky;top:0;z-index:2;backdrop-filter:blur(10px)}}h1{{margin:0 0 6px;font-size:24px}}h2{{font-size:16px;margin:0 0 9px}}h3{{margin:0;font-size:15px}}h4{{margin:0 0 6px;font-size:13px}}p{{margin:5px 0;color:var(--muted)}}code,.mono{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;word-break:break-word}}.sub{{max-width:900px;color:var(--muted)}}.badges{{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}}.badge{{display:inline-flex;padding:3px 9px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:12px}}.badge.safe{{border-color:#397958;color:var(--green)}}.badge.warn{{border-color:#886937;color:var(--amber)}}main{{max-width:1500px;margin:auto;padding:18px 22px 42px;display:grid;gap:16px}}.grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:16px}}.panel{{background:#171f23dd;border:1px solid var(--line);border-radius:11px;overflow:hidden;box-shadow:0 14px 42px #0006}}.head{{padding:12px 14px;border-bottom:1px solid var(--line);background:#1a2428}}.body{{padding:14px}}.hash{{display:grid;grid-template-columns:155px 1fr;gap:7px;padding:5px 0;border-bottom:1px solid #2a373c}}.hash span{{color:var(--muted)}}.hash code{{font-size:12px}}.ok{{color:var(--green)}}.warn{{color:var(--amber)}}.checks{{display:grid;grid-template-columns:repeat(2,minmax(190px,1fr));gap:7px}}.check{{display:flex;gap:8px;align-items:flex-start;padding:9px;border:1px solid var(--line);border-radius:8px;background:#12191c}}.check.pass{{border-color:#32644e}}.check.fail{{border-color:#7a3e3d;color:var(--red)}}.check>span{{font-size:18px;line-height:1}}.check small,.edge small,.role-head .badge{{display:block;color:var(--muted);font-size:11px;margin-top:2px}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:7px 6px;text-align:left;border-bottom:1px solid #2d3a3f;vertical-align:top}}th{{color:var(--muted);font-weight:500}}.table-wrap{{overflow:auto}}.role{{border:1px solid var(--line);border-radius:10px;background:#12191c;margin-bottom:12px;overflow:hidden}}.role:last-child{{margin-bottom:0}}.role-head{{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:11px 12px;background:#1b262a;border-bottom:1px solid var(--line)}}.role-head code{{color:var(--blue);font-size:12px;margin-left:5px}}.identity-line{{padding:8px 12px;color:var(--muted);font-size:12px;border-bottom:1px solid #26343a}}.branch-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;padding:10px}}.branch{{border:1px solid #2e3b41;border-radius:8px;padding:10px;background:#151e21;min-width:0}}.branch.official{{border-style:dashed}}.branch.role-expr{{border-color:#587b63}}.branch.role-action{{border-color:#80663b}}.branch h4{{color:#dbe9e5}}.edge{{display:grid;grid-template-columns:auto auto auto auto 1fr;gap:5px;align-items:center;padding:6px 0;border-top:1px solid #2b383d;font-size:12px}}.edge small{{grid-column:1/-1;padding-left:2px}}.edge .intent{{color:var(--muted)}}.edge .semantic{{color:var(--blue)}}.edge.action{{grid-template-columns:1fr auto 1fr}}.edge.action small{{grid-column:1/-1}}.empty{{padding:10px;border:1px dashed var(--line);color:var(--muted)}}ul{{margin:0;padding:0;list-style:none;display:grid;gap:6px}}li{{display:grid;grid-template-columns:22px 220px 1fr;gap:6px;padding:6px 0;border-bottom:1px solid #2b383d}}li small{{color:var(--muted)}}.notice{{padding:11px;border:1px solid #695636;background:#261f15;color:#e6c68d;border-radius:8px;font-size:12px}}@media(max-width:1000px){{.grid,.branch-grid{{grid-template-columns:1fr}}.checks{{grid-template-columns:1fr}}}}@media(max-width:620px){{header{{padding:17px}}main{{padding:12px}}.hash{{grid-template-columns:1fr}}li{{grid-template-columns:22px 1fr}}li small{{grid-column:2}}}}</style></head>
<body><header><h1>E1.32 · 角色与表达契约审计</h1><div class="sub">独立核对 E1.31 角色草稿与 E1.18 官方表达基线：三种身份隔离、表情与动作分支、白名单、布局兼容、版本/哈希和共享资源影响。页面是只读离线审计，不代表设备激活、LCD 播放或动作执行。</div><div class="badges"><span class="badge safe">审计通过</span><span class="badge">E1.18 基线只读</span><span class="badge warn">E1.31 draft only</span><span class="badge">无设备 / 无网络 / 无刷写</span></div></header><main>
<div class="grid"><section class="panel"><div class="head"><h2>E1.18 基线独立重算</h2></div><div class="body" id="baseline"></div></section><section class="panel"><div class="head"><h2>正向检查</h2></div><div class="body checks" id="checks"></div></section></div>
<section class="panel"><div class="head"><h2>三种身份命名空间</h2><p>外壳身份、磁场档案身份和客户端角色身份必须始终独立。</p></div><div class="body table-wrap"><table><thead><tr><th>角色</th><th>shell_id</th><th>magnet_profile_id</th><th>role_id</th><th>结果</th></tr></thead><tbody id="identities"></tbody></table></div></section>
<section class="panel"><div class="head"><h2>两类表达分支</h2><p>每个角色同时展示官方语义事实、角色表情接线和独立的角色动作意图。动作意图不覆盖官方动作事实，也不等于执行。</p></div><div class="body" id="branches"></div></section>
<div class="grid"><section class="panel"><div class="head"><h2>共享 EAF 影响</h2><p>共享只做影响追踪，不据此删除或合并资源。</p></div><div class="body table-wrap"><table><thead><tr><th>资源</th><th>E1.18 语义</th><th>草稿引用</th><th>大小</th></tr></thead><tbody id="shared"></tbody></table></div></section><section class="panel"><div class="head"><h2>负向用例</h2><p>每个篡改都应被拒绝。</p></div><div class="body"><ul id="negative"></ul></div></section></div>
<section class="panel"><div class="body notice">本轮只完成契约审计。草稿不代表外壳已登记、角色已激活、资源已部署或动作已执行；下一闸门是新动画导入、官方播放器/圆屏布局和容量审计。</div></section>
</main><script id="e132-data" type="application/json">{data}</script><script>{script}</script></body></html>'''


def write_outputs(report: dict[str, Any], page_data: dict[str, Any], *, check: bool) -> None:
    products = {
        OUT / "index.html": page_html(page_data).encode("utf-8"),
        OUT / "report.json": (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        OUT / "README.txt": ("DeskBot E1.32 角色与表达契约审计 / Offline role-expression contract audit\n\n"
            "This is a self-contained, read-only audit of E1.31 drafts against the E1.18 baseline.\n"
            "It checks identity isolation, separate expression/action branches, allow-lists, layout and hash pins.\n"
            "It does not access hardware, serial ports, MCP, network, firmware, NVS, or create a flashable package.\n").encode("utf-8"),
    }
    for path, content in products.items():
        if check:
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError(f"stale generated artifact: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated outputs without rewriting")
    args = parser.parse_args(argv)
    try:
        report, page_data = build_report()
        write_outputs(report, page_data, check=args.check)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print(f"E1.32 audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"result": "checked" if args.check else report["result"], "edition": EDITION,
                      "roles": report["inventory"]["role_count"], "semantics": report["inventory"]["semantic_count"],
                      "resources": report["inventory"]["official_resource_count"], "negative_cases": len(report["negative_cases"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
