#!/usr/bin/env python3
"""Build the self-contained, offline E1.33 candidate import workbench."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_STAGE = ROOT / "tmp/expression-e1-33-import-20261029-final"
REPORT = AUDIT_STAGE / "report.json"
TEMPLATE = ROOT / "scripts/expression-e1-33-import.template.html"
EAF = ROOT.parent.parent / "tmp/research-emote-tool/sample-eaf/shy_20s_40s.eaf"
OUT = ROOT / "artifacts/expression-e1-33-import-20261029"
LOCAL = ROOT / "tmp/expression-e1-33-import-20261029-preview"
E131 = ROOT / "artifacts/role-expression-workbench-e1-31-20261029/index.html"
CANDIDATE_ATLAS_SHA256 = "e4c9159f039adc2e800cb87a15bdeb1790db6b97c061d115547a3c29c7712184"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def extract_json(path: Path, script_id: str) -> dict:
    match = re.search(
        rf'<script id="{re.escape(script_id)}" type="application/json">(.*?)</script>',
        path.read_text(encoding="utf-8"), re.S,
    )
    if not match:
        raise ValueError(f"{script_id} payload is missing from {path}")
    value = json.loads(match.group(1))
    if not isinstance(value, dict):
        raise ValueError(f"{script_id} payload must be an object")
    return value


def write_if_changed(path: Path, raw: bytes, check: bool) -> None:
    if check:
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"stale generated artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def build_payload(report: dict, *, check: bool, local: bool = False) -> dict:
    if report.get("edition") != "E1.33" or report.get("audit_pass") is not True:
        raise ValueError("E1.33 audit report is not a passing report")
    if report.get("deployment_eligible") is not False or report.get("hardware_accessed") is not False:
        raise ValueError("E1.33 report crosses the offline boundary")
    native = AUDIT_STAGE / "native"
    full_atlas = AUDIT_STAGE / "candidate-full-atlas.png"
    if local and not full_atlas.is_file():
        if check:
            raise ValueError("candidate full atlas is missing")
        sys.path.insert(0, str(ROOT))
        from protocol import eaf_preview
        decoded = eaf_preview.decode_eaf(EAF, fps=report["candidate"]["source_fps"])
        eaf_preview.write_rgba_atlas(full_atlas, decoded.frames, decoded.width, decoded.height, columns=8)
    if local and sha(full_atlas.read_bytes()) != CANDIDATE_ATLAS_SHA256:
        raise ValueError("candidate atlas differs from the audited decoded frames")
    current = extract_json(E131, "e131-data")["assets"]["Happy.eaf"]
    renderer = report["official_player"]["renderer"]
    atlas_meta = renderer["preview_atlases"]
    if local:
        for meta in atlas_meta.values():
            if sha((native / meta["path"]).read_bytes()) != meta["sha256"]:
                raise ValueError("official C preview atlas hash mismatch")
    current_png = base64.b64decode(current["atlas"].split(",", 1)[1], validate=True)
    if (sha(current_png) != current["atlas_sha256"]
            or current["deployed_sha256"] != "2ed3a64ecdb7a78e2c9959bfc3758c2742d5447d4a2bcdc6d2bf072763deba5c"
            or (current["frame_width"], current["frame_height"], current["frame_count"], current["fps"])
               != (125, 160, 42, 20)):
        raise ValueError("current Happy preview or deployed source anchor drift")
    payload = {
        "schema": "deskbot.expression_e1_33_import_workbench/v1",
        "edition": "E1.33",
        "date": report["date"],
        "hardware_accessed": False,
        "firmware_changed": False,
        "deployment_eligible": False,
        "flash_authorized": False,
        "media_scope": "local_research" if local else "public_metadata_only",
        "candidate": {
            "name": "shy_candidate.eaf",
            "source_name": report["candidate"]["source_gif"]["member"],
            "sha256": report["candidate"]["sha256"],
            "bytes": report["candidate"]["bytes"],
            "frame_count": report["candidate"]["structure"]["image_frame_count"],
            "stored_frame_count": report["candidate"]["structure"]["stored_frame_count"],
            "width": report["candidate"]["structure"]["dimensions"][0][0],
            "height": report["candidate"]["structure"]["dimensions"][0][1],
            "frame_width": report["candidate"]["structure"]["dimensions"][0][0],
            "frame_height": report["candidate"]["structure"]["dimensions"][0][1],
            "structure": report["candidate"]["structure"],
            "bit_depth": report["candidate"]["structure"]["bit_depths"][0],
            "encodings": report["candidate"]["structure"]["encodings"],
            "fps": report["candidate"]["source_fps"],
            "source_loop": report["candidate"]["source_loop"],
            "columns": 8,
            "atlas_data_url": data_url(full_atlas) if local else None,
            "atlas_sha256": sha(full_atlas.read_bytes()) if local else None,
            "atlas_bytes": full_atlas.stat().st_size if local else None,
            "publication_media_omitted": not local,
            "object_kind": "full_screen",
        },
        "current": {
            "name": "Happy.eaf",
            "semantic": "embarrassed",
            "fps": current["fps"],
            "loop": current["loop"],
            "frame_count": current["frame_count"],
            "stored_frame_count": current["stored_frame_count"],
            "frame_width": current["frame_width"],
            "frame_height": current["frame_height"],
            "columns": current["columns"],
            "atlas_data_url": current["atlas"],
            "atlas_sha256": current["atlas_sha256"],
            "object_kind": current["object_kind"],
        },
        "official_player": {
            "source_commit": report["official_player"]["source_commit"],
            "renderer_layouts_passed": report["compatibility"]["renderer_layouts_passed"],
            "rendered_frame_count": renderer["rendered_frame_count"],
            "preview_atlases": {
                name: {**meta, "data_url": data_url(native / meta["path"]) if local else None,
                       "publication_media_omitted": not local}
                for name, meta in atlas_meta.items()
            },
            "explicit_range": {key: renderer["explicit_range"][key] for key in (
                "start", "end", "end_exclusive", "prepared_image_range", "completion_holds_last_image_for_one_tick",
                "renderer_accepts_range")},
            "renderer": {key: renderer[key] for key in (
                "rendered_frame_count", "all_reference_frames_equal", "all_guards_clean", "explicit_range")},
        },
        "capacity": report["capacity"],
        "negative_cases": report["negative_cases"],
        "compatibility": report["compatibility"],
        "source_anchors": {
            "candidate_gif_sha256": report["candidate"]["source_gif"]["sha256"],
            "candidate_zip_sha256": "946654de50253949b2c687a2b6b53bf3b66fdd699ab107950258e2a830cf15e6",
            "e1_18_rollback_partition_sha256": report["capacity"]["rollback_partition_sha256"],
        },
        "limits": [
            "候选只在浏览器中预览，未接线到正式语义，也不生成刷写包。",
            "完整候选图集用于本机离线审阅；来源素材的再分发许可仍需单独确认。",
            "官方产品索引支持 loop/fps，但不能直接表达候选的局部 [50,150) 循环。",
            "full_screen 与 eye_anim 都通过 C 播放器不等于候选已经适合眼睛布局。",
        ],
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify generated artifacts without rewriting")
    parser.add_argument("--local", action="store_true", help="include research media under ignored tmp only")
    args = parser.parse_args()
    report_path = REPORT if REPORT.is_file() else OUT / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    data = build_payload(report, check=args.check, local=args.local)
    output = LOCAL if args.local else OUT
    template = TEMPLATE.read_text(encoding="utf-8")
    html = template.replace("__E133_DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    if "__E133_DATA__" in html:
        raise ValueError("unexpanded E1.33 template token")
    report_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    readme = ("DeskBot E1.33 新动画导入与兼容性审计 / Offline candidate import workbench\n\n"
              "Open index.html directly or through a loopback HTTP server.\n"
              "The page compares the current Happy.eaf eye_anim mapping with an unconnected full-screen shy candidate.\n"
              "This stage is read-only: no COM, network, firmware, NVS, C61, or flashable package.\n"
              "The candidate media is retained for local review while redistribution permission is reviewed.\n").encode("utf-8")
    write_if_changed(output / "index.html", html.encode("utf-8"), args.check)
    write_if_changed(output / "report.json", report_bytes, args.check)
    write_if_changed(output / "README.txt", readme, args.check)
    print(json.dumps({"result": "checked" if args.check else "built", "edition": "E1.33",
                      "output": str(output), "media_scope": data["media_scope"],
                      "candidate_atlas_bytes": data["candidate"]["atlas_bytes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
