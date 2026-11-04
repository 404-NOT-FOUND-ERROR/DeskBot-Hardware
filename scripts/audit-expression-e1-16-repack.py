#!/usr/bin/env python3
"""Audit a local E1.13 package and a one-token index replacement, offline only.

Raw inputs must be supplied explicitly. Public results contain hashes, never
device IDs or local input paths. There is deliberately no deployment command.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "artifacts/expression-e1-16-repack-20261024"
LOCAL = ROOT / "tmp/expression-e1-16-repack-20261024"
BASELINE_SHA = "5e342db35da13531836339a98c25d6e74ac6fdff446ad4e515fcf39f28b7e81f"
OFFICIAL_SHA = "56b39e7fdd14b9dd0de9b22d3aedc9b04383376dbdef70925675f639d763cd5b"
READBACK_SHA = "6b1bce87fc77bd4482fa73033b34f8ad57d4397dc6631d1475d254300c70c205"
BASELINE_BYTES = 5_538_349
PARTITION_BYTES = 6_144_000
E113_MEMBERS = {
    "insert.eaf": "2eb63094840a657a7e6bc5841dbacbfdda809151f344c403dee4255c9743f744",
    "eat.eaf": "2abe739070ca8e3ae0bd437360f94b24a74d2bf0be6a86eb3c33ba223fef5030",
}
PROPOSAL = "artifacts/expression-e1-15-surprised-shocked-20261024/decision.json"


def load_module():
    spec = importlib.util.spec_from_file_location("e116_package", ROOT / "scripts/audit-expression-package-candidate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PACKAGE = load_module()
AUDIT = PACKAGE.AUDIT


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


DECODER = json.JSONDecoder(object_pairs_hook=unique_pairs)


def ws(text: str, offset: int) -> int:
    while offset < len(text) and text[offset] in " \r\n\t":
        offset += 1
    return offset


def field_spans(text: str, start: int) -> dict[str, tuple[int, int]]:
    """Use JSON decoding, not regex/global replace, to locate object values."""
    if text[start] != "{":
        raise ValueError("expected JSON object")
    spans, offset = {}, ws(text, start + 1)
    while text[offset] != "}":
        key, end = DECODER.raw_decode(text, offset)
        if not isinstance(key, str) or key in spans:
            raise ValueError("invalid/duplicate object key")
        offset = ws(text, end)
        if text[offset] != ":":
            raise ValueError("missing object colon")
        value_start = ws(text, offset + 1)
        _, end = DECODER.raw_decode(text, value_start)
        spans[key] = (value_start, end)
        offset = ws(text, end)
        if text[offset] == "}":
            break
        if text[offset] != ",":
            raise ValueError("missing object separator")
        offset = ws(text, offset + 1)
    return spans


def patch_index(raw: bytes, before: str = "Happy.eaf", after: str = "shocked.eaf") -> tuple[bytes, dict]:
    """Preserve every UTF-8 byte except the unique surprised file string token."""
    text = raw.decode("utf-8")
    original, end = DECODER.raw_decode(text, ws(text, 0))
    if ws(text, end) != len(text) or not isinstance(original, dict):
        raise ValueError("invalid index root/trailing data")
    entries = original.get("emoji_collection")
    if not isinstance(entries, list) or not entries or not all(isinstance(x, dict) for x in entries):
        raise ValueError("missing emoji collection")
    names = [entry.get("name") for entry in entries]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        raise ValueError("invalid/duplicate semantic identity")
    targets = [entry for entry in entries if entry["name"] == "surprised"]
    if len(targets) != 1 or targets[0].get("file") != before:
        raise ValueError("surprised baseline drift")
    if targets[0].get("eaf") != {"loop": True, "fps": 20}:
        raise ValueError("surprised timing drift")
    array_start, _ = field_spans(text, ws(text, 0))["emoji_collection"]
    if text[array_start] != "[":
        raise ValueError("expected emoji array")
    offset = ws(text, array_start + 1)
    span = None
    for entry in entries:
        value, end = DECODER.raw_decode(text, offset)
        if value != entry:
            raise ValueError("index span mismatch")
        if entry["name"] == "surprised":
            span = field_spans(text, offset)["file"]
        offset = ws(text, end)
        if text[offset] == ",":
            offset = ws(text, offset + 1)
    if span is None or text[offset] != "]":
        raise ValueError("surprised token not found")
    a, b = span
    byte_start, byte_end = len(text[:a].encode("utf-8")), len(text[:b].encode("utf-8"))
    old_token = raw[byte_start:byte_end]
    new_token = json.dumps(after, ensure_ascii=False).encode("utf-8")
    changed = raw[:byte_start] + new_token + raw[byte_end:]
    expected = copy.deepcopy(original)
    next(item for item in expected["emoji_collection"] if item["name"] == "surprised")["file"] = after
    if json.loads(changed, object_pairs_hook=unique_pairs) != expected:
        raise ValueError("index patch changed unrelated JSON")
    restored = changed[:byte_start] + old_token + changed[byte_start + len(new_token):]
    if restored != raw:
        raise ValueError("index rollback is not byte-exact")
    return changed, {"semantic": "surprised", "before": before, "after": after,
                     "byte_offset": byte_start, "old_token_bytes": len(old_token),
                     "new_token_bytes": len(new_token), "delta_bytes": len(changed) - len(raw),
                     "method": "replace one parsed JSON string token; retain CRLF, spacing and all other bytes",
                     "all_other_index_bytes_unchanged": True, "exact_token_rollback": True}


def repack(raw: bytes) -> tuple[bytes, bytes, dict]:
    members = AUDIT.parse_package_bytes(raw, modern=False)["members"]
    if PACKAGE.build_legacy_package(members) != raw:
        raise ValueError("baseline is not byte-reproducible by anchored packer")
    for name in ("Happy.eaf", "shocked.eaf", "index.json"):
        if name not in members:
            raise ValueError("required resource missing: " + name)
    candidate = dict(members)
    candidate["index.json"], edit = patch_index(members["index.json"])
    packed = PACKAGE.build_legacy_package(candidate)
    decoded = AUDIT.parse_package_bytes(packed, modern=False)["members"]
    if decoded != candidate or list(decoded) != list(members):
        raise ValueError("candidate round-trip/order failed")
    if [name for name in members if members[name] != decoded[name]] != ["index.json"]:
        raise ValueError("candidate changed non-index members")
    decoded["index.json"] = members["index.json"]
    rollback = PACKAGE.build_legacy_package(decoded)
    if rollback != raw:
        raise ValueError("package rollback is not byte-exact")
    return packed, rollback, edit


def summary(raw: bytes) -> dict:
    parsed = AUDIT.parse_package_bytes(raw, modern=False)
    return {"bytes": len(raw), "sha256": sha(raw), "remaining_bytes": PARTITION_BYTES - len(raw),
            "member_count": parsed["header"]["member_count"], "checksum_valid": True}


def audit(baseline: bytes, official: bytes, readback: bytes) -> tuple[dict, dict[str, bytes]]:
    if len(baseline) != BASELINE_BYTES or sha(baseline) != BASELINE_SHA:
        raise ValueError("accepted E1.13 package hash/size mismatch")
    if sha(official) != OFFICIAL_SHA:
        raise ValueError("official package hash mismatch")
    if (len(readback) != PARTITION_BYTES or sha(readback) != READBACK_SHA
            or readback != baseline + b"\xff" * (PARTITION_BYTES - len(baseline))):
        raise ValueError("archived E1.13 readback mismatch")
    current = AUDIT.parse_package_bytes(baseline, modern=False)["members"]
    upstream = AUDIT.parse_package_bytes(official, modern=False)["members"]
    if len(current) != 29 or set(current) != set(upstream):
        raise ValueError("E1.13 membership mismatch")
    upstream_changes = sorted(name for name in current if current[name] != upstream[name])
    if upstream_changes != ["eat.eaf", "insert.eaf"]:
        raise ValueError("unexpected E1.13 difference from official")
    if any(sha(current[name]) != digest for name, digest in E113_MEMBERS.items()):
        raise ValueError("E1.13 compressed member hash mismatch")
    proposal_raw = (ROOT / PROPOSAL).read_bytes()
    proposal = json.loads(proposal_raw)
    if (proposal["proposal"]["semantic"], proposal["proposal"]["before"], proposal["proposal"]["after"]) != (
            "surprised", "Happy.eaf", "shocked.eaf"):
        raise ValueError("E1.15 proposal drift")
    packed, rollback, edit = repack(baseline)
    candidate_members = AUDIT.parse_package_bytes(packed, modern=False)["members"]
    original_index = json.loads(current["index.json"])
    changed_index = json.loads(candidate_members["index.json"])
    if original_index["layout"] != changed_index["layout"]:
        raise ValueError("layout changed")
    actions = proposal["action_maps"]
    if actions["before"] != actions["proposed"] or actions["before"]["surprised"] != "look_around":
        raise ValueError("E1.15 action mapping drift")
    missing = lambda index: sorted({entry["file"] for key in ("emoji_collection", "icon_collection")
                                  for entry in index[key]} - current.keys())
    if missing(original_index) != missing(changed_index):
        raise ValueError("new unresolved resource references")
    eaf_names = sorted(name for name in current if name.endswith(".eaf"))
    for name in ("Happy.eaf", "shocked.eaf"):
        AUDIT.parse_eaf_bytes(current[name])
    padded = packed + b"\xff" * (PARTITION_BYTES - len(packed))
    report = {
        "schema": "deskbot.expression_actual_repack/v1", "edition": "E1.16", "date": "2026-10-24",
        "result": "offline_repack_verified", "deployment_eligible": False,
        "safety": {"hardware_accessed": False, "serial_ports_accessed": [], "firmware_built": False,
                   "firmware_changed": False, "c61_changed": False, "nvs_changed": False,
                   "device_written": False, "flash_authorized": False},
        "inputs": {"baseline": "local archived E1.12 final package deployed and accepted as E1.13",
                   "readback": "archived post-E1.13-flash full assets readback; not a new device read",
                   "proposal": {"path": PROPOSAL, "sha256": sha(proposal_raw)},
                   "raw_inputs_in_public_checkout": False},
        "baseline": summary(baseline), "official": summary(official), "candidate": summary(packed),
        "partition": {"offset_hex": "0x5EC000", "bytes": PARTITION_BYTES,
                      "end_exclusive_hex": "0xBC8000", "archived_readback_sha256": sha(readback),
                      "baseline_padding_verified": True, "candidate_padded_sha256": sha(padded),
                      "candidate_padded_bytes": len(padded), "device_partition_image_written": False},
        "index_patch": edit | {"before_sha256": sha(current["index.json"]),
                               "after_sha256": sha(candidate_members["index.json"])},
        "member_diff": {"added": [], "removed": [], "changed": ["index.json"],
                        "baseline_changes_from_official": upstream_changes,
                        "eaf_payloads_changed": [], "eaf_count": len(eaf_names),
                        "unchanged_member_count": 28, "member_order_unchanged": True},
        "members": [{"name": name, "baseline_bytes": len(raw), "baseline_sha256": sha(raw),
                     "candidate_bytes": len(candidate_members[name]),
                     "candidate_sha256": sha(candidate_members[name]),
                     "byte_identical": raw == candidate_members[name]}
                    for name, raw in current.items()],
        "preserved": {"other_semantics": True, "timing": True, "layout": True, "actions": True,
                      "surprised_action": "look_around", "fps": 20, "loop": True,
                      "happy_remaining_semantics": [entry["name"] for entry in changed_index["emoji_collection"]
                                                    if entry["file"] == "Happy.eaf"],
                      "inherited_unresolved_icons": missing(original_index),
                      "new_unresolved_references": []},
        "rollback": {"package_byte_exact": rollback == baseline, "package_sha256": sha(rollback),
                     "full_partition_byte_exact": rollback + b"\xff" * (PARTITION_BYTES - len(rollback)) == readback,
                     "full_partition_sha256": sha(readback),
                     "method": "restore retained original index bytes and repack; compare entire package and padded partition"},
        "capacity": {"measurement": "actual serialized package, not projection",
                     "delta_bytes": len(packed) - len(baseline),
                     "new_eaf_bytes": 0, "deleted_eaf_bytes": 0,
                     "fits_partition": len(packed) <= PARTITION_BYTES,
                     "historical_minus_381_not_used": True},
        "gates": [{"id": "actual_package", "status": "passed", "label": "实际包、成员摘要、边界与精确回退"},
                  {"id": "player_compatibility", "status": "pending", "label": "当前播放器静态路径和兼容性复核"},
                  {"id": "visual_selection", "status": "pending", "label": "用户审阅单语义合并的实际表达"},
                  {"id": "device_authorization", "status": "pending", "label": "单独授权本体 assets 部署"},
                  {"id": "hardware_regression", "status": "pending", "label": "圆屏、菜单/对话、摸头、睡眠唤醒；底座维修后补验"}],
        "limitations": ["No LCD, runtime timing, heap or UI concurrency acceptance is inferred.",
                        "A semantic alias reuse adds no new animation and changes no motor action mapping.",
                        "Inherited absent icon_speaker/icon_tips references are recorded, not repaired here."],
    }
    return report, {"candidate-offline-do-not-flash.bin": packed, "rollback-e1-13-exact.bin": rollback}


def immutable(path: Path, raw: bytes) -> None:
    root = ROOT.resolve()
    if (not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError("unsafe output path")
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("refusing to overwrite different evidence: " + path.name)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--readback", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="recompute and compare existing evidence without writes")
    args = parser.parse_args()
    report, binaries = audit(args.baseline.read_bytes(), args.official.read_bytes(), args.readback.read_bytes())
    template = (ROOT / "scripts/expression-e1-16-repack.template.html").read_text(encoding="utf-8")
    if template.count("__E116_DATA__") != 1:
        raise ValueError("invalid template marker count")
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    outputs = {PUBLIC / "report.json": json_bytes(report),
               PUBLIC / "index.html": template.replace("__E116_DATA__", payload).encode("utf-8"),
               PUBLIC / "README.txt": b"E1.16 actual E1.13 package repack audit. Open index.html.\nOffline only; raw inputs and candidate/rollback packages remain private local evidence.\nNo device access or deployment authorization.\n"}
    outputs.update({LOCAL / name: raw for name, raw in binaries.items()})
    for path, raw in outputs.items():
        if args.check:
            if not path.is_file() or path.read_bytes() != raw:
                raise ValueError("stale/missing evidence: " + path.name)
        else:
            immutable(path, raw)
    print(json.dumps({"result": report["result"], "check_only": args.check,
                      "candidate": report["candidate"], "delta_bytes": report["capacity"]["delta_bytes"],
                      "rollback_exact": report["rollback"]["package_byte_exact"], "hardware_accessed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
