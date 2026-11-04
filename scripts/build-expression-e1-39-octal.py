#!/usr/bin/env python3
"""Build and audit explicit-Octal 16/32 MiB candidates without hardware I/O."""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_addressing import rollback_ranges
from protocol.storage_capacity import image_facts
from protocol.storage_live_backup import require_independent_verify, verify_capture_files
from protocol.storage_n32r16 import CAPTURE_SHA256, FORMAL_CSV_SHA256, OLD_TABLE_SHA256, PHYSICAL_FLASH_BYTES, PSRAM_BYTES
from protocol.storage_octal import (
    EXPECTED_RESULT, MIB, NO_WRITE_SAFETY, candidate_mmap, compare_images,
    config_diff, effective_octal_config, expected_explicit_config, parse_config, sha,
    validate_report,
)


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "e139_build_helper", ROOT / "scripts/build-expression-e1-35a-storage.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HELPER = load_helper()
BODY = HELPER.HELPER
SOURCE = ROOT / "firmware/body"
WORK = ROOT / "tmp/e139-octal"
PUBLIC = ROOT / "artifacts/expression-octal-e1-39-20261030"
OUTPUT = PUBLIC / "report.json"
BUILD_REPORT = ROOT / "artifacts/expression-capacity-build-e1-37b-20261030/build-report.json"
CAPTURE = ROOT.parent.parent / "tmp/e135c-live-20261029"
FILES = HELPER.FILES + ("config/sdkconfig.json", "bootloader/config/sdkconfig.json", "xiaozhi.map",
                       "bootloader/bootloader.elf", "bootloader/bootloader.map", "compile_commands.json")


def write_json(path: Path, value: dict) -> None:
    HELPER.write_json(path, value)


def private_directory(path: Path) -> None:
    HELPER.private_directory(path)


def remove_line(text: str, key: str) -> str:
    pattern = re.compile(r"(?m)^(?:# )?" + re.escape(key) + r"(?:=.*| is not set)\r?\n?")
    text, count = pattern.subn("", text)
    if count > 1:
        raise ValueError("duplicate configuration key: " + key)
    return text


def explicit_octal_config(snapshot: str, mib: int) -> str:
    if mib not in (16, 32):
        raise ValueError("only 16 and 32 MiB candidates are allowed")
    keys = (
        "CONFIG_ESPTOOLPY_OCT_FLASH", "CONFIG_ESPTOOLPY_FLASH_MODE_AUTO_DETECT",
        "CONFIG_ESPTOOLPY_FLASHMODE_QIO", "CONFIG_ESPTOOLPY_FLASHMODE_QOUT",
        "CONFIG_ESPTOOLPY_FLASHMODE_DIO", "CONFIG_ESPTOOLPY_FLASHMODE_DOUT",
        "CONFIG_ESPTOOLPY_FLASHMODE_OPI", "CONFIG_ESPTOOLPY_FLASHMODE",
        "CONFIG_FLASHMODE_QIO", "CONFIG_FLASHMODE_QOUT", "CONFIG_FLASHMODE_DIO", "CONFIG_FLASHMODE_DOUT",
        "CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_STR",
        "CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_DTR", "CONFIG_ESPTOOLPY_FLASHSIZE_1MB",
        "CONFIG_ESPTOOLPY_FLASHSIZE_2MB", "CONFIG_ESPTOOLPY_FLASHSIZE_4MB",
        "CONFIG_ESPTOOLPY_FLASHSIZE_8MB", "CONFIG_ESPTOOLPY_FLASHSIZE_16MB",
        "CONFIG_ESPTOOLPY_FLASHSIZE_32MB", "CONFIG_ESPTOOLPY_FLASHSIZE_64MB",
        "CONFIG_ESPTOOLPY_FLASHSIZE_128MB", "CONFIG_ESPTOOLPY_FLASHSIZE",
    )
    for key in keys:
        snapshot = remove_line(snapshot, key)
    snapshot = snapshot.rstrip() + "\n"
    snapshot += "CONFIG_ESPTOOLPY_OCT_FLASH=y\n"
    snapshot += "# CONFIG_ESPTOOLPY_FLASH_MODE_AUTO_DETECT is not set\n"
    snapshot += "# CONFIG_ESPTOOLPY_FLASHMODE_QIO is not set\n"
    snapshot += "# CONFIG_ESPTOOLPY_FLASHMODE_QOUT is not set\n"
    snapshot += "# CONFIG_ESPTOOLPY_FLASHMODE_DIO is not set\n"
    snapshot += "# CONFIG_ESPTOOLPY_FLASHMODE_DOUT is not set\n"
    snapshot += "CONFIG_ESPTOOLPY_FLASHMODE_OPI=y\n"
    snapshot += "CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_STR=y\n"
    snapshot += "# CONFIG_ESPTOOLPY_FLASH_SAMPLE_MODE_DTR is not set\n"
    for candidate in (1, 2, 4, 8, 16, 32, 64, 128):
        state = "y" if candidate == mib else "n"
        if state == "y":
            snapshot += f"CONFIG_ESPTOOLPY_FLASHSIZE_{candidate}MB=y\n"
        else:
            snapshot += f"# CONFIG_ESPTOOLPY_FLASHSIZE_{candidate}MB is not set\n"
    snapshot += f'CONFIG_ESPTOOLPY_FLASHSIZE="{mib}MB"\n'
    return snapshot


def environment() -> tuple[dict, Path, Path]:
    idf = Path(r"D:\Espressif\frameworks\esp-idf-v5.5.5")
    python = Path(r"D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe")
    tool_bins = [r"D:\Espressif\tools\cmake\3.30.2\bin", r"D:\Espressif\tools\ninja\1.12.1",
                 r"D:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin"]
    env = dict(os.environ)
    env.update({"IDF_PATH": str(idf), "IDF_TOOLS_PATH": r"D:\Espressif", "IDF_PYTHON_ENV_PATH": str(python.parent.parent),
                "ESP_IDF_VERSION": "5.5.5", "IDF_TARGET": "esp32s3", "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8", "IDF_COMPONENT_CHECK_NEW_VERSION": "0",
                "CMAKE_NINJA_FORCE_RESPONSE_FILE": "1"})
    env["PATH"] = os.pathsep.join(tool_bins + [str(python.parent), env.get("PATH", "")])
    return env, python, idf


def copy_artifacts(build: Path, archive: Path) -> dict:
    private_directory(archive)
    for name in FILES:
        destination = archive / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build / name, destination)
    return {name: {"bytes": (archive / name).stat().st_size, "sha256": sha((archive / name).read_bytes())}
            for name in FILES}


def build_one(mib: int, project: Path, inputs: dict, locked: dict, env: dict, python: Path, idf: Path) -> dict:
    label = f"{mib}m"
    build = WORK / "o"
    private_directory(build)
    sdkconfig = build / "sdkconfig"
    snapshot = (SOURCE / "sdkconfig.defaults.o2f2").read_text(encoding="utf-8")
    for key in ("DESKBOT_EXPRESSION_OBSERVER_ENABLE", "APP_REPRODUCIBLE_BUILD"):
        snapshot = remove_line(snapshot, "CONFIG_" + key)
    snapshot += "\nCONFIG_APP_REPRODUCIBLE_BUILD=y\nCONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE=y\n"
    sdkconfig.write_text(explicit_octal_config(snapshot, mib), encoding="utf-8")
    common = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
              "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
              "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
    steps = []
    for action in ("reconfigure", "build"):
        if action == "build":
            BODY.quote_image_effects_link_path(build, project)
        log = WORK / f"{label}-{action}.log"
        if log.exists():
            attempts = WORK / "attempts"
            private_directory(attempts)
            index = 1
            while (attempts / f"{label}-{action}-{index}.log").exists():
                index += 1
            shutil.copy2(log, attempts / f"{label}-{action}-{index}.log")
        step = BODY.run(common + [action], project, env, log)
        steps.append({"returncode": step["returncode"], "log": log.relative_to(ROOT).as_posix(),
                      "log_sha256": step["log_sha256"]})
        if BODY.normalized_lock(yaml.safe_load((project / "dependencies.lock").read_bytes())) != BODY.normalized_lock(locked):
            raise ValueError("component manager changed the pinned dependency lock")
    app_config = json.loads((build / "config/sdkconfig.json").read_text(encoding="utf-8"))
    boot_config = json.loads((build / "bootloader/config/sdkconfig.json").read_text(encoding="utf-8"))
    effective_octal_config(app_config, mib)
    effective_octal_config(boot_config, mib)
    if app_config.get("DESKBOT_EXPRESSION_OBSERVER_ENABLE") is not True:
        raise ValueError("observer was not enabled in explicit-Octal App")
    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    observer_members = sum(Path(item["file"]).name == "expression_runtime_observer.cc" for item in commands)
    if observer_members != 1:
        raise ValueError("expected one observer source member")
    HELPER.verify_source(project, inputs, FORMAL_CSV_SHA256)
    archive = WORK / "outputs" / label
    artifacts = copy_artifacts(build, archive)
    if artifacts["partition_table/partition-table.bin"]["sha256"] != OLD_TABLE_SHA256:
        raise ValueError("explicit-Octal build changed the formal partition table")
    app_facts = image_facts((archive / "xiaozhi.bin").read_bytes())
    boot_facts = image_facts((archive / "bootloader/bootloader.bin").read_bytes())
    if app_facts["configured_flash_bytes"] != mib * MIB or boot_facts["configured_flash_bytes"] != mib * MIB:
        raise ValueError("image header capacity does not match explicit configuration")
    result = {
        "configured_flash_bytes": mib * MIB,
        "observer_source_members": observer_members,
        "formal_source_unchanged": True,
        "app_partition_free_bytes": 6000 * 1024 - artifacts["xiaozhi.bin"]["bytes"],
        "archive": archive.relative_to(ROOT).as_posix(),
        "steps": steps,
        "artifacts": artifacts,
        "effective_app_config": effective_octal_config(app_config, mib),
        "effective_bootloader_config": effective_octal_config(boot_config, mib),
        "app_image": app_facts,
        "bootloader_image": boot_facts,
    }
    write_json(WORK / f"{label}-checkpoint.json", result)
    print(f"{label} explicit-Octal build passed; App={artifacts['xiaozhi.bin']['bytes']:,} bytes", flush=True)
    return result


def validate_checkpoint(mib: int) -> dict | None:
    path = WORK / f"{mib}m-checkpoint.json"
    if not path.is_file():
        return None
    row = json.loads(path.read_text(encoding="utf-8"))
    if (row.get("configured_flash_bytes") != mib * MIB
            or row.get("archive") != (WORK / f"outputs/{mib}m").relative_to(ROOT).as_posix()
            or set(row.get("artifacts", {})) != set(FILES)):
        raise ValueError("checkpoint identity mismatch")
    if row.get("effective_app_config") != expected_explicit_config(mib) or row.get("effective_bootloader_config") != expected_explicit_config(mib):
        raise ValueError("checkpoint configuration mismatch")
    if row.get("formal_source_unchanged") is not True or row.get("observer_source_members") != 1:
        raise ValueError("checkpoint source boundary mismatch")
    for name, facts in row["artifacts"].items():
        path = ROOT / row["archive"] / name
        if not path.is_file() or {"bytes": path.stat().st_size, "sha256": sha(path.read_bytes())} != facts:
            raise ValueError("checkpoint artifact changed: " + name)
    if len(row.get("steps", [])) != 2:
        raise ValueError("checkpoint build/configure logs missing")
    for step, action in zip(row["steps"], ("reconfigure", "build")):
        log = WORK / f"{mib}m-{action}.log"
        if (step.get("returncode") != 0 or step.get("log") != log.relative_to(ROOT).as_posix()
                or step.get("log_sha256") != sha(log.read_bytes())):
            raise ValueError("checkpoint build-log identity changed")
    archive = ROOT / row["archive"]
    for name, key in (("config/sdkconfig.json", "effective_app_config"),
                      ("bootloader/config/sdkconfig.json", "effective_bootloader_config")):
        actual = effective_octal_config(json.loads((archive / name).read_bytes()), mib)
        if actual != row[key]:
            raise ValueError("checkpoint does not describe actual generated configuration")
    for name, key in (("xiaozhi.bin", "app_image"), ("bootloader/bootloader.bin", "bootloader_image")):
        if image_facts((archive / name).read_bytes()) != row[key]:
            raise ValueError("checkpoint image integrity mismatch")
    commands = json.loads((archive / "compile_commands.json").read_bytes())
    if sum(Path(item["file"]).name == "expression_runtime_observer.cc" for item in commands) != 1:
        raise ValueError("checkpoint observer source membership mismatch")
    return row


def capture_rollback(builds: list[dict], check_only: bool = False) -> dict:
    index_path = CAPTURE / "capture-index.json"
    if not index_path.is_file():
        raise ValueError("reviewed E1.35c capture is missing")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    raw = verify_capture_files(CAPTURE, index)
    if len(raw) != PHYSICAL_FLASH_BYTES or sha(raw) != CAPTURE_SHA256:
        raise ValueError("capture is not the reviewed 32 MiB full image")
    verify_log = (CAPTURE / "verify-full-keep.log").read_text(encoding="utf-8")
    require_independent_verify(verify_log, index["mac"], len(raw), index["full_image"]["path"])
    images = [{"id": "bootloader", "offset": 0, "payload_bytes": builds[1]["artifacts"]["bootloader/bootloader.bin"]["bytes"],
               "candidate_sha256": builds[1]["artifacts"]["bootloader/bootloader.bin"]["sha256"]},
              {"id": "app", "offset": 0x10000, "payload_bytes": builds[1]["artifacts"]["xiaozhi.bin"]["bytes"],
               "candidate_sha256": builds[1]["artifacts"]["xiaozhi.bin"]["sha256"]}]
    ranges = rollback_ranges(raw, images)
    rollback_dir = WORK / "rollback"
    if not check_only:
        private_directory(rollback_dir)
    files = {}
    for row in ranges:
        path = rollback_dir / f"{row['id']}-live-sectors.bin"
        if not check_only:
            path.write_bytes(raw[row["offset"]:row["end_exclusive"]])
        files[row["id"]] = path.read_bytes()
        row["private_file"] = path.relative_to(ROOT).as_posix()
    from protocol.storage_addressing import validate_rollback_files
    plan = {"images": images, "ranges": [{k: v for k, v in row.items() if k != "private_file"} for row in ranges]}
    validate_rollback_files(raw, plan, files)
    return {"scope": "conditional_e139_explicit_octal_pair_only", "source_capture_sha256": sha(raw),
            "images": images, "ranges": ranges, "files_verified_against_capture": True,
            "prewrite_identity_and_digest_refresh_required": True, "executable_flash_script_generated": False,
            "capture_date": "2026-10-29"}


def render(report: dict) -> str:
    e = html.escape
    build_rows = "".join(
        f"<tr><td>{mib} MiB</td><td>{row['artifacts']['xiaozhi.bin']['bytes']:,} B</td>"
        f"<td>{row['artifacts']['bootloader/bootloader.bin']['bytes']:,} B</td>"
        f"<td><code>{row['artifacts']['xiaozhi.bin']['sha256']}</code></td>"
        f"<td><code>{row['effective_app_config']['ESPTOOLPY_FLASHMODE']}</code>; Octal 开；"
        f"cache32 {'开' if row['effective_app_config']['BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH'] else '关'}</td></tr>"
        for mib, row in zip((16, 32), report["builds"])
    )
    diff_rows = "".join(
        f"<tr><td>{e(name)}</td><td>{result['differing_bytes_in_shared_range']:,}</td>"
        f"<td>{result['length_delta_bytes']:+,}</td><td>{'一致' if result['segments_equal'] else '有变化，需运行时回归'}</td></tr>"
        for name, result in report["image_comparisons"].items()
    )
    config_rows = "".join(
        f"<tr><td><code>{e(key)}</code></td><td>{e(str(change['old']))}</td><td>{e(str(change['candidate']))}</td></tr>"
        for key, change in report["config_diffs_vs_e137_32m"].items()
    )
    rollback_rows = "".join(
        f"<tr><td>{e(row['id'])}</td><td><code>0x{row['offset']:X} → 0x{row['end_exclusive']:X}</code></td>"
        f"<td>{row['bytes']:,} B</td><td><code>{row['sha256']}</code></td></tr>"
        for row in report["rollback"]["ranges"]
    )
    data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'unsafe-inline';script-src 'unsafe-inline';connect-src 'none'"><title>E1.39 · 显式 Octal 候选</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f7faf8;color:#24332d;font:15px/1.65 system-ui,sans-serif}}main{{max-width:1160px;margin:auto;padding:28px}}section{{padding:22px 0;border-bottom:1px solid #d9e3dd}}h1{{font-size:30px;line-height:1.35}}h2{{font-size:21px}}.muted{{color:#5d6e66}}.ok{{color:#146444}}.pending{{color:#96500b}}.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}.card{{border-top:3px solid #4b8c73;padding-top:10px}}.card strong{{display:block;font-size:24px}}.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:680px}}th,td{{padding:11px;text-align:left;vertical-align:top;border-bottom:1px solid #d9e3dd}}code{{font:12px/1.55 Consolas,monospace;overflow-wrap:anywhere}}details{{padding:10px 0;border-bottom:1px solid #d9e3dd}}summary{{cursor:pointer}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf2ef;padding:12px;font:12px/1.5 Consolas,monospace}}.flow{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.flow div{{border-top:2px solid #a7beb3;padding:10px 0}}@media(max-width:680px){{main{{padding:16px}}h1{{font-size:25px}}.cards,.flow{{grid-template-columns:1fr 1fr}}}}@media(max-width:440px){{.cards,.flow{{grid-template-columns:1fr}}}}</style></head><body><main>
<section><div class="muted">DeskBot · E1.39 · 2026-10-30 · 离线构建与容量预检</div><h1>显式 Octal 16/32 MiB 候选</h1><p class="ok">两份隔离构建通过：显式 Octal、OPI、STR、旧分区表与产品源码边界均已核对。</p><p class="pending">本页不代表设备已刷写、已重启或已验证上半区播放；下一步仍需单独授权的成对容量启动。</p><div class="cards"><div class="card">物理 Flash<strong>32 MiB · MXIC Octal</strong>C2:8039 · OPI_STR 现场证据</div><div class="card">候选配置<strong>OPI + STR</strong>关闭自动识别，避免把运行时切换当作编译保证</div><div class="card">上半区缓存<strong>32 MiB 派生开启</strong>Octal cache32 开，Quad 实验开关关闭</div></div></section>
<section><h2>① 双构建结果</h2><div class="table"><table><thead><tr><th>候选</th><th>App</th><th>bootloader</th><th>App SHA-256</th><th>关键有效配置</th></tr></thead><tbody>{build_rows}</tbody></table></div><p class="muted">16 MiB 是显式 Octal 控制版，32 MiB 是成对容量候选。两版都保留 E1.26 observer 和旧正式分区；构建成功不等于启动成功。</p><p class="pending">构建参考资源为 5,889,001 B，设备 E1.18 活动包为 5,538,351 B。容量启动只审阅 bootloader + App，通用构建 flash_args 不能直接执行。</p></section>
<section><h2>② 与 E1.37 QIO/自动识别候选的差异</h2><div class="table"><table><thead><tr><th>配置</th><th>E1.37 32 MiB</th><th>E1.39 显式 Octal 32 MiB</th></tr></thead><tbody>{config_rows}</tbody></table></div><p>显式 Octal 让 IDF 自动派生 Octal cache32；未启用 Quad cache32，也没有打开 <code>IDF_EXPERIMENTAL_FEATURES</code>。App 与 bootloader 的配置均单独审计。</p></section>
<section><h2>③ 镜像差异不是“只改容量头”</h2><div class="table"><table><thead><tr><th>镜像</th><th>共享范围差异字节</th><th>长度变化</th><th>可执行段</th></tr></thead><tbody>{diff_rows}</tbody></table></div><p class="pending">显式 Octal 会进入启动与 Flash 驱动的条件编译，不能把新镜像当作 E1.37 只换容量字段的替代品；运行时启动、读写、mmap、模型和官方 UI 回归仍必须分开验证。</p></section>
<section><h2>④ 资源寻址预检</h2><div class="flow"><div>16 MiB 显式 Octal<br><strong>上半区 guard 拒绝</strong></div><div>32 MiB 显式 Octal<br><strong>源码 guard 放行</strong></div><div>下一步<br><strong>成对容量启动</strong></div><div>启动后<br><strong>跨界 mmap / EAF</strong></div></div><p>32 MiB 候选只说明编译时具备 Octal cache32 路径；连续 MMU 页、EAF 整区 mmap、模型双路径、内存峰值和 LCD 播放没有被离线构建证明。</p></section>
<section><h2>⑤ 按新镜像长度重算的现场回退</h2><div class="table"><table><thead><tr><th>镜像</th><th>完整扇区范围</th><th>字节</th><th>现场快照 SHA-256</th></tr></thead><tbody>{rollback_rows}</tbody></table></div><p class="muted">回退片段来自 E1.35c 32 MiB 全片快照，并已按 E1.39 32 MiB 候选重新计算。写入前必须重新识别 COM4/芯片、刷新全片快照和保护摘要；本轮没有生成可执行刷写脚本。</p></section>
<section><h2>结论与下一阶段</h2><p class="pending">E1.39 离线预检通过，部署资格仍为 false。接下来先做获授权的成对 bootloader + App 容量启动验证，确认驱动容量、OPI_STR、资源挂载和官方交互；通过后才进入上半区资源迁移。</p><p><a href="../../docs/expression-octal-e1-39-20261030.md">审计说明</a> · <a href="../../docs/roadmap.md">开发管线</a> · <a href="report.json">机器报告</a></p></section>
<section><h2>机器报告</h2><details><summary>展开 JSON</summary><pre id="e139-json"></pre></details></section><script id="e139-data" type="application/json">{data}</script><script>const d=JSON.parse(document.getElementById('e139-data').textContent);document.getElementById('e139-json').textContent=JSON.stringify(d,null,2);</script>
</main></body></html>'''


def make_report(builds: list[dict], check_only: bool = False) -> dict:
    inputs = BODY.inventory()
    locked = yaml.safe_load((SOURCE / "dependencies.lock").read_bytes())
    HELPER.verify_source(WORK / "p", inputs, FORMAL_CSV_SHA256)
    if BODY.normalized_lock(yaml.safe_load((WORK / "p/dependencies.lock").read_bytes())) != BODY.normalized_lock(locked):
        raise ValueError("retained dependency lock changed")
    components = BODY.validate_components(WORK / "p", locked)
    old_build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    old32 = old_build["builds"][1]
    old_config = parse_config((ROOT / old32["archive"] / "sdkconfig").read_text(encoding="utf-8"))
    new_config = parse_config((ROOT / builds[1]["archive"] / "sdkconfig").read_text(encoding="utf-8"))
    rollback = capture_rollback(builds, check_only)
    protected_names = ("partition_table/partition-table.bin", "expression_assets.bin", "srmodels/srmodels.bin", "ota_data_initial.bin")
    protected = {name: builds[0]["artifacts"][name] == builds[1]["artifacts"][name] for name in protected_names}
    if any(builds[1]["artifacts"][name] != old32["artifacts"][name] for name in protected_names):
        raise ValueError("protected output differs from retained E1.37 reference")
    report = {
        "schema": "deskbot.n32r16.explicit_octal_build.v1", "edition": "E1.39", "date": "2026-10-30",
        "result": EXPECTED_RESULT, "physical_flash_bytes": PHYSICAL_FLASH_BYTES, "psram_bytes": PSRAM_BYTES,
        "hardware": {"manufacturer": "Macronix (MXIC)", "jedec": "C2:8039", "physical_flash": "octal", "runtime_io": "opi_str"},
        "formal_source_unchanged": True, "formal_partition_unchanged": True,
        "live_snapshot_current": False,
        "work_dir": WORK.relative_to(ROOT).as_posix(),
        "source_manifest_sha256": sha((SOURCE / "SOURCE_MANIFEST.sha256").read_bytes()),
        "components": {"count": components["count"], "actual_contents_match_lock": True,
                       "local_touch_path_is_only_lock_difference": True},
        "build_invocation": {"original_script_sha256": json.loads((WORK / "inputs.json").read_bytes())["script_sha256"],
                             "review_script_sha256": sha(Path(__file__).read_bytes()),
                             "inputs_sha256": sha((WORK / "inputs.json").read_bytes()),
                             "checkpoint_sha256": {f"{mib}m": sha((WORK / f"{mib}m-checkpoint.json").read_bytes()) for mib in (16, 32)}},
        "build_report_e137_sha256": sha(BUILD_REPORT.read_bytes()), "builds": builds,
        "protected_outputs": protected,
        "config_diffs_vs_e137_32m": config_diff(old_config, new_config),
        "image_comparisons": {"xiaozhi.bin": compare_images((ROOT / old32["archive"] / "xiaozhi.bin").read_bytes(),
                                                                (ROOT / builds[1]["archive"] / "xiaozhi.bin").read_bytes()),
                              "bootloader/bootloader.bin": compare_images((ROOT / old32["archive"] / "bootloader/bootloader.bin").read_bytes(),
                                                                            (ROOT / builds[1]["archive"] / "bootloader/bootloader.bin").read_bytes())},
        "cache_preflight": {"16m_octal_cache32": False, "32m_octal_cache32": True,
                             "quad_cache32": False, "experimental_features": False,
                             "mmap_16m_upper": candidate_mmap(16), "mmap_32m_upper": candidate_mmap(32),
                             "upper_half_runtime_verified": False, "eaf_runtime_verified": False},
        "rollback": rollback,
        "deployment_eligible": False, "flash_authorized": False, "runtime_verified": False, "hardware_accepted": False,
        "safety": dict(NO_WRITE_SAFETY),
        "limitations": ["This is an offline candidate; no serial port, reset, Flash write, C61 or NVS access occurred.",
                        "Build assets are original source references, not the accepted E1.18 deployment package; generic flash arguments must not be executed.",
                        "Explicit Octal changes conditional boot/driver code, so image differences require runtime regression.",
                        "Upper-half cache guard passing is not proof of EAF mmap, model loading, memory peaks or LCD playback.",
                        "The commercial MXIC part number and independent SFDP compatibility evidence remain pending."]
    }
    validate_report(report)
    return report


def main() -> int:
    global WORK, PUBLIC, OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--review-completed", action="store_true",
                        help="review completed hash-verified builds without compiling")
    parser.add_argument("--work-dir", default="tmp/e139-octal-20261030",
                        help="isolated build directory under tmp/")
    parser.add_argument("--output", default="artifacts/expression-octal-e1-39-20261030/report.json",
                        help="report JSON under artifacts/")
    args = parser.parse_args()
    WORK = (ROOT / args.work_dir).resolve()
    WORK.relative_to((ROOT / "tmp").resolve())
    OUTPUT = (ROOT / args.output).resolve()
    PUBLIC = OUTPUT.parent
    PUBLIC.relative_to((ROOT / "artifacts").resolve())
    private_directory(WORK)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    inputs = BODY.inventory()
    previous = json.loads((ROOT / "tmp/e1-26-full-body-report.json").read_text(encoding="utf-8"))
    if inputs != previous["source_sha256"]:
        raise ValueError("formal source differs from accepted E1.26")
    locked = yaml.safe_load((SOURCE / "dependencies.lock").read_bytes())
    invocation = {"source_sha256": inputs,
                  "dependency_lock_sha256": sha((SOURCE / "dependencies.lock").read_bytes()),
                  "script_sha256": sha(Path(__file__).read_bytes())}
    input_path = WORK / "inputs.json"
    if input_path.is_file():
        recorded = json.loads(input_path.read_text(encoding="utf-8"))
        if ({k: v for k, v in recorded.items() if k != "script_sha256"}
                != {k: v for k, v in invocation.items() if k != "script_sha256"}
                or sha((WORK / "invoked-builder.py").read_bytes()) != recorded.get("script_sha256")):
            raise ValueError("retained build invocation or source inputs changed")
        if not (args.check or args.review_completed) and recorded != invocation:
            raise ValueError("builder changed; review completed evidence or choose a new work directory")
    elif args.check or args.review_completed:
        raise ValueError("retained build invocation is missing")
    else:
        write_json(input_path, invocation)
        shutil.copy2(Path(__file__), WORK / "invoked-builder.py")
    if args.check:
        builds = [validate_checkpoint(mib) for mib in (16, 32)]
        if any(row is None for row in builds):
            raise ValueError("both retained builds are required")
        report = make_report(builds, check_only=True)
        validate_report(report)
        if json.loads(OUTPUT.read_bytes()) != report or (PUBLIC / "index.html").read_text(encoding="utf-8") != render(report):
            raise ValueError("stale E1.39 retained report or page")
        print("E1.39 retained explicit-Octal build and report evidence verified; no device I/O.")
        return 0
    if args.review_completed:
        builds = [validate_checkpoint(mib) for mib in (16, 32)]
        if any(row is None for row in builds):
            raise ValueError("both completed explicit-Octal builds are required")
        report = make_report(builds)
        write_json(OUTPUT, report)
        (PUBLIC / "index.html").write_text(render(report), encoding="utf-8")
        write_json(WORK / "report.json", report)
        print("E1.39 completed builds reviewed without compiling or device I/O.")
        return 0
    project, _ = HELPER.stage(WORK, ROOT / "tmp/e135a-storage/p", inputs, locked)
    env, python, idf = environment()
    builds = []
    for mib in (16, 32):
        cached = validate_checkpoint(mib) if args.resume else None
        builds.append(cached or build_one(mib, project, inputs, locked, env, python, idf))
    if BODY.inventory() != inputs:
        raise ValueError("formal source changed during explicit-Octal builds")
    report = make_report(builds)
    write_json(OUTPUT, report)
    (PUBLIC / "README.txt").write_text(
        "E1.39 显式 Octal 16/32 MiB 候选。离线构建与容量预检通过；未访问串口、未重启、未刷写。\n",
        encoding="utf-8")
    (PUBLIC / "index.html").write_text(render(report), encoding="utf-8")
    write_json(WORK / "report.json", report)
    print(EXPECTED_RESULT + ": " + OUTPUT.relative_to(ROOT).as_posix(), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print("E1.39 explicit-Octal build blocked: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
