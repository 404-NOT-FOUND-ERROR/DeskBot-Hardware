#!/usr/bin/env python3
"""E3d-1: saved package directory, source audit and offline page fixtures."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_installed_catalog as contract
from protocol import studio_catalog_audit as audit
from protocol import expression_control
from protocol import studio_resource_package as packages

PUBLIC = ROOT / "artifacts/deskbot-studio-e3d1-20261105"
LOCAL = ROOT / "tmp/deskbot-studio-e3d1-20261105"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def products(local=False):
    previous = module("e3d_parent", "scripts/build-deskbot-studio-e3c.py").products(local)
    saved = json.loads(previous["expression-catalog.json"])
    expression_control.validate_catalog(saved)
    parser, _ = packages.load_legacy_tools()
    image = packages.BASE_IMAGE.read_bytes()
    raw = packages.extract_package(image, 0x800000)
    if packages.sha(image) != packages.BASE_IMAGE_SHA or packages.sha(raw) != packages.BASE_PACKAGE_SHA:
        raise ValueError("saved E1.18 package digest changed")
    members = parser.parse_package_bytes(raw, modern=False)["members"]
    source_name = "firmware/body/main/display/emote_display.cc"
    source = (ROOT / source_name).read_bytes()
    lock = json.loads((ROOT / "artifacts/deskbot-studio-e3c8-20261105/firmware-build-report.json").read_bytes())
    if packages.sha(source) != lock["source_sha256"]["main/display/emote_display.cc"]:
        raise ValueError("action source differs from the E3c-8 build")
    defaults_name = "firmware/body/main/boards/esp_vocat/assets/360_360/emote.json"
    result = audit.audit(members, saved, json.loads((ROOT / defaults_name).read_bytes()), source.decode("utf-8"))
    manifest = contract.build_manifest(result.pop("entries"), packages.sha(raw), packages.sha(members["index.json"]))
    context = dict(target_id="device-" + "a" * 32, boot_id="boot-" + "b" * 32,
                   session_id="session-" + "b" * 32 + "-1")
    pages = [contract.make_page(manifest, **context, request_id=f"catalog-{cursor}", cursor=cursor,
                               sequence=cursor // contract.PAGE_SIZE + 1, at_ms=cursor * 1000)
             for cursor in range(0, len(manifest["entries"]), contract.PAGE_SIZE)]
    collector = contract.CatalogCollector(**context)
    for page in pages:
        collector.accept(page, request_id=page["request_id"], cursor=page["cursor"])
    if collector.confirmed != manifest:
        raise ValueError("directory page roundtrip changed")
    html = previous["index.html"].decode("utf-8")
    match = re.search(r'<script id="studio-data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(match[1])
    capability = dict(source="source_audit", live=False, supported_expression_ids=["neutral"],
                      supported_action_ids=[], firmware="E3c-8", device_catalog_query=False)
    data["installed_directory"] = dict(source="verified_package_snapshot", live=False, revision="E1.18",
        manifest=manifest, audit=result, usb=capability)
    html = html[:match.start(1)] + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + html[match.end(1):]

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError("E3d input anchor changed: " + old[:80])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3c·6</span>', '<span class="edition">E3d·1</span>')
    once('const liveControl=DeskBotStudioE3CUI.create({observation:()=>body.state()});',
         'const liveControl=DeskBotStudioE3CUI.create({observation:()=>body.state()});\n  const directory=DeskBotStudioE3DUI.create(D,{canvas,role:()=>active(),paint:()=>{observeCanvases();startRaf();}});')
    once('<div id="body-observer-slot">${body.view()}</div></div>',
         '<div id="body-observer-slot">${body.view()}</div><div id="installed-directory-slot">${directory.view()}</div></div>')
    once('  liveControl.init();', '  liveControl.init();\n  directory.init();')
    once('control:()=>liveControl.state(),', 'control:()=>liveControl.state(),directory:()=>directory.state(),')
    once('window.DeskBotStudioE3CUI=Object.freeze({create});\n})();',
         'window.DeskBotStudioE3CUI=Object.freeze({create});\n})();\n;\n' + (ROOT / "scripts/deskbot-studio-e3d.js").read_text(encoding="utf-8"))
    once('</style>', (ROOT / "scripts/deskbot-studio-e3d.css").read_text(encoding="utf-8") + '</style>')
    sources = ["protocol/studio_installed_catalog.py", "protocol/studio_catalog_audit.py",
        "protocol/deskbot-studio-catalog-page-v1.schema.json", "scripts/build-deskbot-studio-e3d.py",
        "scripts/deskbot-studio-e3d.js", "scripts/deskbot-studio-e3d.css",
        "scripts/serve-deskbot-studio-e3d.py", source_name, defaults_name]
    report = dict(edition="E3d-1", result="saved_directory_and_offline_contract_verified",
        source="verified_package_snapshot", live=False, firmware_changed=False,
        hardware_accessed=False, device_written=False, device_catalog_query=False,
        physical_acceptance="pending", manifest_id=manifest["catalog_id"],
        package_sha256=packages.sha(raw), index_sha256=packages.sha(members["index.json"]),
        legacy_control_catalog_id=saved["catalog_id"],
        manifest_id_is_independent_of_legacy_control=True,
        entries=len(manifest["entries"]), pages=len(pages), page_size=contract.PAGE_SIZE,
        largest_frame_bytes=max(len(contract.canonical(page)) + 1 for page in pages),
        usb=capability, audit=result,
        next_step="device_owned_directory_snapshot_and_paged_usb_reader",
        sources=[dict(path=name, sha256=packages.sha((ROOT / name).read_bytes())) for name in sources])
    return {**previous, "index.html": html.encode("utf-8"), "report.json": packages.json_bytes(report),
        "installed-directory.json": packages.json_bytes(manifest),
        "catalog-page-fixtures.json": packages.json_bytes(dict(source="offline_fixture", live=False, pages=pages))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    folder = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            if (folder / name).read_bytes() != raw:
                raise ValueError("E3d generated product changed: " + name)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print(json.dumps(dict(edition="E3d-1", result="checked" if args.check else "built")))
