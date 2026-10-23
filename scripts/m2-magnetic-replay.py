#!/usr/bin/env python3
"""Offline M2 magnetic-shell registration and recognition harness.

The input is an M1 ``samples.jsonl`` file (or an M1 output directory). Raw
logs are decoded through the M1 parser. Training recordings are supplied with
explicit ``LABEL=PATH`` pairs; labels are never inferred from historic logs.
This command never opens a serial port and writes only below the project.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "m2-magnetic-recognition-20261016"
# Keep the host-side replay bounded as well as the recognizer window.  A replay
# is an experiment input, not a general-purpose log viewer.
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_INPUT_LINES = 250_000
MAX_INPUT_SAMPLES = 100_000
MAX_REJECTED_RECORDS = 4_096
MAX_PREVIEW_ROWS = 2_000
MAX_PREVIEW_BYTES = 4 * 1024 * 1024
CONTENT_DIGEST_SCHEMA = "deskbot.magnetic.samples.content.v1"
sys.path.insert(0, str(ROOT))

from protocol.magnetic_recognition import MagneticRecognizer, RecognitionConfig  # noqa: E402
from protocol.magnetic_telemetry import MagneticSample, decode_frame, decode_payload  # noqa: E402


def output_dir(value: str | None) -> Path:
    requested = Path(value) if value else ARTIFACT_ROOT / "results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = (requested if requested.is_absolute() else ROOT / requested).resolve()
    try:
        path.relative_to(ROOT.resolve())
        path.relative_to(ARTIFACT_ROOT.resolve())
    except ValueError as exc:
        raise SystemExit(f"Refusing output outside {ARTIFACT_ROOT}") from exc
    path.mkdir(parents=True, exist_ok=True)
    return path


def _m1_module():
    path = ROOT / "scripts" / "m1-magnetic-replay.py"
    spec = importlib.util.spec_from_file_location("deskbot_m1_replay", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import M1 parser: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_from_dict(value: dict[str, object]) -> MagneticSample:
    """Recheck transported values instead of trusting cached decoded fields."""
    required = ("payload_hex", "frame_hex", "source", "kind", "kind_name", "kind_known", "xyz_available", "units", "x", "y", "z")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError("incompatible M1 sample schema; missing " + ", ".join(missing) + "; regenerate samples.jsonl from the original log with the current M1 parser")
    timestamp = value.get("host_received_at")
    kwargs = dict(host_received_at=timestamp, host_time_source=value.get("host_time_source"), raw_line=value.get("raw_line"))
    payload = bytes.fromhex(value["payload_hex"])
    if value.get("source") == "checksum_verified":
        if value.get("checksum_valid") is not True:
            raise ValueError("verified sample must carry checksum_valid=true")
        sample = decode_frame(bytes.fromhex(value["frame_hex"]), **kwargs)
        if sample is None or sample.payload_hex != payload.hex().upper():
            raise ValueError("magnetic payload and frame do not agree")
    elif value.get("source") == "callback_reconstructed":
        if value.get("checksum_valid") is not None:
            raise ValueError("a callback log cannot prove the wire checksum")
        sample = decode_payload(payload, **kwargs)
        if sample.frame_hex != value["frame_hex"]:
            raise ValueError("callback canonical frame does not agree with payload")
    else:
        raise ValueError("unrecognized sample provenance")
    for key in ("kind", "kind_name", "kind_known", "xyz_available", "units", "x", "y", "z"):
        if key not in value or value[key] != getattr(sample, key):
            raise ValueError(f"decoded {key} does not agree with payload")
    return sample


def _canonical_timestamp(value: str | None) -> str | None:
    """Normalize equivalent ISO-8601 instants before content hashing."""

    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("host_received_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("host_received_at requires an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def load_samples(value: str | Path) -> tuple[list[MagneticSample], dict[str, object]]:
    original = Path(value).resolve()
    path = original / "samples.jsonl" if original.is_dir() else original
    if not path.is_file():
        raise ValueError(f"sample input not found: {path}")
    with path.open("rb") as handle:
        contents = handle.read(MAX_INPUT_BYTES + 1)
    if len(contents) > MAX_INPUT_BYTES:
        raise ValueError(f"sample input exceeds {MAX_INPUT_BYTES} byte limit: {path}")
    digest = hashlib.sha256(contents).hexdigest()
    lines = contents.decode("utf-8", errors="replace").splitlines()
    if len(lines) > MAX_INPUT_LINES:
        raise ValueError(f"sample input exceeds {MAX_INPUT_LINES} line limit: {path}")
    if path.suffix.lower() == ".jsonl":
        samples: list[MagneticSample] = []
        rejected: list[dict[str, object]] = []
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("JSON value is not an object")
                sample = _sample_from_dict(value)
            except (ValueError, TypeError, KeyError) as exc:
                if len(rejected) >= MAX_REJECTED_RECORDS:
                    raise ValueError(f"sample input exceeds {MAX_REJECTED_RECORDS} rejected-record limit") from exc
                rejected.append({"line": number, "reason": str(exc)})
                continue
            if len(samples) >= MAX_INPUT_SAMPLES:
                raise ValueError(f"sample input exceeds {MAX_INPUT_SAMPLES} sample limit")
            samples.append(sample)
        metadata = {"input_path": str(path), "input_sha256": digest, "input_format": "m1_samples_jsonl", "input_lines": len(lines), "rejected_records": rejected}
        metadata["content_sha256"] = _content_digest(samples)
        metadata["content_digest_schema"] = CONTENT_DIGEST_SCHEMA
        metadata["sample_count"] = len(samples)
        return samples, metadata
    m1 = _m1_module()
    # The legacy parser materializes one dict per decoded frame.  Reject a
    # clearly overfull raw stream before invoking it; the post-parse check is
    # retained for inputs whose marker count is only an upper-bound estimate.
    marker_count = sum(line.upper().count("AA55") + line.lower().count("cmd=06") for line in lines)
    if marker_count > MAX_INPUT_SAMPLES:
        raise ValueError(f"sample input may exceed {MAX_INPUT_SAMPLES} sample limit; split the raw recording before replay")
    values, stats = m1.parse_lines(lines, as_of=None, threshold=1.0)
    samples = [_sample_from_dict(item) for item in values]
    if len(samples) > MAX_INPUT_SAMPLES:
        raise ValueError(f"sample input exceeds {MAX_INPUT_SAMPLES} sample limit")
    if len(stats.get("rejected_lines", [])) > MAX_REJECTED_RECORDS:
        raise ValueError(f"sample input exceeds {MAX_REJECTED_RECORDS} rejected-record limit")
    metadata = {"input_path": str(path), "input_sha256": digest, "input_format": "raw_log_decoded_by_m1", "input_lines": len(lines), "m1_stats": stats, "rejected_records": []}
    metadata["content_sha256"] = _content_digest(samples)
    metadata["content_digest_schema"] = CONTENT_DIGEST_SCHEMA
    metadata["sample_count"] = len(samples)
    return samples, metadata


def _content_digest(samples: Iterable[MagneticSample]) -> str:
    """Digest canonical sample values, independent of JSON formatting."""

    records = []
    for sample in samples:
        records.append({"kind": sample.kind, "x": sample.x, "y": sample.y, "z": sample.z, "host_received_at": _canonical_timestamp(sample.host_received_at)})
    payload = json.dumps({"schema": CONTENT_DIGEST_SCHEMA, "samples": records}, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_recording(samples: list[MagneticSample], config: RecognitionConfig, *, purpose: str) -> None:
    """Reject discontinuous recordings instead of silently using a later segment."""

    timestamps = []
    kinds = set()
    for sample in samples:
        if not sample.host_received_at:
            raise ValueError(f"{purpose} recording requires host timestamps")
        timestamp = datetime.fromisoformat(sample.host_received_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError(f"{purpose} recording timestamp requires timezone")
        timestamps.append(timestamp)
        kinds.add(sample.kind)
    if len(kinds) != 1:
        raise ValueError(f"{purpose} recording must contain one sensor kind")
    for previous, current in zip(timestamps, timestamps[1:]):
        gap = (current - previous).total_seconds()
        if gap <= 0:
            raise ValueError(f"{purpose} recording timestamps must be strictly increasing")
        if gap > config.max_gap_s:
            raise ValueError(f"{purpose} recording contains a {gap:g}s gap; split it before replay")


def _validate_evaluation_timestamps(samples: list[MagneticSample]) -> None:
    """Require usable timestamps but permit gaps and sensor changes in evaluation."""

    for sample in samples:
        _canonical_timestamp(sample.host_received_at)


def _feed_training_continuous(recognizer: MagneticRecognizer, samples: list[MagneticSample]) -> None:
    """Feed a training recording and reject any recoverable stream boundary."""

    disallowed = {
        "timestamp_non_monotonic_reset",
        "timestamp_gap_reset",
        "sensor_changed_reset",
        "timestamp_invalid",
        "timestamp_missing",
        "invalid_xyz",
        "unsupported_sensor",
        "sample_stale",
        "future_sample",
    }
    for index, sample in enumerate(samples, 1):
        result = recognizer.feed(sample)
        if result.reason in disallowed:
            raise ValueError(f"training recording is not one continuous valid stream at sample {index}: {result.reason}")


def _last_timestamp(samples: Iterable[MagneticSample]) -> str:
    values = [sample.host_received_at for sample in samples if sample.host_received_at]
    if not values:
        raise ValueError("samples require host_received_at timestamps for M2 replay")
    return values[-1]


def _registration_spec(value: str) -> tuple[str, Path]:
    label, separator, filename = value.partition("=")
    if not separator or not label.strip() or not filename.strip():
        raise ValueError("--register must use LABEL=PATH")
    return label.strip(), Path(filename.strip()).resolve()


def build_recognizer(registrations: list[str], config: RecognitionConfig) -> tuple[MagneticRecognizer, list[dict[str, object]]]:
    recognizer = MagneticRecognizer(config)
    records: list[dict[str, object]] = []
    for specification in registrations:
        label, path = _registration_spec(specification)
        samples, metadata = load_samples(path)
        stats = metadata.get("m1_stats", {})
        if metadata["rejected_records"] or stats.get("rejected_lines") or any(stats.get(key, 0) for key in ("rejected_checksum", "rejected_length", "rejected_payload", "incomplete_tail_bytes")):
            details = metadata["rejected_records"] or stats.get("rejected_lines", [])
            reason = "; first rejection: " + str(details[0]["reason"]) if details else ""
            raise ValueError(f"registration input contains rejected/truncated records: {path}{reason}")
        if not samples:
            raise ValueError(f"registration input has no samples: {path}")
        _validate_recording(samples, config, purpose="training")
        if any(record["training"]["content_sha256"] == metadata["content_sha256"] for record in records):
            raise ValueError("the same recording cannot be registered under multiple labels")
        recognizer.reset_stream()
        _feed_training_continuous(recognizer, samples)
        profile = recognizer.register(label, as_of=_last_timestamp(samples))
        records.append({"profile": profile.as_dict(), "training": metadata})
    return recognizer, records


def _row(index: int, result) -> dict[str, object]:
    value = result.as_dict()
    value["sequence"] = index
    return value


def replay(recognizer: MagneticRecognizer, samples: list[MagneticSample], tick_after_seconds: float | None) -> list[dict[str, object]]:
    # Evaluation starts at an explicit recording boundary, retaining profiles
    # but never inheriting training samples or confirmations.
    recognizer.reset_stream()
    if tick_after_seconds is not None and (not math.isfinite(tick_after_seconds) or tick_after_seconds < 0):
        raise ValueError("--tick-after-seconds must be finite and non-negative")
    # The replay is a faithful trace: exactly one result per feed.  If a
    # sample crosses more than one boundary, keep the recognizer's primary
    # reason rather than inventing an additional state or sample sequence.
    rows = [_row(index, recognizer.feed(sample)) for index, sample in enumerate(samples, 1)]
    if tick_after_seconds is not None:
        if not samples or not samples[-1].host_received_at:
            raise ValueError("--tick-after-seconds requires a timestamped sample")
        clock = datetime.fromisoformat(samples[-1].host_received_at.replace("Z", "+00:00"))
        if clock.tzinfo is None:
            raise ValueError("sample timestamp requires timezone")
        rows.append(_row(len(rows) + 1, recognizer.tick(clock + timedelta(seconds=tick_after_seconds))))
    return rows


def write_outputs(target: Path, rows: list[dict[str, object]], profiles: list[dict[str, object]], summary: dict[str, object]) -> None:
    preview_rows = rows
    if len(rows) > MAX_PREVIEW_ROWS:
        head = MAX_PREVIEW_ROWS // 2
        preview_rows = rows[:head] + rows[-(MAX_PREVIEW_ROWS - head):]
    preview_truncated = len(preview_rows) != len(rows)
    summary["preview"] = {
        "rows": len(preview_rows),
        "total_rows": len(rows),
        "truncated": preview_truncated,
        "max_rows": MAX_PREVIEW_ROWS,
    }
    (target / "recognition.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    columns = ["sequence", "timestamp", "status", "reason", "kind", "kind_name", "window_count", "window_span_s", "profile_id", "label", "similarity", "nearest_distance", "runner_up_distance", "margin", "candidate_id", "confirmation_count", "center", "stddev", "ranges"]
    with (target / "recognition.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows({column: json.dumps(row.get(column)) if column in ("center", "stddev", "ranges") else row.get(column) for column in columns} for row in rows)
    (target / "profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    data = json.dumps(preview_rows, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    # Do not duplicate potentially large per-line rejection diagnostics in the
    # browser document; the complete evidence remains in summary.json.
    preview_summary = {key: value for key, value in summary.items() if key not in {"evaluation", "training"}}
    info = json.dumps(preview_summary, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>M2 磁吸外壳识别离线回放</title><style>
body{font:15px system-ui,sans-serif;background:#101722;color:#edf3fb;max-width:1180px;margin:auto;padding:24px}h1{font-size:25px}p{color:#b8c8dc;line-height:1.65}.panel{background:#182437;border-radius:10px;padding:14px;margin:16px 0;overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px;border-bottom:1px solid #30435c;text-align:left}th{color:#b8c8dc}.known{color:#64e0b1}.unknown,.ambiguous{color:#ffd07d}.unstable,.missing{color:#ff9999}.warming_up{color:#9db8ff}pre{white-space:pre-wrap;padding:14px;border-radius:10px;font-size:12px}input{width:100%}canvas{width:100%;height:220px}
</style><h1>M2 磁吸外壳识别离线回放</h1><p>电脑端离线回放，不连接设备。分数表示距离相似度，并非概率；历史日志没有被自动赋予外壳标签。缺数据不等于外壳被取下。XYZ 单位为传输计数值。</p>
<div class="panel"><strong id="meta"></strong><p id="previewNotice"></p><canvas id="chart" width="1080" height="220"></canvas><label>选择样本<input id="position" type="range" min="0" value="0"></label><pre id="detail"></pre></div>
<div class="panel"><p>显示所选样本附近最多 25 条；完整数据见 recognition.csv/jsonl。若数据量过大，图表和详情只载入摘要预览。</p><table><thead><tr><th>#</th><th>状态</th><th>原因</th><th>候选</th><th>分数</th><th>距离</th><th>差距</th><th>窗口</th><th>确认</th></tr></thead><tbody id="rows"></tbody></table></div>
<details class="panel"><summary>运行摘要与登记档案</summary><pre id="summary"></pre></details><script id="data" type="application/json">__DATA__</script><script id="info" type="application/json">__INFO__</script><script>
const rows=JSON.parse(document.querySelector('#data').textContent),summary=JSON.parse(document.querySelector('#info').textContent),slider=document.querySelector('#position');
document.querySelector('#meta').textContent=`${summary.source_kind} / ${summary.states_written||rows.length} 个状态 / ${summary.status_counts.known||0} 次已知匹配`;
document.querySelector('#previewNotice').textContent=summary.preview&&summary.preview.truncated?`当前为 ${summary.preview.rows} / ${summary.preview.total_rows} 条的安全预览；完整结果见 recognition.csv/jsonl。`:'';
document.querySelector('#summary').textContent=JSON.stringify(summary,null,2);slider.max=Math.max(0,rows.length-1);slider.disabled=!rows.length;
const names={known:'已知',unknown:'未知',unstable:'不稳定',warming_up:'积累/确认中',ambiguous:'难以区分',missing:'缺数据'};
const fmt=n=>typeof n==='number'?Number(n.toFixed(3)):n??'';
function render(){let i=Number(slider.value);document.querySelector('#detail').textContent=rows[i]?JSON.stringify(rows[i],null,2):'尚无有效样本';
const body=document.querySelector('#rows');body.replaceChildren();for(const r of rows.slice(Math.max(0,i-12),i+13)){const tr=document.createElement('tr');let values=[r.sequence,names[r.status]||r.status,r.reason,r.label||r.candidate_id||'',fmt(r.similarity),fmt(r.nearest_distance),fmt(r.margin),r.window_count,r.confirmation_count];for(const [j,v]of values.entries()){const td=document.createElement('td');td.textContent=v;if(j===1)td.className=r.status;tr.append(td);}body.append(tr);}
const canvas=document.querySelector('#chart'),ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height,p=26;ctx.clearRect(0,0,w,h);let vals=rows.flatMap(r=>r.center||[]);let lo=vals.length?Math.min(...vals):0,hi=vals.length?Math.max(...vals):1;if(lo===hi){lo--;hi++;}const x=j=>p+(w-2*p)*((rows[j]?.sequence||1)-1)/Math.max(1,(summary.states_written||rows.length)-1),y=v=>h-p-(v-lo)*(h-2*p)/(hi-lo);
for(const[a,color]of [[0,'#63d6bd'],[1,'#ffd080'],[2,'#9caaff']]){ctx.strokeStyle=color;ctx.beginPath();let open=false;rows.forEach((r,j)=>{if(!r.center){open=false;return;}if(j&&r.sequence!==rows[j-1].sequence+1)open=false;if(open)ctx.lineTo(x(j),y(r.center[a]));else ctx.moveTo(x(j),y(r.center[a]));open=true;});ctx.stroke();}ctx.strokeStyle='#edf3fb';ctx.beginPath();ctx.moveTo(x(i),p);ctx.lineTo(x(i),h-p);ctx.stroke();ctx.fillStyle='#b8c8dc';ctx.font='13px system-ui';ctx.fillText('窗口均值 X / Y / Z · 横轴为样本序号；省略区段不连线',p,18);}
slider.addEventListener('input',render);render();
</script></html>'''
    rendered = html.replace("__DATA__", data).replace("__INFO__", info)
    if len(rendered.encode("utf-8")) > MAX_PREVIEW_BYTES:
        raise ValueError(f"HTML preview exceeds {MAX_PREVIEW_BYTES} byte safety limit")
    (target / "preview.html").write_text(rendered, encoding="utf-8")
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="M1 samples.jsonl/output directory or raw log")
    parser.add_argument("--register", action="append", default=[], metavar="LABEL=PATH", help="explicit labelled training recording; repeatable")
    parser.add_argument("--output", help="subdirectory under artifacts/m2-magnetic-recognition-20261016")
    parser.add_argument("--synthetic-fixture", action="store_true", help="mark inputs as synthetic test data")
    parser.add_argument("--tick-after-seconds", type=float)
    parser.add_argument("--window-size", type=int, default=8)
    parser.add_argument("--confirmations-required", type=int, default=3)
    parser.add_argument("--match-radius", type=float, default=80.0)
    parser.add_argument("--allow-rejected-evaluation", "--allow-rejected", dest="allow_rejected_evaluation", action="store_true", help="allow rejected/truncated evaluation input; summary remains invalid")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    evaluation, evaluation_metadata = load_samples(args.input)
    config = RecognitionConfig(window_size=args.window_size, confirmations_required=args.confirmations_required, match_radius=args.match_radius)
    recognizer, training = build_recognizer(args.register, config)
    if any(record["training"]["content_sha256"] == evaluation_metadata["content_sha256"] for record in training):
        raise ValueError("training and evaluation must use separate recordings")
    evaluation_stats = evaluation_metadata.get("m1_stats", {})
    evaluation_rejected = bool(evaluation_metadata["rejected_records"] or evaluation_stats.get("rejected_lines") or any(evaluation_stats.get(key, 0) for key in ("rejected_checksum", "rejected_length", "rejected_payload", "incomplete_tail_bytes")))
    if evaluation_rejected and not args.allow_rejected_evaluation:
        details = evaluation_metadata["rejected_records"] or evaluation_stats.get("rejected_lines", [])
        reason = "; first rejection: " + str(details[0]["reason"]) if details else ""
        raise ValueError("evaluation input contains rejected/truncated records; repair it or pass --allow-rejected-evaluation" + reason)
    if not evaluation:
        raise ValueError("evaluation input contains no valid samples; regenerate it with the current M1 parser")
    _validate_evaluation_timestamps(evaluation)
    rows = replay(recognizer, evaluation, args.tick_after_seconds)
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    summary = {"schema": "deskbot.magnetic.recognition.replay.v1", "source_kind": "synthetic_fixture" if args.synthetic_fixture else "recorded_with_manual_profiles" if training else "recorded_unlabelled", "hardware_io": False, "serial_opened": False, "profiles_are_explicitly_registered": bool(args.register), "ground_truth_available": False, "similarity_is_probability": False, "config": asdict(config), "limits": {"max_input_bytes": MAX_INPUT_BYTES, "max_input_lines": MAX_INPUT_LINES, "max_input_samples": MAX_INPUT_SAMPLES, "max_rejected_records": MAX_REJECTED_RECORDS, "max_preview_rows": MAX_PREVIEW_ROWS, "max_preview_bytes": MAX_PREVIEW_BYTES}, "evaluation": evaluation_metadata, "evaluation_input_valid": not evaluation_rejected, "rejected_evaluation_bypass": bool(evaluation_rejected and args.allow_rejected_evaluation), "training": training, "samples_evaluated": len(evaluation), "states_written": len(rows), "status_counts": status_counts, "profiles": [record["profile"] for record in training], "note": "No identity label is inferred for historic logs. Real upper/lower/removed recordings and manual acceptance are still required. Evaluation may include gaps or sensor changes so missing/recovery behavior can be inspected."}
    target = output_dir(args.output)
    write_outputs(target, rows, training, summary)
    print(json.dumps({"output": str(target), **summary}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
