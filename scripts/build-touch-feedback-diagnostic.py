"""Build the E3f-9d.3 touch probe from reviewed, frozen inputs."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from touch_feedback_diagnostic import BOARD, BASE_REPORT_SHA, EDITION, patch_touch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "touch_probe_build_parent", ROOT / "scripts/build-studio-role-activation-e3f9d.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
H, runtime = parent.H, parent.runtime
BASE = ROOT / "tmp/display-clock-e3f9d3"
WORK = ROOT / "tmp/touch-e3f9d4"
REPORT = WORK / "firmware-build-report.json"


def build():
    raw = (ROOT / "artifacts/display-clock-e3f9d3/firmware-build-report.json").read_bytes()
    if H.sha256(raw) != BASE_REPORT_SHA:
        raise ValueError("retained build report changed")
    baseline = json.loads(raw)
    source, reference = BASE / "p", BASE / "on"
    manifest = dict(baseline["source_sha256"])
    H.verify_frozen_sources(source, manifest)
    locked = H.load_locked_dependencies(source)
    parent.validate_components(source, locked)
    H.verify_project_file_inventory(source, manifest, locked)
    for name in H.ARTIFACTS:
        if H.file_record(reference / name) != baseline["artifacts"][name]:
            raise ValueError(f"retained binary changed: {name}")
    patched = patch_touch((source / BOARD).read_bytes())
    manifest[BOARD] = H.sha256(patched)

    project, folder = WORK / "p", WORK / "on"
    WORK.mkdir()
    shutil.copytree(source, project)
    H.rebind_dependency_lock(project, source)
    (project / BOARD).write_bytes(patched)
    locked = H.load_locked_dependencies(project)
    components = parent.validate_components(project, locked)
    inventory = H.verify_project_file_inventory(project, manifest, locked)
    H.verify_frozen_sources(project, manifest)
    env, python, idf = runtime.environment()
    # Keep scratch files on the same volume as the build.
    scratch = WORK / "scratch"
    scratch.mkdir()
    env.update(TMP=str(scratch), TEMP=str(scratch))
    folder.mkdir()
    shutil.copyfile(reference / "sdkconfig", folder / "sdkconfig")
    command = [str(python), str(idf / "tools/idf.py"), "--no-ccache", "-B", str(folder),
               "-D", "IDF_TARGET=esp32s3", "-D", "BOARD_NAME=esp_vocat", "-D", "BOARD_TYPE=esp_vocat",
               "-D", "CMAKE_CXX_FLAGS=-fstack-usage", "-D", f'SDKCONFIG={folder / "sdkconfig"}',
               "-D", f'SDKCONFIG_DEFAULTS={project / "sdkconfig.defaults.o2f2"}']
    print(f"{EDITION}: reconfigure", flush=True)
    runtime.run(command + ["reconfigure"], project, env, WORK / "reconfigure.log")
    if runtime.HELPER.HELPER.quote_image_effects_link_path(folder, project) != 1:
        raise ValueError("image-effects link repair changed")
    ninja = shutil.which("ninja.exe", path=env["PATH"])
    if not ninja:
        raise FileNotFoundError("pinned Ninja required")
    print(f"{EDITION}: build", flush=True)
    runtime.run([ninja, "-j", "8", "-C", str(folder)], project, env, WORK / "build.log")
    for action in ("reconfigure", "build"):
        parent.stable.validate_log(WORK / f"{action}.log", action)
    effective = (folder / "config/sdkconfig.json").read_bytes()
    if json.loads(effective) != json.loads((reference / "config/sdkconfig.json").read_bytes()):
        raise ValueError("effective firmware configuration changed")
    parent.stable.release.effective_octal_config(json.loads(effective), 32)
    artifacts = {name: H.file_record(folder / name) for name in H.ARTIFACTS}
    protected = {name: artifacts[name] == baseline["artifacts"][name] for name in H.PROTECTED}
    if not all(protected.values()):
        raise ValueError("protected outputs changed")
    elf = H.validate_app_elf((folder / "xiaozhi.bin").read_bytes(), (folder / "xiaozhi.elf").read_bytes())
    size = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    nm = shutil.which("xtensa-esp32s3-elf-nm.exe", path=env["PATH"])
    if not size or not nm:
        raise FileNotFoundError("pinned ELF tools required")
    internal = H.static_data_bytes(folder / "xiaozhi.elf", size, env)
    delta = internal - baseline["internal_static_data_bytes"]
    symbols = subprocess.check_output([nm, "-C", str(folder / "xiaozhi.elf")], env=env, text=True)
    if not all(symbol in symbols for symbol in ("touch_probe_task", "touch_probe_event",
                                               "Application::WakeWordInvoke",
                                               "deskbot_role_activation::Observe")):
        raise ValueError("touch probe or inherited features missing")
    H.verify_frozen_sources(project, manifest)
    if (parent.validate_components(project, locked) != components
            or H.verify_project_file_inventory(project, manifest, locked) != inventory):
        raise ValueError("build inputs changed")
    result = dict(edition=EDITION, result="build_verified", baseline="E3f-9d.3",
                  baseline_report_sha256=BASE_REPORT_SHA, source_delta=[BOARD],
                  touch_source_sha256=manifest[BOARD], effective_config_equal=True,
                  protected_equal=protected, artifacts=artifacts, app_elf_sha256=elf,
                  internal_static_delta_bytes=delta, probe_task_stack_bytes=3072)
    with REPORT.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    build()
