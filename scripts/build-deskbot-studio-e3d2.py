#!/usr/bin/env python3
"""E3d-2 workbench: a device directory alongside the verified package snapshot."""
import argparse
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e3d2_parent", ROOT / "scripts/build-deskbot-studio-e3d.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
PUBLIC = ROOT / "artifacts/deskbot-studio-e3d2-20261105"
LOCAL = ROOT / "tmp/deskbot-studio-e3d2-20261105"


def products(local=False):
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError("E3d-2 input anchor changed: " + old[:80])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3d·1</span>', '<span class="edition">E3d·2</span>')
    anchor = 'const directory=DeskBotStudioE3DUI.create(D,{canvas,role:()=>active(),paint:()=>{observeCanvases();startRaf();}});'
    once(anchor, anchor + '\n  const liveDirectory=DeskBotStudioE3D2UI.create(D,{control:()=>liveControl.state(),observation:()=>body.state()});')
    once('<div id="installed-directory-slot">${directory.view()}</div>',
         '<div id="installed-directory-slot">${directory.view()}</div><div id="live-directory-slot">${liveDirectory.view()}</div>')
    once('  directory.init();', '  directory.init();\n  liveDirectory.init();')
    once('directory:()=>directory.state(),', 'directory:()=>directory.state(),liveDirectory:()=>liveDirectory.state(),')
    once('设备实时目录 · 待接入', '实时目录 · 见下方')
    once('下一步：设备返回真实目录', '设备实时目录如何读取')
    once('window.DeskBotStudioE3DUI=Object.freeze({create});\n})();',
         'window.DeskBotStudioE3DUI=Object.freeze({create});\n})();\n;\n' + (ROOT / "scripts/deskbot-studio-e3d2.js").read_text(encoding="utf-8"))
    once('</style>', (ROOT / "scripts/deskbot-studio-e3d2.css").read_text(encoding="utf-8") + '</style>')
    names = ["scripts/build-deskbot-studio-e3d2.py", "scripts/serve-deskbot-studio-e3d2.py",
             "scripts/start-deskbot-studio-e3d2.ps1", "scripts/deskbot-studio-e3d2.js", "scripts/deskbot-studio-e3d2.css",
             "protocol/studio_catalog_query.py", "protocol/studio_control_link.py",
             "protocol/studio_control_bridge.py", "protocol/studio_installed_catalog.py",
             "protocol/deskbot-studio-control-link-v1.schema.json", "protocol/deskbot-studio-catalog-page-v1.schema.json",
             "tests/test_studio_catalog_query.py", "tests/test_studio_catalog_native.py",
             "tests/test_deskbot_studio_e3d2.py", "tests/deskbot-studio-e3d2-browser.cjs",
             "tests/cpp/studio_catalog_test.cc", "tests/vendor/studio_catalog/SOURCES.json",
             "scripts/build-deskbot-studio-e3c.py", "tests/test_deskbot_studio_e3c.py",
             "scripts/publication_evidence.py", "scripts/build-e3d2-source-review.py",
             "scripts/validate-deskbot-studio-e3d2.py", "scripts/build-deskbot-studio-e3d2-firmware.py",
             "scripts/validate-deskbot-studio-e3c3.py",
             "tests/test_publication_evidence.py",
             "protocol/studio_runtime_query.py", "protocol/studio_app_deployment.py",
             "tests/test_studio_runtime_query.py", "tests/test_studio_runtime_snapshot.py",
             "tests/cpp/studio_runtime_snapshot_test.cc",
             "scripts/run-offline-checks.py", "scripts/check-publication.py", "scripts/check-workbenches.py",
             "firmware/body/SOURCE_MANIFEST.sha256", "firmware/body/main/assets.cc",
             "firmware/body/main/Kconfig.projbuild", "firmware/body/main/application.cc",
             "firmware/body/main/device_state.h",
             *("firmware/body/main/boards/esp_vocat/" + name for name in (
                 "studio_usb_diagnostic.cc", "studio_usb_session.h", "studio_catalog.h",
                 "studio_catalog_actions.h", "studio_catalog_builder.cc", "studio_catalog_adapter.h",
                 "studio_catalog_adapter.cc", "studio_runtime_query.h", "studio_runtime_snapshot.h",
                 "studio_runtime_state.h", "studio_runtime_state.cc", "ui_bridge.cc"))]
    names = sorted(set(names))
    report = dict(edition="E3d-2", result="offline_workbench_generated",
        source="verified_package_snapshot_and_explicit_device_query", hardware_accessed=False,
        device_written=False, physical_acceptance="pending", device_catalog_query=True,
        additional_usb_owners=0, automatic_device_commands=False,
        parent_report=parent.packages.sha(previous["report.json"]),
        sources=[dict(path=name, sha256=parent.packages.sha((ROOT / name).read_bytes())) for name in names],
        next_step="device_catalog_and_existing_neutral_regression")
    return {**previous, "index.html": html.encode("utf-8"), "report.json": parent.packages.json_bytes(report)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    folder = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            saved = (folder / name).read_bytes()
            from scripts.archived_products import product_matches
            if not product_matches(saved, raw, PUBLIC / name, local=args.local):
                raise ValueError("E3d-2 generated product changed: " + name)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print(json.dumps(dict(edition="E3d-2", result="checked" if args.check else "built")))
