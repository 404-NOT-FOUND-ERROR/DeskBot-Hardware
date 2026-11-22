#!/usr/bin/env python3
"""Build and audit the default-off E3f-9d role player on frozen E3f-9c."""
from __future__ import annotations

import argparse
import hashlib
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
SOURCE = ROOT / "tmp/studio-role-player-e3f9c/p"
REFERENCE = ROOT / "tmp/studio-role-player-e3f9c"
REPORT_PATH = ROOT / "artifacts/studio-role-player-e3f9c/firmware-build-report.json"
WORK, PUBLIC = ROOT / "tmp/studio-role-activation-e3f9d", ROOT / "artifacts/studio-role-activation-e3f9d"
LOG_ROOT = WORK
FLAG = "DESKBOT_ROLE_ACTIVATION_ENABLE"
EDITION = "E3f-9d"
NEW = {"main/boards/esp_vocat/" + name for name in (
    "studio_role_status.h", "studio_role_activation.h", "studio_role_activation.cc")}
DELTA = {"main/Kconfig.projbuild", "main/assets.cc", "main/display/emote_display.cc",
    *("main/boards/esp_vocat/" + name for name in ("studio_usb_session.h", "studio_usb_diagnostic.cc",
    "studio_role_graph.h", "studio_role_graph.cc", "studio_role_player.h", "studio_role_player.cc")), *NEW}

GFX = "managed_components/espressif2022__esp_emote_gfx/src/widget/gfx_anim.c"

def gfx_patch(raw):
    text = raw.decode("utf-8")
    before = "        gfx_anim_prepare_frame(obj);\n        anim->current_frame++;"
    original_line = text[:text.index(before)].count("\n") + 1
    include_line = text[:text.index('#include <string.h>')].count("\n") + 1
    after = """#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
        if (gfx_anim_prepare_frame(obj) != ESP_OK) {
            anim->is_playing = false;
            if (ctx->callbacks.update_cb)
                ctx->callbacks.update_cb(ctx, GFX_PLAYER_EVENT_ALL_FRAME_DONE, obj);
            return;
        }
#else
        gfx_anim_prepare_frame(obj);
#endif
        anim->current_frame++;"""
    if text.count(before) != 1: raise ValueError("reviewed GFX timer anchor changed")
    # ESP error macros embed __LINE__ in executable bytes. Preserve the original
    # logical line numbers around the added include and conditional timer branch.
    after = after.replace('        anim->current_frame++;', f'#line {original_line + 1}\n        anim->current_frame++;')
    return text.replace('#include <string.h>', f'#include "sdkconfig.h"\n#line {include_line}\n#include <string.h>', 1).replace(before, after, 1).encode("utf-8")

DELTA.add(GFX)



def validate_components(project: Path, locked: dict) -> dict:
    """Verify registry contents with the exact accepted ST77916 replacement.

    The lock still names the upstream component. Its aggregate is reconstructed
    from the original driver; the actual patched file is checked separately.
    Neither the lock nor the registry inventory is rewritten.
    """
    project = H.ensure_safe_path(project, ROOT / "tmp")
    checked_parents = {project}

    def read_component(relative: str) -> bytes:
        # Inventory paths are lexical children. Check every parent once per
        # validation, so large component trees do not repeat NTFS resolution.
        relative = H._safe_component_relative(relative)
        path = project / relative
        parent = path.parent
        while parent not in checked_parents:
            if H._is_reparse_point(parent):
                raise ValueError("component directory is redirected: " + relative)
            checked_parents.add(parent)
            parent = parent.parent
        if H._is_reparse_point(path):
            raise ValueError("component file is redirected: " + relative)
        return path.read_bytes()

    original = H.safe_read(stable.BASE.e3c3b_display_dma.FROZEN, stable.BASE.e3c3b_display_dma.DRIVER)
    patched = stable.BASE.e3c3b_display_dma.patch_driver(original)
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
        names = [H._safe_component_relative(row["path"]) for row in entries]
        if len(names) != len(set(names)):
            raise ValueError("duplicate component inventory entry: " + name)
        aggregate = hashlib.sha256()
        for entry in sorted(entries, key=lambda row: row["path"].replace("\\", "/")):
            relative = H._safe_component_relative(entry["path"])
            repo_name = (component / relative).relative_to(project).as_posix()
            raw = read_component(repo_name)
            if repo_name == GFX:
                upstream_gfx = (SOURCE / GFX).read_bytes()
                if raw != gfx_patch(upstream_gfx): raise ValueError("reviewed GFX preparation branch differs")
                raw = upstream_gfx
            if repo_name == stable.BASE.e3c3b_display_dma.DRIVER:
                if raw != patched:
                    raise ValueError("reviewed ST77916 replacement differs")
                # The registry's aggregate describes the original component.
                raw = original
                override_seen = True
            if type(entry.get("size")) is not int or len(raw) != entry["size"]:
                raise ValueError("component file size differs: " + repo_name)
            aggregate.update(relative.encode("utf-8"))
            aggregate.update(H.sha256(raw).encode("ascii"))
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
            GFX: {"upstream_sha256": H.sha256((SOURCE / GFX).read_bytes()),
                  "accepted_sha256": H.sha256(gfx_patch((SOURCE / GFX).read_bytes()))},
            stable.BASE.e3c3b_display_dma.DRIVER: {"upstream_sha256": H.sha256(original),
                                      "accepted_sha256": H.sha256(patched)},
        },
    }

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
    if report.get("edition") != "E3f-9c" or report.get("result") != "off_on_builds_verified":
        raise ValueError("verified E3f-9c report required")
    H.verify_frozen_sources(SOURCE, report["source_sha256"])
    manifest, patches = dict(report["source_sha256"]), {}
    for name in sorted(DELTA):
        raw = gfx_patch((SOURCE / GFX).read_bytes()) if name == GFX else (ROOT / "firmware/body" / name).read_bytes()
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
    components = validate_components(project, locked)
    inventory = H.verify_project_file_inventory(project, manifest, locked)
    return report, manifest, project, components, inventory


def printf_portability(project, config_text):
    spec = importlib.util.spec_from_file_location("role_printf_guard", ROOT / "tools/audit-printf-portability.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    before, after = [], []
    for name in sorted(DELTA):
        if Path(name).suffix.lower() not in guard.SOURCE_SUFFIXES:
            continue
        old, current = SOURCE / name, project / name
        if old.is_file():
            before.extend(guard.detect(old.read_text(encoding="utf-8"), name))
        if current.is_file():
            after.extend(guard.detect(current.read_text(encoding="utf-8"), name))
    result = guard.classify(before, after, guard.nano_configuration(config_text)["nano_enabled"])
    if result["status"] != "pass":
        raise ValueError("nano printf audit failed: " + repr(result["new_blocking"]))
    return result


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
    if not base_internal <= internal <= base_internal + 128:
        raise ValueError("role internal static data budget exceeded")
    symbols = subprocess.check_output([nm, "-S", "-C", str(folder / "xiaozhi.elf")], env=env, text=True)
    for marker in ("deskbot_studio_runtime::ReadDisplay()", "deskbot_studio_expression::AdapterHooks()",
                   "deskbot_studio_catalog::BuildFromMounted("):
        if marker not in symbols:
            raise ValueError("missing inherited feature: " + marker)
    for marker in ("deskbot_role_activation::Observe", "deskbot_role_activation::Mounted", "deskbot_role_activation::AdapterHooks"):
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
    printf = printf_portability(WORK / "p", (folder / "sdkconfig").read_text(encoding="utf-8"))
    headroom = H.APP_PARTITION_BYTES - artifacts["xiaozhi.bin"]["bytes"]
    if headroom < 1024 * 1024:
        raise ValueError("App headroom below 1 MiB")
    return dict(enabled=enabled, artifacts=artifacts, app_elf_sha256=elf, config_delta=sorted(delta),
                protected_equal=protected, internal_static_data_bytes=internal,
                internal_static_delta_bytes=internal-base_internal, app_headroom_bytes=headroom,
                compiler_stack_usage=stacks, printf_portability=printf)


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
        printf_portability(project, (folder / "sdkconfig").read_text(encoding="utf-8"))
        command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(folder),
            "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
            "-D", "CMAKE_CXX_FLAGS=-fstack-usage", "-D", f"SDKCONFIG={folder / 'sdkconfig'}",
            "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
        for action in ("reconfigure", "build"):
            log = LOG_ROOT / f"{label}-{action}.log"
            if not check:
                if log.exists():
                    number = 1
                    while log.with_suffix(f".attempt{number}.log").exists():
                        number += 1
                    log.rename(log.with_suffix(f".attempt{number}.log"))
                print(f"{EDITION} {label}: {action}", flush=True)
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
    if (components != validate_components(project, locked)
            or inventory != H.verify_project_file_inventory(project, manifest, locked)
            or source_inputs()[1] != manifest or H.file_record(REPORT_PATH)["sha256"] != baseline_sha):
        raise ValueError("build inputs changed during verification")
    result = dict(schema="deskbot.studio_role_activation_e3f9d_build/v1", baseline="E3f-9c", edition=EDITION,
        result="off_on_builds_verified", source_sha256=manifest, source_delta=sorted(DELTA),
        components=components, input_inventory=inventory, baseline_report_sha256=baseline_sha,
        builds=builds, steps=steps, disabled_comparison=comparison, new_tasks=0,
        usb_reads_per_iteration=512, usb_byte_budget=512, usb_yield_ms=10, usb_stack_bytes=6144, usb_priority=1,
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
    print(EDITION + " role player build " + ("checked" if check else "complete"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    build(args.resume, args.check)
