#!/usr/bin/env python3
"""Build E1.26 in an isolated, locked-dependency body copy; never access a device."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys

import yaml

ROOT = Path(__file__).absolute().parents[1]
SOURCE = ROOT / "firmware/body"
DEFAULT_REPORT = "tmp/e1-26-full-body-report.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory() -> dict[str, str]:
    result = {}
    for row in (SOURCE / "SOURCE_MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = row.split("  ", 1)
        if sha(SOURCE / name) != digest:
            raise ValueError("stale source manifest: " + name)
        result[name] = digest
    return result


def validate_components(project: Path, locked: dict) -> dict:
    """Check actual registry file contents, not just the .component_hash marker."""
    records = []
    for name, item in locked["dependencies"].items():
        expected = item.get("component_hash")
        if expected is None:
            continue
        component = project / "managed_components" / name.replace("/", "__")
        checksums = component / "CHECKSUMS.json"
        if (component / ".component_hash").read_text().strip().lower() != expected:
            raise ValueError("component marker differs from lock: " + name)
        content = json.loads(checksums.read_text(encoding="utf-8"))
        if content.get("algorithm") != "sha256" or content.get("version") != "1.0":
            raise ValueError("unsupported component inventory: " + name)
        entries = content["files"]
        names = [entry["path"].replace("\\", "/") for entry in entries]
        if not names or len(names) != len(set(names)):
            raise ValueError("duplicate/empty component inventory: " + name)
        aggregate = hashlib.sha256()
        for entry in sorted(entries, key=lambda row: row["path"].replace("\\", "/")):
            relative = entry["path"].replace("\\", "/")
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or ":" in relative:
                raise ValueError("unsafe component inventory path")
            file = component / relative
            digest = sha(file)
            # The inventory aggregate is anchored in dependencies.lock.
            if file.stat().st_size != entry["size"]:
                raise ValueError("component file size mismatch: " + str(file))
            aggregate.update(relative.encode("utf-8"))
            aggregate.update(digest.encode("ascii"))
        if aggregate.hexdigest() != expected:
            raise ValueError("actual registry contents differ from lock: " + name)
        records.append({"name": name, "version": item["version"], "component_hash": expected,
                        "files": len(entries)})
    return {"count": len(records), "actual_contents_match_lock": True, "components": records}


def normalized_lock(value: dict) -> dict:
    value = json.loads(json.dumps(value))
    value["dependencies"]["espressif/touch_sensor_fsm"]["source"]["path"] = "<local-touch>"
    return value


def config_map(text: str) -> dict[str, str]:
    result = {}
    for row in text.splitlines():
        match = re.fullmatch(r"(CONFIG_\w+)=(.*)", row)
        unset = re.fullmatch(r"# (CONFIG_\w+) is not set", row)
        if match:
            result[match[1]] = match[2]
        elif unset:
            result[unset[1]] = "n"
    return result


def run(command: list[str], cwd: Path, env: dict, log: Path) -> dict:
    print("Running " + log.name, flush=True)
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, text=True, check=False)
    if completed.returncode:
        print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-22:]), flush=True)
        raise ValueError(f"command failed ({completed.returncode}); log={log}")
    return {"returncode": completed.returncode, "log": str(log.resolve()), "log_sha256": sha(log)}


def quote_image_effects_link_path(build: Path, project: Path) -> int:
    # Same generated-file-only workaround as scripts/build-vocat-xiaozhi.ps1.
    ninja = build / "build.ninja"
    text = ninja.read_text(encoding="utf-8")
    directory = project / "managed_components/espressif__esp_image_effects/lib/esp32s3"
    # CMake may canonicalize a subst drive into the original path containing
    # spaces. Quote either spelling, only in the generated Ninja file.
    count = 0
    for spelling in {directory.as_posix(), directory.resolve().as_posix()}:
        before = "-L " + spelling
        count += text.count(before)
        text = text.replace(before, '-L"' + spelling + '"')
    if count:
        ninja.write_text(text, encoding="utf-8")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default="tmp/e126-body")
    parser.add_argument("--output", default=DEFAULT_REPORT)
    args = parser.parse_args()
    output_arg = Path(args.output)
    output = output_arg if output_arg.is_absolute() else ROOT / output_arg
    work_arg = Path(args.work_dir)
    work = work_arg if work_arg.is_absolute() else ROOT / work_arg
    # All generated files stay under this repository's tmp/. Retain the subst
    # spelling for IDF/CMake arguments, but validate canonical containment.
    work.resolve().relative_to((ROOT / "tmp").resolve())
    output_relative = output.resolve().relative_to(ROOT.resolve())
    if output_relative.parts[0] not in {"tmp", "artifacts"} or output.suffix != ".json":
        raise ValueError("output must be a JSON file inside artifacts/ or tmp/")
    inputs = inventory()
    project = work / "p"
    work.mkdir(parents=True, exist_ok=True)
    project.mkdir(exist_ok=True)
    for name in inputs:
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, target)
    locked_bytes = (SOURCE / "dependencies.lock").read_bytes()
    locked = yaml.safe_load(locked_bytes)
    # A published relative local path must become the copied component's path.
    # Replace that one field before the manager runs, so it need not resolve
    # unrelated registry versions after a local-source validity failure.
    touch = (project / "components/espressif__touch_sensor_fsm").as_posix()
    lock_text = locked_bytes.decode("utf-8")
    local = locked["dependencies"]["espressif/touch_sensor_fsm"]["source"]["path"]
    lock_text, count = re.subn(r"(?m)^      path: " + re.escape(local) + r"(?=\r?$)",
                               "      path: " + touch, lock_text)
    if count != 1:
        raise ValueError("expected exactly one local touch path in lock")
    (project / "dependencies.lock").write_bytes(lock_text.encode("utf-8"))
    managed = project / "managed_components"
    # Path.is_junction() is unavailable on IDF Python 3.11.  Reject Windows
    # reparse points via lstat as well as symlinks before copying dependencies.
    reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    reparse = (managed.exists() and
               getattr(managed.lstat(), "st_file_attributes", 0) & reparse_attr)
    if managed.is_symlink() or reparse:
        raise ValueError("managed_components must be a private directory, not a link")
    managed.mkdir(exist_ok=True)
    for name, item in locked["dependencies"].items():
        if "component_hash" not in item:
            continue
        relative = name.replace("/", "__")
        source = SOURCE / "managed_components" / relative
        destination = managed / relative
        marker = destination / ".component_hash"
        if destination.exists() and (not marker.is_file() or marker.read_text().strip() != item["component_hash"]):
            rejected = work / "rejected-dependencies" / relative
            rejected.parent.mkdir(exist_ok=True)
            if rejected.exists():
                raise ValueError("refusing to overwrite rejected component: " + relative)
            destination.rename(rejected)
        if not destination.exists():
            shutil.copytree(source, destination)
    components_before = validate_components(project, locked)
    idf = Path(os.environ.get("IDF_PATH", r"D:\Espressif\frameworks\esp-idf-v5.5.5"))
    python = Path(os.environ.get("IDF_PYTHON_ENV_PATH", r"D:\Espressif\python_env\idf5.5_py3.11_env")) / "Scripts/python.exe"
    tool_bins = [r"D:\Espressif\tools\cmake\3.30.2\bin", r"D:\Espressif\tools\ninja\1.12.1",
                 r"D:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin"]
    env = os.environ.copy()
    env.update({"IDF_PATH": str(idf), "IDF_TOOLS_PATH": r"D:\Espressif",
                "IDF_PYTHON_ENV_PATH": str(python.parent.parent), "ESP_IDF_VERSION": "5.5.5",
                "IDF_TARGET": "esp32s3", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "IDF_COMPONENT_CHECK_NEW_VERSION": "0", "CMAKE_NINJA_FORCE_RESPONSE_FILE": "1"})
    env["PATH"] = os.pathsep.join(tool_bins + [str(python.parent), env.get("PATH", "")])
    results = []
    base_config = (SOURCE / "sdkconfig.defaults.o2f2").read_text(encoding="utf-8")
    for enabled in (False, True):
        label = "on" if enabled else "off"
        build = work / ("o-" + label)
        build.mkdir(exist_ok=True)
        sdkconfig = build / "sdkconfig"
        snapshot = re.sub(r"(?m)^(?:# )?CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE.*\n?", "", base_config)
        snapshot = re.sub(r"(?m)^(?:# )?CONFIG_APP_REPRODUCIBLE_BUILD.*\n?", "", snapshot)
        sdkconfig.write_text(snapshot + "\nCONFIG_APP_REPRODUCIBLE_BUILD=y\n" +
                             ("CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE=y\n" if enabled
                              else "# CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE is not set\n"), encoding="utf-8")
        common = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(build),
                  "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
                  "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={project / 'sdkconfig.defaults.o2f2'}"]
        steps = [run(common + ["reconfigure"], project, env, work / f"locked-{label}-configure.log")]
        effective_lock = yaml.safe_load((project / "dependencies.lock").read_bytes())
        if normalized_lock(effective_lock) != normalized_lock(locked):
            raise ValueError("manager changed pinned dependencies; do not build")
        patches = quote_image_effects_link_path(build, project)
        steps.append(run(common + ["build"], project, env, work / f"locked-{label}-build.log"))
        if normalized_lock(yaml.safe_load((project / "dependencies.lock").read_bytes())) != normalized_lock(locked):
            raise ValueError("lock changed during build")
        config = json.loads((build / "config/sdkconfig.json").read_text(encoding="utf-8"))
        if config.get("DESKBOT_EXPRESSION_OBSERVER_ENABLE") is not enabled:
            raise ValueError("wrong observer Kconfig state")
        if config.get("APP_REPRODUCIBLE_BUILD") is not True:
            raise ValueError("full-body comparison must use deterministic build metadata")
        commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
        members = [c["file"] for c in commands if Path(c["file"]).name == "expression_runtime_observer.cc"]
        if bool(members) is not enabled:
            raise ValueError("observer source membership does not match configuration")
        nm = str(Path(tool_bins[-1]) / "xtensa-esp32s3-elf-nm.exe")
        symbols = subprocess.run([nm, "-C", str(build / "xiaozhi.elf")], capture_output=True,
                                 text=True, check=True).stdout
        observer_symbols = [row for row in symbols.splitlines() if "ExpressionRuntimeObserver" in row]
        if bool(observer_symbols) is not enabled:
            raise ValueError("observer ELF symbols do not match configuration")
        files = ("xiaozhi.bin", "xiaozhi.elf", "partition_table/partition-table.bin",
                 "bootloader/bootloader.bin", "expression_assets.bin", "srmodels/srmodels.bin")
        artifacts = {name: {"bytes": (build / name).stat().st_size, "sha256": sha(build / name)} for name in files}
        app_partition_bytes = 6000 * 1024  # v2/16m.csv ota_0: 6000 KiB
        if artifacts["xiaozhi.bin"]["bytes"] >= app_partition_bytes:
            raise ValueError("App partition has no remaining capacity")
        results.append({"enabled": enabled, "build_directory": str(build.resolve()), "build_pass": True,
                        "steps": steps, "config_value": config["DESKBOT_EXPRESSION_OBSERVER_ENABLE"],
                        "reproducible_build": config["APP_REPRODUCIBLE_BUILD"],
                        "observer_source_member": bool(members), "observer_elf_symbol_count": len(observer_symbols),
                        "sdkconfig_sha256": sha(sdkconfig), "generated_link_path_quoting": patches,
                        "artifacts": artifacts, "app_partition_bytes": app_partition_bytes,
                        "app_partition_free_bytes": app_partition_bytes - artifacts["xiaozhi.bin"]["bytes"]})
        print(f"locked {label} build passed: {artifacts['xiaozhi.bin']['bytes']} App bytes", flush=True)
    unchanged = ("partition_table/partition-table.bin", "bootloader/bootloader.bin", "expression_assets.bin", "srmodels/srmodels.bin")
    for name in unchanged:
        if results[0]["artifacts"][name] != results[1]["artifacts"][name]:
            raise ValueError("non-App output differs between observer modes: " + name)
    if inventory() != inputs:
        raise ValueError("formal source changed while building")
    copied = {name: sha(project / name) for name in inputs if name != "dependencies.lock"}
    if copied != {name: digest for name, digest in inputs.items() if name != "dependencies.lock"}:
        raise ValueError("isolated source differs from the audited source inventory")
    report = {"schema": "deskbot.expression.e1_26_full_body_build/v1", "edition": "E1.26",
              "result": "locked_full_body_builds_passed", "audit_pass": True,
              "source_manifest_sha256": sha(SOURCE / "SOURCE_MANIFEST.sha256"), "source_sha256": inputs,
              "source_lock_sha256": hashlib.sha256(locked_bytes).hexdigest(),
              "effective_lock_sha256": sha(project / "dependencies.lock"),
              "lock_difference": "local touch path only", "components": validate_components(project, locked),
              "configurations": results, "unchanged_outputs": list(unchanged),
              "full_firmware_build_passed": True, "deployment_eligible": False, "flash_authorized": False,
              "safety": {"hardware_accessed": False, "serial_accessed": False, "device_written": False,
                          "nvs_changed": False, "flash_executed": False},
              "limitations": ["No physical regression, SMP timing or LCD presentation has been verified.",
                              "Generated assets are build references, not the accepted E1.18 deployment image."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("locked_full_body_builds_passed: " + output_relative.as_posix(), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("E1.26 full body build blocked: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
