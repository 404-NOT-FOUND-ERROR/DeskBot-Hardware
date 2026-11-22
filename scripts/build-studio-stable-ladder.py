#!/usr/bin/env python3
"""Offline display/control/catalog candidates on the accepted E3c-3b baseline."""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from protocol.studio_resource_package import json_bytes, load_target
from e3c4a_display import DISPLAY_FILES, normalized_source


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


BASE = module("stable_ladder_base", "scripts/build-expression-e1-43b-e3c3b.py")
H, RUNTIME = BASE.helpers, BASE.build_runtime.BASE
release = BASE.build_runtime.release
WORK = ROOT / "tmp/studio-stable-ladder-20261108"
PUBLIC = ROOT / "artifacts/studio-stable-ladder-20261108"
BOARD = "main/boards/esp_vocat/"
USB = BOARD + "studio_usb_diagnostic.cc"
FLAGS = ("DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE", "DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE",
         "DESKBOT_STUDIO_CATALOG_ENABLE")
STAGES = ("display", "neutral", "catalog")
CHECKED = set()
DONORS = {
    "display": ("tmp/e3c4a-display-20261107/p", "artifacts/studio-display-e3c4a-20261107/firmware-build-report.json",
                "1e7c1ff38d8a2814f5e1167b25019a3b2b0477c23f3fb3f3f93df32da827cb54"),
    "neutral": ("tmp/e3c5-expression-20261105/p", "artifacts/deskbot-studio-e3c5-20261105/firmware-build-report.json",
                "b251d438b5f49531f3c1204218d12c5f064b616e4c7a901f8750f7a6ad3490ac"),
    "catalog": ("tmp/e3d2-catalog-20261105/p", "artifacts/deskbot-studio-e3d2-20261105/firmware-build-report.json",
                "a56820f6f810e675700d7fa422eaccb509f39ebc3ab6c9daa494493e945be9df"),
}
DELTAS = {
    "display": DISPLAY_FILES,
    "neutral": {"main/Kconfig.projbuild", USB, *(BOARD + n for n in (
        "studio_usb_session.h", "studio_expression_request.h", "studio_expression_control.h",
        "studio_expression_adapter.h", "studio_expression_adapter.cc"))},
    "catalog": {"main/Kconfig.projbuild", "main/assets.cc", USB, *(BOARD + n for n in (
        "studio_usb_session.h", "studio_catalog.h", "studio_catalog_actions.h", "studio_catalog_builder.cc",
        "studio_catalog_adapter.h", "studio_catalog_adapter.cc"))},
}


def replace(raw, old, new):
    if raw.count(old) != 1:
        raise ValueError("stable USB patch anchor differs")
    return raw.replace(old, new, 1)


def usb_source(accepted, level):
    """Keep the accepted owner, VFS, one read/tick, delay and task parameters.

    Only provider registration, bounded reply storage and receipt polling are
    added. Throughput changes belong to a separate, measurable increment.
    """
    raw = accepted
    hook = b"#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE\n    endpoint.SetStateProvider(deskbot_studio_runtime::Read);\n#endif\n"
    raw = replace(raw, hook, hook + b"#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE\n    endpoint.SetDisplayProvider(deskbot_studio_runtime::ReadDisplay);\n#endif\n")
    if level >= 1:
        raw = replace(raw, b'#include "studio_usb_session.h"\n', b'#include "studio_usb_session.h"\n#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE\n#include "studio_expression_adapter.h"\n#endif\n')
        raw = replace(raw, b"        vTaskDelay(pdMS_TO_TICKS(10));", b"#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE\n        if (deskbot_studio_expression::Poll(reply, sizeof(reply)))\n            ESP_LOGI(\"DeskBotSession\", \"%s\", reply);\n#endif\n        vTaskDelay(pdMS_TO_TICKS(10));")
        raw = replace(raw, b'    if (xTaskCreate(DiagnosticTask,', b'#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE\n    endpoint.SetExpressionHooks(deskbot_studio_expression::AdapterHooks());\n#endif\n    if (xTaskCreate(DiagnosticTask,')
    if level >= 2:
        raw = replace(raw, b'#include "studio_usb_session.h"\n', b'#include "studio_usb_session.h"\n#if CONFIG_DESKBOT_STUDIO_CATALOG_ENABLE\n#include "studio_catalog_adapter.h"\n#endif\n')
        raw = replace(raw, b"Endpoint endpoint;", b"Endpoint endpoint;\n#if CONFIG_DESKBOT_STUDIO_CATALOG_ENABLE\nchar catalog_reply[deskbot_studio_catalog::kReplyBytes];\n#endif")
        raw = replace(raw, b"    char reply[1024];", b"#if CONFIG_DESKBOT_STUDIO_CATALOG_ENABLE\n    auto& reply = catalog_reply;\n#else\n    char reply[1024];\n#endif")
        raw = replace(raw, hook, b"#if CONFIG_DESKBOT_STUDIO_CATALOG_ENABLE\n    endpoint.SetCatalogProvider(deskbot_studio_catalog::ReadPage);\n#endif\n" + hook)
    return raw


def sources(stage, baseline=None):
    if stage not in STAGES:
        raise ValueError("unknown stage")
    metadata = baseline or BASE.load_reference()[2]
    result = {name: H.safe_read(BASE.FROZEN, name) for name in metadata["source_sha256"]}
    manifests, donors, previous = {}, {}, metadata["source_sha256"]
    for level, name in enumerate(STAGES[:STAGES.index(stage) + 1]):
        folder, report_path, digest = DONORS[name]
        raw = (ROOT / report_path).read_bytes()
        if H.sha256(raw) != digest:
            raise ValueError("donor report differs: " + name)
        report = json.loads(raw)
        tree = ROOT / folder
        H.verify_frozen_sources(tree, report["source_sha256"])
        for path in DELTAS[name] - {USB}:
            result[path] = H.safe_read(tree, path)
        result[USB] = usb_source(H.safe_read(BASE.FROZEN, USB), level)
        manifest = {path: H.sha256(normalized_source(content, path, BASE.FROZEN))
                    for path, content in result.items()}
        changed = {path for path in previous.keys() | manifest.keys()
                   if previous.get(path) != manifest.get(path)}
        if changed != DELTAS[name]:
            raise ValueError("source delta differs: " + name + " " + repr(sorted(changed)))
        manifests[name], previous = manifest, manifest
        donors[name] = {"path": report_path, "sha256": digest}
    return result, manifests[stage], donors


def compare_disabled(reference, disabled):
    """Compare all loadable bytes, excluding only the embedded ELF digest.

    The image checksum and appended SHA necessarily follow that digest. Build
    timestamps, descriptions, strings, section layout and code stay identical.
    """
    a, b = release.image_facts(reference), release.image_facts(disabled)
    layout = lambda facts: [(r["load_address"], r["bytes"]) for r in facts["segments"]]
    if len(reference) != len(disabled) or layout(a) != layout(b):
        raise ValueError("disabled App layout differs")
    first = b["segments"][0]["file_offset"]
    if struct.unpack_from("<I", disabled, first)[0] != 0xABCD5432:
        raise ValueError("App description invalid")
    allowed = set(range(first + 144, first + 176)) | {b["checksum_offset"]} | set(range(len(disabled)-32, len(disabled)))
    changed = {i for i, (x, y) in enumerate(zip(reference, disabled)) if x != y}
    if not changed <= allowed:
        raise ValueError("disabled executable/data differ at " + repr(sorted(changed - allowed)[:10]))
    return {"bytes": len(disabled), "differing_bytes": len(changed),
            "byte_identical": reference == disabled, "executable_and_data_equal": True,
            "allowed_fields": ["embedded_elf_sha256", "image_checksum", "image_sha256"]}


def config_delta(reference, actual, level, enabled):
    # New Kconfig symbols are absent from older reports; absent bool means off.
    normalize = lambda d: {**d, **{flag: d.get(flag, False) for flag in FLAGS}}
    a, b = normalize(reference), normalize(actual)
    changed = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
    expected = {FLAGS[level]} if enabled else set()
    if changed != expected or any(b[flag] is not (i < level or i == level and enabled)
                                  for i, flag in enumerate(FLAGS)):
        raise ValueError("unexpected config delta: " + repr(changed))
    return sorted(changed)


def inspect(build, reference, level, enabled, env):
    for name in H.ARTIFACTS:
        H.ensure_safe_path(build / name, ROOT / "tmp")
    actual = json.loads((build / "config/sdkconfig.json").read_bytes())
    before = json.loads((reference / "config/sdkconfig.json").read_bytes())
    delta = config_delta(before, actual, level, enabled)
    release.effective_octal_config(actual, 32)
    artifacts = {name: H.file_record(build / name) for name in H.ARTIFACTS}
    protected = {name: artifacts[name] == H.file_record(BASE.REFERENCE_BUILD / name) for name in H.PROTECTED}
    if not all(protected.values()):
        raise ValueError("protected outputs changed: " + repr(protected))
    target = load_target()
    headroom = H.APP_PARTITION_BYTES - artifacts["xiaozhi.bin"]["bytes"]
    if headroom < target["minimum_app_headroom_bytes"]:
        raise ValueError("App headroom below target")
    app, elf = (build / "xiaozhi.bin").read_bytes(), (build / "xiaozhi.elf").read_bytes()
    elf_digest = H.validate_app_elf(app, elf)
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    size = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    symbols = subprocess.check_output([nm, "-S", "-C", str(build / "xiaozhi.elf")], env=env, text=True)
    checks = ("deskbot_studio_runtime::ReadDisplay()", "deskbot_studio_expression::AdapterHooks()",
              "deskbot_studio_catalog::BuildFromMounted(")
    for i, marker in enumerate(checks):
        if (marker in symbols) != (i < level or i == level and enabled):
            raise ValueError("feature marker differs: " + marker)
    if any(marker in symbols for marker in ("RunContinuousMappingOnce", "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce")):
        raise ValueError("temporary probe linked")
    internal = H.static_data_bytes(build / "xiaozhi.elf", size, env)
    if not 0 < internal < 80 * 1024:
        raise ValueError("internal static budget exceeded")
    stacks = {}
    for path in (build / "esp-idf/main").rglob("*.su"):
        if path.name.startswith(("studio_", "assets.cc")):
            for line in path.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    stacks[parts[0]] = {"bytes": int(parts[1]), "allocation": parts[2]}
    if not stacks or any(row["bytes"] > 2048 or row["allocation"] not in ("static", "dynamic,bounded")
                         for row in stacks.values()):
        raise ValueError("single-function stack budget exceeded")
    return {"enabled": enabled, "build_directory": build.relative_to(ROOT).as_posix(), "artifacts": artifacts,
            "config_delta": delta, "protected_equal": protected, "app_elf_sha256": elf_digest,
            "app_headroom_bytes": headroom, "internal_static_data_bytes": internal,
            "internal_static_delta_previous": internal - H.static_data_bytes(reference / "xiaozhi.elf", size, env),
            "internal_static_delta_baseline": internal - 50800, "compiler_stack_usage": stacks}


def build_stage(stage, resume=False, check=False):
    _, _, baseline = BASE.load_reference()
    source, manifest, donors = sources(stage, baseline)
    level = STAGES.index(stage)
    reference = BASE.REFERENCE_BUILD if not level else WORK / STAGES[level-1] / "on"
    if level and STAGES[level-1] not in CHECKED:
        build_stage(STAGES[level-1], check=True)
    work, project = WORK / stage, WORK / stage / "p"
    output = PUBLIC / (stage + "-build.json")
    H.ensure_safe_path(work, ROOT / "tmp")
    H.ensure_safe_path(output, ROOT / "artifacts")
    for item in (project, work / "off", work / "on", PUBLIC / (stage + ".patch")):
        H.ensure_safe_path(item, ROOT / ("artifacts" if item.is_relative_to(PUBLIC) else "tmp"))
    for label in ("off", "on"):
        H.ensure_safe_path(work / label / "sdkconfig", ROOT / "tmp")
        for action in ("reconfigure", "build"):
            H.ensure_safe_path(work / (label + "-" + action + ".log"), ROOT / "tmp")
    env, python, idf = RUNTIME.environment()
    if check:
        saved = json.loads(output.read_bytes())
        if saved["source_sha256"] != manifest or saved["donors"] != donors:
            raise ValueError("saved source inputs changed")
        validate_report_structure(saved, stage, reference, baseline)
        for row, enabled in zip(saved["builds"], (False, True)):
            if row != inspect(work / ("on" if enabled else "off"), reference, level, enabled, env):
                raise ValueError("saved build changed")
        if saved["disabled_comparison"] != compare_disabled((reference / "xiaozhi.bin").read_bytes(), (work / "off/xiaozhi.bin").read_bytes()):
            raise ValueError("disabled comparison changed")
        for step in saved["steps"]:
            log = work / (step["configuration"] + "-" + step["action"] + ".log")
            if H.file_record(log)["sha256"] != step["sha256"]:
                raise ValueError("build log changed")
            validate_log(log, step["action"])
    else:
        if (work.exists() or output.exists()) and not resume:
            raise FileExistsError("retained stage exists; use --resume or --check")
        if not project.exists():
            work.mkdir(parents=True, exist_ok=True)
            shutil.copytree(BASE.FROZEN, project)
            H.rebind_dependency_lock(project, BASE.FROZEN)
            for name, raw in source.items():
                if name != "dependencies.lock":
                    path = project / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
        H.verify_frozen_sources(project, manifest)
        locked = H.load_locked_dependencies(project)
        components = BASE.validate_reviewed_components(project, locked)
        inventory = H.verify_project_file_inventory(project, manifest, locked)
        rows, steps = [], []
        for enabled in (False, True):
            label = "on" if enabled else "off"
            folder = work / label
            H.ensure_safe_path(folder, ROOT / "tmp")
            folder.mkdir(exist_ok=True)
            sdkconfig = folder / "sdkconfig"
            config = (reference / "sdkconfig").read_text(encoding="utf-8")
            overlay = "\n".join(f"CONFIG_{flag}=y" if i < level or i == level and enabled else f"# CONFIG_{flag} is not set" for i, flag in enumerate(FLAGS)) + "\n"
            sdkconfig.write_text(release.apply_overlay(config, overlay), encoding="utf-8")
            command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(folder),
                       "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
                       "-D", "CMAKE_CXX_FLAGS=-fstack-usage", "-D", f"SDKCONFIG={sdkconfig}",
                       "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
            for action in ("reconfigure", "build"):
                log = work / (label + "-" + action + ".log")
                if log.exists():
                    number = 1
                    while log.with_suffix(f".attempt{number}.log").exists():
                        number += 1
                    log.rename(log.with_suffix(f".attempt{number}.log"))
                print(f"stable {stage} {label}: {action}", flush=True)
                if action == "reconfigure":
                    RUNTIME.run(command + [action], project, env, log)
                else:
                    patched = RUNTIME.HELPER.HELPER.quote_image_effects_link_path(folder, project)
                    if patched != 1:
                        raise ValueError("image-effects link repair differs")
                    ninja = shutil.which("ninja.exe", path=env["PATH"]) or shutil.which("ninja", path=env["PATH"])
                    RUNTIME.run([ninja, "-C", str(folder)], project, env, log)
                validate_log(log, action)
                steps.append({"configuration": label, "action": action,
                              "log": log.relative_to(ROOT).as_posix(), "sha256": H.file_record(log)["sha256"]})
            rows.append(inspect(folder, reference, level, enabled, env))
        comparison = compare_disabled((reference / "xiaozhi.bin").read_bytes(), (work / "off/xiaozhi.bin").read_bytes())
        patch = ""
        prior_source = BASE.FROZEN if not level else WORK / STAGES[level-1] / "p"
        for name in sorted(DELTAS[stage]):
            before = (prior_source / name).read_text(encoding="utf-8").splitlines() if (prior_source / name).exists() else []
            patch += "\n".join(difflib.unified_diff(before, source[name].decode("utf-8").splitlines(),
                                                  fromfile="previous/"+name, tofile=stage+"/"+name, lineterm="")) + "\n"
        PUBLIC.mkdir(parents=True, exist_ok=True)
        (PUBLIC / (stage + ".patch")).write_text(patch, encoding="utf-8", newline="\n")
        saved = {"schema": "deskbot.stable_candidate_ladder/v1", "stage": stage, "baseline": "E3c-3b",
                 "result": "off_on_builds_verified", "source_sha256": manifest,
                 "source_delta": sorted(DELTAS[stage]), "donors": donors, "components": components,
                 "input_inventory": inventory, "reference_app": H.file_record(reference / "xiaozhi.bin"),
                 "stable_report_sha256": baseline["report_sha256"], "builds": rows, "steps": steps,
                 "disabled_comparison": comparison, "target": load_target(),
                 "usb": {"reads_per_iteration": 1, "yield_ms": 10, "stack_bytes": 6144, "priority": 1},
                 "new_tasks": 0, "catalog_snapshot_max_psram_bytes": 68*1024 if level == 2 else 0,
                 "device_accessed": False, "device_written": False, "hardware_acceptance": "pending"}
        validate_report_structure(saved, stage, reference, baseline)
    H.verify_frozen_sources(project, manifest)
    locked = H.load_locked_dependencies(project)
    if (BASE.validate_reviewed_components(project, locked) != saved["components"] or
            H.verify_project_file_inventory(project, manifest, locked) != saved["input_inventory"]):
        raise ValueError("final input inventory changed")
    # Seal success only after the source trees and reviewed component override
    # have been checked again. A partial run never leaves a success report.
    H.verify_frozen_sources(BASE.FROZEN, baseline["source_sha256"])
    if H.verify_project_file_inventory(BASE.FROZEN, baseline["source_sha256"], H.load_locked_dependencies(BASE.FROZEN)) != baseline["input_inventory"]:
        raise ValueError("stable auxiliary inventory changed")
    if BASE.validate_reviewed_components(BASE.FROZEN, H.load_locked_dependencies(BASE.FROZEN)) != baseline["components"]:
        raise ValueError("stable source components changed")
    final_sources, final_manifest, final_donors = sources(stage, baseline)
    if final_sources != source or final_manifest != manifest or final_donors != donors:
        raise ValueError("donor inputs changed during build")
    if not check:
        temporary = output.with_suffix(".json.new")
        H.ensure_safe_path(temporary, ROOT / "artifacts")
        with temporary.open("wb") as stream:
            stream.write(json_bytes(saved))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    CHECKED.add(stage)
    print(json.dumps({"stage": stage, "result": saved["result"],
                      "app_bytes": [r["artifacts"]["xiaozhi.bin"]["bytes"] for r in saved["builds"]]}), flush=True)
    return saved


def validate_log(path, action):
    text = path.read_text(encoding="utf-8", errors="replace")
    marker = "-- Build files have been written to:" if action == "reconfigure" else "xiaozhi.bin binary size"
    if (marker not in text and not (action == "build" and "ninja: no work to do." in text)) or "FAILED:" in text:
        raise ValueError("build success marker missing")


def validate_report_structure(saved, stage, reference, baseline):
    level = STAGES.index(stage)
    expected = {"schema": "deskbot.stable_candidate_ladder/v1", "stage": stage, "baseline": "E3c-3b",
        "result": "off_on_builds_verified", "source_delta": sorted(DELTAS[stage]), "target": load_target(),
        "reference_app": H.file_record(reference / "xiaozhi.bin"),
        "stable_report_sha256": baseline["report_sha256"],
        "usb": {"reads_per_iteration": 1, "yield_ms": 10, "stack_bytes": 6144, "priority": 1},
        "new_tasks": 0, "catalog_snapshot_max_psram_bytes": 68*1024 if level == 2 else 0,
        "device_accessed": False, "device_written": False, "hardware_acceptance": "pending"}
    extra = {"source_sha256", "donors", "components", "input_inventory", "builds", "steps", "disabled_comparison"}
    if type(saved) is not dict or set(saved) != set(expected) | extra or any(saved.get(k) != v for k, v in expected.items()):
        raise ValueError("saved report metadata differs")
    rows = saved["builds"]
    if (type(rows) is not list or len(rows) != 2 or any(type(row) is not dict or row.get("enabled") is not enabled
        or row.get("build_directory") != (WORK / stage / label).relative_to(ROOT).as_posix()
        for row, enabled, label in zip(rows, (False, True), ("off", "on")))):
        raise ValueError("saved off/on build rows differ")
    steps = saved["steps"]
    names = [(label, action) for label in ("off", "on") for action in ("reconfigure", "build")]
    if (type(steps) is not list or len(steps) != 4 or any(type(row) is not dict
        or set(row) != {"configuration", "action", "log", "sha256"}
        or (row.get("configuration"), row.get("action")) != (label, action)
        or row.get("log") != (WORK / stage / (label+"-"+action+".log")).relative_to(ROOT).as_posix()
        for row, (label, action) in zip(steps, names))):
        raise ValueError("saved build steps differ")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=(*STAGES, "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for stage in STAGES if args.stage == "all" else (args.stage,):
        build_stage(stage, args.resume, args.check)
