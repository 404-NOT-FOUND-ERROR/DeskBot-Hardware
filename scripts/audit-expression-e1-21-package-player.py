#!/usr/bin/env python3
"""Audit the E1.20 mapping as a real package through the official C player.

This is an offline release gate.  It consumes the E1.20 mapping decision,
applies exactly one parsed ``index.json`` token change to the retained E1.18
package, rebuilds it with the anchored legacy packer, and runs the anchored
Espressif C player against the resulting ``Happy.eaf`` and ``shocked.eaf``.
The retained E1.18 package is copied as the exact rollback package.  No
serial port, firmware source, NVS, or device partition is accessed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT_DEFAULT = ROOT.parent.parent
PUBLIC = ROOT / "artifacts/expression-e1-21-package-player-20261026"
LOCAL_DEFAULT = ROOT / "tmp/expression-e1-21-package-player-20261026"
CURRENT_PACKAGE = ROOT / "tmp/expression-e1-16-repack-20261024/candidate-offline-do-not-flash.bin"
E113_EXACT = ROOT / "tmp/expression-e1-16-repack-20261024/rollback-e1-13-exact.bin"
E116_REPORT = ROOT / "artifacts/expression-e1-16-repack-20261024/report.json"
E120_REPORT = ROOT / "artifacts/expression-e1-20-mapping-audit-20261025/report.json"
E120_DRAFT = ROOT / "artifacts/expression-e1-20-mapping-audit-20261025/draft.json"
PARTITION_BYTES = 6_144_000
CURRENT_SHA = "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1"
CURRENT_BYTES = 5_538_351
E113_SHA = "5e342db35da13531836339a98c25d6e74ac6fdff446ad4e515fcf39f28b7e81f"
E113_BYTES = 5_538_349


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load local audit helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


E116 = load_module(ROOT / "scripts/audit-expression-e1-16-repack.py", "deskbot_e121_e116")
E117_PATH = ROOT / "scripts/audit-expression-e1-17-player.py"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"refusing to overwrite different evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def safe_output(path: Path) -> Path:
    resolved = path.resolve()
    tmp_root = (ROOT / "tmp").resolve()
    if resolved.parent != tmp_root or not resolved.name.startswith("expression-e1-21-package-player-"):
        raise ValueError("E1.21 output must be a direct child of project tmp with the approved prefix")
    if any(parent.is_symlink() for parent in (resolved, *resolved.parents) if parent.exists()):
        raise ValueError("E1.21 output path must not traverse symlinks")
    if resolved.exists():
        allowed = {"cache-global", "cache-local", "resources", "candidate-e1-20-do-not-flash.bin",
                   "rollback-e1-18-exact.bin", "audit.json", "compile.log", "gfx_player_harness.lib",
                   "player-audit-e1-11.dll", "player-audit-e1-11.pdb",
                   "case-happy.json", "case-happy-full.json", "case-shocked.json",
                   "case-shocked-full.json", "worker-happy.log", "worker-shocked.log"}
        entries = list(resolved.iterdir())
        unexpected = [entry.name for entry in entries if entry.name not in allowed]
        if unexpected:
            raise ValueError(f"E1.21 output contains unexpected files: {', '.join(sorted(unexpected))}")
        if any(entry.is_symlink() or (entry.is_dir() and entry.name not in {"cache-global", "cache-local", "resources"})
               for entry in entries):
            raise ValueError("E1.21 output contains an unsafe entry")
    return resolved


def json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def validate_inputs(current: bytes, e113: bytes) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if len(current) != CURRENT_BYTES or sha(current) != CURRENT_SHA:
        raise ValueError("retained E1.18 package hash/size mismatch")
    if len(e113) != E113_BYTES or sha(e113) != E113_SHA:
        raise ValueError("retained E1.13 exact rollback hash/size mismatch")
    e116 = json_object(E116_REPORT)
    if (e116.get("edition") != "E1.16" or e116.get("result") != "offline_repack_verified"
            or e116.get("candidate", {}).get("sha256") != CURRENT_SHA
            or e116.get("candidate", {}).get("bytes") != CURRENT_BYTES):
        raise ValueError("E1.16 evidence does not anchor the retained E1.18 package")
    e120 = json_object(E120_REPORT)
    draft = json_object(E120_DRAFT)
    if (e120.get("edition") != "E1.20" or e120.get("result") != "offline_mapping_audit_passed"
            or e120.get("deployment_eligible") is not False
            or e120.get("hardware_accessed") is not False
            or e120.get("firmware_changed") is not False
            or e120.get("nvs_changed") is not False):
        raise ValueError("E1.20 report is not a safe offline mapping audit")
    change = e120.get("mapping_comparison", {}).get("changes", [])
    if len(change) != 1 or change[0] != {
        "semantic": "surprised", "disconnected": "shocked.eaf", "connected": "Happy.eaf",
        "disconnected_sha256": change[0].get("disconnected_sha256") if change else None,
        "connected_sha256": change[0].get("connected_sha256") if change else None,
        "fps": 20, "loop": True, "action_id": "look_around", "layout_id": "eye_anim",
    }:
        # Compare protected fields explicitly; the resource hashes are evidence
        # values and may be represented by an earlier report serializer.
        if (len(change) != 1 or change[0].get("semantic") != "surprised"
                or change[0].get("disconnected") != "shocked.eaf"
                or change[0].get("connected") != "Happy.eaf"
                or change[0].get("fps") != 20 or change[0].get("loop") is not True
                or change[0].get("action_id") != "look_around"
                or change[0].get("layout_id") != "eye_anim"):
            raise ValueError("E1.20 mapping does not describe the approved one-token change")
    changes = draft.get("changes", [])
    if (draft.get("schema") != "deskbot.expression_mapping_draft/v1"
            or draft.get("firmware_deployable") is not False
            or draft.get("hardware_accessed") is not False
            or len(changes) != 1
            or changes[0].get("semantic") != "surprised"
            or changes[0].get("before") != "shocked.eaf"
            or changes[0].get("after") != "Happy.eaf"):
        raise ValueError("E1.20 draft does not match the approved mapping")
    return e116, e120, draft


def repack(current: bytes, e113: bytes) -> tuple[bytes, dict[str, Any], dict[str, bytes]]:
    parsed = E116.AUDIT.parse_package_bytes(current, modern=False)
    members = parsed["members"]
    if E116.PACKAGE.build_legacy_package(members) != current:
        raise ValueError("retained E1.18 package is not reproducible by the anchored packer")
    if "index.json" not in members or "Happy.eaf" not in members or "shocked.eaf" not in members:
        raise ValueError("required package members are missing")
    patched_index, edit = E116.patch_index(members["index.json"], before="shocked.eaf", after="Happy.eaf")
    candidate_members = dict(members)
    candidate_members["index.json"] = patched_index
    candidate = E116.PACKAGE.build_legacy_package(candidate_members)
    decoded = E116.AUDIT.parse_package_bytes(candidate, modern=False)["members"]
    if list(decoded) != list(members) or decoded != candidate_members:
        raise ValueError("candidate member order or round-trip content changed")
    changed = [name for name in members if members[name] != decoded[name]]
    if changed != ["index.json"]:
        raise ValueError(f"unexpected changed package members: {changed}")
    restored_members = dict(decoded)
    restored_members["index.json"] = members["index.json"]
    restored = E116.PACKAGE.build_legacy_package(restored_members)
    if restored != current:
        raise ValueError("restoring the E1.18 index is not byte-exact")
    if candidate != e113:
        raise ValueError("E1.20 candidate is not the anchored E1.13 exact package")
    index_before = json.loads(members["index.json"])
    index_after = json.loads(decoded["index.json"])
    if index_before.get("layout") != index_after.get("layout"):
        raise ValueError("index layout changed")
    return candidate, edit, {
        "candidate-e1-20-do-not-flash.bin": candidate,
        "rollback-e1-18-exact.bin": current,
    }


def package_summary(raw: bytes) -> dict[str, Any]:
    parsed = E116.AUDIT.parse_package_bytes(raw, modern=False)
    return {"bytes": len(raw), "sha256": sha(raw), "remaining_bytes": PARTITION_BYTES - len(raw),
            "member_count": parsed["header"]["member_count"], "fits_partition": len(raw) <= PARTITION_BYTES}


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def run_player(candidate: bytes, output: Path, runtime_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    e117 = load_module(E117_PATH, "deskbot_e121_e117")
    player = e117.load_player(runtime_root.resolve())
    parsed = E116.AUDIT.parse_package_bytes(candidate, modern=False)["members"]
    index = json.loads(parsed["index.json"].decode("utf-8"))
    entries = {item["name"]: item for item in index["emoji_collection"]}
    if entries["surprised"]["file"] != "Happy.eaf" or entries["happy"]["file"] != "Happy.eaf":
        raise ValueError("candidate index does not contain the E1.20 mapping")
    output.mkdir(parents=True, exist_ok=True)
    resources = output / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    inputs = {}
    for case, member in (("happy", "Happy.eaf"), ("shocked", "shocked.eaf")):
        raw = parsed[member]
        path = resources / member
        immutable(path, raw)
        inputs[case] = {"path": path, "sha256": sha(raw), "size_bytes": len(raw), "member": member}
    dll = output / "player-audit-e1-11.dll"
    compile_log = output / "compile.log"
    if dll.is_file() and compile_log.is_file():
        compile_text = compile_log.read_text(encoding="utf-8", errors="replace")
        if "cc -std=c11 -O1 -Wall -Wextra" not in compile_text:
            raise ValueError("retained player compile evidence is missing the anchored C build command")
        log_hashes = []
        for case in ("happy", "shocked"):
            worker_log = output / f"worker-{case}.log"
            result_json = output / f"case-{case}.json"
            if not worker_log.is_file() or not result_json.is_file():
                raise ValueError("retained player run is incomplete")
            log_text = worker_log.read_text(encoding="utf-8", errors="replace")
            marker = "--dll-sha256 "
            if marker not in log_text:
                raise ValueError("retained player worker log has no DLL digest")
            log_hashes.append(log_text.split(marker, 1)[1].split()[0])
            worker_result = json.loads(result_json.read_text(encoding="utf-8"))
            if worker_result.get("result") != "pass" or not worker_result.get("case", {}).get("case_pass"):
                raise ValueError("retained player case did not pass")
        dll_hash = file_sha(dll)
        if log_hashes != [dll_hash, dll_hash]:
            raise ValueError("retained player DLL differs from worker evidence")
        compiler = player.COMPILER
        version_result = subprocess.run([str(compiler), "version"], capture_output=True,
                                        text=True, timeout=30, check=False)
        toolchain = {"compiler_version": (version_result.stdout + version_result.stderr).strip(),
                     "compiler_sha256": file_sha(compiler), "dll_sha256": dll_hash,
                     "dll_size_bytes": dll.stat().st_size, "optimization": "O1"}
    else:
        toolchain = player.compile_harness(output)
    cases: dict[str, Any] = {}
    for case in ("happy", "shocked"):
        payload = e117.run_case_worker(E117_PATH, runtime_root.resolve(), output, case,
                                       inputs[case]["path"], dll, toolchain["dll_sha256"])
        immutable(output / f"case-{case}-full.json",
                  (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        cases[case] = e117.summarize_case(payload)
    source_anchor = player.RENDER.source_anchor_report()
    compression_anchor = player.RENDER.COMPRESSION._verify_player_anchor()
    tool_anchors = player.verify_audit_tools()
    production_route = player.production_route_report()
    return {
        "source_anchor": source_anchor, "compression_anchor": compression_anchor,
        "audit_tool_anchors": tool_anchors, "production_route": production_route,
        "toolchain": {"compiler_version": toolchain["compiler_version"],
                       "compiler_sha256": toolchain["compiler_sha256"],
                       "player_dll_sha256": toolchain["dll_sha256"],
                       "player_dll_bytes": toolchain["dll_size_bytes"],
                       "optimization": toolchain["optimization"]},
        "cases": cases,
    }, {"happy": inputs["happy"], "shocked": inputs["shocked"]}


def audit(current_path: Path, e113_path: Path, runtime_root: Path, output: Path) -> dict[str, Any]:
    output = safe_output(output)
    current, e113 = current_path.read_bytes(), e113_path.read_bytes()
    e116, e120, draft = validate_inputs(current, e113)
    candidate, edit, binaries = repack(current, e113)
    for name, raw in binaries.items():
        immutable(output / name, raw)
    player_report, player_inputs = run_player(candidate, output, runtime_root)
    cases = player_report["cases"]
    player_pass = all(case.get("case_pass") for case in cases.values())
    candidate_summary = package_summary(candidate)
    rollback_summary = package_summary(current)
    current_members = E116.AUDIT.parse_package_bytes(current, modern=False)["members"]
    candidate_members = E116.AUDIT.parse_package_bytes(candidate, modern=False)["members"]
    report = {
        "schema": "deskbot.expression_e1_21_package_player_audit/v1",
        "edition": "E1.21", "date": "2026-10-26",
        "result": "offline_package_player_rollback_passed" if player_pass else "offline_package_player_rollback_rejected",
        "audit_pass": player_pass, "deployment_eligible": False,
        "safety": {"hardware_accessed": False, "serial_accessed": False,
                   "firmware_built": False, "firmware_changed": False,
                   "c61_changed": False, "nvs_changed": False,
                   "device_written": False, "flash_authorized": False},
        "inputs": {"e120_report": {"path": relative(E120_REPORT), "sha256": file_sha(E120_REPORT)},
                   "e120_draft": {"path": relative(E120_DRAFT), "sha256": file_sha(E120_DRAFT)},
                   "e116_report": {"path": relative(E116_REPORT), "sha256": file_sha(E116_REPORT)},
                   "raw_inputs_in_public_checkout": False},
        "mapping": {"semantic": "surprised", "before": "shocked.eaf", "after": "Happy.eaf",
                    "fps": 20, "loop": True, "action_id": "look_around", "layout_id": "eye_anim",
                    "index_patch": edit},
        "current_e118_package": rollback_summary,
        "candidate_package": candidate_summary,
        "rollback_package": {"label": "E1.18 retained package", **rollback_summary,
                              "byte_exact_from_candidate_after_index_restore": True},
        "e113_exact_reference": {"bytes": len(e113), "sha256": sha(e113),
                                 "candidate_byte_exact": candidate == e113},
        "package_comparison": {
            "member_count_before": len(current_members), "member_count_after": len(candidate_members),
            "member_order_unchanged": list(current_members) == list(candidate_members),
            "changed_members": [name for name in current_members if current_members[name] != candidate_members[name]],
            "eaf_payloads_changed": [name for name in current_members
                                     if name.endswith(".eaf") and current_members[name] != candidate_members[name]],
            "all_non_index_members_byte_identical": all(
                current_members[name] == candidate_members[name] for name in current_members if name != "index.json"),
            "candidate_matches_e113_exact": candidate == e113,
            "candidate_padded_fits_partition": len(candidate) <= PARTITION_BYTES,
        },
        "player": {"implementation": "hash-anchored Espressif C EAF decoder, gfx_anim timer and RGB565 renderer",
                    "cases": cases,
                    "inputs": {name: {key: value for key, value in item.items() if key != "path"}
                               for name, item in player_inputs.items()},
                    **{k: v for k, v in player_report.items() if k not in {"cases"}}},
        "rollback": {"candidate_to_e118_exact": True, "candidate_to_e113_exact": candidate == e113,
                      "device_rollback_performed": False,
                      "method": "restore the retained E1.18 index bytes and rebuild with the anchored legacy packer"},
        "gates": [
            {"id": "e120_input", "status": "passed", "label": "E1.20 映射报告和草稿锚定"},
            {"id": "actual_candidate_package", "status": "passed", "label": "真实候选包重打包、成员顺序和容量"},
            {"id": "payload_identity", "status": "passed", "label": "仅 index.json 改变，EAF payload 字节不变"},
            {"id": "official_c_player", "status": "passed" if player_pass else "failed", "label": "官方 C 播放器逐帧/计时器/边界"},
            {"id": "exact_rollback", "status": "passed", "label": "回退到 E1.18 精确包并匹配 E1.13 锚点"},
            {"id": "device_authorization", "status": "pending", "label": "单独授权本体 assets 部署"},
        ],
        "limitations": ["仅验证主机端官方 C 播放器，不证明 LCD、运行时堆、UI 并发或实机视觉。",
                        "候选包没有写入设备；COM4/COM8、C61、NVS 和固件均未访问。",
                        "E1.21 通过不等于部署授权；下一步需用户审阅视觉变化并单独授权 assets 分区。"],
    }
    immutable(PUBLIC / "report.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    template = (ROOT / "scripts/expression-e1-21-package-player.template.html").read_text(encoding="utf-8")
    if template.count("__E121_DATA__") != 1:
        raise ValueError("invalid E1.21 page template")
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    immutable(PUBLIC / "index.html", template.replace("__E121_DATA__", payload).encode("utf-8"))
    immutable(PUBLIC / "README.txt", b"E1.21 offline package, official C-player, and exact rollback audit.\nOpen index.html.\nNo device access or deployment authorization.\n")
    immutable(output / "audit.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, default=CURRENT_PACKAGE)
    parser.add_argument("--e113", type=Path, default=E113_EXACT)
    parser.add_argument("--runtime-root", type=Path, default=RUNTIME_ROOT_DEFAULT)
    parser.add_argument("--output", type=Path, default=LOCAL_DEFAULT)
    args = parser.parse_args()
    try:
        report = audit(args.current, args.e113, args.runtime_root, args.output)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"result": "rejected", "audit_pass": False,
                          "deployment_eligible": False, "hardware_accessed": False,
                          "device_written": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"result": report["result"], "audit_pass": report["audit_pass"],
                      "deployment_eligible": False, "candidate": report["candidate_package"],
                      "cases": report["player"]["cases"],
                      "report": str((PUBLIC / "report.json").resolve())}, ensure_ascii=False, indent=2))
    return 0 if report["audit_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
