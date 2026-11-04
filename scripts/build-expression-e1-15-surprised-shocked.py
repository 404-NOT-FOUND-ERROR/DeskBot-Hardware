#!/usr/bin/env python3
"""Build a public, offline single-semantic proposal; no device/package writes."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/expression-e1-15-surprised-shocked-20261024"
TEMPLATE = ROOT / "scripts/expression-e1-15-surprised-shocked.template.html"
MATRIX = "artifacts/expression-binding-matrix-e1-9-20261022/matrix.json"
BINDINGS = "artifacts/expression-binding-e1-4a-20261021/bindings.json"
REVIEW = "artifacts/expression-e1-10-review-20261022/report.json"
PREVIEW = "artifacts/expression-e1-10-review-20261022/index.html"
DECISION = "artifacts/expression-binding-decision-e1-14-20261024/decision.json"
CAPACITY_SNAPSHOT = ROOT / "artifacts/expression-binding-decision-e1-14-20261024/capacity-evidence-e1-13.md"
CAPACITY_SNAPSHOT_SHA256 = "5170a89feefd1a0f900ec76f74bc64b4394ffd6f82d4a588283a33d005cbc46c"
INPUT_HASHES = {
    MATRIX: "f2867059513910d91d21a534b5953869dd5312eadcfb961494fc5ae6d29d90c1",
    BINDINGS: "98d57c67e52e240cb7ef0ed8c99cfc088f834fbf662424cd48c2c03c50bd4921",
    REVIEW: "facf6a60fbdaff3247ff82c30df7f646f16907474233040ddee305cff1e32114",
    PREVIEW: "ca8d1dd0a9c0739d246127b2f6ffbd041367777d0906bebff4857bed8e89484d",
    DECISION: "e2a90ecbf25b35329bfa7801d746f49cfdd694c6ec60e1e9ba43f8827d81a75d",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read_pinned(relative: str) -> bytes:
    raw = (ROOT / relative).read_bytes()
    if digest(raw) != INPUT_HASHES[relative]:
        raise ValueError("pinned input drift: " + relative)
    return raw


def propose(semantics: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return logical snapshots, deliberately not a serialized device index."""
    before = copy.deepcopy(semantics)
    if len({item["id"] for item in before}) != len(before):
        raise ValueError("duplicate semantic identity")
    targets = [item for item in before if item["id"] == "surprised"]
    if len(targets) != 1 or targets[0]["src"] != "Happy.eaf":
        raise ValueError("surprised baseline drift")
    after = copy.deepcopy(before)
    next(item for item in after if item["id"] == "surprised")["src"] = "shocked.eaf"
    return before, after


def restore(after: list[dict], before: list[dict]) -> list[dict]:
    restored = copy.deepcopy(after)
    target = next(item for item in restored if item["id"] == "surprised")
    original = next(item for item in before if item["id"] == "surprised")
    if target["src"] != "shocked.eaf":
        raise ValueError("rollback input drift")
    target["src"] = original["src"]
    if restored != before:
        raise ValueError("rollback would mask unrelated changes")
    return restored


def build() -> tuple[dict, dict]:
    raw = {name: read_pinned(name) for name in INPUT_HASHES}
    matrix, bindings, history, prior = (json.loads(raw[name]) for name in (MATRIX, BINDINGS, REVIEW, DECISION))
    match = re.search(r'<script id="data" type="application/json">(.*?)</script>',
                      raw[PREVIEW].decode("utf-8"), re.S)
    if match is None:
        raise ValueError("missing official preview payload")
    archived_preview = json.loads(match[1])["preview"]
    preview = {key: copy.deepcopy(archived_preview[key]) for key in ("happy", "shocked")}
    resources = {item["event_or_entry"]: item for item in matrix["resources"]
                 if item["row_type"] == "official_eaf_resource"}
    for key, name in (("happy", "Happy.eaf"), ("shocked", "shocked.eaf")):
        source_path = "firmware/body/" + resources[name]["resource_path"]
        if digest((ROOT / source_path).read_bytes()) != resources[name]["resource_sha256"]:
            raise ValueError("official EAF input drift: " + name)
        if (preview[key]["fps"], preview[key]["layout"], preview[key]["frame_width"],
                preview[key]["frame_height"]) != (20, "eye", 360, 360):
            raise ValueError("preview layout drift")
        if not preview[key]["data_url"].startswith("data:image/png;base64,"):
            raise ValueError("official preview must be embedded PNG")

    before, after = propose(bindings["semantics"])
    restored = restore(after, before)
    actions = bindings["emotion_action_map"]
    target = next(item for item in before if item["id"] == "surprised")
    if actions != history["official_emotion_action_map"] or actions["surprised"] != "look_around":
        raise ValueError("official action mapping drift")
    if (target["fps"], target["loop"]) != (20, True):
        raise ValueError("surprised timing drift")
    capacity = copy.deepcopy(prior["capacity"])
    if (capacity["partition_bytes"], capacity["package_bytes"], capacity["remaining_bytes"],
            capacity["member_count"]) != (6_144_000, 5_538_349, 605_651, 29):
        raise ValueError("accepted E1.13 capacity drift")
    record = CAPACITY_SNAPSHOT.read_bytes()
    if digest(record) != CAPACITY_SNAPSHOT_SHA256:
        raise ValueError("pinned E1.13 capacity snapshot changed")
    acceptance = (ROOT / "docs/e1-13-acceptance-20261024.md").read_bytes()
    if (capacity["package_sha256"] not in record.decode("utf-8")
            or "605,651 B" not in record.decode("utf-8") or "`accepted`" not in acceptance.decode("utf-8")
            or digest(acceptance) != prior["source"]["acceptance_record_sha256"]):
        raise ValueError("accepted baseline record missing")
    historical = next(item for item in history["semantic_plans"] if item["id"] == "surprised-reuse-shocked")
    delta = historical["package"]["delta_bytes"]
    if (delta, historical["index_delta_bytes"], historical["package_table_and_separator_delta_bytes"],
            historical["added_resource_bytes"]) != (-381, -381, 0, 0):
        raise ValueError("historical index delta drift")
    references = lambda rows, name: [item["id"] for item in rows if item["src"] == name]
    changed = [a["id"] for a, b in zip(before, after) if a != b]
    if changed != ["surprised"]:
        raise ValueError("proposal is not single-semantic")
    diff = len("shocked.eaf".encode("utf-8")) - len("Happy.eaf".encode("utf-8"))
    data = {
        "schema": "deskbot.expression_single_semantic_proposal/v1", "edition": "E1.15",
        "date": "2026-10-24", "result": "offline_validated",
        "deployment_eligible": False, "replacement_authorized": False, "hardware_accessed": False,
        "source": {"official_commit": bindings["source"]["official_commit"],
                   "inputs": [{"path": name, "sha256": value} for name, value in INPUT_HASHES.items()],
                   # Inherit the recorded historical document fingerprint, not
                   # a hash of today's living status or the separate snapshot.
                   "capacity_record_sha256": prior["source"]["capacity_record_sha256"],
                   "acceptance_record_sha256": digest(acceptance),
                   "official_preview_basis": "E1.10 archived host decode; eye layout composed before atlas export"},
        "safety": {"hardware_accessed": False, "serial_ports_accessed": [], "firmware_changed": False,
                   "firmware_built": False, "device_profile_changed": False, "package_built": False,
                   "nvs_changed": False, "c61_changed": False, "candidate_media_included": False},
        "proposal": {"semantic": "surprised", "before": "Happy.eaf", "after": "shocked.eaf",
                     "changed_semantics": changed, "scope": "logical snapshot only; not device index.json",
                     "fps": target["fps"], "loop": target["loop"], "layout_id": "eye_anim",
                     "action_before": actions["surprised"], "action_after": actions["surprised"],
                     "new_visual_added": False, "visual_diversity_note": "surprised 与 shocked 将共享同一官方画面；不是新增独立动画。"},
        "semantic_snapshots": {"before": before, "proposed": after},
        "action_maps": {"before": actions, "proposed": copy.deepcopy(actions)},
        "layout": {"before": bindings["layouts"]["eye_anim"], "proposed": copy.deepcopy(bindings["layouts"]["eye_anim"]),
                   "display": {"width": 360, "height": 360, "mask": "circle"},
                   "runtime_layout_changed": False, "physical_lcd_verified": False},
        "shared_impact": {"Happy.eaf": {"before": references(before, "Happy.eaf"),
                                        "proposed": references(after, "Happy.eaf"), "retained": True, "bytes_freed": 0},
                          "shocked.eaf": {"before": references(before, "shocked.eaf"),
                                          "proposed": references(after, "shocked.eaf"), "already_present": True}},
        "member_diff": {"member_count_before": 29, "member_count_proposed": 29, "added": [], "removed": [],
                        "proposed_changed": ["index.json"], "eaf_payloads_changed": [],
                        "current_package_byte_comparison_performed": False,
                        "basis": "planned membership; E1.10 reported no resource changes; current E1.13 bin absent from public inputs"},
        "capacity": {"baseline": capacity,
                     "historical_index_projection": {"status": "projection_not_repacked", "delta_bytes": delta,
                         "package_bytes": capacity["package_bytes"] + delta,
                         "remaining_bytes": capacity["remaining_bytes"] - delta,
                         "basis": "E1.10 verified index reserialization delta applied to accepted E1.13 package size",
                         "assumptions": ["E1.13 changed insert/eat only; index bytes unchanged", "same E1.10 serializer and container", "same member order/names"],
                         "package_sha256": None, "package_built": False},
                     "filename_only_scenario": {"status": "projection_not_repacked", "delta_bytes": diff,
                         "package_bytes": capacity["package_bytes"] + diff,
                         "remaining_bytes": capacity["remaining_bytes"] - diff,
                         "basis": "one UTF-8 filename replacement with all other index bytes preserved; not a measured package"},
                     "serialization_note": "旧提案 -381 B 包含 JSON 重排，文件名本身反而 +2 B；不宣称复用释放了 EAF 容量。",
                     "rebuild_required_before_release": True},
        "rollback": {"semantic": "surprised", "from": "shocked.eaf", "to": "Happy.eaf",
                     "logical_snapshot_exact": restored == before,
                     "restored_snapshot_sha256": digest(json_bytes(restored)),
                     "original_snapshot_sha256": digest(json_bytes(before)),
                     "current_package_byte_exact_tested": False,
                     "release_requirement": "retain original serialized E1.13 index/package; restore exact bytes rather than reserialize",
                     "baseline_package_sha256": capacity["package_sha256"],
                     "baseline_partition_sha256": capacity["partition_readback_sha256"]},
        "validation": {"only_surprised_changed": True, "other_semantics_unchanged": True,
                       "fallbacks_unchanged": True, "timing_unchanged": True, "action_map_unchanged": True,
                       "layout_unchanged": True, "logical_rollback_exact": True,
                       "browser_visual_review": "not_performed", "user_visual_approval": False,
                       "device_playback_verified": False, "motor_completion_observed": False},
        "gates": [
            {"id": "visual_selection", "status": "pending", "label": "审阅当前/提案画面，确认是否采用此语义合并"},
            {"id": "actual_package", "status": "pending", "label": "基于原 E1.13 bin 独立打包，核对仅 index 变化、其他成员摘要与精确回退"},
            {"id": "player_compatibility", "status": "pending", "label": "当前 C 播放器与循环、帧率、内存边界验证"},
            {"id": "device_authorization", "status": "pending", "label": "单独授权资源分区部署与完整回读；不动 App/C61/NVS"},
            {"id": "body_regression", "status": "pending", "label": "圆屏、对话/菜单、摸头、睡眠/唤醒回归；底座触发待维修后补验"}],
    }
    return data, preview


def render_outputs() -> dict[str, bytes]:
    data, preview = build()
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("__E115_DATA__") != 1:
        raise ValueError("expected one template payload")
    embedded = json.dumps({"decision": data, "preview": preview}, ensure_ascii=False,
                          separators=(",", ":")).replace("<", "\\u003c")
    return {"decision.json": json_bytes(data),
            "index.html": template.replace("__E115_DATA__", embedded).encode("utf-8"),
            "README.txt": ("E1.15: offline surprised -> shocked.eaf proposal. Open index.html.\n"
                           "Official animations only; no firmware/package/serial/device writes.\n"
                           "Capacity is a labeled projection from accepted E1.13, not a repack.\n"
                           "Rebuild: python scripts/build-expression-e1-15-surprised-shocked.py\n").encode("utf-8")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = render_outputs()
    if OUTPUT.is_symlink() or OUTPUT.resolve().parent != (ROOT / "artifacts").resolve():
        raise ValueError("unsafe output directory")
    for name, raw in outputs.items():
        target = OUTPUT / name
        if target.is_symlink():
            raise ValueError("refusing symlink output")
        if args.check:
            if not target.is_file() or target.read_bytes() != raw:
                raise SystemExit("stale/missing artifact: " + name)
        else:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    print("E1.15 " + ("reproduction verified" if args.check else "generated") + "; offline only, no package/device writes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
