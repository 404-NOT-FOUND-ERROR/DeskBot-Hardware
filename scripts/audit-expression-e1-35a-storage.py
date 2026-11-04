#!/usr/bin/env python3
"""Finish the offline E1.35a storage gate from exact build/archive inputs."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_budget import compare_layouts, mapped_image_segments, reserve_projection

PUBLIC = ROOT / "artifacts/expression-storage-preflight-e1-35a-20261029"
STRATEGY = ROOT / "artifacts/expression-storage-strategy-e1-35-20261029/report.json"
PACKAGE = ROOT / "tmp/expression-e1-21-package-player-20261026-run2/rollback-e1-18-exact.bin"
BUILD = PUBLIC / "build-report.json"
EXPECTED_APPS = {
    False: "7291c201819b3ad762e5647daa9d27a5d5b52b25f04b005b010bb008bd4fa51f",
    True: "f95a762df79e13a3678f0e739bfc91b9913cdbb85e0e015522d31f0a36a029c2",
}
PACKAGE_SHA = "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1"
EMBEDDED_MODEL_SHA = "4e0b56d3c987b060078d7ab1e79baf814b473ce0a4230444a7a6185e301fa632"
SOURCE_MODEL_SHA = "cec059b2f1f574523e7e53d34166b224f6ae2f393652515ae7a1ef94ed44d7ba"
PHASES = [
    {"id": "E1.35a", "name": "离线扩容预检", "status": "offline_passed_runtime_pending",
     "change": "4 份隔离构建、分区/模型路径/MMU 范围、未来预算与迁移清单",
     "acceptance": "仅布局产物变化；当前正式源码与分区保持原样；运行时仍待验证"},
    {"id": "E1.35b", "name": "原资源迁移包", "status": "next_offline_step",
     "change": "固定 E1.18 与原模型，准备旧/新布局、原区回退和版本化清单",
     "acceptance": "逐成员不变、8 MiB 填充镜像、错布局/错模型/缺回退拒绝；不刷设备"},
    {"id": "E1.35c", "name": "授权后迁移与回归", "status": "requires_separate_device_authorization",
     "change": "实机识别、完整备份、模型先复制、资源后写、分区表最后提交",
     "acceptance": "全范围回读、挂载/模型/内存证据和对话切菜单/摸头/睡醒回归；失败精确恢复"},
    {"id": "E1.35d/e", "name": "动画精选与单点替换", "status": "after_storage_acceptance",
     "change": "小鱼归档候选；完整脸与单眼分开；尾帧/局部循环和布局按最小增量验证",
     "acceptance": "全部调用有安全回退、官方 C 全帧、节点新旧边预览、用户视觉选择、单资源部署"},
    {"id": "角色表达", "name": "活动角色包与表达能力表", "status": "after_player_and_assets_acceptance",
     "change": "复用 E1.31/32 草稿与已验证资源；定义版本、共享引用、布局能力、动作意图和回执",
     "acceptance": "角色/外壳身份分离；错误版本拒绝；仲裁不接管官方 UI/触摸/DOA 所有者"},
    {"id": "底座恢复后", "name": "外壳识别与持久化", "status": "awaits_base_repair",
     "change": "固定布局 A/B/C、移除/重装/运动/未知状态；确认登记和有界档案",
     "acceptance": "训练/评估分开、不确定不误绑定；断电恢复、校验、删除/导出、写入频率上限"},
    {"id": "客户端协作", "name": "身体事实 → 角色绑定 → 受控表达", "status": "requires_client_contract_review",
     "change": "先有界只读接口，再由用户确认 shell/profile，最后接语音与表达命令",
     "acceptance": "断线/过期/乱序/重放/背压与打断测试；流式语音和菜单并发稳定"},
    {"id": "服务替换", "name": "DeepSeek 与 ASR/TTS", "status": "last_integration_phase",
     "change": "人格、世界、记忆和完整母库在客户端；设备保留身体事实与播放职责",
     "acceptance": "鉴权、重连、取消、离线降级、端到端角色切换与官方能力回归"},
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def helper(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verify_builds(build: dict) -> dict:
    if build.get("build_pass") is not True or build.get("formal_inputs_match_accepted_e126") is not True:
        raise ValueError("four builds and accepted source identity are required")
    expected_safety = {"hardware_accessed": False, "serial_accessed": False, "device_written": False,
                       "nvs_changed": False, "flash_executed": False, "formal_partition_changed": False}
    if build.get("formal_inputs_unchanged") is not True or build.get("safety") != expected_safety:
        raise ValueError("formal source/device boundary changed")
    if build.get("deployment_eligible") is not False or build.get("flash_authorized") is not False:
        raise ValueError("build report must remain offline only")
    if len(build.get("builds", [])) != 4 or len(build.get("comparisons", [])) != 2:
        raise ValueError("missing build comparison")
    archives = {}
    for row, expected in zip(build["builds"], ((False, "old"), (False, "8m"), (True, "old"), (True, "8m"))):
        if (row["observer_enabled"], row["layout"]) != expected:
            raise ValueError("build order/identity mismatch")
        enabled, layout = expected
        directory = ROOT / row["archive"]
        if directory.resolve() != (ROOT / "tmp/e135a-storage/outputs" / f"{'on' if enabled else 'off'}-{layout}").resolve():
            raise ValueError("unexpected build archive path")
        if set(row["artifacts"]) != {"xiaozhi.bin", "xiaozhi.elf", "bootloader/bootloader.bin",
                                      "partition_table/partition-table.bin", "expression_assets.bin",
                                      "srmodels/srmodels.bin", "ota_data_initial.bin", "sdkconfig", "flasher_args.json"}:
            raise ValueError("build artifact set changed")
        for name, info in row["artifacts"].items():
            target = directory / name
            target.resolve().relative_to(directory.resolve())
            if target.stat().st_size != info["bytes"] or sha(target) != info["sha256"]:
                raise ValueError("stale build artifact: " + name)
        if row["artifacts"]["xiaozhi.bin"]["sha256"] != EXPECTED_APPS[enabled]:
            raise ValueError("App differs from archived E1.26")
        if row["artifacts"]["srmodels/srmodels.bin"]["sha256"] != SOURCE_MODEL_SHA:
            raise ValueError("source model changed")
        archives[expected] = directory
    for enabled in (False, True):
        old, new = archives[(enabled, "old")], archives[(enabled, "8m")]
        a, b = load(old / "flasher_args.json"), load(new / "flasher_args.json")
        # Generated flash maps are evidence, not an approved deployment recipe.
        amended = copy.deepcopy(a)
        if amended["flash_files"].pop("0xbc8000") != "srmodels/srmodels.bin":
            raise ValueError("unexpected original model mapping")
        amended["flash_files"]["0xdec000"] = "srmodels/srmodels.bin"
        amended["model"]["offset"] = "0xdec000"
        if amended != b:
            raise ValueError("flash map changed beyond model relocation")
        for name in ("xiaozhi.bin", "xiaozhi.elf", "bootloader/bootloader.bin", "expression_assets.bin",
                     "srmodels/srmodels.bin", "ota_data_initial.bin", "sdkconfig"):
            if sha(old / name) != sha(new / name):
                raise ValueError("non-layout output changed: " + name)
    return archives


def audit() -> dict:
    strategy, builds = load(STRATEGY), load(BUILD)
    archives = verify_builds(builds)
    builder = helper("e135a_builder", "build-expression-e1-35a-storage.py")
    formal = builder.HELPER.inventory()
    previous = load(ROOT / "tmp/e1-26-full-body-report.json")
    if formal != previous["source_sha256"]:
        raise ValueError("formal source drift")
    for relative, expected in strategy["inputs"]["public_source_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise ValueError("strategy source changed: " + relative)
    proposal = ROOT / strategy["inputs"]["proposed_partition_csv"]
    if sha(proposal) != strategy["inputs"]["proposed_csv_sha256"]:
        raise ValueError("proposal CSV drift")

    # Recreate both reviewed tables with IDF and parse their binary records.
    env, python, idf = builder.environment()
    output = ROOT / "tmp/e135a-storage/table-check"
    output.mkdir(parents=True, exist_ok=True)
    table_module_spec = importlib.util.spec_from_file_location("e135a_partitions", idf / "components/partition_table/gen_esp32part.py")
    tables = importlib.util.module_from_spec(table_module_spec)
    table_module_spec.loader.exec_module(tables)
    partition_rows = {}
    for layout, csv in (("old", ROOT / "firmware/body/partitions/v2/16m.csv"), ("8m", proposal)):
        binary = output / f"{layout}.bin"
        subprocess.run([str(python), str(idf / "components/partition_table/gen_esp32part.py"),
                        "--flash-size", "16MB", str(csv), str(binary)], env=env, check=True,
                       capture_output=True)
        table = tables.PartitionTable.from_binary(binary.read_bytes())
        table.verify()
        rows = [{"name": part.name, "type": part.type, "subtype": part.subtype,
                 "start": part.offset, "size_bytes": part.size,
                 "end_exclusive": part.offset + part.size,
                 "flags": int(part.encrypted) | (int(part.readonly) << 1)} for part in table]
        expected = strategy["current" if layout == "old" else "proposal"]["partitions"]
        if rows != expected or sha(binary) != sha(archives[(True, layout)] / "partition_table/partition-table.bin"):
            raise ValueError("generated/archived/strategy partition mismatch")
        partition_rows[layout] = rows
    facts = compare_layouts(partition_rows["old"], partition_rows["8m"])
    raw = PACKAGE.read_bytes()
    if len(raw) != 5538351 or sha(PACKAGE) != PACKAGE_SHA:
        raise ValueError("accepted E1.18 package identity mismatch")
    package_audit = helper("e135a_package", "audit-expression-e1-16-repack.py")
    members = package_audit.AUDIT.parse_package_bytes(raw, modern=False)["members"]
    index = json.loads(members["index.json"])
    embedded_name = index.get("srmodels")
    if embedded_name != "srmodels.bin" or hashlib.sha256(members[embedded_name]).hexdigest() != EMBEDDED_MODEL_SHA:
        raise ValueError("embedded speech model changed")
    surprised = next(item for item in index["emoji_collection"] if item["name"] == "surprised")
    if surprised["file"] != "shocked.eaf":
        raise ValueError("accepted visual selection changed")
    app = archives[(True, "old")] / "xiaozhi.bin"
    budget = reserve_projection(len(raw), app.stat().st_size)
    segments = mapped_image_segments(app.read_bytes())
    bus_page_spans = {kind: sum(row["span_pages"] for row in segments if row["kind"] == kind)
                      for kind in ("DROM", "IROM")}
    sources = ("main/application.cc", "main/assets.cc", "main/audio/audio_service.cc",
               "main/audio/wake_words/afe_wake_word.cc", "main/audio/processors/afe_audio_processor.cc")
    component_root = ROOT / "tmp/e135a-storage/p/managed_components"
    evidence = {"firmware/body/" + name: sha(ROOT / "firmware/body" / name) for name in sources}
    for relative in ("espressif__esp_mmap_assets/esp_mmap_assets.c", "espressif__esp-sr/src/model_path.c",
                     "espressif2022__esp_emote_expression/src/emote_load.c"):
        evidence["locked_components/" + relative] = sha(component_root / relative)
    for relative in ("components/esp_mm/esp_mmu_map.c", "components/esp_psram/system_layer/esp_psram.c",
                     "components/soc/esp32s3/include/soc/ext_mem_defs.h"):
        evidence["esp_idf_5_5_5/" + relative] = sha(idf / relative)
    result = {
        "schema": "deskbot.expression.storage-preflight.v1", "edition": "E1.35a", "date": "2026-10-29",
        "result": "offline_gate_passed_live_mmap_and_migration_pending", "audit_pass": True,
        "deployment_eligible": False, "flash_authorized": False,
        "safety": {"hardware_accessed": False, "serial_accessed": False, "device_written": False,
                   "formal_firmware_changed": False, "formal_partition_changed": False, "nvs_written": False},
        "input_reports": {STRATEGY.relative_to(ROOT).as_posix(): sha(STRATEGY), BUILD.relative_to(ROOT).as_posix(): sha(BUILD)},
        "source_manifest_sha256": sha(ROOT / "firmware/body/SOURCE_MANIFEST.sha256"),
        "audit_tool_sha256": {name: sha(ROOT / name) for name in (
            "scripts/audit-expression-e1-35a-storage.py", "scripts/build-expression-e1-35a-storage.py",
            "scripts/expression-e1-35a-storage.template.html", "protocol/storage_budget.py")},
        "source_evidence_sha256": evidence, "configured_flash_bytes": 16777216,
        "physical_flash_verified_this_stage": False,
        "layout": facts, "partitions": partition_rows, "future_budget": budget,
        "build_summary": [{"observer_enabled": row["observer_enabled"], "layout": row["layout"],
                           "app": row["artifacts"]["xiaozhi.bin"], "table": row["artifacts"]["partition_table/partition-table.bin"]}
                          for row in builds["builds"]],
        "accepted_assets": {"bytes": len(raw), "sha256": PACKAGE_SHA, "member_count": len(members),
                            "surprised_file": "shocked.eaf", "generated_build_assets_are_deployment_payload": False,
                            "embedded_model": {"file": embedded_name, "bytes": len(members[embedded_name]), "sha256": EMBEDDED_MODEL_SHA},
                            "standalone_build_model": {"bytes": 582107, "sha256": SOURCE_MODEL_SHA},
                            "live_standalone_model_full_partition": "backup_required_in_E1.35c"},
        "mmap": {"page_bytes": 65536, "soc_entry_count_per_documented_table": 512, "app_mapped_segments": segments,
                 "app_bus_page_span_sums": bus_page_spans, "candidate_assets_span_pages": facts["candidate_assets_span_pages"],
                 "component_guard_minimum_reported_free_pages": 129,
                 "guard_requires_reported_free_pages_greater_than": 128,
                 "guard_pass_does_not_prove_contiguous_mapping": True,
                 "normal_model_path": "assets constructor mounts full partition; Apply loads index.srmodels and passes non-null models",
                 "fallback_model_path": "audio/wake/AFE null-model paths can call esp_srmodel_init(model)",
                 "actual_psram_bytes": None, "live_free_mmap_pages": None, "live_largest_block_bytes": None,
                 "runtime_mmap_pass": None,
                 "runtime_probe_requirements": ["reported DROM free pages before and after assets mount",
                                                "contiguous DROM mapping result and handle",
                                                "PSRAM total/free/largest block before and after mount",
                                                "independent model fallback decision and success", "stable UI/audio after mount"],
                 "runtime_probe_pass": None},
        "future_tail": {"unallocated_bytes": facts["candidate_tail_bytes"], "allocated_this_stage": 0,
                        "possible_profile_store_bytes_for_later_review": 256 * 1024,
                        "remaining_if_profile_proposal_adopted_bytes": facts["candidate_tail_bytes"] - 256 * 1024,
                        "full_second_app_fits": False, "full_second_assets_fits": False,
                        "atomic_resource_update_implemented": False, "automatic_layout_rollback_implemented": False},
        "pipeline": PHASES,
        "limitations": ["No physical Flash, PSRAM, free-page, heap or largest-block measurement in this stage.",
                        "Generated build assets are original source resources, not accepted E1.18; do not use generic flash maps.",
                        "Tail and assets reserve are planning proposals, not firmware-enforced quotas.",
                        "Migration is not power-loss atomic; table-last is ordering discipline, not automatic rollback.",
                        "Base repair and all C61/whole-device regressions remain outside this offline result."]}
    validate_report(result)
    return result


def validate_report(value: dict) -> None:
    if value.get("schema") != "deskbot.expression.storage-preflight.v1" or value.get("edition") != "E1.35a":
        raise ValueError("unexpected storage report identity")
    if value.get("audit_pass") is not True or value.get("deployment_eligible") is not False or value.get("flash_authorized") is not False:
        raise ValueError("offline audit boundary")
    safety_names = {"hardware_accessed", "serial_accessed", "device_written", "formal_firmware_changed",
                    "formal_partition_changed", "nvs_written"}
    if set(value.get("safety", {})) != safety_names or any(value["safety"][name] is not False for name in safety_names):
        raise ValueError("device/source boundary")
    if value.get("physical_flash_verified_this_stage") is not False:
        raise ValueError("physical Flash not measured")
    facts = compare_layouts(value["partitions"]["old"], value["partitions"]["8m"])
    if facts != value["layout"] or facts["runtime_mmap_proven"]:
        raise ValueError("layout arithmetic/runtime claim")
    current = value["accepted_assets"]
    expected_rows = ((False, "old"), (False, "8m"), (True, "old"), (True, "8m"))
    summary = value["build_summary"]
    if len(summary) != 4:
        raise ValueError("four build records required")
    for row, (enabled, layout) in zip(summary, expected_rows):
        if row["observer_enabled"] is not enabled or row["layout"] != layout or row["app"]["sha256"] != EXPECTED_APPS[enabled]:
            raise ValueError("App/build identity")
    enabled = [row for row in value["build_summary"] if row["observer_enabled"] and row["layout"] == "old"]
    expected = reserve_projection(current["bytes"], enabled[0]["app"]["bytes"])
    if value["future_budget"] != expected:
        raise ValueError("future budget arithmetic")
    if current["sha256"] != PACKAGE_SHA or current["embedded_model"]["sha256"] != EMBEDDED_MODEL_SHA:
        raise ValueError("resource identity")
    if value["mmap"]["runtime_probe_pass"] is not None:
        raise ValueError("runtime evidence must remain unknown")
    if any(value["mmap"][name] is not None for name in ("actual_psram_bytes", "live_free_mmap_pages", "live_largest_block_bytes", "runtime_mmap_pass")):
        raise ValueError("runtime memory was not measured")
    if value["future_tail"]["allocated_this_stage"] != 0 or value["future_tail"]["full_second_assets_fits"]:
        raise ValueError("tail/rollback claim")


def render(report: dict) -> str:
    text = (ROOT / "scripts/expression-e1-35a-storage.template.html").read_text(encoding="utf-8")
    rows = "".join(f'<tr><td>{html.escape(item["id"])}</td><td>{html.escape(item["name"])}</td>'
                   f'<td>{html.escape(item["change"])}</td><td>{html.escape(item["acceptance"])}</td></tr>' for item in report["pipeline"])
    builds = "".join(f'<tr><td>{"启用" if row["observer_enabled"] else "关闭"}</td><td>{row["layout"]}</td>'
                     f'<td>{row["app"]["bytes"]:,} B</td><td><code>{row["app"]["sha256"][:16]}…</code></td></tr>'
                     for row in report["build_summary"])
    return text.replace("%%PIPELINE%%", rows).replace("%%BUILDS%%", builds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify report/page without changing them")
    args = parser.parse_args()
    report = audit()
    contents = {"report.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n", "index.html": render(report)}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for filename, content in contents.items():
        target = PUBLIC / filename
        if args.check:
            if target.read_text(encoding="utf-8") != content:
                raise ValueError("stale storage report/page: " + filename)
        else:
            target.write_text(content, encoding="utf-8")
    print("E1.35a offline gate passed; runtime mmap and device migration pending; no serial/device access.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("E1.35a gate blocked: " + str(error), file=sys.stderr)
        raise SystemExit(2)
