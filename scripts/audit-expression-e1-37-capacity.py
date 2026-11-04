#!/usr/bin/env python3
"""Recheck retained capacity builds and render a read-only review page."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.storage_capacity import compare_images, validate_build_report

PUBLIC = ROOT / "artifacts/expression-capacity-build-e1-37b-20261030"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_report() -> dict:
    builder = ROOT / "scripts/build-expression-e1-37-capacity.py"
    subprocess.run([sys.executable, str(builder), "--check"], check=True, cwd=ROOT)
    build_path = PUBLIC / "build-report.json"
    build = json.loads(build_path.read_text(encoding="utf-8"))
    validate_build_report(build)
    old, candidate = [ROOT / item["archive"] for item in build["builds"]]
    images = {name: compare_images((old / name).read_bytes(), (candidate / name).read_bytes())
              for name in ("xiaozhi.bin", "bootloader/bootloader.bin")}
    if any(not result["only_header_capacity_and_digest_changed"] for result in images.values()):
        raise ValueError("capacity candidate changed executable payloads; independent review required")
    return {"schema": "deskbot.n32r16.capacity_review.v1", "edition": "E1.37", "date": "2026-10-30",
            "result": "capacity_build_integrity_verified_runtime_pending",
            "build_report_sha256": sha(build_path),
            "physical_flash_bytes": build["physical_flash_bytes"], "psram_bytes": build["psram_bytes"],
            "comparison": build["comparison"], "image_checks": images,
            "upper_half_cache": build["upper_half_cache"],
            "safety": build["safety"], "deployment_eligible": False, "flash_authorized": False,
            "runtime_verified": False, "hardware_accepted": False,
            "next_step": "capacity_runtime_preflight_and_upper_flash_addressing_audit",
            "unmeasured": ["32 MiB application boot", "Flash driver capacity after initialization",
                           "assets mmap and model initialization", "internal heap and PSRAM peaks",
                           "official UI/audio/touch/sleep regression", "base motion and DOA after repair"]}


def render(report: dict) -> str:
    escape = html.escape
    rows = []
    for name, result in report["image_checks"].items():
        changed = "仅容量头与末尾摘要" if result["only_header_capacity_and_digest_changed"] else "包含内部变化，须逐项审阅"
        rows.append(f"<tr><td><code>{escape(name)}</code></td><td>{result['control']['bytes']:,} B</td>"
                    f"<td>{result['candidate']['bytes']:,} B</td><td>{changed}</td>"
                    f"<td>{result['differing_bytes_in_shared_range']:,}</td></tr>")
    configs = "".join(f"<tr><td><code>{escape(key)}</code></td><td>{escape(value['old'] or '未生成')}</td>"
                      f"<td>{escape(value['candidate'] or '未生成')}</td></tr>"
                      for key, value in report["comparison"]["config_changes"].items())
    protected = "".join(f"<li><code>{escape(name)}</code>：逐字节一致</li>"
                        for name in report["comparison"]["unchanged_outputs"])
    data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return ("""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'unsafe-inline';script-src 'unsafe-inline';connect-src 'none'">
<title>E1.37 · N32R16 容量构建对照</title><style>
*{box-sizing:border-box;letter-spacing:0}body{margin:0;color:#20282d;background:#f8faf9;font:15px/1.65 system-ui,sans-serif}
main{max-width:1160px;margin:auto;padding:26px}section{padding:22px 0;border-bottom:1px solid #d8dfdc}
h1{font-size:28px;line-height:1.4}h2{font-size:20px}p{margin:10px 0}.muted{color:#586563}.ok{color:#176347}.pending{color:#8a4e05}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.stat{padding:12px 0;border-top:3px solid #38846a}.stat:nth-child(2){border-color:#bc8a21}.stat:nth-child(3){border-color:#547fa2}.stat strong{display:block;font-size:24px}
code{font:12px/1.5 Consolas,monospace;overflow-wrap:anywhere}a{color:#176a5c}.table{overflow:auto}table{border-collapse:collapse;min-width:650px;width:100%}
td,th{text-align:left;border-bottom:1px solid #d8dfdc;padding:12px;vertical-align:top}.flow{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.flow div{border-top:2px solid #9aaca5;padding:12px 0;overflow-wrap:anywhere}
@media(max-width:680px){main{padding:16px}section{padding:16px 0}.cards{grid-template-columns:1fr}.flow{grid-template-columns:1fr 1fr}h1{font-size:24px}}
</style></head><body><main>
<section><div class="muted">DeskBot · E1.37 · 2026-10-30</div><h1>N32R16 容量构建对照</h1>
<p class="ok">16 MiB 控制版与 32 MiB 候选版已完成独立构建和离线完整性检查。</p>
<p>控制版复现已验收 E1.26 App。候选只切换容量配置，沿用旧分区表；当前设备仍保持原版本。</p>
<div class="cards"><div class="stat">物理 Flash<strong>32 MiB</strong></div><div class="stat">候选容量声明<strong>32 MiB</strong></div><div class="stat">PSRAM 运行内存<strong>16 MiB</strong></div></div>
<p class="pending">仅离线构建：启动、mmap、模型、内存峰值和实机交互尚未验证；没有刷写。</p></section>
<section><h2>三项容量选择 + 三项 IDF 自动派生配置</h2>
<p>后三项是 IDF 根据 32 MiB 容量自动启用的隐藏寻址配置。没有改变 UI、音频、触摸或电机配置；缓存访问上半区的实验开关仍关闭。</p>
<div class="table"><table><thead><tr><th>配置</th><th>控制版</th><th>候选版</th></tr></thead><tbody>""" + configs + """</tbody></table></div></section>
<section><h2>镜像差异与完整性</h2><div class="table"><table><thead><tr><th>镜像</th><th>16 MiB</th><th>32 MiB</th><th>变化范围</th><th>差异字节数</th></tr></thead><tbody>""" + "".join(rows) + """</tbody></table></div>
<p class="muted">两份镜像的段长度、XOR 校验和及末尾 SHA-256 均已核对；“构建通过”不代替启动验证。</p></section>
<section><h2>32 MiB 声明 ≠ 上半区动画已可用</h2>
<p class="pending">当前 Quad Flash 配置没有开启 16 MiB 以上地址的缓存访问。两份镜像各只有 33 字节变化，不能据此认定上半区 EAF 可以 mmap 播放。</p>
<p>IDF 5.5.5 将该缓存选项标为实验功能。本轮保留关闭；下一步先核对 Flash 芯片与驱动条件，并准备容量验证的现场回退。上半区 8 MiB 资源槽继续作为待审计候选，旧 16 MiB 范围内扩容保留为比较路线。</p></section>
<section><h2>保护边界</h2><ul>""" + protected + """</ul>
<p>构建生成的原始资源包只作比较，部署资源必须继续使用已验收的 E1.18 包。现场 bootloader 与构建参考存在差异，下一步部署预检必须保留精确现场回退。</p></section>
<section><h2>后续路径</h2><div class="flow"><div>① 本轮<br><strong>容量构建与离线审计</strong></div><div>② 下一步<br><strong>寻址兼容性与精确回退预检</strong></div><div>③ 单独授权后<br><strong>启动与本体回归</strong></div><div>④ 兼容性通过后<br><strong>8 MiB 资源槽迁移</strong></div></div>
<p>本轮不修改 UI、触摸、音频、DOA 或电机算法。底座维修后的整机动作另行验收。</p>
<p><a href="../../docs/roadmap.md">开发管线</a> · <a href="report.json">审计 JSON</a> · <a href="build-report.json">构建 JSON</a></p></section>
<script id="e137-data" type="application/json">""" + data + "</script></main></body></html>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = make_report()
    contents = {"report.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                "index.html": render(report),
                "README.txt": "E1.37 offline capacity-only builds and integrity review. No device I/O or Flash write.\n"}
    for name, content in contents.items():
        path = PUBLIC / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise ValueError("stale E1.37 artifact: " + name)
        else:
            path.write_text(content, encoding="utf-8")
    print(json.dumps({"edition": "E1.37", "result": report["result"],
                      "images": {key: value["only_header_capacity_and_digest_changed"]
                                 for key, value in report["image_checks"].items()},
                      "device_written": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("E1.37 review blocked: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
