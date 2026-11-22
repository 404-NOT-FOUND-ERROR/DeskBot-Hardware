"""Build standalone or shared-worker Wi-Fi Apps on the accepted display/touch baseline."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("wifi_touch_build", ROOT / "scripts/build-head-touch-wifi.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
H, runtime = base.H, base.runtime
BOARD = base.BOARD
EDITION = "E3f-9d.5-expression-wifi"


def build(profile, touch_profile=None, network_settings=False, role_assets=False, layered_face=False):
    if layered_face and not role_assets:
        raise ValueError("layered_face_requires_current_role_baseline")
    configuration = profile / "receiver.json"
    settings = json.loads(configuration.read_bytes())
    if settings["schema"] != "deskbot.expression-wifi-config.v1" or settings["firmware_sha256"] is not None:
        raise ValueError("requires a fresh expression profile")
    if network_settings and touch_profile is None:
        raise ValueError("network_settings_requires_shared_worker")
    touch_settings = None
    if touch_profile is not None:
        touch_settings = json.loads((touch_profile / "receiver.json").read_bytes())
        if (touch_settings["schema"] != "deskbot.head-touch-wifi-config.v1"
                or touch_settings["firmware_sha256"] is not None
                or any(settings[k] != touch_settings[k] for k in ("host", "device_id", "lab"))
                or settings["port"] == touch_settings["port"]
                or (profile / settings["token_file"]).read_bytes() == (touch_profile / touch_settings["token_file"]).read_bytes()):
            raise ValueError("requires two independent fresh profiles for the same device and network")
    work = profile / "build"
    work.mkdir(exist_ok=True)
    manifest = dict(json.loads(base.RETAINED.read_bytes())["source_sha256"])
    accepted = json.loads((base.BASE / "firmware-build-report.json").read_bytes())
    touch_path = BOARD / "touch_sensor.cc"
    manifest[touch_path.as_posix()] = accepted["touch_source_sha256"]
    H.verify_frozen_sources(base.PROJECT, manifest)
    locked = H.load_locked_dependencies(base.PROJECT)
    base.threshold.parent.parent.validate_components(base.PROJECT, locked)
    H.verify_project_file_inventory(base.PROJECT, manifest, locked)
    for name in H.ARTIFACTS:
        if H.file_record(base.BASE / "on" / name) != accepted["artifacts"][name]:
            raise ValueError("retained artifact changed: " + name)
    env, _, _ = runtime.environment()
    scratch = work / "scratch"
    scratch.mkdir(exist_ok=True)
    env.update(TMP=str(scratch), TEMP=str(scratch))
    tools = {name: shutil.which(name + ".exe", path=env["PATH"])
             for name in ("ninja", "xtensa-esp32s3-elf-size", "xtensa-esp32s3-elf-nm")}
    if not all(tools.values()):
        raise ValueError("use the pinned ESP-IDF runtime")
    names = ("studio_device_link.cc", "studio_role_activation.cc", "studio_expression_adapter.cc")
    if touch_settings is not None:
        names += ("touch_sensor.cc",)
    if network_settings:
        names += ("studio_usb_diagnostic.cc",)
    originals = {BOARD / name: (base.PROJECT / BOARD / name).read_bytes() for name in names}
    patches = {}
    for path, raw in originals.items():
        if path.name == "studio_usb_diagnostic.cc":
            newline = b'\r\n' if b'\r\n' in raw else b'\n'
            anchor = b'#if CONFIG_DESKBOT_STUDIO_CATALOG_ENABLE' + newline + b'    endpoint.SetCatalogProvider'
            patches[path] = base.inject(raw, anchor,
                b'    endpoint.SetNetworkProvider(deskbot_body_network::Handle);' + newline + anchor)
            continue
        if path.name == "touch_sensor.cc":
            raw = base.inject(raw, b'#include "touch_sensor.h"', b'#include "touch_sensor.h"\n#include "head_touch_wifi.h"')
            raw = base.inject(raw, b'            ESP_LOGI(TAG, "Triple PRESS_DOWN detected!");',
                b'            ESP_LOGI(TAG, "Triple PRESS_DOWN detected!");\n'
                b'            deskbot_head_touch_wifi::Observe(static_cast<uint64_t>(now_ms));')
            patches[path] = raw
            continue
        include = b'#include "' + path.name.replace(".cc", ".h").encode() + b'"'
        raw = base.inject(raw, include, include + b'\n#include "wifi_expression.h"')
        if path.name == "studio_device_link.cc":
            module = "wifi_expression" if touch_settings is None else "wifi_body_runtime"
            raw = base.inject(raw, b'#include "wifi_expression.h"',
                b'#include "wifi_expression.h"\n#include "' + module.encode() + b'.inc"')
            start = "deskbot_wifi_expression" if touch_settings is None else "deskbot_body_wifi"
            raw = base.inject(raw, b'    ToHex(app->app_elf_sha256, 32, app_hash);',
                b'    ToHex(app->app_elf_sha256, 32, app_hash);\n    ' + start.encode() + b'::Start(device_hex, boot_hex, app_hash);')
        elif path.name == "studio_role_activation.cc":
            raw = base.inject(raw, b'void Observe(void* handle) {\n    const auto device = deskbot_role::Describe(static_cast<emote_handle_t>(handle));',
                b'void Observe(void* handle) {\n    const auto device = deskbot_role::Describe(static_cast<emote_handle_t>(handle));\n    deskbot_wifi_expression::Observe(device);')
        else:
            raw = base.inject(raw, b'void SynchronizeSession(const SessionView& view, uint64_t) {',
                b'void SynchronizeSession(const SessionView& view, uint64_t) {\n    deskbot_wifi_expression::UsbLease(view.active, view.lease_deadline);')
            raw = base.inject(raw, b'void DisconnectSession() {',
                b'void DisconnectSession() {\n    deskbot_wifi_expression::UsbLease(false, 0);')
        patches[path] = raw
    for name in ("wifi_expression.h", "wifi_expression.inc"):
        patches[BOARD / name] = (ROOT / "firmware/body" / BOARD / name).read_bytes()
    local_header = BOARD / "wifi_expression.local.h"
    patches[local_header] = (profile / local_header.name).read_bytes()
    if touch_settings is not None:
        for name in ("head_touch_wifi.h", "head_touch_wifi.inc", "wifi_body_runtime.h", "wifi_body_runtime.inc"):
            patches[BOARD / name] = (ROOT / "firmware/body" / BOARD / name).read_bytes()
        patches[BOARD / "head_touch_wifi.local.h"] = (touch_profile / "head_touch_wifi.local.h").read_bytes()
    if network_settings:
        session_path = BOARD / "studio_usb_session.h"
        originals[session_path] = (base.PROJECT / session_path).read_bytes()
        for name in ("body_network_config.h", "body_network_config.inc", "studio_usb_session.h"):
            patches[BOARD / name] = (ROOT / "firmware/body" / BOARD / name).read_bytes()
        patches[BOARD / "body_network_defaults.local.h"] = (
            '#pragma once\n#include <cstdint>\n'
            f'inline constexpr char kBodyNetworkHost[]="{settings["host"]}";\n'
            f'inline constexpr uint16_t kBodyTouchPort={touch_settings["port"]};\n'
            f'inline constexpr uint16_t kBodyExpressionPort={settings["port"]};\n').encode("ascii")
    base_manifest = dict(manifest)
    if role_assets:
        if not network_settings:
            raise ValueError("role_assets_require_current_network_baseline")
        for name in ("studio_role_graph.h", "studio_role_graph.cc", "studio_role_json.h", "studio_role_player.cc",
                     "studio_asset_layout_manifest.cc", "studio_expression_control.h", "studio_display_readiness.h"):
            path = BOARD / name
            if (base.PROJECT / path).exists():
                originals[path] = (base.PROJECT / path).read_bytes()
            patches[path] = (ROOT / "firmware/body" / path).read_bytes()
    if layered_face:
        from protocol.layered_gfx_patch import patch as patch_gfx
        gfx = Path("managed_components/espressif2022__esp_emote_gfx/src/widget/gfx_anim.c")
        originals[gfx] = (base.PROJECT / gfx).read_bytes()
        patches[gfx] = patch_gfx(originals[gfx])
        surface = Path("managed_components/espressif2022__esp_emote_gfx/include/widget/gfx_anim_surface.h")
        patches[surface] = (ROOT / "firmware/body" / BOARD / "studio_layered_gfx.h").read_bytes()
        for name in ("studio_role_player.h", "studio_layered_face.h", "studio_layered_face.inc", "studio_layered_runtime.inc", "studio_layered_gfx.h"):
            path = BOARD / name
            if (base.PROJECT/path).exists(): originals[path] = (base.PROJECT/path).read_bytes()
            patches[path] = (ROOT / "firmware/body" / path).read_bytes()
        player = BOARD / "studio_role_player.cc"
        patches[player] = b'#define DESKBOT_LAYERED_FACE 1\n#line 1\n' + patches[player]
        for name in ("emote_display.cc", "emote_display.h"):
            path = Path("main/display") / name
            originals[path] = (base.PROJECT/path).read_bytes()
            patches[path] = (ROOT / "firmware/body" / path).read_bytes()
        adapter = BOARD / "studio_expression_adapter.cc"
        patches[adapter] = base.inject(patches[adapter], b'display->SetEmotion(semantic);', b'display->ReplayEmotion(semantic);')
    staged = []
    for path in patches:
        if path not in originals and (base.PROJECT / path).exists():
            raise ValueError("candidate source already exists: " + str(path))
    try:
        for path, data in patches.items():
            (base.PROJECT / path).write_bytes(data)
            staged.append(path); manifest[path.as_posix()] = H.sha256(data)
        guard_spec = importlib.util.spec_from_file_location(
            "printf_portability_guard", ROOT / "tools/audit-printf-portability.py")
        guard = importlib.util.module_from_spec(guard_spec)
        guard_spec.loader.exec_module(guard)
        for path in patches:
            if path.name.endswith(".local.h"):
                continue
            source = base.PROJECT / path
            if guard.detect(source.read_text(encoding="utf-8"), str(source)):
                raise ValueError("new wide-integer printf format in nano-libc target")
        H.verify_frozen_sources(base.PROJECT, manifest)
        H.verify_project_file_inventory(base.PROJECT, manifest, locked)
        runtime.run([tools["ninja"], "-j", "8", "-C", str(base.CACHE)], base.PROJECT, env, work / "build.log")
        base.threshold.parent.parent.stable.validate_log(work / "build.log", "build")
        H.verify_frozen_sources(base.PROJECT, manifest)
        artifacts = {name: H.file_record(base.CACHE / name) for name in H.ARTIFACTS}
        protected = {name: artifacts[name] == accepted["artifacts"][name] for name in H.PROTECTED}
        if not all(protected.values()):
            raise ValueError("protected outputs changed")
        effective = json.loads((base.CACHE / "config/sdkconfig.json").read_bytes())
        if effective != json.loads((base.BASE / "on/config/sdkconfig.json").read_bytes()):
            raise ValueError("effective configuration changed")
        if effective["ESPTOOLPY_FLASHSIZE"] != "32MB" or not effective["SPIRAM_MODE_OCT"]:
            raise ValueError("N32R16 effective configuration required")
        size_tool, nm = tools["xtensa-esp32s3-elf-size"], tools["xtensa-esp32s3-elf-nm"]
        elf_path, app_path = base.CACHE / "xiaozhi.elf", base.CACHE / "xiaozhi.bin"
        elf = H.validate_app_elf(app_path.read_bytes(), elf_path.read_bytes())
        symbols = subprocess.check_output([nm, "-C", str(elf_path)], env=env, text=True)
        expected = ["deskbot_wifi_expression::Observe", "deskbot_wifi_expression::UsbLease",
            "esp_http_client_perform", "deskbot_role::Request", "Application::WakeWordInvoke"]
        if touch_settings is None:
            expected += ["deskbot_wifi_expression::Start"]
        else:
            expected += ["deskbot_body_wifi::Start", "deskbot_body_wifi::(anonymous namespace)::Run",
                         "deskbot_head_touch_wifi::Observe", "deskbot_head_touch_wifi::Poll"]
            # Poll is a thin wrapper and can be inlined. The actual exchange
            # implementation must still be present in the shared task image.
            if not any(name in symbols for name in ("deskbot_wifi_expression::Poll(",
                    "deskbot_wifi_expression::(anonymous namespace)::PollOnce(")):
                raise ValueError("compiled expression exchange missing")
            if any(name in symbols for name in ("deskbot_head_touch_wifi::(anonymous namespace)::Run(",
                                                "deskbot_wifi_expression::(anonymous namespace)::Run(")):
                raise ValueError("standalone worker linked into shared-worker candidate")
        if not all(name in symbols for name in expected):
            raise ValueError("expected compiled features missing")
        if network_settings and not all(name in symbols for name in ("deskbot_body_network::Handle",
                "deskbot_body_network::Initialize", "nvs_set_blob", "nvs_get_blob", "nvs_commit")):
            raise ValueError("compiled network settings missing")
        internal = H.static_data_bytes(elf_path, size_tool, env)
        before = H.static_data_bytes(base.BASE / "on/xiaozhi.elf", size_tool, env)
        shutil.copy2(app_path, work / "xiaozhi.bin"); shutil.copy2(elf_path, work / "xiaozhi.elf")
    finally:
        for path in reversed(staged):
            file = base.PROJECT / path
            if file.read_bytes() != patches[path]:
                raise ValueError("staged source changed during build: " + str(path))
            if path in originals: file.write_bytes(originals[path])
            else: file.unlink()
    H.verify_frozen_sources(base.PROJECT, base_manifest)
    settings["firmware_sha256"] = elf
    temporary = configuration.with_suffix(".new")
    temporary.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8"); temporary.replace(configuration)
    if touch_settings is not None:
        touch_settings["firmware_sha256"] = elf
        temporary = touch_profile / "receiver.new"
        temporary.write_text(json.dumps(touch_settings, indent=2) + "\n", encoding="utf-8")
        temporary.replace(touch_profile / "receiver.json")
    report = dict(edition="E3f-9d.7-network-settings" if network_settings else
        EDITION if touch_settings is None else "E3f-9d.6-body-wifi",
        baseline=accepted["edition"], lab=settings["lab"], app_elf_sha256=elf,
        app=artifacts["xiaozhi.bin"], protected_equal=protected, effective_config_equal=True,
        flash="32MB", psram_mode="octal", internal_static_delta_bytes=internal-before, worker_stack_bytes=6144,
        network_workers=1, features=["wifi_expression"] + (["wifi_head_touch"] if touch_settings is not None else []),
        network_settings=network_settings, nvs_namespace="deskbot_net" if network_settings else None,
        role_assets="airboat-face-layered-v1" if layered_face else "airboat-face-v1" if role_assets else None,
        layered_face=layered_face,
        device_written=False, physical_expression_verified=False, rollback_app=accepted["artifacts"]["xiaozhi.bin"],
        source_sha256={p.as_posix(): H.sha256(raw) for p, raw in patches.items() if not p.name.endswith(".local.h")})
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--touch-profile", type=Path)
    parser.add_argument("--network-settings", action="store_true")
    parser.add_argument("--role-assets", action="store_true")
    parser.add_argument("--layered-face", action="store_true")
    args = parser.parse_args()
    build(args.profile.resolve(), args.touch_profile.resolve() if args.touch_profile else None, args.network_settings,
          args.role_assets, args.layered_face)
