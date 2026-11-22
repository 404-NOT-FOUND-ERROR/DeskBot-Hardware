#!/usr/bin/env python3
"""Build and audit the default-off E3f-9c role player on frozen E3f-9a."""
from __future__ import annotations

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
spec = importlib.util.spec_from_file_location("layout_build", ROOT / "scripts/build-studio-layout-manifest-e3f9a.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
H, stable, runtime = base.H, base.stable, base.runtime
SOURCE = ROOT / "tmp/studio-layout-manifest-e3f9a/p"
REFERENCE = ROOT / "tmp/studio-layout-manifest-e3f9a"
REPORT_PATH = ROOT / "artifacts/studio-layout-manifest-e3f9a/firmware-build-report.json"
WORK, PUBLIC = ROOT / "tmp/studio-role-player-e3f9c", ROOT / "artifacts/studio-role-player-e3f9c"
FLAG = "DESKBOT_ROLE_PLAYER_ENABLE"
NEW = {"main/boards/esp_vocat/studio_role_" + name + "." + ext
       for name in ("graph", "player") for ext in ("h", "cc")}
DELTA = {"main/Kconfig.projbuild", "main/display/emote_display.cc", "main/assets.cc",
         "main/boards/esp_vocat/ui_bridge.cc", "main/boards/esp_vocat/studio_asset_layout.h",
         "main/boards/esp_vocat/studio_asset_layout.cc", *NEW}


def without_role(text, name):
    if name == "main/Kconfig.projbuild":
        start = text.index("config " + FLAG + "\n")
        end = text.index("config DESKBOT_STUDIO_CATALOG_ENABLE\n", start)
        return text[:start] + text[end:]
    output, depth, branch = [], 0, False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not depth and stripped == "#if CONFIG_" + FLAG:
            depth, branch = 1, False
        elif depth and stripped.startswith(("#if ", "#ifdef ", "#ifndef ")):
            depth += 1
        elif depth and stripped == "#else" and depth == 1:
            branch = True
        elif depth and stripped == "#endif":
            depth -= 1
        elif not depth or branch:
            output.append(line)
    if depth:
        raise ValueError("unterminated role source branch")
    return "".join(output)


def source_inputs():
    report = json.loads(REPORT_PATH.read_bytes())
    if report.get("edition") != "E3f-9a" or report.get("result") != "off_on_builds_verified":
        raise ValueError("verified E3f-9a report required")
    H.verify_frozen_sources(SOURCE, report["source_sha256"])
    manifest, patches = dict(report["source_sha256"]), {}
    for name in sorted(DELTA):
        raw = (ROOT / "firmware/body" / name).read_bytes()
        if name not in NEW:
            before = (SOURCE / name).read_text(encoding="utf-8")
            if without_role(raw.decode("utf-8").replace("\r\n", "\n"), name) != before:
                raise ValueError("unrelated source delta: " + name)
        patches[name], manifest[name] = raw, H.sha256(raw)
    return report, manifest, patches


def prepare(*, refresh=False):
    report, manifest, patches = source_inputs()
    project = WORK / "p"
    H.ensure_safe_path(project, ROOT / "tmp")
    created = not project.exists()
    if created:
        shutil.copytree(SOURCE, project)
        H.rebind_dependency_lock(project, SOURCE)
    else:
        H.verify_frozen_sources(project, {k: v for k, v in manifest.items() if k not in DELTA})
    if created or refresh:
        # Only the declared increment may be refreshed on --resume. All other
        # source files and dependency contents retain their reviewed hashes.
        for name, raw in patches.items():
            if not (project / name).exists() or (project / name).read_bytes() != raw:
                (project / name).write_bytes(raw)
    H.verify_frozen_sources(project, manifest)
    locked = H.load_locked_dependencies(project)
    components = stable.BASE.validate_reviewed_components(project, locked)
    inventory = H.verify_project_file_inventory(project, manifest, locked)
    return report, manifest, project, components, inventory


def inspect(folder: Path, enabled: bool, env):
    reference = REFERENCE / "on"
    config = json.loads((folder / "config/sdkconfig.json").read_bytes())
    original = json.loads((reference / "config/sdkconfig.json").read_bytes())
    original[FLAG] = original.get(FLAG, False)
    delta = {k for k in config.keys() | original.keys() if config.get(k) != original.get(k)}
    if delta != ({FLAG} if enabled else set()) or config.get(FLAG) is not enabled:
        raise ValueError("unexpected effective config delta: " + repr(delta))
    stable.release.effective_octal_config(config, 32)
    artifacts = {name: H.file_record(folder / name) for name in H.ARTIFACTS}
    protected = {name: artifacts[name] == H.file_record(reference / name) for name in H.PROTECTED}
    if not all(protected.values()):
        raise ValueError("protected outputs changed")
    elf = H.validate_app_elf((folder / "xiaozhi.bin").read_bytes(), (folder / "xiaozhi.elf").read_bytes())
    size = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    if not size or not nm:
        raise FileNotFoundError("ESP-IDF size/nm tools required")
    internal = H.static_data_bytes(folder / "xiaozhi.elf", size, env)
    base_internal = H.static_data_bytes(reference / "xiaozhi.elf", size, env)
    if not base_internal <= internal <= base_internal + 64:
        raise ValueError("role internal static data budget exceeded")
    symbols = subprocess.check_output([nm, "-S", "-C", str(folder / "xiaozhi.elf")], env=env, text=True)
    for marker in ("deskbot_studio_runtime::ReadDisplay()", "deskbot_studio_expression::AdapterHooks()",
                   "deskbot_studio_catalog::BuildFromMounted("):
        if marker not in symbols:
            raise ValueError("missing inherited feature: " + marker)
    for marker in ("deskbot_role::LoadGraph", "deskbot_role::Drain", "deskbot_role::SyncOwners"):
        if (marker in symbols) != enabled:
            raise ValueError("role symbol differs from configuration: " + marker)
    if any(m in symbols for m in ("RunContinuousMappingOnce", "RunUpperPatternOnce", "ExportFlashDiagnosticsOnce")):
        raise ValueError("temporary diagnostic linked")
    stacks = {}
    for path in (folder / "esp-idf/main").rglob("*.su"):
        if path.name.startswith(("studio_", "assets.cc", "emote_display")):
            for line in path.read_text(encoding="utf-8").splitlines():
                fields = line.split("\t")
                if len(fields) >= 3:
                    stacks[fields[0]] = dict(bytes=int(fields[1]), allocation=fields[2])
    if not stacks or any(row["bytes"] > 2048 or row["allocation"] not in ("static", "dynamic,bounded") for row in stacks.values()):
        raise ValueError("bounded role function stack budget exceeded")
    headroom = H.APP_PARTITION_BYTES - artifacts["xiaozhi.bin"]["bytes"]
    if headroom < 1024 * 1024:
        raise ValueError("App headroom below 1 MiB")
    return dict(enabled=enabled, artifacts=artifacts, app_elf_sha256=elf, config_delta=sorted(delta),
                protected_equal=protected, internal_static_data_bytes=internal,
                internal_static_delta_bytes=internal-base_internal, app_headroom_bytes=headroom,
                compiler_stack_usage=stacks)


def build(resume=False, check=False):
    if check and not (WORK / "p").is_dir():
        raise FileNotFoundError("saved frozen project required for --check")
    baseline_sha = H.file_record(REPORT_PATH)["sha256"]
    reference, manifest, project, components, inventory = prepare(refresh=resume and not check)
    output = PUBLIC / "firmware-build-report.json"
    H.ensure_safe_path(output, ROOT / "artifacts")
    if output.exists() and not resume and not check:
        raise FileExistsError("retained build exists; use --resume")
    saved = json.loads(output.read_bytes()) if check else None
    env, python, idf = runtime.environment()
    steps, builds = [], []
    for label, enabled in (("off", False), ("on", True)):
        folder = WORK / label
        H.ensure_safe_path(folder, ROOT / "tmp")
        if not check:
            folder.mkdir(parents=True, exist_ok=True)
            config = (REFERENCE / "on/sdkconfig").read_text(encoding="utf-8")
            overlay = f"CONFIG_{FLAG}=y\n" if enabled else f"# CONFIG_{FLAG} is not set\n"
            (folder / "sdkconfig").write_text(stable.release.apply_overlay(config, overlay), encoding="utf-8")
        command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(folder),
            "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
            "-D", "CMAKE_CXX_FLAGS=-fstack-usage", "-D", f"SDKCONFIG={folder / 'sdkconfig'}",
            "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
        for action in ("reconfigure", "build"):
            log = WORK / f"{label}-{action}.log"
            if not check:
                if log.exists():
                    number = 1
                    while log.with_suffix(f".attempt{number}.log").exists():
                        number += 1
                    log.rename(log.with_suffix(f".attempt{number}.log"))
                print(f"E3f-9c {label}: {action}", flush=True)
                if action == "reconfigure":
                    runtime.run(command + [action], project, env, log)
                else:
                    if runtime.HELPER.HELPER.quote_image_effects_link_path(folder, project) != 1:
                        raise ValueError("image-effects link repair differs")
                    ninja = shutil.which("ninja.exe", path=env["PATH"])
                    if not ninja:
                        raise FileNotFoundError("ESP-IDF Ninja required")
                    runtime.run([ninja, "-j", "8", "-C", str(folder)], project, env, log)
            stable.validate_log(log, action)
            steps.append(dict(configuration=label, action=action, log=log.relative_to(ROOT).as_posix(),
                              sha256=H.file_record(log)["sha256"]))
        builds.append(inspect(folder, enabled, env))
    comparison = stable.compare_disabled((REFERENCE / "on/xiaozhi.bin").read_bytes(), (WORK / "off/xiaozhi.bin").read_bytes())
    H.verify_frozen_sources(project, manifest)
    locked = H.load_locked_dependencies(project)
    if (components != stable.BASE.validate_reviewed_components(project, locked)
            or inventory != H.verify_project_file_inventory(project, manifest, locked)
            or source_inputs()[1] != manifest or H.file_record(REPORT_PATH)["sha256"] != baseline_sha):
        raise ValueError("build inputs changed during verification")
    result = dict(schema="deskbot.studio_role_player_e3f9c_build/v1", baseline="E3f-9a", edition="E3f-9c",
        result="off_on_builds_verified", source_sha256=manifest, source_delta=sorted(DELTA),
        components=components, input_inventory=inventory, baseline_report_sha256=baseline_sha,
        builds=builds, steps=steps, disabled_comparison=comparison, new_tasks=0,
        usb_reads_per_iteration=1, usb_yield_ms=10, usb_stack_bytes=6144, usb_priority=1,
        build_jobs=8, device_accessed=False, device_written=False, hardware_acceptance="pending")
    if check:
        if result != saved:
            raise ValueError("saved report differs")
    else:
        PUBLIC.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".json.new")
        with temporary.open("xb") as stream:
            stream.write(stable.json_bytes(result)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, output)
    print("E3f-9c role player build " + ("checked" if check else "complete"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    build(args.resume, args.check)
