"""Build an App-only Wi-Fi touch candidate from the accepted threshold image."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "threshold_build", ROOT / "scripts/build-touch-threshold.py")
threshold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(threshold)
H, runtime = threshold.H, threshold.runtime
BOARD = Path("main/boards/esp_vocat")
EDITION = "E3f-9d.5-touch-wifi"
BASE = ROOT / "tmp/touch-threshold-e3f9d4b"
PROJECT, CACHE = ROOT / "tmp/touch-e3f9d4/p", ROOT / "tmp/touch-e3f9d4/on"
RETAINED = ROOT / "artifacts/display-clock-e3f9d3/firmware-build-report.json"


def inject(raw, before, after):
    if raw.count(before) != 1:
        raise ValueError("reviewed source anchor changed")
    return raw.replace(before, after)


def build(profile):
    configuration = profile / "receiver.json"
    settings = json.loads(configuration.read_bytes())
    if settings["schema"] != "deskbot.head-touch-wifi-config.v1" or settings["firmware_sha256"] is not None:
        raise ValueError("requires a fresh trial profile")
    work = profile / "build"
    work.mkdir()
    manifest = dict(json.loads(RETAINED.read_bytes())["source_sha256"])
    accepted = json.loads((BASE / "firmware-build-report.json").read_bytes())
    touch_path = BOARD / "touch_sensor.cc"
    link_path = BOARD / "studio_device_link.cc"
    manifest[touch_path.as_posix()] = accepted["touch_source_sha256"]
    originals = {path: (PROJECT / path).read_bytes() for path in (touch_path, link_path)}
    for name in H.ARTIFACTS:
        if H.file_record(BASE / "on" / name) != accepted["artifacts"][name]:
            raise ValueError("retained artifact changed: " + name)
    H.verify_frozen_sources(PROJECT, manifest)
    locked = H.load_locked_dependencies(PROJECT)
    threshold.parent.parent.validate_components(PROJECT, locked)
    H.verify_project_file_inventory(PROJECT, manifest, locked)
    env, _, _ = runtime.environment()
    scratch = work / "scratch"
    scratch.mkdir()
    env.update(TMP=str(scratch), TEMP=str(scratch))
    tools = {name: shutil.which(name + ".exe", path=env["PATH"])
             for name in ("ninja", "xtensa-esp32s3-elf-size", "xtensa-esp32s3-elf-nm")}
    if not all(tools.values()):
        raise ValueError("use the pinned ESP-IDF runtime")

    touch = inject(originals[touch_path], b'#include "touch_sensor.h"',
                   b'#include "touch_sensor.h"\n#include "head_touch_wifi.h"')
    touch = inject(touch, b'            ESP_LOGI(TAG, "Triple PRESS_DOWN detected!");',
                   b'            ESP_LOGI(TAG, "Triple PRESS_DOWN detected!");\n'
                   b'            deskbot_head_touch_wifi::Observe(static_cast<uint64_t>(now_ms));')
    link = inject(originals[link_path], b'#include "studio_device_link.h"',
                  b'#include "studio_device_link.h"\n#include "head_touch_wifi.h"\n#include "head_touch_wifi.inc"')
    link = inject(link, b'    ToHex(app->app_elf_sha256, 32, app_hash);',
                  b'    ToHex(app->app_elf_sha256, 32, app_hash);\n'
                  b'    deskbot_head_touch_wifi::Start(device_hex, boot_hex, app_hash);')
    patches = {touch_path: touch, link_path: link}
    for name in ("head_touch_wifi.h", "head_touch_wifi.inc"):
        patches[BOARD / name] = (ROOT / "firmware/body" / BOARD / name).read_bytes()
    local_header = BOARD / "head_touch_wifi.local.h"
    patches[local_header] = (profile / local_header.name).read_bytes()
    base_manifest = dict(manifest)
    staged = []
    for path in patches:
        if path not in originals and (PROJECT / path).exists():
            raise ValueError("candidate source already exists: " + str(path))
    try:
        for path, data in patches.items():
            (PROJECT / path).write_bytes(data)
            staged.append(path)
            manifest[path.as_posix()] = H.sha256(data)
        H.verify_frozen_sources(PROJECT, manifest)
        H.verify_project_file_inventory(PROJECT, manifest, locked)
        runtime.run([tools["ninja"], "-j", "8", "-C", str(CACHE)], PROJECT, env, work / "build.log")
        threshold.parent.parent.stable.validate_log(work / "build.log", "build")
        H.verify_frozen_sources(PROJECT, manifest)
        artifacts = {name: H.file_record(CACHE / name) for name in H.ARTIFACTS}
        protected = {name: artifacts[name] == accepted["artifacts"][name] for name in H.PROTECTED}
        if not all(protected.values()):
            raise ValueError("protected outputs changed")
        if (json.loads((CACHE / "config/sdkconfig.json").read_bytes())
                != json.loads((BASE / "on/config/sdkconfig.json").read_bytes())):
            raise ValueError("effective configuration changed")
        size_tool, nm = tools["xtensa-esp32s3-elf-size"], tools["xtensa-esp32s3-elf-nm"]
        elf_path, app_path = CACHE / "xiaozhi.elf", CACHE / "xiaozhi.bin"
        elf = H.validate_app_elf(app_path.read_bytes(), elf_path.read_bytes())
        symbols = subprocess.check_output([nm, "-C", str(elf_path)], env=env, text=True)
        if not all(name in symbols for name in ("deskbot_head_touch_wifi::Observe", "esp_http_client_perform",
                                                "Application::WakeWordInvoke", "deskbot_role_activation::Observe")):
            raise ValueError("expected compiled features missing")
        internal = H.static_data_bytes(elf_path, size_tool, env)
        before = H.static_data_bytes(BASE / "on/xiaozhi.elf", size_tool, env)
        shutil.copy2(app_path, work / "xiaozhi.bin")
        shutil.copy2(elf_path, work / "xiaozhi.elf")
    finally:
        for path in reversed(staged):
            file = PROJECT / path
            if file.read_bytes() != patches[path]:
                raise ValueError("staged source changed during build: " + str(path))
            if path in originals:
                file.write_bytes(originals[path])
            else:
                file.unlink()
    H.verify_frozen_sources(PROJECT, base_manifest)
    settings["firmware_sha256"] = elf
    updated = configuration.with_suffix(".new")
    updated.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    updated.replace(configuration)
    report = dict(edition=EDITION, baseline=accepted["edition"], lab=settings["lab"],
                  app_elf_sha256=elf, app=artifacts["xiaozhi.bin"], protected_equal=protected,
                  effective_config_equal=True, internal_static_delta_bytes=internal-before,
                  worker_stack_bytes=6144, device_written=False,
                  rollback_app=accepted["artifacts"]["xiaozhi.bin"],
                  source_sha256={p.as_posix(): H.sha256(raw) for p, raw in patches.items() if p != local_header})
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    build(parser.parse_args().profile.resolve())
