#!/usr/bin/env python3
"""Build an isolated capacity-only 16/32 MiB comparison; no hardware I/O."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys

import yaml

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_live_backup import configured_flash_bytes
from protocol.storage_n32r16 import FORMAL_CSV_SHA256, OLD_TABLE_SHA256
from protocol.storage_capacity import CAPACITY_CONFIG_DELTA


def load_helper():
    spec = importlib.util.spec_from_file_location("e137_locked_build", ROOT / "scripts/build-expression-e1-35a-storage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_helper()
BODY = HELPER.HELPER
SOURCE = ROOT / "firmware/body"
WORK = ROOT / "tmp/e137b-capacity"
OUTPUT = ROOT / "artifacts/expression-capacity-build-e1-37b-20261030/build-report.json"
FILES = HELPER.FILES + ("config/sdkconfig.json", "bootloader/config/sdkconfig.json", "xiaozhi.map")


def write_json(path: Path, value: dict) -> None:
    HELPER.write_json(path, value)


def config_for_capacity(text: str, mib: int) -> str:
    if mib not in (16, 32):
        raise ValueError("only the reviewed capacities are allowed")
    text = re.sub(r"(?m)^(?:# )?CONFIG_ESPTOOLPY_FLASHSIZE(?:_[A-Z0-9]+)?(?:=.*| is not set)\r?\n?", "", text)
    # Other capacity-choice defaults stay off; retain only the selected choice.
    return text.rstrip() + (f"\nCONFIG_ESPTOOLPY_FLASHSIZE_{mib}MB=y\n"
                            f"CONFIG_ESPTOOLPY_FLASHSIZE=\"{mib}MB\"\n")


def effective_changes(left: Path, right: Path) -> dict:
    return HELPER.diff_config(left, right)


def build_one(mib: int, project: Path, inputs: dict, locked: dict,
              env: dict, python: Path, idf: Path) -> dict:
    label = f"{mib}m"
    build = WORK / "o"
    HELPER.private_directory(build)
    sdkconfig = build / "sdkconfig"
    if mib == 16:
        snapshot = (SOURCE / "sdkconfig.defaults.o2f2").read_text(encoding="utf-8")
        for key in ("DESKBOT_EXPRESSION_OBSERVER_ENABLE", "APP_REPRODUCIBLE_BUILD"):
            snapshot = re.sub(r"(?m)^(?:# )?CONFIG_" + key + r".*\n?", "", snapshot)
        snapshot += "\nCONFIG_APP_REPRODUCIBLE_BUILD=y\nCONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE=y\n"
    else:
        snapshot = (WORK / "outputs/16m/sdkconfig").read_text(encoding="utf-8")
    sdkconfig.write_text(config_for_capacity(snapshot, mib), encoding="utf-8")
    common = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
              "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
              "-D", f"SDKCONFIG={sdkconfig}",
              "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
    steps = []
    for action in ("reconfigure", "build"):
        if action == "build":
            BODY.quote_image_effects_link_path(build, project)
        step = BODY.run(common + [action], project, env, WORK / f"{label}-{action}.log")
        steps.append({**step, "log": (WORK / f"{label}-{action}.log").relative_to(ROOT).as_posix()})
        current_lock = yaml.safe_load((project / "dependencies.lock").read_bytes())
        if BODY.normalized_lock(current_lock) != BODY.normalized_lock(locked):
            raise ValueError("component manager changed the pinned dependency lock")
    effective = json.loads((build / "config/sdkconfig.json").read_text(encoding="utf-8"))
    expected = {"ESPTOOLPY_FLASHSIZE": f"{mib}MB", "DESKBOT_EXPRESSION_OBSERVER_ENABLE": True,
                "APP_REPRODUCIBLE_BUILD": True, "USE_EMOTE_MESSAGE_STYLE": True,
                "BOARD_TYPE_ESP_VOCAT": True, "MMU_PAGE_SIZE": 65536,
                "PARTITION_TABLE_CUSTOM_FILENAME": "partitions/v2/16m.csv"}
    if any(effective.get(key) != value for key, value in expected.items()):
        raise ValueError("effective configuration differs from the reviewed build")
    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    observer_count = sum(Path(item["file"]).name == "expression_runtime_observer.cc" for item in commands)
    if observer_count != 1:
        raise ValueError("expected exactly one unchanged observer source")
    HELPER.verify_source(project, inputs, FORMAL_CSV_SHA256)
    archive = WORK / "outputs" / label
    HELPER.private_directory(archive)
    for name in FILES:
        target = archive / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build / name, target)
    artifacts = {name: HELPER.record(archive / name) for name in FILES}
    if artifacts["partition_table/partition-table.bin"]["sha256"] != OLD_TABLE_SHA256:
        raise ValueError("capacity-only build changed the partition table")
    for name in ("xiaozhi.bin", "bootloader/bootloader.bin"):
        if configured_flash_bytes((archive / name).read_bytes()[:4]) != mib * 1024 * 1024:
            raise ValueError("image header capacity does not match configuration")
    record = {"configured_flash_bytes": mib * 1024 * 1024, "observer_enabled": True,
              "archive": archive.relative_to(ROOT).as_posix(), "steps": steps,
              "artifacts": artifacts, "observer_source_members": observer_count}
    write_json(WORK / f"{label}-checkpoint.json", record)
    print(f"{label} complete: App {artifacts['xiaozhi.bin']['bytes']:,} B", flush=True)
    return record


def checkpoint(mib: int) -> dict | None:
    path = WORK / f"{mib}m-checkpoint.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    archive = WORK / "outputs" / f"{mib}m"
    if (record.get("archive") != archive.relative_to(ROOT).as_posix()
            or record.get("configured_flash_bytes") != mib * 1024 * 1024
            or record.get("observer_enabled") is not True
            or set(record.get("artifacts", {})) != set(FILES)
            or record.get("observer_source_members") != 1):
        raise ValueError("unexpected capacity-build checkpoint identity")
    for name in FILES:
        if HELPER.record(archive / name) != record["artifacts"][name]:
            raise ValueError("checkpoint artifact changed: " + name)
    steps = record.get("steps", [])
    if len(steps) != 2:
        raise ValueError("incomplete build checkpoint")
    for step, action in zip(steps, ("reconfigure", "build")):
        log = WORK / f"{mib}m-{action}.log"
        if (step.get("returncode") != 0 or step.get("log") != log.relative_to(ROOT).as_posix()
                or step.get("log_sha256") != HELPER.sha(log)):
            raise ValueError("checkpoint build-log identity changed")
    return record


def compare(records: list[dict], previous: dict) -> dict:
    old, new = records
    changes = effective_changes(ROOT / old["archive"] / "sdkconfig", ROOT / new["archive"] / "sdkconfig")
    if changes != CAPACITY_CONFIG_DELTA:
        raise ValueError("configuration changed beyond capacity: " + json.dumps(changes))
    same = {name: old["artifacts"][name] == new["artifacts"][name]
            for name in ("partition_table/partition-table.bin", "expression_assets.bin",
                         "srmodels/srmodels.bin", "ota_data_initial.bin")}
    if not all(same.values()):
        raise ValueError("non-capacity output changed: " + json.dumps(same))
    archived = next(item for item in previous["configurations"] if item["enabled"] is True)
    if old["artifacts"]["xiaozhi.bin"] != archived["artifacts"]["xiaozhi.bin"]:
        raise ValueError("new 16 MiB control App does not reproduce accepted E1.26")
    return {"config_changes": changes, "unchanged_outputs": same,
            "control_app_matches_accepted_e126": True,
            "candidate_app_partition_free_bytes": 6000 * 1024 - new["artifacts"]["xiaozhi.bin"]["bytes"],
            "images": {name: {"baseline": old["artifacts"][name], "candidate": new["artifacts"][name]}
                       for name in ("xiaozhi.bin", "xiaozhi.elf", "bootloader/bootloader.bin")}}


def report_for(records: list[dict], comparison: dict, inputs: dict, locked: dict,
               invocation: dict) -> dict:
    project = WORK / "p"
    HELPER.verify_source(project, inputs, FORMAL_CSV_SHA256)
    if BODY.normalized_lock(yaml.safe_load((project / "dependencies.lock").read_bytes())) != BODY.normalized_lock(locked):
        raise ValueError("retained dependency lock changed")
    validated = BODY.validate_components(project, locked)
    effective = json.loads((ROOT / records[1]["archive"] / "config/sdkconfig.json").read_text(encoding="utf-8"))
    _, _, idf = HELPER.environment()
    cache = {"quad_cache_enabled": bool(effective.get("BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH")),
             "octal_cache_enabled": bool(effective.get("BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH")),
             "experimental_features_enabled": bool(effective.get("IDF_EXPERIMENTAL_FEATURES")),
             "upper_assets_deployment_ready": False,
             "idf_kconfig": "components/bootloader/Kconfig.projbuild",
             "idf_kconfig_sha256": HELPER.sha(idf / "components/bootloader/Kconfig.projbuild"),
             "reason": "32 MiB declaration does not enable cached access above 16 MiB for this Quad Flash configuration."}
    if any(cache[key] for key in ("quad_cache_enabled", "octal_cache_enabled", "experimental_features_enabled")):
        raise ValueError("capacity-only candidate unexpectedly enabled an upper-half cache experiment")
    if BODY.inventory() != inputs:
        raise ValueError("formal source changed during capacity review")
    return {"schema": "deskbot.n32r16.capacity_build.v1", "edition": "E1.37", "date": "2026-10-30",
            "result": "isolated_capacity_builds_passed_runtime_pending",
            "candidate": "capacity-only-32m", "physical_flash_bytes": 32 * 1024 * 1024,
            "psram_bytes": 16 * 1024 * 1024, "source_manifest_sha256": HELPER.sha(SOURCE / "SOURCE_MANIFEST.sha256"),
            "formal_source_unchanged": True,
            "components": {"count": validated["count"], "actual_contents_match_lock": True,
                           "local_touch_path_is_only_lock_difference": True},
            "build_invocation": {"script_sha256": invocation["script_sha256"],
                                 "review_script_sha256": HELPER.sha(Path(__file__)),
                                 "inputs_sha256": HELPER.sha(WORK / "inputs.json"),
                                 "checkpoint_sha256": {f"{mib}m": HELPER.sha(WORK / f"{mib}m-checkpoint.json")
                                                       for mib in (16, 32)}},
            "builds": records, "comparison": comparison, "upper_half_cache": cache,
            "deployment_eligible": False, "flash_authorized": False,
            "runtime_verified": False, "hardware_accepted": False,
            "safety": {"serial_accessed": False, "device_written": False, "c61_accessed": False,
                       "nvs_written": False, "formal_partition_changed": False},
            "limitations": ["Build assets are original source references, not the accepted E1.18 deployment package.",
                            "Live bootloader differs from the build reference; its exact backup must be retained.",
                            "Boot, capacity discovery, mmap, model loading and official hardware regression are pending.",
                            "Upper-half cached access needs a separate chip/driver compatibility review; capacity declaration is insufficient."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true", help="validate retained build evidence without compiling")
    parser.add_argument("--review-completed", action="store_true",
                        help="review both completed checkpoints without compiling; retain original invocation evidence")
    args = parser.parse_args()
    HELPER.private_directory(WORK)
    inputs = BODY.inventory()
    previous = json.loads((ROOT / "tmp/e1-26-full-body-report.json").read_text(encoding="utf-8"))
    if inputs != previous["source_sha256"]:
        raise ValueError("formal source differs from accepted E1.26")
    locked = yaml.safe_load((SOURCE / "dependencies.lock").read_bytes())
    invocation = {"script_sha256": HELPER.sha(Path(__file__)), "source_sha256": inputs,
                  "dependency_lock_sha256": HELPER.sha(SOURCE / "dependencies.lock")}
    input_path = WORK / "inputs.json"
    if input_path.exists():
        recorded = json.loads(input_path.read_text(encoding="utf-8"))
        if recorded != invocation:
            # A review may update comparison logic, but may never silently
            # adopt different build inputs or rewrite the invocation record.
            if (not (args.check or args.review_completed)
                    or {**recorded, "script_sha256": invocation["script_sha256"]} != invocation
                    or HELPER.sha(WORK / "invoked-builder.py") != recorded.get("script_sha256")):
                raise ValueError("build inputs or script changed; retain evidence and select a new audited work directory")
    elif args.check or args.review_completed:
        raise ValueError("missing capacity-build inputs")
    else:
        write_json(input_path, invocation)
        recorded = invocation
    if args.check or args.review_completed:
        records = [checkpoint(16), checkpoint(32)]
        if any(item is None for item in records):
            raise ValueError("both completed capacity builds are required")
        comparison = compare(records, previous)
        expected_report = report_for(records, comparison, inputs, locked, recorded)
        if args.review_completed:
            write_json(OUTPUT, expected_report)
            write_json(WORK / "build-report.json", expected_report)
        elif json.loads(OUTPUT.read_text(encoding="utf-8")) != expected_report:
            raise ValueError("stale build report")
        print("E1.37 retained build artifacts, source, configuration and logs verified; no device I/O.")
        return 0
    project, components = HELPER.stage(WORK, ROOT / "tmp/e135a-storage/p", inputs, locked)
    env, python, idf = HELPER.environment()
    records = []
    for mib in (16, 32):
        prior = checkpoint(mib) if args.resume else None
        if prior:
            print(f"Reusing hash-verified {mib} MiB build", flush=True)
        records.append(prior or build_one(mib, project, inputs, locked, env, python, idf))
    comparison = compare(records, previous)
    if BODY.inventory() != inputs:
        raise ValueError("formal source changed during build")
    BODY.validate_components(project, locked)
    report = report_for(records, comparison, inputs, locked, recorded)
    write_json(OUTPUT, report)
    write_json(WORK / "build-report.json", report)
    print(report["result"], flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print("E1.37 capacity build blocked: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
