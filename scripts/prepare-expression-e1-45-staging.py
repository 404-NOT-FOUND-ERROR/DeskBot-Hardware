#!/usr/bin/env python3
"""Stage the selected E1.44 mirrored-eye asset; all operations are offline."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.eaf_legacy_terminal import inspect_stream
from protocol.storage_continuous_mmap_baseline import accepted_snapshot, load_migrated_baseline

OUTPUT = ROOT / "tmp/expression-e1-45-staging-20261104"
PUBLIC = ROOT / "artifacts/expression-e1-45-staging-20261104"
START, SIZE, FLASH_SIZE = 0x1000000, 0x800000, 0x2000000
SOFT_LIMIT = 0x700000
MEMBER = "shy_candidate.eaf"
INPUT_REPORT_SHA = "972c434bd86188a9a8fd45181d20ecbded45b71e338a9d2e7aee6c3a59a63544"
EAF_SHA = "46ac913fa186e033cba78b11ef6e7da580ab337528f80a7e33b686019aadc93c"
PACKAGE_SHA = "c6381793a88da27947651b0b7096963af80ccf6a2c8ee5f5e9df254e7c3a2c91"
ROLLBACK_SHA = "8e9dae01b99060c313a76f2dfc7c2b854b80c4584d76f607a74dd2903d39fce4"
IMAGE_SHA = "e5ac78a187866cff05a16e5fd223c934a45d6cb4bf13dfcd1766cc93db805748"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


E144 = load_module(ROOT / "scripts/audit-expression-e1-44-adaptation.py", "deskbot_e144_for_e145")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def immutable(path, raw):
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
           for p in (path, *path.parents) if p.exists()):
        raise ValueError("output path traverses a link")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("retained stage output differs: " + str(path))
    if not path.exists():
        with path.open("xb") as handle:
            handle.write(raw)


def pad_package(package):
    if type(package) is not bytes or not 12 <= len(package) <= SOFT_LIMIT:
        raise ValueError("package must fit the 7 MiB publication budget")
    return package.ljust(SIZE, b"\xff")


def extract_package(image):
    if type(image) is not bytes or len(image) != SIZE:
        raise ValueError("exact 8 MiB partition image required")
    end = 12 + struct.unpack_from("<I", image, 8)[0]
    if not 12 < end <= SOFT_LIMIT or image[end:] != b"\xff" * (SIZE - end):
        raise ValueError("invalid package length or erased padding")
    return image[:end]


def audit_member_delta(before, after, eaf):
    if set(after) != set(before) | {MEMBER} or MEMBER in before or after[MEMBER] != eaf:
        raise ValueError("candidate member set differs")
    if any(after[key] != value for key, value in before.items() if key != "index.json"):
        raise ValueError("existing resource member changed")
    old = json.loads(before["index.json"])
    new = json.loads(after["index.json"])
    expected = copy.deepcopy(old)
    matches = [row for row in expected["emoji_collection"] if row["name"] == "embarrassed"]
    if len(matches) != 1 or matches[0] != {"name": "embarrassed", "file": "Happy.eaf", "eaf": {"loop": True, "fps": 20}}:
        raise ValueError("expected single embarrassed baseline")
    matches[0]["file"], matches[0]["eaf"]["fps"] = MEMBER, 25
    if new != expected:
        raise ValueError("candidate changes other semantics, playback or layout")
    return {"added": [MEMBER], "removed": [], "changed": ["index.json"],
            "all_existing_non_index_members_byte_exact": True, "layout_unchanged": True,
            "semantic_changes": [{"name": "embarrassed", "before": next(row for row in old["emoji_collection"]
                                     if row["name"] == "embarrassed"), "after": matches[0]}],
            "other_happy_references": [row["name"] for row in new["emoji_collection"] if row["file"] == "Happy.eaf"],
            "surprised_resource": next(row["file"] for row in new["emoji_collection"] if row["name"] == "surprised")}


def simulate_restore(snapshot, candidate, rollback):
    """Check exact resource replacement and restore in a 32 MiB memory copy.

    This tests byte coverage, including an interrupted write. It does not
    exercise transport, durable recovery or power loss on the physical device.
    """
    if (type(snapshot) is not bytes or len(snapshot) != FLASH_SIZE
            or type(candidate) is not bytes or len(candidate) != SIZE
            or type(rollback) is not bytes or len(rollback) != SIZE
            or snapshot[START:START + SIZE] != rollback):
        raise ValueError("complete snapshot and matching recovery image required")
    checks = []
    for scenario, written in (("full_resource_replacement", SIZE), ("interrupted_half_write", SIZE // 2)):
        simulated = bytearray(snapshot)
        simulated[START:START + SIZE] = b"\xff" * SIZE
        simulated[START:START + written] = candidate[:written]
        if (len(simulated) != FLASH_SIZE or simulated[:START] != snapshot[:START]
                or simulated[START + SIZE:] != snapshot[START + SIZE:]):
            raise ValueError("resource write escaped its partition")
        after_write_sha = sha(simulated)
        simulated[START:START + SIZE] = rollback
        if bytes(simulated) != snapshot:
            raise ValueError("resource restoration differs from the complete snapshot")
        checks.append({"scenario": scenario, "written_bytes": written,
                       "outside_partition_byte_exact": True, "after_write_sha256": after_write_sha,
                       "restored_flash_sha256": sha(simulated), "full_32MiB_restore_byte_exact": True})
    return {"mode": "offline_memory_shadow", "snapshot_sha256": sha(snapshot), "scenarios": checks,
            "device_io": False, "physical_power_loss_tested": False}


def image_info(path, image):
    package = extract_package(image)
    return {"path": path.relative_to(ROOT).as_posix(), "partition_bytes": len(image),
            "partition_sha256": sha(image), "package_bytes": len(package), "package_sha256": sha(package)}


def prepare():
    report_raw = (E144.PUBLIC / "report.json").read_bytes()
    if sha(report_raw) != INPUT_REPORT_SHA:
        raise ValueError("E1.44 approved evidence differs")
    prior = json.loads(report_raw)
    E144.validate_report(prior)
    if E144.source_state() != prior["source_state"]:
        raise ValueError("firmware or resource adapter changed since E1.44")
    selected = prior["variants"][1]
    raw = (E144.OUTPUT / "eye-terminal.eaf").read_bytes()
    if sha(raw) != EAF_SHA or len(raw) != 443238 or selected["eaf_sha256"] != EAF_SHA:
        raise ValueError("selected E1.44 eye candidate differs")
    stream = inspect_stream(raw)
    if len(stream.images) != 188 or not stream.has_terminal:
        raise ValueError("complete selected candidate required")
    # Reuse the anchored C evidence only after rechecking the source and DLL.
    _, _, cached = E144.E134.player_context()
    if (cached["anchors"] != prior["official_player_anchors"]
            or cached["toolchain"]["dll_sha256"] != prior["toolchain"]["dll_sha256"]):
        raise ValueError("official player evidence differs")
    baseline = load_migrated_baseline()
    context = E144.E134.baseline_context()
    resources, packer, before, old_index, _ = context
    new_index = copy.deepcopy(old_index)
    row = next(item for item in new_index["emoji_collection"] if item["name"] == "embarrassed")
    row["file"], row["eaf"]["fps"] = MEMBER, 25
    members = {**before, MEMBER: raw,
               "index.json": (json.dumps(new_index, ensure_ascii=False, indent=4) + "\n").encode("utf-8")}
    package = packer.build_legacy_package(members)
    if sha(package) != PACKAGE_SHA or len(package) != selected["capacity"]["package_bytes"]:
        raise ValueError("actual package differs from approved projection")
    candidate = pad_package(package)
    parsed = resources.parse_package_bytes(extract_package(candidate), modern=False)["members"]
    if parsed != members:
        raise ValueError("candidate package round trip differs")
    delta = audit_member_delta(before, parsed, raw)
    if len(parsed) != 30 or len(before) != 29 or len(delta["other_happy_references"]) != 8:
        raise ValueError("expected resource/semantic counts differ")
    rollback = pad_package(packer.build_legacy_package(before))
    if sha(rollback) != ROLLBACK_SHA:
        raise ValueError("rollback reference differs from migrated E1.18")
    # Read exact paths in the sealed migration report, never scan generated dirs.
    migration = json.loads((ROOT / baseline["report"]["path"]).read_bytes())
    snapshot = b"".join((ROOT / item["path"]).read_bytes() for item in migration["final_readback_blocks"])
    if sha(snapshot) != baseline["saved_readback_sha256"]:
        raise ValueError("saved migration readback changed")
    accepted_snapshot(snapshot, baseline)
    simulation = simulate_restore(snapshot, candidate, rollback)
    candidate_path = OUTPUT / "candidate-assets-8m.bin"
    rollback_path = OUTPUT / "rollback-reference-e1-18-assets-8m.bin"
    immutable(candidate_path, candidate)
    immutable(rollback_path, rollback)
    decision = {"schema": "deskbot.expression_selection.v1", "stage": "E1.45", "date": "2026-11-04",
                "user_message": "ok，确认", "basis": "confirmed_recommended_next_step",
                "selected_id": "eye", "source_edition": "E1.44", "eaf_sha256": EAF_SHA,
                "semantic": "embarrassed", "layout": "eye_anim_auto_mirror",
                "full_face_original_retained": True,
                "scope": "visual_choice_and_offline_staging", "hardware_test": "deferred"}
    immutable(PUBLIC / "selection.json", json_bytes(decision))
    manifest = {"schema": "deskbot.expression_resource_stage_manifest.v1", "stage": "E1.45",
                "images": {"candidate": image_info(candidate_path, candidate),
                           "rollback_reference": image_info(rollback_path, rollback)},
                "partition": {"name": "assets", "layout": "upper-assets-8m", "offset": START,
                              "bytes": SIZE, "end_exclusive": START + SIZE},
                "rollback_source": "saved_verified_E1.43c3_reference",
                "fresh_live_backup_present": False,
                "required_before_hardware": ["continuous_mmap_interaction_test",
                    "fresh_body_identity_and_32MiB_snapshot", "current_table_App_and_assets_match",
                    "same_run_resource_backup_and_durable_restore_plan"]}
    immutable(OUTPUT / "manifest.json", json_bytes(manifest))
    report = {"schema": "deskbot.expression_e1_45_staging/v1", "edition": "E1.45", "date": "2026-11-04",
              "result": "offline_package_prepared_hardware_preflight_pending", "audit_pass": True,
              "hardware_accessed": False, "device_written": False, "firmware_changed": False,
              "selection": decision, "partition": manifest["partition"], **manifest["images"],
              "budget": {"partition_bytes": SIZE, "soft_limit_bytes": SOFT_LIMIT,
                         "package_bytes": len(package), "free_bytes": SIZE - len(package),
                         "soft_limit_free_bytes": SOFT_LIMIT - len(package)},
              "member_delta": delta,
              "manifest_sha256": sha(json_bytes(manifest)),
              "input_e1_44": {"path": (E144.PUBLIC / "report.json").relative_to(ROOT).as_posix(),
                              "sha256": INPUT_REPORT_SHA},
              "baseline": {key: baseline[key] for key in ("layout", "report", "saved_readback_sha256", "original_app")},
              "native_evidence": {"source": "E1.44", "eaf_bytes_match": True,
                                  "player_source_and_dll_match": True, "candidate_comparisons": 376,
                                  "candidate_timer_scenarios": 4, "native_tests_rerun": False},
              "package_round_trip": True, "simulation": simulation,
              "official_action": "CAT_NUZZLE", "action_branch_changed": False,
              "ready_for_device_write": False, "pending": manifest["required_before_hardware"],
              "media": {"candidate_binaries": "ignored_tmp", "public": "metadata_and_current_preview"}}
    validate_report(report)
    if E144.source_state() != prior["source_state"]:
        raise ValueError("source inputs changed during staging")
    immutable(PUBLIC / "report.json", json_bytes(report))
    immutable(OUTPUT / "report.json", json_bytes(report))
    return report


def validate_report(report):
    if (report.get("schema") != "deskbot.expression_e1_45_staging/v1" or report.get("edition") != "E1.45"
            or report.get("result") != "offline_package_prepared_hardware_preflight_pending"
            or report.get("audit_pass") is not True):
        raise ValueError("E1.45 report identity differs")
    if any(report.get(key) is not False for key in ("hardware_accessed", "device_written", "firmware_changed", "ready_for_device_write", "action_branch_changed")):
        raise ValueError("staging evidence scope differs")
    if report["partition"] != {"name": "assets", "layout": "upper-assets-8m", "offset": START, "bytes": SIZE, "end_exclusive": START + SIZE}:
        raise ValueError("resource partition differs")
    if (report["selection"]["selected_id"] != "eye" or report["selection"]["eaf_sha256"] != EAF_SHA
            or report["input_e1_44"]["sha256"] != INPUT_REPORT_SHA
            or report["candidate"]["package_sha256"] != PACKAGE_SHA
            or report["candidate"]["package_bytes"] != 5981260
            or report["candidate"]["partition_bytes"] != SIZE
            or report["candidate"]["partition_sha256"] != IMAGE_SHA
            or report["rollback_reference"]["partition_sha256"] != ROLLBACK_SHA):
        raise ValueError("selected inputs or images differ")
    budget = report["budget"]
    if budget != {"partition_bytes": SIZE, "soft_limit_bytes": SOFT_LIMIT, "package_bytes": 5981260,
                  "free_bytes": SIZE - 5981260, "soft_limit_free_bytes": SOFT_LIMIT - 5981260}:
        raise ValueError("package budget differs")
    delta = report["member_delta"]
    expected = {"name": "embarrassed", "before": {"name": "embarrassed", "file": "Happy.eaf", "eaf": {"loop": True, "fps": 20}},
                "after": {"name": "embarrassed", "file": MEMBER, "eaf": {"loop": True, "fps": 25}}}
    if (delta["added"] != [MEMBER] or delta["removed"] != [] or delta["changed"] != ["index.json"]
            or delta["semantic_changes"] != [expected] or len(delta["other_happy_references"]) != 8
            or not delta["all_existing_non_index_members_byte_exact"] or not delta["layout_unchanged"]
            or delta["surprised_resource"] != "shocked.eaf" or report["official_action"] != "CAT_NUZZLE"):
        raise ValueError("single-semantic resource delta differs")
    sim = report["simulation"]
    if (sim["mode"] != "offline_memory_shadow" or sim["device_io"] is not False
            or sim["physical_power_loss_tested"] is not False or len(sim["scenarios"]) != 2
            or any(not row["outside_partition_byte_exact"] or not row["full_32MiB_restore_byte_exact"]
                   or row["restored_flash_sha256"] != sim["snapshot_sha256"] for row in sim["scenarios"])):
        raise ValueError("offline restoration coverage differs")


def check_saved(report):
    validate_report(report)
    if json.loads((PUBLIC / "selection.json").read_bytes()) != report["selection"]:
        raise ValueError("saved visual selection differs")
    if not OUTPUT.exists():
        return  # Public clone checks the report; local images are private.
    for key in ("candidate", "rollback_reference"):
        row = report[key]
        path = ROOT / row["path"]
        if path.resolve().parent != OUTPUT.resolve() or path.is_symlink():
            raise ValueError("staged image path differs")
        raw = path.read_bytes()
        if image_info(path, raw) != row:
            raise ValueError("staged image differs: " + key)
    if sha((OUTPUT / "manifest.json").read_bytes()) != report["manifest_sha256"]:
        raise ValueError("staged manifest differs")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = json.loads((PUBLIC / "report.json").read_bytes()) if args.check else prepare()
    check_saved(report)
    print(json.dumps({"result": "checked" if args.check else report["result"],
                      "package_bytes": report["budget"]["package_bytes"],
                      "free_bytes": report["budget"]["free_bytes"],
                      "partition_sha256": report["candidate"]["partition_sha256"],
                      "hardware_accessed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
