#!/usr/bin/env python3
"""Generate and audit the first N32R16 32 MiB layout candidates offline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocol.storage_n32r16 import (
    CAPTURE_SHA256, FORMAL_CSV_SHA256, LEGACY_FLASH_BYTES, PHYSICAL_FLASH_BYTES, PSRAM_BYTES,
    proposals, summarize, validate_report, validate_capacity_evidence, to_csv,
)

CAPTURE = ROOT.parent.parent / "tmp/e135c-live-20261029"
WORK = ROOT / "tmp/e136-n32r16-layout-20261030"
PUBLIC = ROOT / "artifacts/expression-storage-n32r16-e1-36-20261030"
GEN = Path(r"D:\Espressif\frameworks\esp-idf-v5.5.5\components\partition_table\gen_esp32part.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_idf_module():
    spec = importlib.util.spec_from_file_location("e136_gen_esp32part", GEN)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load gen_esp32part.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def roundtrip(module, plan: dict, index: int) -> dict:
    name = plan["id"]
    csv_path = WORK / "tables" / f"{index:02d}-{name}.csv"
    bin_path = WORK / "tables" / f"{index:02d}-{name}.bin"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(to_csv(plan["rows"]), encoding="utf-8")
    flash = "16MB" if plan["configured_flash_bytes"] == LEGACY_FLASH_BYTES else "32MB"
    subprocess.run([sys.executable, str(GEN), "--flash-size", flash, str(csv_path), str(bin_path)],
                   check=True, capture_output=True, text=True)
    table = module.PartitionTable.from_binary(bin_path.read_bytes())
    table.verify()
    rows = []
    for part in table:
        rows.append({"name": part.name, "type": part.type, "subtype": part.subtype,
                     "start": part.offset, "size_bytes": part.size,
                     "end_exclusive": part.offset + part.size,
                     "flags": int(part.encrypted) | (int(part.readonly) << 1)})
    if rows != plan["rows"]:
        raise ValueError(f"IDF partition round trip changed {name}")
    result = summarize(plan)
    result.update({"table_sha256": sha(bin_path), "table_file": bin_path.relative_to(ROOT).as_posix(),
                   "idf_roundtrip_verified": True})
    return result


def render(report: dict) -> str:
    def size_text(value: int) -> str:
        mib = value / (1024 * 1024)
        if value % (1024 * 1024) == 0:
            return f"{mib:.0f} MiB"
        return f"{mib:.2f} MiB ({value:,} B)"

    html_rows = []
    for item in report["candidates"]:
        a, m = item["assets"], item["model"]
        role = next((row for row in item["rows"] if row["name"] == "assets" and row["start"] >= 0x1000000), None)
        role_text = "—" if role is None else size_text(role["size_bytes"])
        html_rows.append(
            f"<tr><td><code>{item['id']}</code><br>{item['label']}</td>"
            f"<td>{size_text(a['size_bytes'])}<br><code>0x{a['start']:X}..0x{a['end_exclusive']:X}</code><br>{a['mmap_span_pages']} mapping pages</td>"
            f"<td><code>0x{m['start']:X}</code><br>{size_text(m['size_bytes'])}</td>"
            f"<td>{a['free_bytes']:,} B<br>soft limit {a['soft_limit_bytes']:,} B</td>"
            f"<td>{role_text}<br>{item['purpose']}</td>"
            f"<td>{'yes' if item['table_changes'] else 'no'} / {'yes' if item['model_moves'] else 'no'}</td></tr>")
    data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return ("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'unsafe-inline';script-src 'unsafe-inline';connect-src 'none'"><title>E1.36 · N32R16 layout audit</title><style>*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#e8eef8;font:15px/1.6 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:24px}.card{background:#121d31;border:1px solid #2c415e;border-radius:16px;padding:20px;margin:16px 0}.hero{background:linear-gradient(125deg,#183853,#141e31)}h1{font-size:30px;line-height:1.3}h2{font-size:20px}.muted{color:#aec0d7}.ok{color:#7be2b8}.warn{color:#ffd27e}.badge{display:inline-block;background:#253b53;padding:4px 12px;border-radius:16px;margin:3px 6px 3px 0;font-size:13px}.table{overflow:auto}table{border-collapse:collapse;width:100%;min-width:930px}td,th{border-bottom:1px solid #2a3d56;text-align:left;padding:10px;vertical-align:top}th{color:#b9cbe0}code{color:#c7ddff;font:12px ui-monospace,Consolas,monospace;overflow-wrap:anywhere}.steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.step{background:#0e1929;border:1px solid #2c415e;border-radius:10px;padding:15px}.step b{display:block;color:#8fd3ff;margin-bottom:5px}@media(max-width:700px){main{padding:14px}h1{font-size:25px}.steps{grid-template-columns:1fr}.card{padding:16px}}</style></head><body><main>""" +
        "<section class=\"card hero\"><div class=muted>DeskBot · E1.36 · 2026-10-30</div><h1>N32R16 · 32 MiB Flash layout gate</h1><p>Physical Flash is 32 MiB and PSRAM is 16 MiB. The accepted image still uses the legacy 16 MiB configuration. This is an offline layout audit only.</p><span class=\"badge ok\">32 MiB readback verified</span><span class=\"badge ok\">IDF table round trips passed</span><span class=\"badge warn\">mmap / boot / hardware regression pending</span></section>" +
        "<section class=card><h2>Capacity layers</h2><div class=steps><div class=step><b>Physical Flash</b>32 MiB / 0x2000000</div><div class=step><b>Legacy image</b>16 MiB header and partition map</div><div class=step><b>PSRAM</b>16 MiB runtime memory</div></div><p class=muted>The upper half is a read-only migration input, not a deployment permission.</p></section>" +
        "<section class=card><h2>Candidate comparison</h2><div class=table><table><thead><tr><th>Candidate</th><th>assets</th><th>model</th><th>package room</th><th>purpose</th><th>table / model move</th></tr></thead><tbody>" + "".join(html_rows) + "</tbody></table></div></section>" +
        f"<section class=card><h2>First-step decision</h2><p class=ok><strong>Next build: capacity-only-32m</strong></p><p>Keep the current assets/model addresses and contents, change only the image capacity declaration and validate the 32 MiB partition boundary. Only after that passes should the upper resource slot or in-place model move be built.</p><ol><li>Build in a separate directory.</li><li>Inspect boot, partition discovery, assets mount, mmap pages and heap/PSRAM peaks.</li><li>Run the official UI, audio, touch, DOA and sleep regression before any resource migration.</li></ol><p class=warn>No device write or flash authorization is included.</p></section>" +
        f"<script id=\"e136-data\" type=\"application/json\">{data}</script></main></body></html>"


    )


README_TEXT = "E1.36 N32R16 offline layout audit. No device access or flash write.\n"


def build_report() -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    raw = (CAPTURE / "body-full-32MiB.bin").read_bytes()
    index = json.loads((CAPTURE / "capture-index.json").read_text(encoding="utf-8"))
    if (len(raw) != PHYSICAL_FLASH_BYTES or sha_bytes(raw) != index["full_image"]["sha256"]
            or index.get("physical_flash_bytes") != PHYSICAL_FLASH_BYTES
            or index.get("flash_written") is not False):
        raise ValueError("verified 32 MiB read-only capture is required")
    module = load_idf_module()
    records = [roundtrip(module, plan, i) for i, plan in enumerate(proposals())]
    report = {
        "schema": "deskbot.n32r16.storage_audit.v1", "edition": "E1.36", "date": "2026-10-30",
        "result": "offline_layout_audit_passed_runtime_pending", "deployment_eligible": False, "flash_authorized": False,
        "device": {"chip": "esp32s3", "model": "N32R16", "physical_flash_bytes": PHYSICAL_FLASH_BYTES,
                   "configured_flash_bytes": LEGACY_FLASH_BYTES, "psram_bytes": PSRAM_BYTES},
        "whole_device_sha256": index["full_image"]["sha256"], "baseline_matches": True,
        "capture": {"path": "tmp/e135c-live-20261029/body-full-32MiB.bin", "independent_verify_passed": True, "upper_16m_all_ff": all(byte == 0xFF for byte in raw[LEGACY_FLASH_BYTES:])},
        "formal_csv_sha256": FORMAL_CSV_SHA256,
        "candidates": records,
        "recommendation": {"next_build": "capacity-only-32m", "resource_candidate": "upper-assets-8m"},
        "safety": {"serial_accessed": False, "device_written": False, "nvs_written": False, "formal_firmware_changed": False, "c61_accessed": False},
    }
    validate_capacity_evidence({"device": report["device"], "whole_device_sha256": report["whole_device_sha256"], "baseline_matches": True, "capture": report["capture"], "device_written": False, "flash_authorized": False})
    validate_report(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit N32R16 layout candidates without device I/O")
    parser.add_argument("--check", action="store_true", help="fail if generated public artifacts are stale")
    args = parser.parse_args()
    report = build_report()
    report_json = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    page = render(report)
    if args.check:
        expected = {
            PUBLIC / "report.json": report_json,
            PUBLIC / "index.html": page,
            PUBLIC / "README.txt": README_TEXT,
            WORK / "report.json": report_json,
        }
        for path, content in expected.items():
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise ValueError(f"stale or missing E1.36 artifact: {path}")
    else:
        PUBLIC.mkdir(parents=True, exist_ok=True)
        (PUBLIC / "report.json").write_text(report_json, encoding="utf-8")
        (PUBLIC / "index.html").write_text(page, encoding="utf-8")
        (PUBLIC / "README.txt").write_text(README_TEXT, encoding="utf-8")
        (WORK / "report.json").write_text(report_json, encoding="utf-8")
    print(json.dumps({"edition": "E1.36", "result": report["result"], "candidates": len(report["candidates"]), "next_build": "capacity-only-32m", "deployment_eligible": False, "flash_authorized": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"E1.36 blocked: {exc}", file=sys.stderr)
        raise SystemExit(2)
