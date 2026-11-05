#!/usr/bin/env python3
"""Check the public snapshot, documentation links and offline visualizations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    failures = []
    # Only public inputs: never scan ignored local logs, caches or .git objects.
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    files = [ROOT / name for name in set(names) if name and (ROOT / name).is_file()]
    private_path = re.compile(r"[A-Za-z]:[\\/][^\n\"']*(?:Codex Project|Users[\\/])", re.I)
    credential = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")
    device_id = re.compile(r'"(?:mac|device_id)"\s*:\s*"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}"|"device_id"\s*:\s*"esp32s3-[0-9a-f]{12}"', re.I)
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if path.stat().st_size > 10 * 1024 * 1024:
            failures.append("large public file: " + relative)
        if path.suffix.lower() in {".md", ".json", ".jsonl", ".py", ".ps1", ".yml", ".yaml", ".lock", ".html", ".txt", ".log", ".js", ".cjs", ".css", ".csv"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            if private_path.search(text) or credential.search(text) or device_id.search(text):
                failures.append("private path, credential or device ID: " + relative)
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8-sig"))
            except ValueError:
                failures.append("invalid JSON: " + relative)

    for name, other in (("README.md", "README.en.md"), ("README.en.md", "README.md")):
        text = (ROOT / name).read_text(encoding="utf-8")
        if other not in text or "O2f2" not in text or "E1.13" not in text:
            failures.append("missing language link or current baseline: " + name)
    e3a = ROOT / "artifacts/deskbot-studio-e3a-20261104"
    try:
        build = json.loads((e3a / "report.json").read_text(encoding="utf-8"))
        live = json.loads((e3a / "live-validation.json").read_text(encoding="utf-8"))
        page = (e3a / "index.html").read_text(encoding="utf-8")
        if (build.get("edition") != "E3a" or build.get("hardware_accessed") is not False
                or build.get("firmware_changed") is not False
                or live.get("result") != "read_only_observation_received"
                or live.get("hardware_accessed") is not True or live.get("serial_commands") is not False
                or live.get("device_written") is not False or live.get("nvs_changed") is not False
                or live.get("observation", {}).get("raw_console_lines_saved") is not False
                or "id=\"body-observer-slot\"" not in page or "saved_release_record" not in page):
            failures.append("E3a build/live evidence or source separation mismatch")
    except (OSError, ValueError, TypeError):
        failures.append("E3a build/live evidence is missing or invalid")
    e3b = ROOT / "artifacts/deskbot-studio-e3b-20261104"
    try:
        build = json.loads((e3b / "report.json").read_bytes())
        firmware = json.loads((e3b / "firmware-build-report.json").read_bytes())
        verification = json.loads((e3b / "verification.json").read_bytes())
        rows = firmware["builds"]
        if (build.get("edition") != "E3b" or build.get("device_written") is not False
                or build.get("device_link", {}).get("catalog_confirmed") is not False
                or build.get("device_link", {}).get("expression_controls") is not False
                or firmware.get("device_written") is not False or firmware.get("hardware_acceptance") != "pending"
                or [row.get("enabled") for row in rows] != [False, True]
                or any(not all(row["protected_matches"].values())
                       or row["identity_symbols_present"] != row["enabled"]
                       or row["app_headroom_bytes"] < 1048576 for row in rows)
                or verification.get("hardware_acceptance") != "pending"
                or verification.get("device_written") is not False):
            failures.append("E3b build, capability or acceptance scope mismatch")
    except (OSError, ValueError, TypeError, KeyError):
        failures.append("E3b build or verification evidence is missing or invalid")
    e3c4 = ROOT / "artifacts/deskbot-studio-e3c4-20261105"
    try:
        tests = json.loads((e3c4 / "offline-validation.json").read_bytes())
        build = json.loads((e3c4 / "firmware-build-report.json").read_bytes())
        audit = json.loads((e3c4 / "firmware-audit.json").read_bytes())
        if (tests.get("tests_run") != 212 or tests.get("display_tests") != 34
                or any(tests.get(key) != 0 for key in ("failures", "errors", "skipped"))
                or build.get("result") != "isolated_off_on_builds_verified"
                or build.get("device_written") is not False or build.get("hardware_accessed") is not False
                or build.get("expression_controls") is not False or build.get("new_tasks") != 0
                or build.get("sleep_state") != "unknown"
                or [row.get("enabled") for row in build["builds"]] != [False, True]
                or any(not all(row["protected_matches"].values())
                       or row["display_query_reader_present"] != row["enabled"]
                       or row["display_store_bytes"] != (8 if row["enabled"] else 0)
                       or row["app_headroom_bytes"] < 1048576 for row in build["builds"])
                or audit.get("result") != "passed"
                or audit.get("disabled_comparison", {}).get("executable_and_other_data_equal") is not True
                or audit.get("source_delta") != build.get("source_delta")
                or audit.get("protected_artifacts_equal") is not True):
            failures.append("E3c-4 tests, build or audit evidence mismatch")
    except (OSError, ValueError, TypeError, KeyError):
        failures.append("E3c-4 tests, build or audit evidence is missing or invalid")
    e3c5 = ROOT / "artifacts/deskbot-studio-e3c5-20261105"
    try:
        tests = json.loads((e3c5 / "offline-validation.json").read_bytes())
        build = json.loads((e3c5 / "firmware-build-report.json").read_bytes())
        audit = json.loads((e3c5 / "firmware-audit.json").read_bytes())
        if (tests.get("tests_run") != 254 or tests.get("expression_tests") != 42
                or any(tests.get(key) != 0 for key in ("failures", "errors", "skipped"))
                or build.get("result") != "isolated_off_on_builds_verified"
                or build.get("device_written") is not False or build.get("hardware_accessed") is not False
                or build.get("new_tasks") != 0 or build.get("sleep_state") != "unknown"
                or [row.get("enabled") for row in build["builds"]] != [False, True]
                or any(not all(row["protected_matches"].values())
                       or row["expression_adapter_present"] != row["enabled"]
                       or row["neutral_request_enabled"] != row["enabled"]
                       or row["app_headroom_bytes"] < 1048576 for row in build["builds"])
                or audit.get("result") != "passed"
                or audit.get("disabled_comparison", {}).get("executable_and_other_data_equal") is not True
                or audit.get("owner_application_ui_player_unchanged") is not True
                or audit.get("source_delta") != build.get("source_delta")
                or audit.get("protected_artifacts_equal") is not True):
            failures.append("E3c-5 tests, build or audit evidence mismatch")
    except (OSError, ValueError, TypeError, KeyError):
        failures.append("E3c-5 tests, build or audit evidence is missing or invalid")
    # Check local links in project-owned documentation, not imported upstream docs.
    docs = [ROOT / "README.md", ROOT / "README.en.md", ROOT / "THIRD_PARTY.md", *(ROOT / "docs").glob("*.md")]
    for path in docs:
        for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if re.match(r"(?:[a-z]+:|#)", link):
                continue
            if not (path.parent / link.split("#", 1)[0]).exists():
                failures.append(f"broken local link: {path.relative_to(ROOT)} -> {link}")

    body = ROOT / "firmware/body"
    manifest = body / "SOURCE_MANIFEST.sha256"
    if manifest.is_file():
        listed = set()
        for line in manifest.read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            listed.add(relative)
            path = body / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                failures.append("source manifest mismatch: " + relative)
        expected = {p.relative_to(body).as_posix() for p in files if p.is_relative_to(body) and p != manifest}
        if listed != expected:
            failures.append("source manifest membership mismatch")
    else:
        failures.append("source manifest missing")
    for name in ("expression-node-workbench-e1-4d-20261021", "expression-binding-matrix-e1-9-20261022", "expression-e1-10-review-20261022", "expression-binding-decision-e1-14-20261024", "expression-e1-15-surprised-shocked-20261024", "expression-e1-16-repack-20261024", "expression-e1-17-player-20261024", "expression-node-preview-e1-19-20261024", "expression-e1-21-package-player-20261026", "expression-e1-22-candidate-preview-20261026", "expression-e1-27-runtime-observation-20261027", "expression-e1-28-semantic-review-20261027", "expression-e1-29-mcp-capture-20261028", "role-expression-workbench-e1-31-20261029", "role-expression-audit-e1-32-20261029", "expression-e1-33-import-20261029", "expression-e1-34-preprocess-20261029", "expression-storage-strategy-e1-35-20261029", "expression-storage-migration-e1-35b-20261029", "expression-addressing-e1-38-20261030"):
        page = ROOT / "artifacts" / name / "index.html"
        if not page.is_file() or "connect-src 'none'" not in page.read_text(encoding="utf-8"):
            failures.append("missing offline visualization: " + name)
    for directory, script_id in (("expression-node-workbench-e1-4d-20261021", "workbench-data"),
                                 ("expression-e1-10-review-20261022", "data"),
                                 ("expression-binding-decision-e1-14-20261024", "decision-data"),
                                 ("expression-e1-15-surprised-shocked-20261024", "e115-data"),
                                 ("expression-e1-16-repack-20261024", "e116-data"),
                                 ("expression-e1-17-player-20261024", "e117-data"),
                                 ("expression-node-preview-e1-19-20261024", "e119-data"),
                                 ("expression-e1-21-package-player-20261026", "e121-data"),
                                 ("expression-e1-22-candidate-preview-20261026", "e122-data"),
                                 ("expression-e1-27-runtime-observation-20261027", "e127-data"),
                                 ("expression-e1-28-semantic-review-20261027", "e128-data"),
                                 ("expression-e1-29-mcp-capture-20261028", "e129-data"),
                                 ("role-expression-workbench-e1-31-20261029", "e131-data"),
                                 ("role-expression-audit-e1-32-20261029", "e132-data"),
                                 ("expression-e1-33-import-20261029", "e133-data"),
                                 ("expression-e1-34-preprocess-20261029", "e134-data")):
        page = ROOT / "artifacts" / directory / "index.html"
        text = page.read_text(encoding="utf-8")
        match = re.search(r'<script id="' + script_id + r'" type="application/json">(.*?)</script>', text, re.S)
        if match is None:
            failures.append("missing workbench payload: " + directory)
            continue
        data = json.loads(match[1])
        if script_id == "e115-data":
            decision = data.get("decision", {})
            proposal = decision.get("proposal", {})
            media = data.get("preview", {})
            if (decision.get("edition") != "E1.15" or decision.get("deployment_eligible") is not False
                    or decision.get("replacement_authorized") is not False
                    or decision.get("hardware_accessed") is not False
                    or (proposal.get("semantic"), proposal.get("before"), proposal.get("after"))
                    != ("surprised", "Happy.eaf", "shocked.eaf")
                    or decision.get("member_diff", {}).get("added") != []
                    or decision.get("member_diff", {}).get("removed") != []
                    or set(media) != {"happy", "shocked"}
                    or any(not item.get("data_url", "").startswith("data:image/png;base64,")
                           for item in media.values())):
                failures.append("E1.15 proposal boundary or preview mismatch: " + directory)
            continue
        if script_id == "e116-data":
            if (data.get("edition") != "E1.16" or data.get("result") != "offline_repack_verified"
                    or data.get("deployment_eligible") is not False
                    or data.get("member_diff", {}).get("changed") != ["index.json"]
                    or data.get("member_diff", {}).get("added") != []
                    or data.get("member_diff", {}).get("removed") != []
                    or data.get("member_diff", {}).get("eaf_payloads_changed") != []
                    or data.get("candidate", {}).get("bytes") != 5_538_351
                    or not data.get("rollback", {}).get("package_byte_exact")
                    or not data.get("rollback", {}).get("full_partition_byte_exact")):
                failures.append("E1.16 repack boundary or rollback mismatch: " + directory)
            continue
        if script_id == "e117-data":
            cases = data.get("cases", {})
            if (data.get("edition") != "E1.17"
                    or data.get("result") != "offline_c_player_compatibility_passed"
                    or data.get("audit_pass") is not True
                    or data.get("deployment_eligible") is not False
                    or data.get("safety", {}).get("hardware_accessed") is not False
                    or data.get("safety", {}).get("device_written") is not False
                    or data.get("semantic_contract", {}).get("surprised_resource") != "shocked.eaf"
                    or set(cases) != {"happy", "shocked"}
                    or any(item.get("case_pass") is not True for item in cases.values())):
                failures.append("E1.17 player report boundary mismatch: " + directory)
            continue
        if script_id == "e119-data":
            if (data.get("edition") != "E1.19"
                    or data.get("hardware_accessed") is not False
                    or data.get("firmware_changed") is not False
                    or data.get("counts") != {"semantics": 29, "official_resources": 18, "routes": 56}
                    or data.get("change", {}).get("semantic") != "surprised"
                    or data.get("change", {}).get("disconnected") != "Happy.eaf"
                    or data.get("change", {}).get("connected") != "shocked.eaf"
                    or len(data.get("assets", {})) != 18
                    or "data:image" not in text):
                failures.append("E1.19 node preview boundary mismatch: " + directory)
            continue
        if script_id == "e121-data":
            cases = data.get("player", {}).get("cases", {})
            safety = data.get("safety", {})
            if (data.get("edition") != "E1.21"
                    or data.get("result") != "offline_package_player_rollback_passed"
                    or data.get("audit_pass") is not True
                    or data.get("deployment_eligible") is not False
                    or safety.get("hardware_accessed") is not False
                    or safety.get("serial_accessed") is not False
                    or safety.get("device_written") is not False
                    or data.get("package_comparison", {}).get("changed_members") != ["index.json"]
                    or data.get("package_comparison", {}).get("candidate_matches_e113_exact") is not True
                    or data.get("rollback", {}).get("candidate_to_e118_exact") is not True
                    or set(cases) != {"happy", "shocked"}
                    or any(item.get("case_pass") is not True for item in cases.values())):
                failures.append("E1.21 package/player release boundary mismatch: " + directory)
            continue
        if script_id == "e122-data":
            report = data.get("report", {})
            comparison = report.get("comparison", {})
            previews = data.get("previews", {})
            safety = report.get("safety", {})
            if (report.get("edition") != "E1.22"
                    or report.get("result") != "offline_before_after_preview_ready"
                    or report.get("audit_pass") is not True
                    or report.get("deployment_eligible") is not False
                    or set(previews) != {"Happy.eaf", "shocked.eaf"}
                    or (comparison.get("semantic"), comparison.get("before_resource"), comparison.get("candidate_resource"))
                    != ("surprised", "shocked.eaf", "Happy.eaf")
                    or comparison.get("old_edge", {}).get("operation") != "disconnect"
                    or comparison.get("candidate_edge", {}).get("operation") != "connect"
                    or not comparison.get("action_unchanged")
                    or not comparison.get("layout_unchanged")
                    or not comparison.get("timing_unchanged")
                    or any(value is not False for value in safety.values())
                    or any(item.get("origin") != "official"
                           or not item.get("atlas", "").startswith("data:image/png;base64,")
                           or item.get("fps") != 20 or item.get("loop") is not True
                           for item in previews.values())
                    or "data:image/png;base64," not in text):
                failures.append("E1.22 preview or deployment boundary mismatch: " + directory)
            continue
        if script_id == "e127-data":
            sample = data.get("sample", {})
            snapshot = sample.get("snapshot", {})
            oral = data.get("oralSummary", {})
            oral_file = ROOT / "artifacts" / directory / "oral-summary.json"
            report = ROOT / "artifacts" / directory / "report.json"
            if (data.get("edition") != "E1.27"
                    or not report.is_file()
                    or not oral_file.is_file()
                    or sample.get("origin") != "synthetic_offline_sample"
                    or snapshot.get("schema") != "deskbot.expression.runtime_observation.e1_26/v1"
                    or len(snapshot.get("records", [])) != 7
                    or not any(row.get("semantic") == "other" for row in snapshot.get("records", []))
                    or oral.get("source") != "user_reported_voice_response"
                    or oral.get("raw_json_available") is not False
                    or oral.get("reported_recent_record_count") != 24
                    or {row.get("semantic") for row in oral.get("reported_semantics", [])}
                    != {"happy", "embarrassed", "sleepy", "neutral"}
                    or "e127-core" not in text
                    or "e127-js" not in text
                    or "e127-oral-list" not in text
                    or "connect-src 'none'" not in text):
                failures.append("E1.27 observation workbench boundary mismatch: " + directory)
            else:
                try:
                    oral_data = json.loads(oral_file.read_text(encoding="utf-8"))
                    report_data = json.loads(report.read_text(encoding="utf-8"))
                    if (report_data.get("edition") != "E1.27"
                            or report_data.get("deployment_eligible") is not False
                            or report_data.get("flash_authorized") is not False
                            or report_data.get("hardware_accessed") is not False
                            or report_data.get("serial_accessed") is not False
                            or report_data.get("device_written") is not False
                            or report_data.get("nvs_changed") is not False
                            or report_data.get("user_reported_oral_summary") != oral_data):
                        failures.append("E1.27 report authorizes deployment or hardware access")
                except (OSError, ValueError, TypeError):
                    failures.append("invalid E1.27 observation report")
            continue
        if script_id == "e128-data":
            bindings = data.get("bindings", {})
            report = json.loads((ROOT / "artifacts" / directory / "report.json").read_text(encoding="utf-8"))
            if (data.get("edition") != "E1.28"
                    or set(bindings) != {"happy", "embarrassed", "sleepy", "neutral"}
                    or bindings.get("happy", {}).get("resource_id") != "Happy.eaf"
                    or bindings.get("embarrassed", {}).get("resource_id") != "Happy.eaf"
                    or bindings.get("sleepy", {}).get("resource_id") != "sleep.eaf"
                    or bindings.get("neutral", {}).get("resource_id") != "winking.eaf"
                    or bindings.get("happy", {}).get("action_id") != "shark_head"
                    or bindings.get("embarrassed", {}).get("action_id") != "cat_nuzzle"
                    or bindings.get("sleepy", {}).get("action_id") != "shark_head_decay"
                    or bindings.get("neutral", {}).get("action_id") is not None
                    or len(data.get("semantics", [])) != 4
                    or report.get("edition") != "E1.28"
                    or report.get("result") != "offline_semantic_review_ready"
                    or report.get("deployment_eligible") is not False
                    or report.get("flash_authorized") is not False
                    or report.get("hardware_accessed") is not False
                    or report.get("serial_accessed") is not False
                    or report.get("device_written") is not False
                    or report.get("nvs_changed") is not False
                    or "connect-src 'none'" not in text
                    or "参考合成" not in text
                    or "不模拟底座执行结果" not in text):
                failures.append("E1.28 semantic review boundary mismatch: " + directory)
            continue
        if script_id == "e129-data":
            sample = data.get("sample", {})
            capture = data.get("capture", {})
            snapshot = data.get("snapshot", {})
            provenance = data.get("sample_provenance", {})
            report = ROOT / "artifacts" / directory / "report.json"
            if (data.get("edition") != "E1.29"
                    or data.get("sample_origin") != "synthetic_offline_sample"
                    or not report.is_file()
                    or capture.get("schema") != "deskbot.expression.mcp_capture_input.e1_29/v1"
                    or capture.get("request_message", {}).get("payload", {}).get("params", {}).get("name") != "self.expression.observation"
                    or capture.get("response_message", {}).get("payload", {}).get("id") != 29
                    or snapshot.get("schema") != "deskbot.expression.runtime_observation.e1_26/v1"
                    or len(snapshot.get("records", [])) != 2
                    or provenance.get("edition") != "E1.29"
                    or provenance.get("safety", {}).get("deployment_eligible") is not False
                    or provenance.get("privacy", {}).get("session_id_copied_to_output") is not False
                    or "connect-src 'none'" not in text
                    or "合成样例" not in text
                    or "self.expression.observation" not in text):
                failures.append("E1.29 capture/workbench boundary mismatch: " + directory)
            else:
                try:
                    report_data = json.loads(report.read_text(encoding="utf-8"))
                    boundaries = report_data.get("boundaries", {})
                    if (report_data.get("edition") != "E1.29"
                            or report_data.get("result") != "offline_mcp_capture_contract_and_visualization_ready"
                            or report_data.get("real_device_capture_available") is not False
                            or any(boundaries.get(key) is not False for key in (
                                "hardware_accessed", "serial_accessed", "device_written", "nvs_changed",
                                "deployment_eligible", "flash_authorized"))):
                        failures.append("E1.29 report authorizes deployment or hardware access")
                except (OSError, ValueError, TypeError):
                    failures.append("invalid E1.29 capture report")
            continue
        if script_id == "e131-data":
            report = ROOT / "artifacts" / directory / "report.json"
            try:
                report_data = json.loads(report.read_text(encoding="utf-8"))
                initial = data.get("initial", {})
                roles = initial.get("roles", [])
                semantics = data.get("semantics", {})
                assets = data.get("assets", {})
                compatible = all(
                    resource is None or resource in semantics[semantic].get("allowed_resources", [])
                    for role in roles
                    for semantic, resource in role.get("semantic_resources", {}).items()
                    if semantic in semantics
                )
                complete_maps = all(set(role.get("semantic_resources", {})) == set(semantics) for role in roles)
                if (data.get("edition") != "E1.31"
                        or data.get("hardware_accessed") is not False
                        or data.get("firmware_changed") is not False
                        or data.get("deployment_eligible") is not False
                        or data.get("baseline", {}).get("release_id") != "E1.18"
                        or len(semantics) != 29 or len(assets) != 18 or len(roles) != 2
                        or any(role.get("role_profile", {}).get("binding_state") != "draft" for role in roles)
                        or not complete_maps or not compatible
                        or report_data.get("edition") != "E1.31"
                        or report_data.get("deployment_eligible") is not False
                        or report_data.get("hardware_accessed") is not False
                        or "connect-src 'none'" not in text):
                    failures.append("E1.31 offline role-expression boundary mismatch: " + directory)
            except (OSError, ValueError, TypeError, KeyError):
                failures.append("invalid E1.31 role-expression report or payload")
            continue
        if script_id == "e132-data":
            report = ROOT / "artifacts" / directory / "report.json"
            try:
                report_data = json.loads(report.read_text(encoding="utf-8"))
                inventory = data.get("inventory", {})
                checks = data.get("checks", {})
                identities = data.get("identity_isolation", [])
                branches = data.get("branches", [])
                negative_cases = data.get("negative_cases", [])
                if (data.get("edition") != "E1.32"
                        or data.get("baseline", {}).get("recomputed", {}).get("release_id") != "E1.18"
                        or inventory.get("semantic_count") != 29
                        or inventory.get("official_resource_count") != 18
                        or inventory.get("role_count") != 2
                        or not checks or not all(checks.values())
                        or len(identities) != 2
                        or any(row.get("all_distinct") is not True for row in identities)
                        or len(branches) != 2
                        or any(row.get("binding_state") != "draft"
                               or not row.get("official_branch")
                               or not row.get("expression_branch")
                               or not row.get("action_branch")
                               for row in branches)
                        or len(negative_cases) != 10
                        or any(case.get("passed") is not True for case in negative_cases)
                        or report_data.get("edition") != "E1.32"
                        or report_data.get("result") != "offline_role_expression_audit_passed"
                        or report_data.get("audit_pass") is not True
                        or report_data.get("hardware_accessed") is not False
                        or report_data.get("serial_accessed") is not False
                        or report_data.get("firmware_changed") is not False
                        or report_data.get("nvs_changed") is not False
                        or report_data.get("deployment_eligible") is not False
                        or report_data.get("flash_authorized") is not False
                        or report_data.get("next_gate") != "offline_asset_import_and_player_layout_audit"
                        or "connect-src 'none'" not in text):
                    failures.append("E1.32 role-expression audit boundary mismatch: " + directory)
            except (OSError, ValueError, TypeError, KeyError):
                failures.append("invalid E1.32 role-expression audit report or payload")
            continue
        if script_id == "e134-data":
            report = ROOT / "artifacts/expression-e1-34-preprocess-20261029/report.json"
            try:
                report_data = json.loads(report.read_text(encoding="utf-8"))
                if (data.get("edition") != "E1.34"
                        or data.get("media_scope") != "public_metadata_only"
                        or data.get("hardware_accessed") is not False
                        or data.get("firmware_changed") is not False
                        or data.get("deployment_eligible") is not False
                        or data.get("flash_authorized") is not False
                        or set(data.get("candidates", {})) != {"full", "eye"}
                        or any(item.get("atlas", {}).get("data_url") is not None for item in data.get("candidates", {}).values())
                        or any(item.get("publication_media_omitted") is not True for item in data.get("candidates", {}).values())
                        or report_data.get("edition") != "E1.34"
                        or report_data.get("deployment_eligible") is not False
                        or report_data.get("flashable_package_emitted") is not False
                        or len(report_data.get("lossless_variants", [])) != 13
                        or report_data.get("crop_research", {}).get("visual_accepted") is not False
                        or data.get("report_sha256") != hashlib.sha256(report.read_bytes()).hexdigest()
                        or "connect-src 'none'" not in text):
                    failures.append("E1.34 public preprocessing boundary mismatch")
            except (OSError, ValueError, TypeError, KeyError):
                failures.append("invalid E1.34 preprocessing report or payload")
            continue
        elif script_id == "e133-data":
            report = ROOT / "artifacts" / directory / "report.json"
            try:
                report_data = json.loads(report.read_text(encoding="utf-8"))
                candidate = data.get("candidate", {})
                renderer = data.get("official_player", {})
                capacity = data.get("capacity", {})
                if (data.get("edition") != "E1.33"
                        or data.get("media_scope") != "public_metadata_only"
                        or data.get("hardware_accessed") is not False
                        or data.get("firmware_changed") is not False
                        or data.get("deployment_eligible") is not False
                        or data.get("flash_authorized") is not False
                        or candidate.get("atlas_data_url") is not None
                        or candidate.get("publication_media_omitted") is not True
                        or renderer.get("renderer_layouts_passed") is not True
                        or renderer.get("preview_atlases", {}).get("full_screen", {}).get("data_url") is not None
                        or renderer.get("preview_atlases", {}).get("eye_anim", {}).get("data_url") is not None
                        or report_data.get("edition") != "E1.33"
                        or report_data.get("result") != "offline_audit_complete_deployment_blocked"
                        or report_data.get("audit_pass") is not True
                        or report_data.get("deployment_eligible") is not False
                        or report_data.get("capacity", {}).get("append_candidate", {}).get("fits_partition") is not False
                        or "connect-src 'none'" not in text
                        or "候选素材未嵌入公开副本" not in text):
                    failures.append("E1.33 public import boundary mismatch: " + directory)
            except (OSError, ValueError, TypeError, KeyError):
                failures.append("invalid E1.33 import report or payload")
            continue
        if script_id == "workbench-data":
            candidates = [v for v in data["preview"]["assets"].values() if v.get("origin") != "official"]
            media_key = "atlas"
        elif script_id == "data":
            candidates = [v for k, v in data["preview"].items() if k.startswith("shy-")]
            media_key = "data_url"
        else:
            candidates = [v for v in data.get("resource_decisions", []) if v.get("kind") == "offline_candidate"]
            media_key = "media"
        if script_id == "decision-data":
            if (not candidates or data.get("deployment_eligible") is not False
                    or any(v.get("current_profile_member") is not False for v in candidates)
                    or any(v.get("replacement_authorized") or v.get("removal_authorized")
                           for v in data["resource_decisions"])
                    or "data:image" in text):
                failures.append("E1.14 candidate must remain metadata-only: " + directory)
        elif not candidates or any(v.get(media_key) or not v.get("publication_media_omitted") for v in candidates):
            failures.append("unlicensed candidate media must be withheld: " + directory)
    e120 = ROOT / "artifacts" / "expression-e1-20-mapping-audit-20261025"
    e120_report = e120 / "report.json"
    e120_draft = e120 / "draft.json"
    if not e120_report.is_file() or not e120_draft.is_file():
        failures.append("missing E1.20 mapping audit inputs")
    else:
        try:
            audit = json.loads(e120_report.read_text(encoding="utf-8"))
            draft = json.loads(e120_draft.read_text(encoding="utf-8"))
            if (audit.get("edition") != "E1.20"
                    or audit.get("result") != "offline_mapping_audit_passed"
                    or audit.get("deployment_eligible") is not False
                    or audit.get("hardware_accessed") is not False
                    or audit.get("firmware_changed") is not False
                    or audit.get("nvs_changed") is not False
                    or audit.get("mapping_comparison", {}).get("action_map_unchanged") is not True
                    or audit.get("mapping_comparison", {}).get("timing_unchanged") is not True
                    or audit.get("mapping_comparison", {}).get("layout_unchanged") is not True
                    or audit.get("mapping_comparison", {}).get("rollback_exact") is not True
                    or audit.get("capacity", {}).get("fits_partition") is not True
                    or draft.get("firmware_deployable") is not False
                    or draft.get("hardware_accessed") is not False):
                failures.append("E1.20 mapping audit boundary mismatch")
        except (OSError, ValueError, TypeError):
            failures.append("invalid E1.20 mapping audit JSON")
    e123_report = ROOT / "artifacts" / "expression-e1-23-deployment-preflight-20261026" / "report.json"
    if not e123_report.is_file():
        failures.append("missing E1.23 deployment preflight report")
    else:
        try:
            preflight = json.loads(e123_report.read_text(encoding="utf-8"))
            candidate = preflight.get("candidate", {})
            rollback = preflight.get("rollback", {})
            if (preflight.get("edition") != "E1.23"
                    or preflight.get("result") != "offline_deployment_artifacts_prepared"
                    or preflight.get("audit_pass") is not True
                    or preflight.get("deployment_eligible") is not False
                    or preflight.get("flash_authorized") is not False
                    or preflight.get("hardware_accessed") is not False
                    or preflight.get("serial_accessed") is not False
                    or preflight.get("device_written") is not False
                    or preflight.get("partition", {}).get("offset") != 0x5EC000
                    or preflight.get("partition", {}).get("length_bytes") != 0x5DC000
                    or candidate.get("partition_bytes") != 0x5DC000
                    or candidate.get("partition_sha256") != "6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205"
                    or rollback.get("partition_bytes") != 0x5DC000
                    or rollback.get("partition_sha256") != "d0858a38f9788dc4a57e4f7c2417425f0cf762ebe2268ea049d7d2feb65cada6"
                    or not preflight.get("pending_gates")):
                failures.append("E1.23 deployment authorization or partition boundary mismatch")
        except (OSError, ValueError, TypeError):
            failures.append("invalid E1.23 deployment preflight JSON")
    e122_decision = ROOT / "artifacts" / "expression-e1-22-candidate-preview-20261026" / "selection-record.json"
    e124_report = ROOT / "artifacts" / "expression-e1-24-coverage-audit-20261026" / "report.json"
    if not e122_decision.is_file() or not e124_report.is_file():
        failures.append("missing E1.22 selection record or E1.24 coverage report")
    else:
        try:
            decision = json.loads(e122_decision.read_text(encoding="utf-8"))
            coverage = json.loads(e124_report.read_text(encoding="utf-8"))
            recorded_decision = coverage.get("sources", {}).get("decision", {})
            if (decision.get("edition") != "E1.22"
                    or decision.get("decision") != "retain_current"
                    or decision.get("semantic") != "surprised"
                    or decision.get("selected_resource") != "shocked.eaf"
                    or decision.get("mapping_changed") is not False
                    or decision.get("flash_authorized") is not False
                    or coverage.get("edition") != "E1.24"
                    or coverage.get("result") != "offline_static_mapping_coverage_audit_passed"
                    or coverage.get("audit_pass") is not True
                    or coverage.get("deployment_eligible") is not False
                    or coverage.get("flash_authorized") is not False
                    or coverage.get("safety", {}).get("hardware_accessed") is not False
                    or coverage.get("safety", {}).get("device_written") is not False
                    or recorded_decision.get("sha256") != hashlib.sha256(e122_decision.read_bytes()).hexdigest()
                    or coverage.get("visual_decision", {}).get("selected_resource") != "shocked.eaf"
                    or coverage.get("static_inventory", {}).get("e114_route_count") != 56
                    or coverage.get("static_inventory", {}).get("current_e118_mapping", {}).get("official_resource_count") != 18
                    or coverage.get("runtime_observation", {}).get("status") != "not_collected"):
                failures.append("E1.24 selection, static-coverage or offline boundary mismatch")
        except (OSError, ValueError, TypeError):
            failures.append("invalid E1.22 selection record or E1.24 coverage JSON")
    storage_path = ROOT / "artifacts/expression-storage-strategy-e1-35-20261029"
    try:
        storage = json.loads((storage_path / "report.json").read_text(encoding="utf-8"))
        proposal_csv = storage_path / "assets-8m-proposal.csv"
        current = storage["current"]
        proposed = storage["proposal"]
        current_parts = {p["name"]: p for p in current["partitions"]}
        proposed_parts = {p["name"]: p for p in proposed["partitions"]}
        protected = ("nvs", "otadata", "phy_init", "ota_0")
        if (storage.get("result") != "offline_layout_verified_runtime_and_migration_pending"
                or any(storage.get(flag) is not False for flag in (
                    "deployment_eligible", "flash_authorized", "hardware_accessed",
                    "serial_accessed", "firmware_modified_by_this_audit", "physical_flash_verified_this_audit"))
                or storage.get("configured_flash_bytes") != 16 * 1024 * 1024
                or storage.get("mmu_page_bytes") != 65536
                or storage["inputs"]["proposed_csv_sha256"] != hashlib.sha256(proposal_csv.read_bytes()).hexdigest()
                or storage["inputs"]["archived_partition_table_sha256"] != storage["inputs"]["current_regenerated_partition_table_sha256"]
                or any(current_parts[name] != proposed_parts[name] for name in protected)
                or current_parts["assets"]["start"] != proposed_parts["assets"]["start"]
                or current_parts["model"]["size_bytes"] != proposed_parts["model"]["size_bytes"]
                or proposed_parts["model"]["start"] != proposed_parts["assets"]["end_exclusive"]
                or current.get("assets_bytes") != 6_144_000
                or proposed.get("assets_bytes") != 8_388_608
                or current.get("package_bytes") != 5_538_351
                or proposed.get("package_bytes") != current.get("package_bytes")
                or current.get("assets_remaining_bytes") != current["assets_bytes"] - current["package_bytes"]
                or proposed.get("assets_remaining_bytes") != proposed["assets_bytes"] - proposed["package_bytes"]
                or current.get("tail_unallocated_bytes") != 16 * 1024 * 1024 - current_parts["model"]["end_exclusive"]
                or proposed.get("tail_unallocated_bytes") != 16 * 1024 * 1024 - proposed_parts["model"]["end_exclusive"]
                or storage["checks"]["runtime_mmap"] != "not_run"
                or storage["checks"]["new_layout_app_build"] != "not_run"
                or storage["checks"]["live_backup_and_migration"] != "not_run"):
            failures.append("E1.35 storage proposal boundary, capacity, or offline status mismatch")
        for relative, digest in storage["inputs"]["public_source_sha256"].items():
            if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != digest:
                failures.append("E1.35 source evidence changed: " + relative)
        for item in (current, proposed):
            part = next(p for p in item["partitions"] if p["name"] == "assets")
            pages = (part["end_exclusive"] + 65535) // 65536 - part["start"] // 65536
            if pages != item.get("assets_mmap_rounded_span_pages"):
                failures.append("E1.35 mmap span arithmetic mismatch")
    except (OSError, ValueError, TypeError, KeyError, StopIteration):
        failures.append("invalid or missing E1.35 storage proposal evidence")
    migration_path = ROOT / "artifacts/expression-storage-migration-e1-35b-20261029"
    try:
        migration = json.loads((migration_path / "report.json").read_text(encoding="utf-8"))
        from protocol.storage_migration import validate_manifest, validate_schema
        manifest = {key: value for key, value in migration.items() if key != "manifest_sha256"}
        validate_schema(manifest)
        # Public/CI validation checks metadata; binary validation is a separate,
        # local-only --check and never silently treats absent bytes as verified.
        validate_manifest(manifest, ROOT, require_files=False)
        manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        page = (migration_path / "index.html").read_text(encoding="utf-8")
        embedded = re.search(r'<script id="e135b-data" type="application/json">(.*?)</script>', page, re.S)
        tail = next(row for row in migration["live_backup_requirements"]["required_before_write"]
                    if row.get("id") == "old_tail_extension")
        if (migration.get("edition") != "E1.35b"
                or migration.get("result") != "offline_build_bundle_ready_live_rollback_pending"
                or migration.get("deployment_eligible") is not False
                or migration.get("flash_authorized") is not False
                or migration.get("safety", {}).get("hardware_accessed") is not False
                or migration.get("safety", {}).get("serial_accessed") is not False
                or migration.get("safety", {}).get("device_written") is not False
                or migration.get("manifest_sha256") != hashlib.sha256(manifest_raw).hexdigest()
                or migration.get("rollback", {}).get("device_complete") is not False
                or migration.get("rollback", {}).get("model_source_kind") != "E1.35a build artifact, not device readback"
                or embedded is None or json.loads(embedded[1]).get("manifest") != manifest
                or len(migration.get("source_inputs", {}).get("asset_members", [])) != migration.get("source_inputs", {}).get("asset_member_count")
                or (tail.get("offset"), tail.get("bytes")) != (0xC90000, 0x224000)
                or migration["source_inputs"]["e135a_report_sha256"] != hashlib.sha256((ROOT / "artifacts/expression-storage-preflight-e1-35a-20261029/report.json").read_bytes()).hexdigest()
                or migration["source_inputs"]["e135a_build_report_sha256"] != hashlib.sha256((ROOT / "artifacts/expression-storage-preflight-e1-35a-20261029/build-report.json").read_bytes()).hexdigest()):
            failures.append("E1.35b migration package boundary or rollback evidence mismatch")
        schema_path = ROOT / "protocol/expression-storage-migration-e1-35b.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if schema.get("properties", {}).get("flash_authorized", {}).get("const") is not False:
            failures.append("E1.35b schema no longer rejects flash authorization")
    except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration):
        failures.append("invalid or missing E1.35b migration evidence")
    capacity_path = ROOT / "artifacts/expression-capacity-build-e1-37b-20261030"
    try:
        from protocol.storage_capacity import validate_build_report
        build_raw = (capacity_path / "build-report.json").read_bytes()
        build = json.loads(build_raw)
        validate_build_report(build)
        review = json.loads((capacity_path / "report.json").read_text(encoding="utf-8"))
        page = (capacity_path / "index.html").read_text(encoding="utf-8")
        embedded = re.search(r'<script id="e137-data" type="application/json">(.*?)</script>', page, re.S)
        if (embedded is None or json.loads(embedded[1]) != review
                or "connect-src 'none'" not in page
                or review.get("schema") != "deskbot.n32r16.capacity_review.v1"
                or review.get("result") != "capacity_build_integrity_verified_runtime_pending"
                or review.get("build_report_sha256") != hashlib.sha256(build_raw).hexdigest()
                or review.get("comparison") != build["comparison"]
                or review.get("upper_half_cache") != build["upper_half_cache"]
                or review.get("safety") != build["safety"]
                or any(review.get(key) is not False for key in
                       ("deployment_eligible", "flash_authorized", "runtime_verified", "hardware_accepted"))):
            failures.append("E1.37 capacity review metadata, page or offline boundary mismatch")
        images = review.get("image_checks", {})
        if set(images) != {"xiaozhi.bin", "bootloader/bootloader.bin"}:
            failures.append("E1.37 missing capacity image comparison")
        for name, image in images.items():
            for position, key in enumerate(("control", "candidate")):
                facts = image[key]
                if ({"bytes": facts["bytes"], "sha256": facts["sha256"]}
                        != build["builds"][position]["artifacts"][name]
                        or facts.get("configured_flash_bytes") != (16 if position == 0 else 32) * 1024 * 1024
                        or facts.get("checksum_verified") is not True
                        or facts.get("appended_sha256_verified") is not True):
                    failures.append("E1.37 image metadata differs from build: " + name)
            if (image.get("only_header_capacity_and_digest_changed") is not True
                    or image.get("segment_payloads_equal") is not True
                    or image.get("first_differing_offset") != 3
                    or image.get("length_delta_bytes") != 0):
                failures.append("E1.37 executable payload change: " + name)
        # This public check compares metadata. Local --check separately reads
        # every retained image, source, dependency file and checkpoint log.
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        failures.append("invalid or missing E1.37 capacity evidence")
    addressing_path = ROOT / "artifacts/expression-addressing-e1-38-20261030"
    try:
        from protocol.storage_addressing import validate_report as validate_addressing_report
        addressing = json.loads((addressing_path / "report.json").read_text(encoding="utf-8"))
        validate_addressing_report(addressing)
        page = (addressing_path / "index.html").read_text(encoding="utf-8")
        embedded = re.search(r'<script id="e138-data" type="application/json">(.*?)</script>', page, re.S)
        if (embedded is None or json.loads(embedded[1]) != addressing or "connect-src 'none'" not in page
                or addressing["evidence"]["e137_build_report_sha256"] != hashlib.sha256(build_raw).hexdigest()):
            failures.append("E1.38 page/offline/source metadata mismatch")
        candidate = build["builds"][1]
        for row, artifact in zip(addressing["candidate_configs"], ("config/sdkconfig.json", "bootloader/config/sdkconfig.json")):
            if row["sha256"] != candidate["artifacts"][artifact]["sha256"]:
                failures.append("E1.38 configuration identity differs from retained E1.37 metadata")
        for row, artifact in zip(addressing["rollback"]["images"], ("bootloader/bootloader.bin", "xiaozhi.bin")):
            if {"bytes": row["payload_bytes"], "sha256": row["candidate_sha256"]} != candidate["artifacts"][artifact]:
                failures.append("E1.38 conditional write geometry differs from reviewed E1.37 images")
        for position, row in enumerate(addressing["live_boot_comparison"]):
            if row["built"] != build["builds"][position]["artifacts"]["bootloader/bootloader.bin"]:
                failures.append("E1.38 bootloader comparison identity mismatch")
        for row in addressing["source_evidence"]:
            if row["origin"] == "repo" and hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() != row["sha256"]:
                failures.append("E1.38 repository source changed: " + row["path"])
        # This public check validates metadata and repository source only. Private
        # capture/build/IDF bytes and rollback files are re-read by the local audit.
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        failures.append("invalid or missing E1.38 addressing evidence")
    octal_path = ROOT / "artifacts/expression-octal-e1-39-20261030"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("octal_public_review", ROOT / "scripts/audit-expression-e1-39-octal.py")
        octal_review = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(octal_review)
        octal_raw = (octal_path / "report.json").read_bytes()
        octal = json.loads(octal_raw)
        reviewed = json.loads((octal_path / "review.json").read_bytes())
        octal_review.validate_public_review(octal, reviewed)
        if (reviewed["build_report_sha256"] != hashlib.sha256(octal_raw).hexdigest()
                or reviewed["review_script_sha256"] != hashlib.sha256((ROOT / "scripts/audit-expression-e1-39-octal.py").read_bytes()).hexdigest()
                or octal["build_invocation"]["review_script_sha256"] != hashlib.sha256((ROOT / "scripts/build-expression-e1-39-octal.py").read_bytes()).hexdigest()
                or octal["build_report_e137_sha256"] != hashlib.sha256(build_raw).hexdigest()):
            failures.append("E1.39 public evidence provenance mismatch")
        page = (octal_path / "index.html").read_text(encoding="utf-8")
        embedded = re.search(r'<script id="e139-data" type="application/json">(.*?)</script>', page, re.S)
        if embedded is None or json.loads(embedded[1]) != octal or "connect-src 'none'" not in page:
            failures.append("E1.39 page/offline/metadata mismatch")
        # Public consistency only. Local --check re-reads each private image,
        # dependency, generated config, full capture and exact rollback sector.
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        failures.append("invalid or missing E1.39 explicit-Octal evidence")
    live_result_path = ROOT / "artifacts/expression-capacity-boot-e1-40-20261030/live-result.json"
    try:
        live = json.loads(live_result_path.read_text(encoding="utf-8"))
        images = live["write"]["images"]
        expected_images = (
            ("bootloader", "0x000000", 15504, "0x000000..0x004000", "bootloader/bootloader.bin"),
            ("app", "0x010000", 4476944, "0x010000..0x456000", "xiaozhi.bin"),
        )
        if (live.get("schema") != "deskbot.n32r16.capacity_boot_live_result.v1"
                or live.get("edition") != "E1.40"
                or live.get("result") != "accepted_old_layout_capacity_boot"
                or len(images) != len(expected_images)):
            failures.append("E1.40 live result identity mismatch")
        for image, expected in zip(images, expected_images):
            identifier, offset, size, erase_span, candidate_key = expected
            candidate = octal["builds"][1]["artifacts"][candidate_key]
            if (image.get("id") != identifier or image.get("offset") != offset
                    or image.get("payload_bytes") != size or image.get("erase_span") != erase_span
                    or image.get("candidate_sha256") != candidate["sha256"]
                    or image.get("payload_matches") is not True or image.get("tail_is_ff") is not True):
                failures.append("E1.40 write/readback metadata differs from the reviewed E1.39 candidate")
        protected = live["protected_readback"]
        for key in ("partition_table", "otadata", "phy_init", "assets", "speech_model", "upper_half"):
            if protected[key].get("unchanged_from_prewrite") is not True:
                failures.append("E1.40 protected range changed: " + key)
        nvs = protected["nvs"]
        if (nvs.get("flash_write_touched") is not False
                or nvs.get("change_cause_determined") is not False
                or nvs.get("changed_active_key") != "audio.output_volume"
                or nvs.get("other_active_entries_identical") is not True
                or nvs.get("active_entry_crcs_verified") is not True):
            failures.append("E1.40 NVS boundary/evidence mismatch")
        markers = live["boot_log"]["markers"]
        if (not all(markers.get(key) is True for key in
                    ("mxic_opi", "opi_str", "lcd_initialized", "touch_initialized", "assets_mounted",
                     "eaf_index_loaded", "speech_model_loaded", "application_idle", "application_listening"))
                or markers.get("fatal_marker_seen") is not False
                or live["boot_log"].get("driver_capacity_directly_queried") is not False):
            failures.append("E1.40 boot evidence or remaining verification boundary mismatch")
        acceptance = live["user_acceptance"]
        if (acceptance.get("reported") is not True or acceptance.get("reported_on") != "2026-10-31"
                or acceptance.get("result") != "all normal"
                or acceptance.get("upper_half_mmap_tested") is not False
                or acceptance.get("base_c61_tested") is not False
                or live["safety"].get("com8_accessed") is not False
                or live["safety"].get("full_flash_erase") is not False):
            failures.append("E1.40 user acceptance scope or safety boundary mismatch")
        preflight = json.loads((ROOT / "artifacts/expression-capacity-boot-e1-40-20261030/report.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "artifacts/expression-capacity-boot-e1-40-20261030/deployment-manifest.json").read_text(encoding="utf-8"))
        if (preflight.get("flash_authorized") is not False or manifest.get("device_written") is not False
                or not (ROOT / "docs/expression-capacity-boot-e1-40-live-20261030.md").is_file()):
            failures.append("E1.40 write-preflight snapshot or live record boundary mismatch")
        # Readback payloads and raw UART captures stay private. The public check
        # validates their reviewed hashes/claims, not the underlying hardware bytes.
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        failures.append("invalid or missing E1.40 live deployment result")
    for failure in failures:
        print("FAIL:", failure)
    if failures:
        return 1
    print(f"publication checks passed: {len(files)} files; links, JSON, source hashes and offline pages")
    print("Pattern scans are safeguards, not a guarantee against all possible secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
