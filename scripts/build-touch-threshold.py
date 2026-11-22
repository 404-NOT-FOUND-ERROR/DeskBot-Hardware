"""Incrementally build the measured 1% threshold comparison."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as app
from touch_feedback_diagnostic import BOARD, patch_touch

spec = importlib.util.spec_from_file_location(
    "threshold_build_parent", ROOT / "scripts/build-touch-feedback-diagnostic.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
H, runtime = parent.H, parent.runtime
BASE = ROOT / "tmp/display-clock-e3f9d3"
CACHE = ROOT / "tmp/touch-e3f9d4"
WORK = ROOT / "tmp/touch-threshold-e3f9d4b"
EDITION = "E3f-9d.4b-touch"


def threshold_source():
    raw = patch_touch((BASE / "p" / BOARD).read_bytes())
    old = b".channel_threshold = 0.05, // Touch threshold (adjust as needed)"
    new = (b".channel_threshold = 0.01, // Measured contact 2.2-3.1%; quiet variation under 0.3%.")
    if raw.count(old) != 1:
        raise ValueError("reviewed threshold changed")
    return raw.replace(old, new).replace(b"threshold_percent=5", b"threshold_percent=1")


def build():
    report = json.loads((CACHE / "firmware-build-report.json").read_bytes())
    project, folder = CACHE / "p", CACHE / "on"
    original = patch_touch((BASE / "p" / BOARD).read_bytes())
    if (project / BOARD).read_bytes() != original:
        raise ValueError("diagnostic workspace changed")
    WORK.mkdir()
    frozen = CACHE / "frozen"
    frozen.mkdir()
    for name, record in report["artifacts"].items():
        if H.file_record(folder / name) != record:
            raise ValueError("diagnostic output changed: " + name)
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / name, target)
    (frozen / "touch_sensor.cc").write_bytes(original)
    shutil.copy2(CACHE / "firmware-build-report.json", frozen / "firmware-build-report.json")
    for name in ("scripts/touch_feedback_diagnostic.py", "scripts/build-touch-feedback-diagnostic.py"):
        shutil.copy2(ROOT / name, frozen / Path(name).name)
    updated = threshold_source()
    (project / BOARD).write_bytes(updated)
    env, python, idf = runtime.environment()
    env.update(TMP=str(CACHE / "scratch"), TEMP=str(CACHE / "scratch"))
    ninja = shutil.which("ninja.exe", path=env["PATH"])
    print("Compiling only the measured touch threshold change.", flush=True)
    runtime.run([ninja, "-j", "8", "-C", str(folder)], project, env, WORK / "build.log")
    parent.parent.stable.validate_log(WORK / "build.log", "build")
    baseline = json.loads((ROOT / "artifacts/display-clock-e3f9d3/firmware-build-report.json").read_bytes())
    manifest = dict(baseline["source_sha256"])
    manifest[BOARD] = H.sha256(updated)
    H.verify_frozen_sources(project, manifest)
    locked = H.load_locked_dependencies(project)
    parent.parent.validate_components(project, locked)
    H.verify_project_file_inventory(project, manifest, locked)
    artifacts = {}
    for name in H.ARTIFACTS:
        target = WORK / "on" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / name, target)
        artifacts[name] = H.file_record(target)
    protected = {name: artifacts[name] == baseline["artifacts"][name] for name in H.PROTECTED}
    if not all(protected.values()):
        raise ValueError("protected output changed")
    if json.loads((WORK / "on/config/sdkconfig.json").read_bytes()) != json.loads(
            (BASE / "on/config/sdkconfig.json").read_bytes()):
        raise ValueError("firmware configuration changed")
    elf = H.validate_app_elf((WORK / "on/xiaozhi.bin").read_bytes(), (WORK / "on/xiaozhi.elf").read_bytes())
    size = shutil.which("xtensa-esp32s3-elf-size.exe", path=env["PATH"])
    internal = H.static_data_bytes(WORK / "on/xiaozhi.elf", size, env)
    result = dict(edition=EDITION, result="build_verified", baseline="E3f-9d.4-touch",
                  source_delta=[BOARD], touch_source_sha256=H.sha256(updated),
                  effective_config_equal=True, protected_equal=protected, artifacts=artifacts,
                  app_elf_sha256=elf, internal_static_delta_bytes=internal-baseline["internal_static_data_bytes"],
                  threshold_percent=1, gesture="original")
    (WORK / "touch_sensor.cc").write_bytes(updated)
    with (WORK / "firmware-build-report.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    build()
