#!/usr/bin/env python3
"""Audit existing local captures/builds for addressing and exact rollback; never opens a device."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_addressing import (
    CONFIG_EXPECTED, LIVE_APP_SHA, LIVE_BOOT_SHA, MIB, PROTECTED_RANGES, SAFETY,
    bounded_image, capacity_case, compare_boot_metadata, effective_config, flash_evidence,
    mmap_guard, rollback_ranges, sha, validate_report, validate_rollback_files,
)
from protocol.storage_capacity import image_facts, validate_build_report
from protocol.storage_live_backup import require_independent_verify, verify_capture_files
from protocol.storage_n32r16 import CAPTURE_SHA256, OLD_TABLE_SHA256, PACKAGE_BYTES, PACKAGE_SHA256

PUBLIC = ROOT / "artifacts/expression-addressing-e1-38-20261030"
PRIVATE = ROOT / "tmp/e138-addressing"
BUILD_REPORT = ROOT / "artifacts/expression-capacity-build-e1-37b-20261030/build-report.json"
BOOT_LOG_SHA = "d3a90eb963db95fde8833d9aee0485c696426d21e162867a4bd0cd3ad0b2181d"
# Pinned whole-file hashes plus independently located semantic line anchors.
SOURCES = (
    ("idf", "components/bootloader/Kconfig.projbuild", "fcc40f81715728514e7bd7b462a3961755c4ce8549491d279ef5a1e809ae0506",
     (("cache32_kconfig", "config BOOTLOADER_FLASH_32BIT_ADDR", 34),)),
    ("idf", "components/spi_flash/flash_mmap.c", "26a1c760f7014ab36127867cea0ed7fbacecb3a67a1cb798eec570f7d1b92494",
     (("mmap_24bit_guard", "#define FLASH_MMAP_ADDR_24BIT_MAX", 20),)),
    ("idf", "components/spi_flash/esp_flash_spi_init.c", "e8435055663c75d4014c63988c92e69e4f197cc847bec8aab08d69adf673f852",
     (("runtime_auto_detect", "static void s_esp_flash_choose_correct_mode", 20),
      ("driver_size_limit", "if (default_chip.size < legacy_chip->chip_size)", 21))),
    ("idf", "components/spi_flash/Kconfig", "da962e007c599936c077918600a492e93b346f8d724c9250b6dc3d24ad3ebea4",
     (("size_override_default", "config SPI_FLASH_SIZE_OVERRIDE", 7),)),
    ("idf", "components/esp_system/port/cpu_start.c", "6e59f72790c1553fef0fc84bafffe25285974c506241c70640fc451c3f9d34d9",
     (("conditional_app_override", "#if CONFIG_SPI_FLASH_SIZE_OVERRIDE", 8),)),
    ("idf", "components/bootloader_support/bootloader_flash/src/bootloader_flash_config_esp32s3.c", "2f52a4ebcb95ba6a534c89b8a1fb6127bcf582e9e7d804c0c30e67691ad47de0",
     (("bootloader_header_size", "static void update_flash_config", 34),)),
    ("idf", "components/spi_flash/spi_flash_chip_mxic_opi.c", "c0fcd0dfd4dd5b29de9a0992fdb1461a30f25b85c2e81a5726857d0d9182b54d",
     (("mxic_opi_probe_size_caps", "esp_err_t spi_flash_chip_mxic_opi_probe", 44),)),
    ("idf", "components/esp_bootloader_format/include/esp_bootloader_desc.h", "920ad502125f7d262f4ce5a97fcca2a48db4b3d94c80747dc4dfa0a40f42a955",
     (("boot_descriptor_layout", "typedef struct {", 15),)),
    ("idf", "components/esp_bootloader_format/esp_bootloader_desc.c", "c41a5a82d91cebf9ccb08e5baed2fda0e3bf8b9955d6fb991ef4c161858f8ce6",
     (("boot_compile_timestamp", "const esp_bootloader_desc_t esp_bootloader_desc =", 17),)),
    ("repo", "firmware/body/main/assets.cc", "9b20821e5f84d55a8074c8e51597b67875178b2f08989c3a722ec652d8bd382c",
     (("emote_requires_mmap", "bool Assets::EmoteStrategy::InitializePartition", 23),)),
)


def source_evidence(idf: Path) -> list[dict]:
    results = []
    for origin, relative, digest, anchors in SOURCES:
        raw = ((idf if origin == "idf" else ROOT) / relative).read_bytes()
        if sha(raw) != digest:
            raise ValueError("reviewed source changed: " + relative)
        lines = raw.decode("utf-8").splitlines()
        sections = []
        for name, token, count in anchors:
            matches = [index for index, line in enumerate(lines) if token in line]
            if len(matches) != 1:
                raise ValueError("source anchor missing or ambiguous: " + name)
            begin = matches[0]
            excerpt = "\n".join(lines[begin:begin + count])
            sections.append({"id": name, "start_line": begin + 1, "end_line": begin + count,
                             "excerpt": excerpt})
        results.append({"origin": origin, "path": relative, "sha256": digest, "anchors": sections})
    return results


def make_report(capture: Path, idf: Path) -> tuple[dict, dict[str, bytes]]:
    # --check verifies retained completed builds; it cannot build, flash or open serial.
    review_python = Path("D:/Espressif/python_env/idf5.5_py3.11_env/Scripts/python.exe")
    if not review_python.is_file():
        review_python = Path(sys.executable)
    subprocess.run([str(review_python), str(ROOT / "scripts/audit-expression-e1-37-capacity.py"), "--check"],
                   cwd=ROOT, check=True)
    build_raw = BUILD_REPORT.read_bytes()
    build = json.loads(build_raw)
    validate_build_report(build)
    sources = source_evidence(idf)
    index_raw = (capture / "capture-index.json").read_bytes()
    index = json.loads(index_raw)
    raw = verify_capture_files(capture, index)
    if len(raw) != 32 * MIB or sha(raw) != CAPTURE_SHA256:
        raise ValueError("not the reviewed physical capture")
    verify_raw = (capture / "verify-full-keep.log").read_bytes()
    require_independent_verify(verify_raw.decode("utf-8"), index["mac"], len(raw), index["full_image"]["path"])
    flash_raw = (capture / "flash-id.log").read_bytes()
    boot_raw = (capture / "boot-after-backup.log").read_bytes()
    if (sha(boot_raw) != BOOT_LOG_SHA
            or set(re.findall(r"MAC: ([0-9a-f:]{17})", flash_raw.decode("utf-8"))) != {index["mac"]}):
        raise ValueError("Flash/boot evidence is not paired with the reviewed capture")
    facts = flash_evidence(flash_raw.decode("utf-8"), boot_raw.decode("utf-8"))
    live_boot = bounded_image(raw, 0, 0x8000)
    live_app = bounded_image(raw, 0x10000, 0x5DC000)
    if sha(live_boot) != LIVE_BOOT_SHA or sha(live_app) != LIVE_APP_SHA:
        raise ValueError("accepted live bootloader/App changed")
    if (image_facts(live_boot)["configured_flash_bytes"] != 16 * MIB
            or image_facts(live_app)["configured_flash_bytes"] != 16 * MIB
            or live_boot[2] != 2 or live_app[2] != 2
            or sha(raw[0x8000:0x8C00]) != OLD_TABLE_SHA256
            or sha(raw[0x5EC000:0x5EC000 + PACKAGE_BYTES]) != PACKAGE_SHA256):
        raise ValueError("protected header, legacy table or accepted E1.18 package differs")
    comparisons, configs = [], []
    for position, row in enumerate(build["builds"]):
        directory = ROOT / row["archive"]
        built_boot = (directory / "bootloader/bootloader.bin").read_bytes()
        if sha(built_boot) != row["artifacts"]["bootloader/bootloader.bin"]["sha256"]:
            raise ValueError("retained bootloader changed")
        comparison = compare_boot_metadata(live_boot, built_boot)
        if not comparison["only_reviewed_metadata_changed"]:
            raise ValueError("bootloader changed beyond reviewed capacity/date metadata")
        comparisons.append(dict(build="16m" if position == 0 else "32m", **comparison))
        if position == 1:
            for target, relative in (("app", "config/sdkconfig.json"), ("bootloader", "bootloader/config/sdkconfig.json")):
                config_raw = (directory / relative).read_bytes()
                if sha(config_raw) != row["artifacts"][relative]["sha256"]:
                    raise ValueError("retained effective configuration changed")
                effective = effective_config(json.loads(config_raw))
                if effective != CONFIG_EXPECTED:
                    raise ValueError("candidate no longer has the reviewed cache/capacity configuration")
                configs.append({"target": target, "sha256": sha(config_raw), "effective": effective})
    candidate = build["builds"][1]
    images = [{"id": name, "offset": offset, "payload_bytes": candidate["artifacts"][relative]["bytes"],
               "candidate_sha256": candidate["artifacts"][relative]["sha256"]}
              for name, offset, relative in (("bootloader", 0, "bootloader/bootloader.bin"), ("app", 0x10000, "xiaozhi.bin"))]
    ranges = rollback_ranges(raw, images)
    private_files = {row["id"]: raw[row["offset"]:row["end_exclusive"]] for row in ranges}
    rollback = {"scope": "conditional_e137_paired_capacity_only", "source_capture_sha256": sha(raw),
                "images": images, "ranges": ranges, "files_verified_against_capture": True,
                "prewrite_identity_and_digest_refresh_required": True, "executable_flash_script_generated": False}
    validate_rollback_files(raw, rollback, private_files)
    for row in ranges:
        row["private_file"] = f"tmp/e138-addressing/rollback/{row['id']}-live-sectors.bin"
    protected = []
    for name, start, size in PROTECTED_RANGES:
        region = raw[start:start + size]
        protected.append({"id": name, "offset": start, "bytes": size, "end_exclusive": start + size,
                          "sha256": sha(region), "all_ff": region.count(255) == size})
    report = {
        "schema": "deskbot.n32r16.addressing_audit.v1", "edition": "E1.38", "date": "2026-10-30",
        "result": "offline_preflight_complete_addressing_candidate_blocked", "hardware": facts,
        "evidence": {"capture_sha256": sha(raw), "capture_bytes": len(raw), "capture_chunks_verified": len(index["chunks"]),
                     "capture_index_sha256": sha(index_raw), "independent_verify_passed": True,
                     "independent_verify_log_sha256": sha(verify_raw), "flash_id_log_sha256": sha(flash_raw),
                     "boot_log_sha256": sha(boot_raw), "e137_build_report_sha256": sha(build_raw),
                     "source_capture_date": "2026-10-29", "idf_version": "5.5.5 (hash-pinned local source files)"},
        "image_header_mode": "dio", "build_mode_selection": "qio_with_runtime_auto_detection",
        "candidate_configs": configs,
        "capacity_cases": [dict(id=name, **capacity_case(boot, app, False)) for name, boot, app in (
            ("accepted_snapshot", 16 * MIB, 16 * MIB), ("app_only_candidate", 16 * MIB, 32 * MIB),
            ("paired_candidate", 32 * MIB, 32 * MIB))],
        "mmap_checks": [dict(id=name, **mmap_guard(start, size, CONFIG_EXPECTED)) for name, start, size in (
            ("legacy_assets", 0x5EC000, 0x5DC000), ("cross_16m_boundary", 0xFF0000, 0x20000),
            ("upper_assets_8m", 0x1000000, 8 * MIB))],
        "live_boot_comparison": comparisons, "rollback": rollback, "protected_ranges": protected,
        "source_evidence": sources, "upper_assets_deployment_ready": False,
        "safety": dict(SAFETY), "deployment_eligible": False, "flash_authorized": False,
        "runtime_verified": False, "hardware_accepted": False, "live_snapshot_current": False,
        "next_step": "E1.39_isolated_explicit_octal_candidate_and_capacity_preflight",
        "pending": ["explicit Octal configuration and derived cache32 review in isolated builds",
                    "commercial part number / SFDP or equivalent driver compatibility evidence before upper-half rollout",
                    "fresh prewrite identity and protected-range digests",
                    "separate boot authorization and body interaction regression",
                    "upper-half reads/mmap, model paths, internal heap and PSRAM peaks",
                    "base motion/DOA regression after repair"],
    }
    validate_report(report)
    return report, {str(ROOT / row["private_file"]): private_files[row["id"]] for row in ranges}


def render(report: dict) -> str:
    e = html.escape
    config_labels = {
        "ESPTOOLPY_OCT_FLASH": "显式 Octal 编译配置", "ESPTOOLPY_FLASH_MODE_AUTO_DETECT": "运行时自动识别",
        "BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH": "Octal 上半区缓存", "BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH": "Quad 上半区缓存",
        "SPI_FLASH_SIZE_OVERRIDE": "App 覆盖 bootloader 容量", "SPI_FLASH_SUPPORT_MXIC_OPI_CHIP": "MXIC OPI 驱动支持",
    }
    config_rows = "".join(f"<tr><td>{e(label)}<br><code>{e(key)}</code></td><td>{'开' if CONFIG_EXPECTED[key] else '关'}</td>"
                          f"<td>{'开' if CONFIG_EXPECTED[key] else '关'}</td></tr>" for key, label in config_labels.items())
    names = {"accepted_snapshot": "已验收现场快照", "app_only_candidate": "只换 32 MiB App", "paired_candidate": "bootloader + App 成对候选"}
    capacity_rows = "".join(f"<tr><td>{names[row['id']]}</td><td>{row['bootloader_header_bytes']//MIB} MiB</td>"
                            f"<td>{row['app_header_bytes']//MIB} MiB</td><td><strong>{row['expected_driver_limit_bytes']//MIB} MiB</strong></td></tr>"
                            for row in report["capacity_cases"])
    mmap_names = {"legacy_assets": "当前资源区", "cross_16m_boundary": "跨过 16 MiB 边界", "upper_assets_8m": "上半区 8 MiB 候选"}
    mmap_rows = "".join(f"<tr><td>{mmap_names[row['id']]}</td><td><code>0x{row['start']:X} → 0x{row['end_exclusive']:X}</code></td>"
                        f"<td class=\"{'pending' if row['rejected_by_24bit_guard'] else 'ok'}\">"
                        f"{'被编译时 guard 拒绝' if row['rejected_by_24bit_guard'] else '仅通过 24 位范围检查'}</td></tr>"
                        for row in report["mmap_checks"])
    rollback_rows = "".join(f"<tr><td>{e(row['id'])}</td><td>{image['payload_bytes']:,} B</td>"
                            f"<td><code>0x{row['offset']:X} → 0x{row['end_exclusive']:X}</code><br>{row['bytes']:,} B</td>"
                            f"<td><code>{row['sha256']}</code></td></tr>"
                            for row, image in zip(report["rollback"]["ranges"], report["rollback"]["images"]))
    protected_rows = "".join(f"<tr><td>{e(row['id'])}</td><td><code>0x{row['offset']:X} → 0x{row['end_exclusive']:X}</code></td>"
                             f"<td>{row['bytes']:,} B</td><td>{'全 FF' if row['all_ff'] else '原始字节已校验'}</td></tr>"
                             for row in report["protected_ranges"])
    source_rows = "".join(f"<details><summary><code>{e(row['path'])}</code></summary><p><code>{row['sha256']}</code></p>"
                          + "".join(f"<p>{e(anchor['id'])} · L{anchor['start_line']}–{anchor['end_line']}</p><pre>{e(anchor['excerpt'])}</pre>" for anchor in row["anchors"])
                          + "</details>" for row in report["source_evidence"])
    data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return ("""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'unsafe-inline';script-src 'unsafe-inline';connect-src 'none'">
<title>E1.38 · N32R16 寻址与回退预检</title><style>
*{box-sizing:border-box}body{margin:0;color:#263530;background:#f8faf9;font:15px/1.7 system-ui,sans-serif}main{max-width:1160px;margin:auto;padding:28px}
section{padding:24px 0;border-bottom:1px solid #d4dfda}h1{font-size:30px;line-height:1.35}h2{font-size:21px}p{margin:10px 0}a{color:#176c58}
.muted{color:#5a6a64}.ok{color:#126344}.pending{color:#99530b}.cards,.flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.card{border-top:3px solid #548b76;padding-top:12px}.card strong{display:block;font-size:24px}.card:nth-child(3){border-color:#bd8b42}
.flow{grid-template-columns:repeat(4,minmax(0,1fr))}.flow div{padding:12px 0;border-top:2px solid #a3bcb2}.table{overflow:auto}table{border-collapse:collapse;width:100%;min-width:640px}th,td{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #dbe4df}code{font:12px/1.65 Consolas,monospace;overflow-wrap:anywhere}details{padding:12px 0;border-bottom:1px solid #dbe4df}summary{cursor:pointer}pre{font:12px/1.55 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;padding:12px;background:#edf2ef}
.map{height:48px;display:flex;margin:16px 0;border-radius:4px;overflow:hidden}.map span{display:flex;align-items:center;justify-content:center;font-size:13px}.lower{width:50%;background:#d4e7dd}.upper{width:25%;background:#f4dfbc}.reserve{width:25%;background:#e2e8e5}
@media(max-width:680px){main{padding:16px}h1{font-size:25px}.cards{grid-template-columns:1fr}.flow{grid-template-columns:1fr 1fr}.map span{font-size:11px}.map{height:65px}section{padding:18px 0}}
</style></head><body><main>
<section><div class="muted">DeskBot · E1.38 · 2026-10-30 · 离线预检</div><h1>N32R16 寻址与精确回退</h1>
<p class="ok">已复核历史全片备份、有效配置、IDF 源码与完整擦写扇区。</p>
<p class="pending">结论：32 MiB 硬件成立；E1.37 候选尚不能用于上半区动画映射。没有刷写或重启设备。</p>
<div class="cards"><div class="card">物理 Flash<strong>32 MiB · Octal</strong>C2:8039 · MXIC</div><div class="card">历史正常启动<strong>OPI_STR</strong>镜像头 DIO / 编译 QIO 自动识别</div><div class="card">当前软件容量<strong>16 MiB</strong>PSRAM 为独立的 16 MiB 运行内存</div></div>
<p class="muted">证据来自 2026-10-29 的已验证快照，不是本轮实时探测。商业料号尚未确认；“Quad”只描述上一份构建配置，不能描述这颗物理 Flash。</p></section>
<section><h2>① 运行时识别与编译时缓存是两个条件</h2><p>已有日志证明固件按 eFuse 自动切到 OPI_STR；这个切换不会回头改变编译进去的缓存 guard。EAF 使用 mmap 挂载资源，所以能通过 USB 回读全片，仍不足以证明上半区可播放。</p>
<div class="table"><table id="config"><thead><tr><th>E1.37 有效配置</th><th>App</th><th>bootloader</th></tr></thead><tbody>""" + config_rows + """</tbody></table></div>
<p>下一份隔离候选优先审阅明确 Octal 的配置及自动派生的 cache32；本轮不启用 Quad 实验选项，也不修改正式源码。</p></section>
<section><h2>② 只刷 App 不会解除旧容量限制</h2><p><code>SPI_FLASH_SIZE_OVERRIDE=false</code> 时，驱动沿用 bootloader 头的容量。下表后两行是源码推导，尚未实机执行。</p>
<div class="table"><table id="capacity"><thead><tr><th>组合</th><th>bootloader</th><th>App</th><th>预期驱动容量</th></tr></thead><tbody>""" + capacity_rows + """</tbody></table></div></section>
<section><h2>③ 上半区的阻断位置</h2><div class="map" aria-label="32 MiB Flash 候选布局"><span class="lower">0–16 MiB · 旧布局</span><span class="upper">16–24 MiB<br>待验证资源槽</span><span class="reserve">24–32 MiB<br>未分配</span></div>
<div class="table"><table id="mmap"><thead><tr><th>地址场景</th><th>范围（末端不含）</th><th>当前源码判定</th></tr></thead><tbody>""" + mmap_rows + """</tbody></table></div>
<p class="pending">通过范围 guard 也不等于 mmap、连续 MMU 页、模型或堆/PSRAM 峰值已经验证。资源布局实验须独立执行。</p></section>
<section><h2>④ 精确现场回退已准备</h2><p>现场 bootloader 与 16 MiB 构建版相差 53 字节，与 32 MiB 构建版相差 54 字节；变化限于编译时间、容量位、校验与摘要。代码差异检查通过，但构建文件仍不是现场逐字节回退。</p>
<p>以下仅列 E1.37 成对容量实验的条件范围。回退片段来自完整现场备份，覆盖最后一个实际擦除扇区，保存在项目私有 <code>tmp/</code>；没有生成刷写脚本。</p>
<div class="table"><table id="rollback"><thead><tr><th>镜像</th><th>候选有效字节</th><th>完整回退扇区</th><th>现场片段 SHA-256</th></tr></thead><tbody>""" + rollback_rows + """</tbody></table></div>
<p class="muted">这组片段仅对应已审阅 E1.37 的镜像长度；新 Octal 构建须重算扇区范围。任何写入前都必须刷新设备身份、实时摘要与备份，之后再单独授权。</p>
<details><summary>查看全片保护范围</summary><div class="table"><table><thead><tr><th>范围</th><th>地址（末端不含）</th><th>大小</th><th>快照状态</th></tr></thead><tbody>""" + protected_rows + """</tbody></table></div></details></section>
<section><h2>⑤ 下一阶段与目标链路</h2><div class="flow"><div>本轮完成<br><strong>寻址 / 现场回退</strong></div><div>E1.39<br><strong>隔离 Octal 构建与预检</strong></div><div>单独授权后<br><strong>旧布局容量启动</strong></div><div>启动与回归通过后<br><strong>资源布局 / mmap 实验</strong></div></div>
<p>扩容稳定后继续精选角色活动包、对称/非对称动画与节点替换；底座修复后恢复固定外壳 A/B/C 识别验证，最后与客户端角色 profile、DeepSeek/TTS 接通。</p>
<p><a href="../../docs/expression-addressing-e1-38-20261030.md">预检说明</a> · <a href="../../docs/roadmap.md">开发管线</a> · <a href="report.json">审计 JSON</a></p></section>
<section><h2>可展开的源码依据</h2><p class="muted">固定 ESP-IDF 5.5.5 本机文件哈希、行锚点与当前 Assets 入口；原始日志、设备标识和回退字节不公开。</p>""" + source_rows + """</section>
<script id="e138-data" type="application/json">""" + data + "</script></main></body></html>")


def write_or_check(path: Path, content: bytes, check: bool) -> None:
    if check:
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError("missing or stale audit output: " + path.relative_to(ROOT).as_posix())
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read existing evidence only; fail on missing or stale outputs")
    parser.add_argument("--capture", type=Path, default=ROOT.parent.parent / "tmp/e135c-live-20261029")
    parser.add_argument("--idf-path", type=Path, default=Path(os.environ.get("IDF_PATH", "D:/Espressif/frameworks/esp-idf-v5.5.5")))
    args = parser.parse_args()
    report, files = make_report(args.capture.resolve(), args.idf_path.resolve())
    for filename, raw in files.items():
        write_or_check(Path(filename), raw, args.check)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    for name, text in {"report.json": encoded, "index.html": render(report),
                       "README.txt": "E1.38 offline addressing and sector-exact rollback review. No device access, reset, build or flash.\n"}.items():
        write_or_check(PUBLIC / name, text.encode("utf-8"), args.check)
    write_or_check(PRIVATE / "rollback-manifest.json", encoded.encode("utf-8"), args.check)
    print("E1.38 existing capture/build/source/rollback audit passed. No serial, reset, build or Flash write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
