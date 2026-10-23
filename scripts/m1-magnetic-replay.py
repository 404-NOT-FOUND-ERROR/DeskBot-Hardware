#!/usr/bin/env python3
"""Decode and replay recorded 0x06 magnetic telemetry without hardware I/O.

This tool accepts either canonical AA55 frames in hex logs or callback lines
such as ``Handle: cmd=06, 01 FF 49 FE 72 FB BC``.  It writes only below the
repository's ``artifacts/m1-magnetic-readonly-20261016`` directory and never
opens a serial port.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "m1-magnetic-readonly-20261016"
sys.path.insert(0, str(ROOT))

from protocol.magnetic_telemetry import (  # noqa: E402
    FrameStreamParser,
    TelemetryError,
    callback_payload_from_line,
    frame_bytes_from_line,
    iter_samples_from_callback_lines,
    line_timestamp,
)


def _inside_artifacts(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        path.resolve().relative_to(ARTIFACT_ROOT.resolve())
        return True
    except ValueError:
        return False


def output_dir(value: str | None) -> Path:
    if value:
        requested = Path(value)
        path = (requested if requested.is_absolute() else ROOT / requested).resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = ARTIFACT_ROOT / "results" / stamp
    if not _inside_artifacts(path):
        raise SystemExit(
            f"Refusing output outside {ARTIFACT_ROOT}; choose a subdirectory under that path."
        )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_input(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise SystemExit(f"Cannot read input log: {path}: {exc}") from exc


def parse_lines(lines: list[str], as_of: str | None, threshold: float) -> tuple[list[dict], dict]:
    if not math.isfinite(threshold) or threshold < 0:
        raise TelemetryError("freshness threshold must be finite and non-negative")
    samples: list[dict] = []
    parser = FrameStreamParser(freshness_threshold_s=threshold)
    rejected: list[dict] = []
    callback_lines = 0
    ignored_lines = 0
    explicit_as_of = as_of is not None
    if as_of is None:
        # Replay freshness means relative to the last recorded host timestamp;
        # it is never interpreted as a statement about a live device.
        for line in reversed(lines):
            candidate = line_timestamp(line)
            if not candidate:
                continue
            try:
                datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            except ValueError:
                continue
            as_of = candidate
            break
    if as_of:
        try:
            parsed_clock = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if parsed_clock.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise TelemetryError("--as-of requires an ISO-8601 timestamp with timezone") from exc
    for number, line in enumerate(lines, 1):
        timestamp = line_timestamp(line)
        if re.match(r"^\s*\d{4}-\d\d-\d\d[T ]", line) and timestamp is None:
            rejected.append({"line": number, "reason": "invalid ISO-8601 host timestamp"})
            continue
        # Callback output is not a complete wire frame. Keep its provenance.
        try:
            callback = callback_payload_from_line(line)
        except TelemetryError as exc:
            rejected.append({"line": number, "reason": str(exc)})
            continue
        if callback is not None:
            callback_lines += 1
            try:
                for sample in iter_samples_from_callback_lines(
                    [line], as_of=as_of, freshness_threshold_s=threshold
                ):
                    samples.append({"sequence": len(samples) + 1, "source_line": number, **sample.as_dict()})
            except TelemetryError as exc:
                rejected.append({"line": number, "reason": str(exc)})
            continue
        try:
            chunk = frame_bytes_from_line(line)
        except TelemetryError as exc:
            rejected.append({"line": number, "reason": str(exc)})
            continue
        if not chunk:
            ignored_lines += 1
            continue
        if timestamp:
            try:
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                rejected.append({"line": number, "reason": "invalid ISO-8601 host timestamp"})
                continue
        for sample in parser.feed(
            chunk,
            host_received_at=timestamp,
            host_time_source="completing_log_line" if timestamp else None,
            as_of=as_of,
            raw_line=line,
        ):
            samples.append({"sequence": len(samples) + 1, "source_line": number, **sample.as_dict()})
    parser.finish()
    stats = parser.stats.as_dict()
    stats["input_lines"] = len(lines)
    stats["ignored_lines"] = ignored_lines
    stats["callback_lines"] = callback_lines
    stats["rejected_lines"] = rejected
    stats["samples_written"] = len(samples)
    stats["callback_reconstructed"] = sum(
        1 for sample in samples if sample["source"] == "callback_reconstructed"
    )
    stats["checksum_verified"] = sum(
        1 for sample in samples if sample["source"] == "checksum_verified"
    )
    stats["unknown_kind_samples"] = sum(1 for sample in samples if not sample["kind_known"])
    stats["schema"] = "deskbot.magnetic.replay.v1"
    stats["units"] = "transport_counts"
    stats["kind_mapping"] = "project_telemetry_extension"
    stats["device_sample_time_available"] = False
    stats["freshness_as_of"] = as_of
    stats["freshness_reference"] = "explicit_as_of" if explicit_as_of else "last_valid_host_log_timestamp"
    stats["freshness_threshold_seconds"] = threshold
    stats["freshness_scope"] = "offline_host_receive_time"
    stats["note"] = "Checksum validation verifies recorded bytes; callback frames are reconstructed, not captured wire frames. No live device state is implied."
    stats["axis_ranges"] = {
        axis: {"min": min(sample[axis] for sample in samples), "max": max(sample[axis] for sample in samples)}
        for axis in ("x", "y", "z")
    } if samples else {}
    stats["per_kind"] = {}
    for kind in sorted({sample["kind"] for sample in samples}):
        group = [sample for sample in samples if sample["kind"] == kind]
        axes = {
            axis: {
                "min": min(sample[axis] for sample in group),
                "max": max(sample[axis] for sample in group),
                "mean": statistics.fmean(sample[axis] for sample in group),
                "stddev": statistics.pstdev(sample[axis] for sample in group),
            } for axis in ("x", "y", "z")
        } if group[0]["xyz_available"] else None
        stats["per_kind"][str(kind)] = {
            "kind_name": group[0]["kind_name"], "count": len(group),
            "xyz_available": group[0]["xyz_available"], "axes": axes,
        }
    latest = samples[-1] if samples else None
    invalid_input = bool(rejected or parser.stats.rejected_checksum or parser.stats.rejected_length or parser.stats.rejected_payload or parser.stats.incomplete_tail_bytes)
    stats["latest"] = {
        "status": ("invalid" if invalid_input else "missing") if latest is None else ("unknown_kind" if not latest["kind_known"] else ("non_xyz" if not latest["xyz_available"] else latest["freshness"])),
        "sequence": latest["sequence"] if latest else None,
        "host_received_at": latest["host_received_at"] if latest else None,
        "age_seconds": latest["age_seconds"] if latest else None,
    }
    return samples, stats


def write_outputs(samples: list[dict], stats: dict, target: Path) -> None:
    (target / "samples.jsonl").write_text(
        "".join(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n" for sample in samples),
        encoding="utf-8",
    )
    with (target / "samples.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "sequence", "source_line", "kind", "kind_name", "kind_known", "xyz_available", "units", "x", "y", "z", "payload_hex", "frame_hex",
            "source", "checksum_valid", "host_received_at", "host_time_source",
            "freshness", "age_seconds", "raw_line",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: sample.get(field) for field in fields} for sample in samples)
    (target / "summary.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_preview(samples, stats, target)


def write_preview(samples: list[dict], stats: dict, target: Path) -> None:
    """Self-contained local replay chart; no network or device interaction."""

    data = json.dumps(samples, ensure_ascii=True).replace("<", "\\u003c").replace("&", "\\u0026")
    summary = json.dumps(stats, ensure_ascii=True).replace("<", "\\u003c").replace("&", "\\u0026")
    template = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>M1 磁场离线回放</title><style>
body{font:16px system-ui,sans-serif;background:#101723;color:#e9eff8;max-width:1100px;margin:auto;padding:28px}
h1{font-size:26px}p{line-height:1.65;color:#b8c8de}canvas{background:#182336;border-radius:12px;width:100%;height:360px}
input{width:100%}button{background:#d5e6ff;border:0;border-radius:8px;padding:10px 18px;cursor:pointer}
pre{white-space:pre-wrap;background:#182336;padding:18px;border-radius:12px;font-size:14px}
.x{color:#63d6bd}.y{color:#ffd080}.z{color:#9caaff}.box{margin:18px 0}
</style><h1>M1 磁场离线回放</h1>
<p>以下来自已保存的日志或帧，不连接设备。坐标单位为传输计数值（counts）。横轴为样本序号，不能据此断言真实采样率。
<span class="x">X</span> / <span class="y">Y</span> / <span class="z">Z</span>。来源与校验方式在每条记录中保留。</p>
<canvas id="chart" width="1040" height="360"></canvas><div class="box"><input id="position" type="range" min="0" value="0">
<button id="play">播放</button> <span id="progress"></span></div><pre id="detail"></pre><p id="empty"></p>
<script id="samples" type="application/json">__SAMPLES__</script><script id="summary" type="application/json">__SUMMARY__</script>
<script>
const samples=JSON.parse(document.querySelector('#samples').textContent), summary=JSON.parse(document.querySelector('#summary').textContent);
const canvas=document.querySelector('#chart'), ctx=canvas.getContext('2d'), slider=document.querySelector('#position');
let timer=null;slider.max=Math.max(0,samples.length-1);slider.disabled=!samples.length;
function draw(){const w=canvas.width,h=canvas.height,p=38;ctx.clearRect(0,0,w,h);
let lo=Infinity,hi=-Infinity;for(const s of samples){if(!s.xyz_available)continue;for(const axis of ['x','y','z']){lo=Math.min(lo,s[axis]);hi=Math.max(hi,s[axis]);}}if(!Number.isFinite(lo)){lo=0;hi=1;}
if(lo===hi){lo-=1;hi+=1;} const X=i=>p+(w-2*p)*i/Math.max(1,samples.length-1),Y=v=>h-p-(v-lo)/(hi-lo)*(h-2*p);
ctx.strokeStyle='#34475f';ctx.strokeRect(p,p,w-2*p,h-2*p);ctx.fillStyle='#b8c8de';ctx.font='13px system-ui';ctx.fillText(hi,3,p);ctx.fillText(lo,3,h-p);
for(const [axis,color] of [['x','#63d6bd'],['y','#ffd080'],['z','#9caaff']]){ctx.strokeStyle=color;ctx.lineWidth=1.8;ctx.beginPath();let previous=false;samples.forEach((s,i)=>{if(!s.xyz_available){previous=false;return;}previous?ctx.lineTo(X(i),Y(s[axis])):ctx.moveTo(X(i),Y(s[axis]));previous=true;});ctx.stroke();}
const index=Number(slider.value),s=samples[index];if(s){ctx.strokeStyle='#ffffff';ctx.beginPath();ctx.moveTo(X(index),p);ctx.lineTo(X(index),h-p);ctx.stroke();
document.querySelector('#progress').textContent=`${index+1} / ${samples.length}`;
document.querySelector('#detail').textContent=JSON.stringify(s,null,2);}else{document.querySelector('#empty').textContent='尚无有效磁场样本。请检查 summary.json 中的拒绝计数与输入格式。';}}
slider.addEventListener('input',draw);document.querySelector('#play').onclick=()=>{if(timer){clearInterval(timer);timer=null;document.querySelector('#play').textContent='播放';return;}
if(!samples.length)return;document.querySelector('#play').textContent='暂停';timer=setInterval(()=>{slider.value=(Number(slider.value)+1)%samples.length;draw();},120);};draw();
</script></html>'''
    (target / "preview.html").write_text(template.replace("__SAMPLES__", data).replace("__SUMMARY__", summary), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="offline log or frame file")
    parser.add_argument("--output", help="subdirectory under artifacts/m1-magnetic-readonly-20261016")
    parser.add_argument("--as-of", help="ISO-8601 host time used to classify freshness")
    parser.add_argument("--freshness-threshold", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not math.isfinite(args.freshness_threshold) or args.freshness_threshold < 0:
        raise SystemExit("--freshness-threshold must be finite and non-negative")
    lines = _read_input(args.input)
    try:
        samples, stats = parse_lines(lines, args.as_of, args.freshness_threshold)
    except TelemetryError as exc:
        raise SystemExit(str(exc)) from exc
    target = output_dir(args.output)
    stats["input_file"] = str(args.input.resolve())
    stats["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    write_outputs(samples, stats, target)
    print(json.dumps({"output": str(target), **stats}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
