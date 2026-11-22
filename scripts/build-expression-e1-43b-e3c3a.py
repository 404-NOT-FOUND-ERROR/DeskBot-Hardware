#!/usr/bin/env python3
"""Build the existing E1.43b probe against the accepted E3c-3a query-on App."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "tmp/e3c3a-usb-path-20261106/p"
REFERENCE_BUILD = ROOT / "tmp/e3c3a-usb-path-20261106/on"
REFERENCE_REPORT = ROOT / "artifacts/ui-menu-regression-20261106/e3c3a-build.json"
REFERENCE_REPORT_SHA256 = "0839fb2d252145b2f264715bf81b12d6d335127270c8ea3279aab3b13415eb10"
WORK_ROOT = ROOT / "tmp/e143b-e3c3a-20261107"
ARTIFACT_ROOT = ROOT / "artifacts/expression-continuous-mmap-e1-43b2-e3c3a-20261107"
FLAG = "DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT"
QUERY_FLAGS = (
    "DESKBOT_EXPRESSION_OBSERVER_ENABLE",
    "DESKBOT_STUDIO_IDENTITY_ENABLE",
    "DESKBOT_STUDIO_STATE_QUERY_ENABLE",
    "DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE",
)
OTHER_PROBE_FLAGS = ("DESKBOT_FLASH_PROBE_BOOT_EXPORT", "DESKBOT_UPPER_PATTERN_BOOT_EXPORT")
PROBE_HEADER = "main/boards/esp_vocat/continuous_mmap_probe.h"
PROBE_SYMBOL = "RunContinuousMappingOnce"
APP_PARTITION_BYTES = 0x5DC000
MINIMUM_APP_HEADROOM = 1024 * 1024
ARTIFACTS = (
    "xiaozhi.bin", "xiaozhi.elf", "bootloader/bootloader.bin",
    "partition_table/partition-table.bin", "srmodels/srmodels.bin", "ota_data_initial.bin",
    "sdkconfig", "flasher_args.json", "config/sdkconfig.json", "bootloader/config/sdkconfig.json",
)
AUXILIARY_INPUT_FILES = (
    "main/assets/lang_config.h",
    "partitions/deskbot-upper-assets-8m.csv",
)
# These IDF inputs affect the build but are not listed in source_sha256.
AUXILIARY_INPUT_ROOTS = (
    "managed_components/espressif2022__esp_emote_assets/scripts/spiffs_assets/build/face",
)
AUXILIARY_INPUTS_SHA256 = "28cd75cdb743c04590d66537ebb2eec8d629a32e5354667443af412cb5ce2faa"
PROTECTED = (
    "bootloader/bootloader.bin", "partition_table/partition-table.bin",
    "srmodels/srmodels.bin", "ota_data_initial.bin", "flasher_args.json",
    "bootloader/config/sdkconfig.json",
)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_record(path: Path) -> dict:
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": sha256(raw)}


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.lstat().st_file_attributes & 0x400)
    except (AttributeError, FileNotFoundError):
        return False


def _has_reparse_ancestor(path: Path) -> bool:
    current = Path(path.anchor)
    for part in Path(path).absolute().parts[1:]:
        current = current / part
        if _is_reparse_point(current):
            return True
    return False


def ensure_safe_path(path: Path, boundary: Path) -> Path:
    boundary = Path(boundary).absolute()
    if not boundary.is_dir() or _has_reparse_ancestor(boundary):
        raise ValueError("output boundary is missing or redirected")
    candidate = Path(path).absolute()
    if _has_reparse_ancestor(candidate):
        raise ValueError("output path contains a symlink or reparse point")
    try:
        relative = candidate.relative_to(boundary)
    except ValueError as error:
        raise ValueError("output path is outside its repository directory") from error
    if ".." in relative.parts:
        raise ValueError("output path contains parent traversal")
    current = boundary
    for part in relative.parts:
        current = current / part
        if _is_reparse_point(current):
            raise ValueError("output path contains a symlink or reparse point")
    return current


def safe_read(root: Path, relative: str) -> bytes:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("source path escapes frozen project")
    base = root.resolve(strict=True)
    path = base
    for part in rel.parts:
        path = path / part
        if _is_reparse_point(path):
            raise ValueError("frozen source contains a symlink: " + relative)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ValueError("frozen source is missing or outside project: " + relative)
    return resolved.read_bytes()


def rebind_dependency_lock(project: Path, source_project: Path) -> None:
    lock = project / "dependencies.lock"
    raw = lock.read_bytes()
    text = raw.decode("utf-8")
    matches = list(re.finditer(r"(?m)^      path: (.+?)(?=\r?$)", text))
    if len(matches) != 1:
        raise ValueError("frozen dependency lock must contain one local path")
    source_component = (source_project / "components/espressif__touch_sensor_fsm").resolve()
    value = matches[0].group(1).strip()
    if Path(value).resolve() != source_component:
        raise ValueError("source dependency path differs")
    destination = str((project / "components/espressif__touch_sensor_fsm").resolve())
    lock.write_bytes(text.replace(value, destination, 1).encode("utf-8"))


def verify_frozen_sources(project: Path, source_sha256: dict) -> None:
    if not isinstance(source_sha256, dict) or not source_sha256:
        raise ValueError("E3c-3a source reference required")
    for name, expected in source_sha256.items():
        raw = safe_read(project, name)
        if name == "dependencies.lock":
            local = (project / "components/espressif__touch_sensor_fsm").resolve()
            text = raw.decode("utf-8")
            matches = list(re.finditer(r"(?m)^      path: (.+?)(?=\r?$)", text))
            if len(matches) != 1:
                raise ValueError("frozen dependency lock must contain one local path")
            value = matches[0].group(1).strip()
            if Path(value).resolve() != local:
                raise ValueError("frozen dependency path differs")
            raw = text.replace(value, "components/espressif__touch_sensor_fsm", 1).encode("utf-8")
        if sha256(raw) != expected:
            raise ValueError("frozen source digest differs: " + name)


def validate_file_inventory(expected: set[str], actual: set[str]) -> None:
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError("frozen input file inventory differs; missing=" + repr(missing[:5])
                         + "; extra=" + repr(extra[:5]))


def load_locked_dependencies(project: Path) -> dict:
    import yaml
    locked = yaml.safe_load(safe_read(project, "dependencies.lock"))
    if not isinstance(locked, dict) or not isinstance(locked.get("dependencies"), dict):
        raise ValueError("locked dependency inventory is missing")
    return locked


def _safe_component_relative(value: str) -> str:
    relative = value.replace("\\", "/")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or ":" in relative or not relative:
        raise ValueError("unsafe managed component inventory path")
    return relative


def verify_project_file_inventory(project: Path, source_sha256: dict, locked: dict) -> dict:
    expected = set(source_sha256)
    dependencies = locked.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("locked dependency inventory is missing")
    for name, item in dependencies.items():
        if not isinstance(item, dict):
            raise ValueError("invalid locked managed component entry: " + str(name))
        if not item.get("component_hash"):
            continue
        component = Path("managed_components") / name.replace("/", "__")
        checksum_path = component / "CHECKSUMS.json"
        marker_path = component / ".component_hash"
        checksum = json.loads(safe_read(project, checksum_path.as_posix()))
        if checksum.get("algorithm") != "sha256" or checksum.get("version") != "1.0":
            raise ValueError("unsupported managed component file inventory: " + name)
        rows = checksum.get("files")
        if not isinstance(rows, list) or not rows:
            raise ValueError("empty managed component file inventory: " + name)
        expected.update((checksum_path.as_posix(), marker_path.as_posix()))
        names = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError("invalid managed component file entry: " + name)
            relative = _safe_component_relative(row["path"])
            if relative in names:
                raise ValueError("duplicate managed component file entry: " + name)
            names.add(relative)
            expected.add((component / relative).as_posix())

    auxiliary = set(AUXILIARY_INPUT_FILES)
    for root_name in AUXILIARY_INPUT_ROOTS:
        root = project / root_name
        if not root.is_dir() or _has_reparse_ancestor(root):
            raise ValueError("required auxiliary build input directory is missing or redirected")
        for current, directories, files in os.walk(root):
            base = Path(current)
            for name in directories:
                if _is_reparse_point(base / name):
                    raise ValueError("auxiliary build input contains a reparse point")
            for name in files:
                path = base / name
                if _is_reparse_point(path):
                    raise ValueError("auxiliary build input contains a reparse point")
                if path.is_file():
                    auxiliary.add(path.relative_to(project).as_posix())
    expected.update(auxiliary)

    actual = set()
    for current, directories, files in os.walk(project):
        base = Path(current)
        for name in directories:
            if _is_reparse_point(base / name):
                raise ValueError("frozen project contains a reparse point")
        for name in files:
            path = base / name
            if _is_reparse_point(path):
                raise ValueError("frozen project contains a reparse point")
            if path.is_file():
                actual.add(path.relative_to(project).as_posix())
    validate_file_inventory(expected, actual)

    records = {name: file_record(project / name) for name in sorted(auxiliary)}
    auxiliary_digest = sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if auxiliary_digest != AUXILIARY_INPUTS_SHA256:
        raise ValueError("auxiliary E3c-3a build input digest differs")
    return {"files": len(actual), "source_manifest_files": len(source_sha256),
            "auxiliary_files": len(records), "auxiliary_bytes": sum(row["bytes"] for row in records.values()),
            "auxiliary_sha256": auxiliary_digest}


def validate_step_log(raw: bytes, action: str) -> str:
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("build step log is empty")
    success_markers = {
        "reconfigure": "-- Build files have been written to:",
        "build": "Project build complete.",
    }
    marker = success_markers.get(action)
    if marker is None:
        raise ValueError("unknown build step action")
    if marker not in raw.decode("utf-8", errors="replace"):
        raise ValueError("build step log lacks success marker: " + action)
    return sha256(raw)


def verify_probe_source(project: Path) -> dict:
    kconfig = safe_read(project, "main/Kconfig.projbuild").decode("utf-8")
    startup = safe_read(project, "main/boards/esp_vocat/dev_tools.cc").decode("utf-8")
    probe = safe_read(project, PROBE_HEADER).decode("utf-8")
    block = re.search(r"(?ms)^config " + FLAG + r"\r?\n(?P<body>.*?)(?=^config |\Z)", kconfig)
    if (block is None or "default n" not in block["body"]
            or "#if CONFIG_" + FLAG not in startup or PROBE_SYMBOL not in probe
            or "kBytes = 0x00800000" not in probe or "kHoldMs = 180000" not in probe):
        raise ValueError("retained E1.43b probe integration differs")
    if startup.count("deskbot_continuous_mmap::Start();") != 1:
        raise ValueError("expected one guarded E1.43b startup")
    task = re.search(r"xTaskCreate\(RunContinuousMappingOnce,\s*\"mmap_diag_once\",\s*(\d+)", probe)
    if task is None:
        raise ValueError("probe task stack declaration missing")
    return {"header": PROBE_HEADER, "stack_bytes": int(task.group(1)), "source_added": False}


def prepare_sdkconfig(text: str, enabled: bool) -> str:
    expression = re.compile(r"(?m)^(?:# )?CONFIG_" + re.escape(FLAG) + r"[^\r\n]*(?:\r?\n|$)")
    text = expression.sub("", text)
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"CONFIG_{FLAG}=y\n" if enabled else f"# CONFIG_{FLAG} is not set\n"
    return text


def validate_app_elf(app_raw: bytes, elf_raw: bytes) -> str:
    if len(app_raw) < 208:
        raise ValueError("App image is too short for its embedded ELF identity")
    embedded = app_raw[176:208].hex()
    if embedded != sha256(elf_raw):
        raise ValueError("App embedded ELF digest differs from the built ELF")
    return embedded


def protected_artifact_results(artifacts: dict, reference_artifacts: dict) -> dict:
    return {name: artifacts[name] == reference_artifacts[name] for name in PROTECTED}


def validate_config(reference: dict, actual: dict, enabled: bool) -> dict:
    expected = dict(reference)
    expected[FLAG] = enabled
    if not all(actual.get(name) is True for name in QUERY_FLAGS):
        raise ValueError("accepted E3c-3a query features must remain enabled")
    if any(actual.get(name) is True for name in OTHER_PROBE_FLAGS):
        raise ValueError("unrelated Flash probes must remain disabled")
    changed = {key for key in expected.keys() | actual.keys() if expected.get(key) != actual.get(key)}
    if changed:
        raise ValueError("unexpected effective configuration delta: " + repr(sorted(changed)))
    return {"changed_keys": [FLAG] if enabled else [], "query_features_preserved": True}


def symbol_facts(elf: Path, nm: str, enabled: bool, env: dict) -> dict:
    symbols = subprocess.check_output([nm, "-C", "-S", str(elf)], env=env, text=True)
    present = PROBE_SYMBOL in symbols and "deskbot_continuous_mmap::" in symbols
    unrelated = any(name in symbols for name in (
        "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce", "deskbot_studio_catalog::"))
    if present is not enabled or unrelated:
        raise ValueError("diagnostic symbol isolation failed")
    return {"probe_present": present, "unrelated_probe_present": unrelated}


def static_data_bytes(elf: Path, size_tool: str, env: dict) -> int:
    output = subprocess.check_output([size_tool, "-A", str(elf)], env=env, text=True)
    total = 0
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0] in (".dram0.data", ".dram0.bss") and len(fields) >= 2:
            total += int(fields[1], 0)
    if total <= 0:
        raise ValueError("internal static data size unavailable")
    return total


def audit_round(build: Path, reference_row: dict, reference_config: dict,
                reference_boot_config: dict, enabled: bool, nm: str,
                size_tool: str, env: dict) -> dict:
    artifacts = {name: file_record(build / name) for name in ARTIFACTS}
    reference_artifacts = reference_row["artifacts"]
    protected_equal = protected_artifact_results(artifacts, reference_artifacts)
    if not all(protected_equal.values()):
        raise ValueError("protected E3c-3a output changed: " + repr(protected_equal))
    app = artifacts["xiaozhi.bin"]
    base_app = reference_artifacts["xiaozhi.bin"]
    embedded_elf_sha256 = validate_app_elf((build / "xiaozhi.bin").read_bytes(),
                                           (build / "xiaozhi.elf").read_bytes())
    if not enabled and embedded_elf_sha256 != reference_row["app_elf_sha256"]:
        raise ValueError("probe-off embedded ELF identity differs from E3c-3a")
    if not enabled and app != base_app:
        raise ValueError("probe-off App must be byte-identical to E3c-3a query-on")
    effective = json.loads((build / "config/sdkconfig.json").read_bytes())
    config = validate_config(reference_config, effective, enabled)
    boot_config = json.loads((build / "bootloader/config/sdkconfig.json").read_bytes())
    if boot_config != reference_boot_config:
        raise ValueError("bootloader configuration changed")
    symbols = symbol_facts(build / "xiaozhi.elf", nm, enabled, env)
    headroom = APP_PARTITION_BYTES - app["bytes"]
    if headroom < MINIMUM_APP_HEADROOM:
        raise ValueError("App headroom is below 1 MiB")
    return {
        "probe_enabled": enabled,
        "build_directory": build.relative_to(ROOT).as_posix(),
        "artifacts": artifacts,
        "protected_equal_e3c3a": protected_equal,
        "effective_config": config,
        "symbols": symbols,
        "app_headroom_bytes": headroom,
        "internal_static_data_bytes": static_data_bytes(build / "xiaozhi.elf", size_tool, env),
        "embedded_elf_sha256": embedded_elf_sha256,
    }


def load_reference() -> tuple[dict, dict, dict]:
    ensure_safe_path(REFERENCE_REPORT, ROOT / "artifacts")
    ensure_safe_path(FROZEN, ROOT / "tmp")
    ensure_safe_path(REFERENCE_BUILD, ROOT / "tmp")
    raw = REFERENCE_REPORT.read_bytes()
    if sha256(raw) != REFERENCE_REPORT_SHA256:
        raise ValueError("accepted E3c-3a build report digest differs")
    report = json.loads(raw)
    if (report.get("schema") != "deskbot.usb_path_build/v1" or report.get("edition") != "E3c-3a"
            or report.get("result") != "off_on_builds_verified"
            or report.get("source_delta") != ["main/boards/esp_vocat/studio_usb_diagnostic.cc"]):
        raise ValueError("accepted E3c-3a build report identity differs")
    source_sha256 = report.get("source_sha256")
    rows = {row.get("query_enabled"): row for row in report.get("builds", [])}
    if set(rows) != {False, True}:
        raise ValueError("both E3c-3a build configurations are required")
    enabled = rows[True]
    if enabled.get("build_directory") != "tmp/e3c3a-usb-path-20261106/on":
        raise ValueError("E3c-3a query-on build directory differs")
    if enabled.get("artifacts", {}).get("xiaozhi.bin", {}).get("sha256") != (
            "e34d29e96bfb68eadda974b1f0a48350551a9b4d669cbf52e58db54b205d6dc3"):
        raise ValueError("accepted E3c-3a query-on App differs")
    if enabled.get("app_elf_sha256") != "cfe8c4b4700e0b0a51c0c7a881988af11d0d7084cfa05931a0af5f97c9618ca5":
        raise ValueError("accepted E3c-3a ELF identity differs")
    verify_frozen_sources(FROZEN, source_sha256)
    input_inventory = verify_project_file_inventory(
        FROZEN, source_sha256, load_locked_dependencies(FROZEN))
    probe = verify_probe_source(FROZEN)
    reference_config = json.loads((REFERENCE_BUILD / "config/sdkconfig.json").read_bytes())
    boot_config = json.loads((REFERENCE_BUILD / "bootloader/config/sdkconfig.json").read_bytes())
    for name, enabled_value in {FLAG: False, **{key: True for key in QUERY_FLAGS},
                                **{key: False for key in OTHER_PROBE_FLAGS}}.items():
        if reference_config.get(name) is not enabled_value:
            raise ValueError("E3c-3a query-on configuration differs: " + name)
    verify_reference_artifacts(enabled)
    enabled["bootloader_config"] = boot_config
    return report, enabled, {"config": reference_config, "probe": probe,
                             "input_inventory": input_inventory}


def verify_reference_artifacts(row: dict) -> None:
    for name, expected in row["artifacts"].items():
        actual = file_record(REFERENCE_BUILD / name)
        if actual != expected:
            raise ValueError("retained E3c-3a build output differs: " + name)


def build(run_label: str) -> dict:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", run_label) is None:
        raise ValueError("run label must use lowercase letters, digits and hyphens")
    work = WORK_ROOT / run_label
    ensure_safe_path(WORK_ROOT, ROOT / "tmp")
    ensure_safe_path(ARTIFACT_ROOT, ROOT / "artifacts")
    ensure_safe_path(work, ROOT / "tmp")
    output = ARTIFACT_ROOT / (run_label + "-build-report.json")
    ensure_safe_path(output, ROOT / "artifacts")
    if work.exists():
        raise FileExistsError("build run already exists; preserve it and choose a new label")
    if output.exists():
        raise FileExistsError("build report already exists")
    report, reference_row, metadata = load_reference()

    spec = importlib.util.spec_from_file_location("e143b2_build_runtime", ROOT / "scripts/build-expression-e1-42-flash-probe.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    env, python, idf = runtime.environment()
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    size_tool = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    if not nm or not size_tool:
        raise ValueError("pinned ESP32-S3 symbol and size tools are required")

    locked_source = load_locked_dependencies(FROZEN)
    source_component_report = runtime.HELPER.HELPER.validate_components(FROZEN, locked_source)
    if source_component_report != report.get("components"):
        raise ValueError("frozen managed components differ from E3c-3a build evidence")
    frozen_inventory = verify_project_file_inventory(FROZEN, report["source_sha256"], locked_source)
    if frozen_inventory != metadata["input_inventory"]:
        raise ValueError("frozen E3c-3a input inventory differs from its reference")

    work.mkdir(parents=True)
    project = work / "source"
    ensure_safe_path(project, work)
    shutil.copytree(FROZEN, project, symlinks=True)
    copied_inventory = verify_project_file_inventory(project, report["source_sha256"], locked_source)
    if copied_inventory != frozen_inventory:
        raise ValueError("isolated copy differs from frozen E3c-3a input inventory")
    rebind_dependency_lock(project, FROZEN)
    verify_frozen_sources(project, report["source_sha256"])
    verify_probe_source(project)
    locked = load_locked_dependencies(project)
    component_report = runtime.HELPER.HELPER.validate_components(project, locked)
    if component_report != source_component_report:
        raise ValueError("isolated managed components differ from frozen source")
    source_inventory = verify_project_file_inventory(project, report["source_sha256"], locked)
    if source_inventory != metadata["input_inventory"]:
        raise ValueError("isolated E3c-3a input inventory differs from frozen source")

    rounds, steps = {}, []
    baseline_sdkconfig = (REFERENCE_BUILD / "sdkconfig").read_text(encoding="utf-8")
    baseline_effective = metadata["config"]
    boot_config = json.loads((REFERENCE_BUILD / "bootloader/config/sdkconfig.json").read_bytes())
    for enabled in (False, True):
        label = "on" if enabled else "off"
        round_root = work / label
        build_dir = round_root / "build"
        evidence = round_root / "evidence"
        build_dir.mkdir(parents=True)
        evidence.mkdir()
        sdkconfig = build_dir / "sdkconfig"
        sdkconfig.write_text(prepare_sdkconfig(baseline_sdkconfig, enabled), encoding="utf-8", newline="\n")
        common = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build_dir),
                  "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
                  "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
        for action in ("reconfigure", "build"):
            log = round_root / f"{action}.log"
            if action == "build":
                runtime.HELPER.HELPER.quote_image_effects_link_path(build_dir, project)
            runtime.run(common + [action], project, env, log)
            log_sha256 = validate_step_log(log.read_bytes(), action)
            verify_frozen_sources(FROZEN, report["source_sha256"])
            verify_project_file_inventory(project, report["source_sha256"], locked)
            steps.append({"configuration": label, "action": action,
                          "returncode": 0, "log": log.relative_to(ROOT).as_posix(),
                          "sha256": log_sha256})

        round_result = audit_round(build_dir, reference_row, baseline_effective, boot_config,
                                   enabled, nm, size_tool, env)
        if verify_project_file_inventory(project, report["source_sha256"], locked) != source_inventory:
            raise ValueError("E3c-3a input inventory changed during build: " + label)
        for name in ARTIFACTS:
            destination = evidence / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build_dir / name, destination)
            if file_record(destination) != round_result["artifacts"][name]:
                raise ValueError("archived output differs: " + label + "/" + name)
        round_result["evidence_directory"] = evidence.relative_to(ROOT).as_posix()
        rounds[label] = round_result

        # Recheck the frozen input before the next configuration.
        verify_frozen_sources(project, report["source_sha256"])

    verify_frozen_sources(FROZEN, report["source_sha256"])
    verify_frozen_sources(project, report["source_sha256"])
    if runtime.HELPER.HELPER.validate_components(project, locked) != component_report:
        raise ValueError("frozen managed components changed during dual build")
    if (verify_project_file_inventory(FROZEN, report["source_sha256"],
                                      load_locked_dependencies(FROZEN)) != metadata["input_inventory"]
            or verify_project_file_inventory(project, report["source_sha256"], locked) != source_inventory):
        raise ValueError("frozen E3c-3a input inventory changed during dual build")

    if rounds["off"]["artifacts"]["xiaozhi.bin"] != reference_row["artifacts"]["xiaozhi.bin"]:
        raise ValueError("probe-off App is not the accepted E3c-3a image")
    if rounds["off"]["protected_equal_e3c3a"] != rounds["on"]["protected_equal_e3c3a"]:
        raise ValueError("protected outputs differ between probe configurations")
    result = {
        "schema": "deskbot.continuous_mmap.build.e1_43b_e3c3a/v1",
        "edition": "E1.43b-2",
        "result": "e3c3a_probe_off_on_builds_verified",
        "run_label": run_label,
        "reference_report": {"path": REFERENCE_REPORT.relative_to(ROOT).as_posix(),
                             "sha256": REFERENCE_REPORT_SHA256},
        "source": {"project": FROZEN.relative_to(ROOT).as_posix(),
                   "build_project": project.relative_to(ROOT).as_posix(),
                   "files_checked": len(report["source_sha256"]),
                   "source_sha256_digest": sha256(json.dumps(report["source_sha256"], sort_keys=True,
                                                              separators=(",", ":")).encode("utf-8")),
                   "input_inventory": source_inventory,
                   "probe": metadata["probe"], "changed": False},
        "target": {"chip": "esp32s3", "flash_bytes": 33554432, "psram_bytes": 16777216,
                   "layout": "upper-assets-8m", "port_for_later_live_test": "COM4"},
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    args = parser.parse_args()
    result = build(args.run_label)
    print(json.dumps({"result": result["result"],
                      "app_bytes": {key: value["artifacts"]["xiaozhi.bin"]["bytes"]
                                    for key, value in result["rounds"].items()},
                      "device_written": result["device_written"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
