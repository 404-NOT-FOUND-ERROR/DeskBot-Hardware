#!/usr/bin/env python3
"""Run the anchored Espressif C EAF player against the E1.16 package offline.

The original EAF files, compiler output, and verbose frame traces stay under
project tmp. The public artifact contains only hashes and summarized results.
No firmware, serial port, device, NVS, or assets partition is accessed.
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT_DEFAULT = PROJECT_ROOT.parent.parent
CANDIDATE_DEFAULT = PROJECT_ROOT / "tmp/expression-e1-16-repack-20261024/candidate-offline-do-not-flash.bin"
E116_REPORT = PROJECT_ROOT / "artifacts/expression-e1-16-repack-20261024/report.json"
PUBLIC = PROJECT_ROOT / "artifacts/expression-e1-17-player-20261024"
OUTPUT_DEFAULT = PROJECT_ROOT / "tmp/expression-e1-17-player-20261024"
PARTITION_BYTES = 6_144_000


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load local audit helper: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_player(runtime_root: Path):
    script = runtime_root / "scripts/audit-expression-e1-11-player.py"
    if not script.is_file():
        raise FileNotFoundError("anchored local E1.11 C-player audit is missing")
    return load_module(script, "deskbot_e117_player_anchor")


def safe_output(path: Path) -> Path:
    resolved = path.resolve()
    tmp_root = (PROJECT_ROOT / "tmp").resolve()
    if resolved.parent != tmp_root or not resolved.name.startswith("expression-e1-17-player-"):
        raise ValueError("E1.17 output must be a direct child of project tmp with the approved prefix")
    if any(parent.is_symlink() for parent in (resolved, *resolved.parents) if parent.exists()):
        raise ValueError("E1.17 output path must not traverse symlinks")
    if resolved.exists() and any(resolved.iterdir()):
        raise ValueError(f"refusing to reuse a non-empty E1.17 output directory: {resolved.name}")
    return resolved


def immutable(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"refusing to overwrite different evidence: {path.name}")
        return
    path.write_bytes(raw)


def publish_page(report: dict[str, Any]) -> None:
    template = (PROJECT_ROOT / "scripts/expression-e1-17-player.template.html").read_text(encoding="utf-8")
    if template.count("__E117_DATA__") != 1:
        raise ValueError("invalid E1.17 page template")
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    immutable(PUBLIC / "index.html", template.replace("__E117_DATA__", payload).encode("utf-8"))


def extract_inputs(candidate: bytes, output: Path, player: Any) -> dict[str, dict[str, Any]]:
    e116 = json.loads(E116_REPORT.read_text(encoding="utf-8"))
    expected = e116.get("candidate", {})
    if (e116.get("edition") != "E1.16" or e116.get("result") != "offline_repack_verified"
            or e116.get("deployment_eligible") is not False
            or len(candidate) != expected.get("bytes") or sha(candidate) != expected.get("sha256")):
        raise ValueError("candidate package does not match the audited E1.16 evidence")
    if len(candidate) > PARTITION_BYTES:
        raise ValueError("candidate exceeds the existing assets partition")

    parsed = player.RENDER.RESOURCES.parse_package_bytes(candidate, modern=False)
    members = parsed["members"]
    index = json.loads(members["index.json"].decode("utf-8"))
    entries = {item["name"]: item for item in index.get("emoji_collection", [])}
    if (entries.get("surprised", {}).get("file") != "shocked.eaf"
            or entries.get("surprised", {}).get("eaf") != {"loop": True, "fps": 20}
            or entries.get("happy", {}).get("file") != "Happy.eaf"):
        raise ValueError("E1.16 semantic mapping or timing differs from the reviewed proposal")

    expected_members = {item["name"]: item for item in e116["members"]}
    resources_dir = output / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    inputs: dict[str, dict[str, Any]] = {}
    for case, member in (("happy", "Happy.eaf"), ("shocked", "shocked.eaf")):
        raw = members.get(member)
        evidence = expected_members.get(member)
        if (raw is None or evidence is None or len(raw) != evidence["candidate_bytes"]
                or sha(raw) != evidence["candidate_sha256"]):
            raise ValueError(f"{member} does not match the E1.16 member digest")
        path = resources_dir / member
        immutable(path, raw)
        player_name = f"e117_{case}"
        relative = path.resolve().relative_to(player.ROOT.resolve()).as_posix()
        inputs[player_name] = {"path": path, "relative": relative, "sha256": sha(raw),
                               "size_bytes": len(raw), "member": member}
    return inputs


def configure_inputs(player: Any, inputs: dict[str, dict[str, Any]]) -> None:
    player.INPUTS = {name: (item["relative"], item["sha256"])
                     for name, item in inputs.items()}


def worker_main(runtime_root: Path, stage: Path, case: str, eaf_path: Path,
                dll_path: Path, dll_sha256: str, result_path: Path) -> int:
    player = load_player(runtime_root.resolve())
    stage = stage.resolve()
    eaf_path, dll_path, result_path = eaf_path.resolve(), dll_path.resolve(), result_path.resolve()
    if any(not path.is_relative_to(stage) for path in (eaf_path, dll_path, result_path)):
        raise ValueError("worker inputs and output must remain inside its isolated stage")
    if not dll_path.is_file() or sha(dll_path.read_bytes()) != dll_sha256:
        raise ValueError("worker DLL hash does not match the compiled player")
    raw = eaf_path.read_bytes()
    name = eaf_path.stem.lower()
    inputs = {f"e117_{name}": {"path": eaf_path,
                               "relative": eaf_path.relative_to(player.ROOT.resolve()).as_posix(),
                               "sha256": sha(raw)}}
    configure_inputs(player, inputs)
    selected = f"e117_{case}"
    if selected not in player.INPUTS or player.INPUTS[selected][1] != sha(raw):
        raise ValueError("worker case identity does not match its EAF input")
    native = player.PlayerNative(dll_path)
    result = player.run_case(native, selected, eaf_path)
    payload = {"result": "pass" if result["case_pass"] else "rejected", "case": result}
    immutable(result_path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"result": payload["result"], "case": selected,
                      "contract_errors": result["contract_errors"]}, ensure_ascii=False))
    return 0 if result["case_pass"] else 1


def summarize_case(payload: dict[str, Any]) -> dict[str, Any]:
    case = payload["case"]
    return {
        "resource": "Happy.eaf" if case["name"].endswith("happy") else "shocked.eaf",
        "sha256": case["sha256"], "size_bytes": case["size_bytes"],
        "fps": case["fps"], "image_frame_count": case["image_frame_count"],
        "metadata_frame_count": case["metadata_frame_count"], "layout": case["layout"],
        "c_rendered_frames": case["render_frame_count"],
        "all_frame_pixels_match": case["render_all_equal"],
        "all_frame_guards_clean": case["render_all_guard_clean"],
        "metadata_frame_rejected_safely": case["metadata_prepare_safety"]["pass"],
        "stop_restart_starts_at_zero": case["stop_restart"]["restart_starts_at_zero"],
        "repeated_reload_cleanup_passed": case["reloads_clean"],
        "nonloop_timer_passed": (case["nonloop"]["prepared_sequence_exact"]
                                  and case["nonloop"]["render_all_equal"]
                                  and case["nonloop"]["stopped_after_end"]
                                  and case["nonloop"]["completion_event"]),
        "loop_timer_passed": (case["loop"]["prepared_sequence_exact"]
                              and case["loop"]["render_all_equal"]
                              and case["loop"]["loop_revisited_start"]),
        "case_pass": payload["result"] == "pass" and case["case_pass"],
    }


def run_case_worker(script: Path, runtime_root: Path, stage: Path, case: str,
                     eaf: Path, dll: Path, dll_sha256: str) -> dict[str, Any]:
    result_path = stage / f"case-{case}.json"
    log_path = stage / f"worker-{case}.log"
    command = [sys.executable, str(script), "--worker", "--runtime-root", str(runtime_root),
               "--stage", str(stage), "--case", case, "--eaf", str(eaf),
               "--dll", str(dll), "--dll-sha256", dll_sha256, "--result", str(result_path)]
    try:
        completed = subprocess.run(command, cwd=stage, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as error:
        output = b"".join(value.encode("utf-8") if isinstance(value, str) else value or b""
                          for value in (error.stdout, error.stderr)).decode("utf-8", errors="replace")
        immutable(log_path, (subprocess.list2cmdline(command) + "\n" + output).encode("utf-8", errors="replace"))
        raise RuntimeError(f"C player worker timed out: {case}") from error
    log = "COMMAND: " + subprocess.list2cmdline(command) + "\n" + completed.stdout + completed.stderr
    immutable(log_path, log.encode("utf-8", errors="replace"))
    if completed.returncode != 0 or not result_path.is_file():
        raise RuntimeError(f"C player worker failed: {case}; see private tmp log")
    return json.loads(result_path.read_text(encoding="utf-8"))


def audit(candidate_path: Path, runtime_root: Path, output: Path) -> dict[str, Any]:
    output = safe_output(output)
    output.mkdir(parents=True, exist_ok=True)
    runtime_root = runtime_root.resolve()
    player = load_player(runtime_root)
    candidate = candidate_path.read_bytes()
    inputs = extract_inputs(candidate, output, player)
    configure_inputs(player, inputs)

    source_anchor = player.RENDER.source_anchor_report()
    compression_anchor = player.RENDER.COMPRESSION._verify_player_anchor()
    tool_anchors = player.verify_audit_tools()
    production_route = player.production_route_report()
    toolchain = player.compile_harness(output)
    dll = output / "player-audit-e1-11.dll"

    cases: dict[str, dict[str, Any]] = {}
    for case in ("happy", "shocked"):
        payload = run_case_worker(Path(__file__).resolve(), runtime_root, output, case,
                                  inputs[f"e117_{case}"]["path"], dll, toolchain["dll_sha256"])
        cases[case] = summarize_case(payload)
        immutable(output / f"case-{case}-full.json",
                  (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    if (source_anchor != player.RENDER.source_anchor_report()
            or compression_anchor != player.RENDER.COMPRESSION._verify_player_anchor()
            or tool_anchors != player.verify_audit_tools()
            or production_route != player.production_route_report()):
        raise RuntimeError("player source or audit anchors changed during E1.17")

    passed = all(item["case_pass"] for item in cases.values())
    report = {
        "schema": "deskbot.expression_e1_17_player_audit/v1",
        "edition": "E1.17", "date": "2026-10-24",
        "result": "offline_c_player_compatibility_passed" if passed else "offline_c_player_compatibility_rejected",
        "audit_pass": passed, "deployment_eligible": False,
        "safety": {"hardware_accessed": False, "serial_accessed": False,
                   "firmware_built": False, "firmware_changed": False,
                   "c61_changed": False, "nvs_changed": False,
                   "device_written": False, "flash_authorized": False},
        "candidate_package": {"bytes": len(candidate), "sha256": sha(candidate),
                              "partition_bytes": PARTITION_BYTES,
                              "fits_partition": len(candidate) <= PARTITION_BYTES},
        "semantic_contract": {"surprised_resource": "shocked.eaf", "surprised_fps": 20,
                              "surprised_loop": True, "happy_resource_preserved": True,
                              "action_mapping_unchanged": "look_around"},
        "player": "hash-anchored Espressif C EAF decoder, gfx_anim timer and RGB565 renderer",
        "player_source_anchor": source_anchor,
        "compression_source_anchor": compression_anchor,
        "audit_tool_anchors": tool_anchors,
        "toolchain": {"compiler_version": toolchain["compiler_version"],
                      "compiler_sha256": toolchain["compiler_sha256"],
                      "player_dll_sha256": toolchain["dll_sha256"],
                      "player_dll_bytes": toolchain["dll_size_bytes"],
                      "optimization": toolchain["optimization"]},
        "cases": cases,
        "limitations": ["This host audit does not prove LCD hardware output, device heap use or UI concurrency.",
                        "Visual acceptance of the surprised-to-shocked semantic change remains a user decision.",
                        "No package or partition was written to a device."],
        "next_gate": "owner visual approval, then separate explicit assets-only flash authorization and hardware regression",
    }
    immutable(PUBLIC / "report.json",
              (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    immutable(PUBLIC / "README.txt",
              b"E1.17 offline Espressif C-player compatibility audit.\nOpen the linked report for frame, timer, and safety results.\nNo device access or deployment authorization.\n")
    immutable(output / "audit.json",
              (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    publish_page(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=CANDIDATE_DEFAULT)
    parser.add_argument("--runtime-root", type=Path, default=RUNTIME_ROOT_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--render-only", action="store_true", help="render the existing public report; do not rerun C tests")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stage", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case", choices=("happy", "shocked"), help=argparse.SUPPRESS)
    parser.add_argument("--eaf", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--dll", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--dll-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.render_only:
        publish_page(json.loads((PUBLIC / "report.json").read_text(encoding="utf-8")))
        print("E1.17 page rendered from retained audit evidence; C tests were not rerun")
        return 0
    if args.worker:
        if not all((args.stage, args.case, args.eaf, args.dll, args.dll_sha256, args.result)):
            parser.error("invalid worker invocation")
        return worker_main(args.runtime_root, args.stage, args.case, args.eaf,
                           args.dll, args.dll_sha256, args.result)
    try:
        report = audit(args.candidate, args.runtime_root, args.output)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"result": "rejected", "audit_pass": False,
                          "hardware_accessed": False, "deployment_eligible": False,
                          "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"result": report["result"], "audit_pass": report["audit_pass"],
                      "deployment_eligible": False,
                      "cases": report["cases"], "report": str((PUBLIC / "report.json").resolve())},
                     ensure_ascii=False, indent=2))
    return 0 if report["audit_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
