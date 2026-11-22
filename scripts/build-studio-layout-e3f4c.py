#!/usr/bin/env python3
"""Build one expression increment from the frozen E3f-4a source with paired asset-layout builds."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("eat_build_helpers", ROOT / "scripts/build-studio-stable-ladder.py")
stable = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stable)
from protocol import studio_eat_deployment as baseline

H, runtime = stable.H, stable.RUNTIME
WORK = ROOT / "tmp/studio-layout-e3f4c-device"
PUBLIC = ROOT / "artifacts/studio-layout-e3f4c-device"
FLAG = "DESKBOT_ASSET_LAYOUT_ENABLE"
DELTA = {"main/Kconfig.projbuild", "main/assets.cc", "main/display/emote_display.cc",
         *("main/boards/esp_vocat/" + name for name in (
             "studio_asset_layout.h", "studio_asset_layout.cc", "studio_catalog.h",
             "studio_catalog_builder.cc", "studio_catalog_adapter.cc"))}


def inputs():
    raw = (ROOT / baseline.BUILD_REPORT).read_bytes()
    if H.sha256(raw) != baseline.BUILD_REPORT_SHA:
        raise ValueError("E3f-4a reference report changed")
    saved = json.loads(raw)
    project = ROOT / baseline.PROJECT
    H.verify_frozen_sources(project, saved["source_sha256"])
    if H.file_record(ROOT / baseline.SOURCE) != dict(bytes=baseline.APP_SIZE, sha256=baseline.APP_SHA):
        raise ValueError("E3f-4a reference App changed")
    manifest = dict(saved["source_sha256"])
    updates = {name: (ROOT / "firmware/body" / name).read_bytes() for name in DELTA}
    manifest.update({name: H.sha256(raw) for name, raw in updates.items()})
    if {name for name in manifest if manifest[name] != saved["source_sha256"].get(name)} != DELTA:
        raise ValueError("expression source delta differs")
    return project, saved, updates, manifest


def inspect(folder, enabled, env):
    reference = ROOT / baseline.SOURCE.parent
    actual = json.loads((folder / "config/sdkconfig.json").read_bytes())
    original = json.loads((reference / "config/sdkconfig.json").read_bytes())
    for optional in (FLAG, "DESKBOT_STUDIO_SHY_REQUEST_ENABLE"):
        original[optional] = original.get(optional, False)
    delta = {k for k in actual.keys() | original.keys() if actual.get(k) != original.get(k)}
    if delta != ({FLAG} if enabled else set()) or actual.get(FLAG, False) is not enabled:
        raise ValueError("unexpected effective config delta: " + repr(delta))
    stable.release.effective_octal_config(actual, 32)
    artifacts = {name: H.file_record(folder / name) for name in H.ARTIFACTS}
    protected = {name: artifacts[name] == H.file_record(reference / name) for name in H.PROTECTED}
    if not all(protected.values()):
        raise ValueError("protected build outputs changed")
    elf_hash = H.validate_app_elf((folder / "xiaozhi.bin").read_bytes(), (folder / "xiaozhi.elf").read_bytes())
    size = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    internal = H.static_data_bytes(folder / "xiaozhi.elf", size, env)
    prior_internal = H.static_data_bytes(reference / "xiaozhi.elf", size, env)
    if internal - prior_internal > (128 if enabled else 0):
        raise ValueError("layout registry exceeds 128-byte internal static budget")
    symbols = subprocess.check_output([nm, "-S", "-C", str(folder / "xiaozhi.elf")], env=env, text=True)
    for marker in ("deskbot_studio_runtime::ReadDisplay()", "deskbot_studio_expression::AdapterHooks()",
                   "deskbot_studio_catalog::BuildFromMounted("):
        if marker not in symbols:
            raise ValueError("missing inherited feature: " + marker)
    if any(marker in symbols for marker in ("RunContinuousMappingOnce", "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce")):
        raise ValueError("temporary diagnostic linked")
    stacks = {}
    for path in (folder / "esp-idf/main").rglob("*.su"):
        if path.name.startswith(("studio_", "assets.cc")):
            for line in path.read_text(encoding="utf-8").splitlines():
                fields = line.split("\t")
                if len(fields) >= 3:
                    stacks[fields[0]] = dict(bytes=int(fields[1]), allocation=fields[2])
    if not stacks or any(r["bytes"] > 2048 or r["allocation"] not in ("static", "dynamic,bounded") for r in stacks.values()):
        raise ValueError("bounded function stack budget exceeded")
    headroom = H.APP_PARTITION_BYTES - artifacts["xiaozhi.bin"]["bytes"]
    if headroom < 1024 * 1024:
        raise ValueError("App headroom below 1 MiB")
    return dict(enabled=enabled, artifacts=artifacts, app_elf_sha256=elf_hash, config_delta=sorted(delta),
                protected_equal=protected, internal_static_data_bytes=internal, app_headroom_bytes=headroom,
                compiler_stack_usage=stacks)


def build(*, resume=False, check=False):
    source, reference, updates, manifest = inputs()
    output = PUBLIC / "firmware-build-report.json"
    project = WORK / "p"
    H.ensure_safe_path(project, ROOT / "tmp")
    H.ensure_safe_path(output, ROOT / "artifacts")
    env, python, idf = runtime.environment()
    if check:
        report = json.loads(output.read_bytes())
        if report["source_sha256"] != manifest:
            raise ValueError("reviewed candidate source changed")
    else:
        if output.exists() and not resume:
            raise FileExistsError("retained build exists; use --check or --resume")
        if not project.exists():
            shutil.copytree(source, project)
            H.rebind_dependency_lock(project, source)
            for name, raw in updates.items():
                (project / name).write_bytes(raw)
        H.verify_frozen_sources(project, manifest)
    H.verify_frozen_sources(project, manifest)
    locked = H.load_locked_dependencies(project)
    components = stable.BASE.validate_reviewed_components(project, locked)
    inventory = H.verify_project_file_inventory(project, manifest, locked)
    steps, builds = [], []
    for label, enabled in (("off", False), ("on", True)):
        folder = WORK / label
        H.ensure_safe_path(folder, ROOT / "tmp")
        if not check:
            folder.mkdir(exist_ok=True)
            config = (ROOT / baseline.SOURCE.parent / "sdkconfig").read_text(encoding="utf-8")
            overlay = f"CONFIG_{FLAG}=y\n" if enabled else f"# CONFIG_{FLAG} is not set\n"
            (folder / "sdkconfig").write_text(stable.release.apply_overlay(config, overlay), encoding="utf-8")
        command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(folder),
                   "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
                   "-D", "CMAKE_CXX_FLAGS=-fstack-usage", "-D", f"SDKCONFIG={folder / 'sdkconfig'}",
                   "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
        for action in ("reconfigure", "build"):
            log = WORK / (label + "-" + action + ".log")
            H.ensure_safe_path(log, ROOT / "tmp")
            if not check:
                if log.exists():
                    number = 1
                    while log.with_suffix(f".attempt{number}.log").exists():
                        number += 1
                    log.rename(log.with_suffix(f".attempt{number}.log"))
                print(f"E3f-4c {label}: {action}", flush=True)
                if action == "reconfigure":
                    runtime.run(command + [action], project, env, log)
                else:
                    if runtime.HELPER.HELPER.quote_image_effects_link_path(folder, project) != 1:
                        raise ValueError("image-effects link repair differs")
                    ninja = shutil.which("ninja.exe", path=env["PATH"])
                    runtime.run([ninja, "-C", str(folder)], project, env, log)
            stable.validate_log(log, action)
            steps.append(dict(configuration=label, action=action, log=log.relative_to(ROOT).as_posix(),
                              sha256=H.file_record(log)["sha256"]))
        builds.append(inspect(folder, enabled, env))
    comparison = stable.compare_disabled((ROOT / baseline.SOURCE).read_bytes(), (WORK / "off/xiaozhi.bin").read_bytes())
    H.verify_frozen_sources(project, manifest)
    H.verify_frozen_sources(source, reference["source_sha256"])
    if (components != stable.BASE.validate_reviewed_components(project, locked)
            or inventory != H.verify_project_file_inventory(project, manifest, locked)
            or inputs()[3] != manifest):
        raise ValueError("build inputs changed during verification")
    result = dict(schema="deskbot.studio_asset_layout_build/v1", baseline="E3f-4a", result="off_on_builds_verified",
                  source_sha256=manifest, source_delta=sorted(DELTA), components=components, input_inventory=inventory,
                  baseline_report_sha256=baseline.BUILD_REPORT_SHA, builds=builds, steps=steps,
                  disabled_comparison=comparison, new_tasks=0, usb_reads_per_iteration=1, usb_yield_ms=10,
                  usb_stack_bytes=6144, usb_priority=1, device_accessed=False, device_written=False,
                  hardware_acceptance="pending")
    if check:
        if result != report:
            raise ValueError("saved build report differs")
    else:
        PUBLIC.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".json.new")
        H.ensure_safe_path(temporary, ROOT / "artifacts")
        with temporary.open("xb") as stream:
            stream.write(stable.json_bytes(result))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    print("E3f-4c paired build " + ("checked" if check else "complete"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    build(resume=args.resume, check=args.check)
