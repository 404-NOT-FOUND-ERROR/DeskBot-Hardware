#!/usr/bin/env python3
"""Build E1.43b probe variants from the accepted E3c-3b source and App."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import e3c3b_display_dma  # noqa: E402

OLD = importlib.util.spec_from_file_location(
    "e143b_e3c3a_build_helpers", ROOT / "scripts/build-expression-e1-43b-e3c3a.py")
if OLD is None or OLD.loader is None:
    raise ImportError("E1.43b shared build helpers are unavailable")
helpers = importlib.util.module_from_spec(OLD)
OLD.loader.exec_module(helpers)

RUNTIME_SPEC = importlib.util.spec_from_file_location(
    "e143b_e3c3b_runtime", ROOT / "scripts/build-e3c3a-usb-path.py")
if RUNTIME_SPEC is None or RUNTIME_SPEC.loader is None:
    raise ImportError("pinned ESP-IDF build runtime is unavailable")
build_runtime = importlib.util.module_from_spec(RUNTIME_SPEC)
RUNTIME_SPEC.loader.exec_module(build_runtime)

FROZEN = e3c3b_display_dma.WORK / "p"
REFERENCE_BUILD = e3c3b_display_dma.WORK / "on"
REFERENCE_REPORT = e3c3b_display_dma.PUBLIC / "firmware-build-report.json"
REFERENCE_REPORT_SHA256 = "1caccb773de444a2184d12ccbdb4fabe2aab301250a3d517ae043f6b55d8eed1"
MANUAL_ACCEPTANCE = e3c3b_display_dma.PUBLIC / "manual-verification.json"
MANUAL_ACCEPTANCE_SHA256 = "4088f73c45411bcb627c3963f7801d1e901b2e0c53302c53bfba98461bbddd9c"
WORK_ROOT = ROOT / "tmp/e143b-e3c3b-20261107"
ARTIFACT_ROOT = ROOT / "artifacts/expression-continuous-mmap-e1-43b2-e3c3b-20261107"
RUN_LABEL_RE = re.compile(r"e143b-e3c3b-20261107-[a-z0-9][a-z0-9-]{0,23}")
FLAG = helpers.FLAG
TARGET = {"chip": "esp32s3", "flash_bytes": 33_554_432,
          "psram_bytes": 16_777_216, "layout": "upper-assets-8m",
          "port_for_later_live_test": "COM4"}


def validate_reviewed_components(project: Path, locked: dict) -> dict:
    """Verify registry contents with the exact accepted ST77916 replacement.

    The lock still names the upstream component. Its aggregate is reconstructed
    from the original driver; the actual patched file is checked separately.
    Neither the lock nor the registry inventory is rewritten.
    """
    project = helpers.ensure_safe_path(project, ROOT / "tmp")
    checked_parents = {project}

    def read_component(relative: str) -> bytes:
        # Inventory paths are lexical children. Check every parent once per
        # validation, so large component trees do not repeat NTFS resolution.
        relative = helpers._safe_component_relative(relative)
        path = project / relative
        parent = path.parent
        while parent not in checked_parents:
            if helpers._is_reparse_point(parent):
                raise ValueError("component directory is redirected: " + relative)
            checked_parents.add(parent)
            parent = parent.parent
        if helpers._is_reparse_point(path):
            raise ValueError("component file is redirected: " + relative)
        return path.read_bytes()

    original = helpers.safe_read(e3c3b_display_dma.FROZEN, e3c3b_display_dma.DRIVER)
    patched = e3c3b_display_dma.patch_driver(original)
    records = []
    override_seen = False
    for name, item in locked["dependencies"].items():
        expected = item.get("component_hash")
        if expected is None:
            continue
        component = project / "managed_components" / name.replace("/", "__")
        marker = read_component((component / ".component_hash").relative_to(project).as_posix())
        if marker.decode("utf-8").strip().lower() != expected:
            raise ValueError("component marker differs from lock: " + name)
        checksum = json.loads(read_component(
            (component / "CHECKSUMS.json").relative_to(project).as_posix()))
        if checksum.get("algorithm") != "sha256" or checksum.get("version") != "1.0":
            raise ValueError("unsupported component inventory: " + name)
        entries = checksum.get("files")
        if not isinstance(entries, list) or not entries:
            raise ValueError("empty component inventory: " + name)
        names = [helpers._safe_component_relative(row["path"]) for row in entries]
        if len(names) != len(set(names)):
            raise ValueError("duplicate component inventory entry: " + name)
        aggregate = hashlib.sha256()
        for entry in sorted(entries, key=lambda row: row["path"].replace("\\", "/")):
            relative = helpers._safe_component_relative(entry["path"])
            repo_name = (component / relative).relative_to(project).as_posix()
            raw = read_component(repo_name)
            if repo_name == e3c3b_display_dma.DRIVER:
                if raw != patched:
                    raise ValueError("reviewed ST77916 replacement differs")
                # The registry's aggregate describes the original component.
                raw = original
                override_seen = True
            if type(entry.get("size")) is not int or len(raw) != entry["size"]:
                raise ValueError("component file size differs: " + repo_name)
            aggregate.update(relative.encode("utf-8"))
            aggregate.update(helpers.sha256(raw).encode("ascii"))
        if aggregate.hexdigest() != expected:
            raise ValueError("registry contents differ from lock: " + name)
        records.append({"name": name, "version": item["version"], "component_hash": expected,
                        "files": len(entries)})
    if not override_seen:
        raise ValueError("reviewed ST77916 component is missing from the lock")
    return {
        "upstream": {"count": len(records), "actual_contents_match_lock": True,
                     "components": records},
        "reviewed_replacements": {
            e3c3b_display_dma.DRIVER: {"upstream_sha256": helpers.sha256(original),
                                      "accepted_sha256": helpers.sha256(patched)},
        },
    }


def load_reference() -> tuple[dict, dict, dict]:
    """Verify the pinned E3c-3b report, source tree, build and ELF identity."""
    helpers.ensure_safe_path(REFERENCE_REPORT, ROOT / "artifacts")
    helpers.ensure_safe_path(FROZEN, ROOT / "tmp")
    helpers.ensure_safe_path(REFERENCE_BUILD, ROOT / "tmp")
    raw = REFERENCE_REPORT.read_bytes()
    if helpers.sha256(raw) != REFERENCE_REPORT_SHA256:
        raise ValueError("accepted E3c-3b build report digest differs")
    acceptance_raw = MANUAL_ACCEPTANCE.read_bytes()
    if helpers.sha256(acceptance_raw) != MANUAL_ACCEPTANCE_SHA256:
        raise ValueError("E3c-3b hardware acceptance record differs")
    acceptance = json.loads(acceptance_raw)
    if (acceptance.get("app") != "E3c-3b" or acceptance.get("result") != "passed"
            or acceptance.get("phase") != "online_menu_regression"):
        raise ValueError("E3c-3b hardware acceptance is not complete")
    report = json.loads(raw)
    if (report.get("schema") != "deskbot.display_dma_repair_build/v1"
            or report.get("edition") != "E3c-3b"
            or report.get("result") != "query_enabled_build_verified"
            or report.get("source_delta") != [e3c3b_display_dma.DRIVER,
                                                 e3c3b_display_dma.BOARD]):
        raise ValueError("E3c-3b build report identity differs")

    row = report.get("build")
    if (not isinstance(row, dict) or row.get("query_enabled") is not True
            or row.get("build_directory") != "tmp/e3c3b-display-dma-20261107/on"
            or row.get("config_equal_reference") is not True
            or not all(row.get("protected_equal", {}).values())):
        raise ValueError("E3c-3b query-on build record differs")
    app_record = row.get("artifacts", {}).get("xiaozhi.bin")
    if (app_record != {"bytes": 4_489_232,
                       "sha256": "180aa8cbcc98e3d5b95329175ff2e3f2ee1d35f806b03cbe0778aa2fc2d0f42a"}
            or row.get("app_elf_sha256") !=
            "e0941ae3c68528763764f918a62b508fcb990008936d7dd1c69228f283345098"):
        raise ValueError("accepted E3c-3b App or ELF identity differs")
    if report.get("target", {}).get("layout") != "upper-assets-8m":
        raise ValueError("E3c-3b storage layout differs")

    source_sha256 = report.get("source_sha256")
    if not isinstance(source_sha256, dict) or not source_sha256:
        raise ValueError("E3c-3b frozen source manifest is missing")
    helpers.verify_frozen_sources(FROZEN, source_sha256)
    locked = helpers.load_locked_dependencies(FROZEN)
    components = validate_reviewed_components(FROZEN, locked)
    if components["upstream"] != report.get("components"):
        raise ValueError("frozen E3c-3b managed components differ")
    input_inventory = helpers.verify_project_file_inventory(FROZEN, source_sha256, locked)
    if input_inventory.get("files", 0) < 1:
        raise ValueError("E3c-3b input inventory is empty")
    probe = helpers.verify_probe_source(FROZEN)

    expected_artifacts = {}
    for name in helpers.ARTIFACTS:
        actual = helpers.file_record(REFERENCE_BUILD / name)
        if name in row["artifacts"] and actual != row["artifacts"][name]:
            raise ValueError("retained E3c-3b output differs: " + name)
        expected_artifacts[name] = actual
    elf_sha256 = helpers.sha256((REFERENCE_BUILD / "xiaozhi.elf").read_bytes())
    if (elf_sha256 != row["app_elf_sha256"]
            or (REFERENCE_BUILD / "xiaozhi.bin").read_bytes()[176:208].hex() != elf_sha256):
        raise ValueError("retained E3c-3b App and ELF identity differ")

    effective = json.loads((REFERENCE_BUILD / "config/sdkconfig.json").read_bytes())
    boot_config = json.loads((REFERENCE_BUILD / "bootloader/config/sdkconfig.json").read_bytes())
    if not all(effective.get(name) is True for name in helpers.QUERY_FLAGS):
        raise ValueError("E3c-3b query features are not enabled")
    if (effective.get(helpers.FLAG) is not False
            or any(effective.get(name) is True for name in helpers.OTHER_PROBE_FLAGS)):
        raise ValueError("E3c-3b baseline contains an unexpected diagnostic probe")
    reference_row = {"artifacts": expected_artifacts, "app_elf_sha256": elf_sha256}
    metadata = {"config": effective, "boot_config": boot_config,
                "source_sha256": source_sha256, "components": components,
                "input_inventory": input_inventory, "probe": probe,
                "report_sha256": helpers.sha256(raw),
                "acceptance_sha256": helpers.sha256(acceptance_raw)}
    return report, reference_row, metadata


def verify_final_inputs(project: Path, metadata: dict) -> None:
    """Recheck both trees after compilation, including registry-only sources."""
    for tree in (FROZEN, project):
        helpers.verify_frozen_sources(tree, metadata["source_sha256"])
        locked = helpers.load_locked_dependencies(tree)
        if helpers.verify_project_file_inventory(tree, metadata["source_sha256"], locked) != metadata["input_inventory"]:
            raise ValueError("E3c-3b input inventory changed during dual build")
        if validate_reviewed_components(tree, locked) != metadata["components"]:
            raise ValueError("E3c-3b registry inputs changed during dual build")


def build(run_label: str) -> dict:
    if RUN_LABEL_RE.fullmatch(run_label) is None:
        raise ValueError("run label must be e143b-e3c3b-20261107-<name>")
    work = WORK_ROOT / run_label
    output = ARTIFACT_ROOT / (run_label + "-build-report.json")
    helpers.ensure_safe_path(WORK_ROOT, ROOT / "tmp")
    helpers.ensure_safe_path(ARTIFACT_ROOT, ROOT / "artifacts")
    helpers.ensure_safe_path(work, ROOT / "tmp")
    helpers.ensure_safe_path(output, ROOT / "artifacts")
    if work.exists() or output.exists():
        raise FileExistsError("build run already exists; keep it and choose a new run label")

    report, reference_row, metadata = load_reference()
    env, python, idf = build_runtime.BASE.environment()
    work.mkdir(parents=True)

    project = work / "source"
    shutil.copytree(FROZEN, project, symlinks=True)
    source_sha256 = metadata["source_sha256"]
    helpers.rebind_dependency_lock(project, FROZEN)
    helpers.verify_frozen_sources(project, source_sha256)
    locked = helpers.load_locked_dependencies(project)
    if helpers.verify_project_file_inventory(project, source_sha256, locked) != metadata["input_inventory"]:
        raise ValueError("isolated E3c-3b source copy differs from the frozen inventory")
    if validate_reviewed_components(project, locked) != metadata["components"]:
        raise ValueError("isolated E3c-3b components differ")
    helpers.verify_probe_source(project)

    baseline_sdkconfig = (REFERENCE_BUILD / "sdkconfig").read_text(encoding="utf-8")
    rounds, steps = {}, []
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    size_tool = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    if not nm or not size_tool:
        raise ValueError("pinned ESP32-S3 symbol and size tools are required")

    for enabled in (False, True):
        label = "on" if enabled else "off"
        round_root = work / label
        build_dir = round_root / "build"
        evidence = round_root / "evidence"
        build_dir.mkdir(parents=True)
        evidence.mkdir()
        sdkconfig = build_dir / "sdkconfig"
        sdkconfig.write_text(helpers.prepare_sdkconfig(baseline_sdkconfig, enabled),
                             encoding="utf-8", newline="\n")
        command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build_dir),
                   "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
                   "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
        for action in ("reconfigure", "build"):
            log = round_root / f"{action}.log"
            if action == "build":
                build_runtime.BASE.HELPER.HELPER.quote_image_effects_link_path(build_dir, project)
            print(f"E1.43b E3c-3b probe-{label}: {action}", flush=True)
            build_runtime.BASE.run(command + [action], project, env, log)
            log_sha256 = helpers.validate_step_log(log.read_bytes(), action)
            helpers.verify_frozen_sources(project, source_sha256)
            steps.append({"configuration": label, "action": action, "returncode": 0,
                          "log": log.relative_to(ROOT).as_posix(), "sha256": log_sha256})

        current = helpers.audit_round(build_dir, reference_row, metadata["config"],
                                       metadata["boot_config"], enabled, nm, size_tool, env)
        if (helpers.verify_project_file_inventory(project, source_sha256, locked)
                != metadata["input_inventory"]):
            raise ValueError("E3c-3b input inventory changed during build: " + label)
        for name in helpers.ARTIFACTS:
            destination = evidence / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build_dir / name, destination)
            if helpers.file_record(destination) != current["artifacts"][name]:
                raise ValueError("archived build output differs: " + label + "/" + name)
        current["evidence_directory"] = evidence.relative_to(ROOT).as_posix()
        current["protected_equal_e3c3b"] = current.pop("protected_equal_e3c3a")
        rounds[label] = current

    if rounds["off"]["artifacts"]["xiaozhi.bin"] != reference_row["artifacts"]["xiaozhi.bin"]:
        raise ValueError("probe-off App is not byte-identical to accepted E3c-3b")
    if rounds["off"]["embedded_elf_sha256"] != reference_row["app_elf_sha256"]:
        raise ValueError("probe-off ELF identity differs from accepted E3c-3b")
    if rounds["off"]["protected_equal_e3c3b"] != rounds["on"]["protected_equal_e3c3b"]:
        raise ValueError("protected outputs differ between probe configurations")
    verify_final_inputs(project, metadata)

    result = {
        "schema": "deskbot.continuous_mmap.build.e1_43b_e3c3b/v1",
        "edition": "E1.43b-2",
        "baseline": "E3c-3b",
        "result": "e3c3b_probe_off_on_builds_verified",
        "run_label": run_label,
        "reference_report": {"path": REFERENCE_REPORT.relative_to(ROOT).as_posix(),
                             "sha256": metadata["report_sha256"]},
        "source": {"project": FROZEN.relative_to(ROOT).as_posix(),
                   "build_project": project.relative_to(ROOT).as_posix(),
                   "files_checked": len(source_sha256),
                   "source_sha256_digest": helpers.sha256(json.dumps(
                       source_sha256, sort_keys=True, separators=(",", ":")).encode("utf-8")),
                   "input_inventory": metadata["input_inventory"],
                   "probe": metadata["probe"], "changed": False},
        "target": dict(TARGET),
        "rounds": rounds,
        "steps": steps,
        "device_accessed": False,
        "device_written": False,
        "partition_table_changed": False,
        "runtime_verified": False,
        "physical_interaction_accepted": False,
    }
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    args = parser.parse_args()
    result = build(args.run_label)
    print(json.dumps({"result": result["result"],
                      "app_bytes": {key: row["artifacts"]["xiaozhi.bin"]["bytes"]
                                    for key, row in result["rounds"].items()},
                      "device_written": result["device_written"]}, ensure_ascii=False))
