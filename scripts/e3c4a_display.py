"""Assemble display queries on the accepted USB receiver from frozen sources."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "tmp/e3c4-display-20261105"
ACCEPTED = ROOT / "tmp/e3c3a-usb-path-20261106"
WORK = ROOT / "tmp/e3c4a-display-20261107"
PUBLIC = ROOT / "artifacts/studio-display-e3c4a-20261107"
USB = "main/boards/esp_vocat/studio_usb_diagnostic.cc"
OPTION = "DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE"
DISPLAY_FILES = {
    "main/Kconfig.projbuild", USB,
    "main/boards/esp_vocat/studio_usb_session.h",
    "main/boards/esp_vocat/studio_display_readiness.h",
    "main/boards/esp_vocat/studio_runtime_state.h",
    "main/boards/esp_vocat/studio_runtime_state.cc",
    "main/display/emote_display.cc",
}
REPORTS = {
    "display": ("artifacts/deskbot-studio-e3c4-20261105/firmware-build-report.json",
                "32f68017f145c7b719e344bd637c553e2067cd02bfa7f9eb3447739ee8d7479d"),
    "accepted": ("artifacts/ui-menu-regression-20261106/e3c3a-build.json",
                 "0839fb2d252145b2f264715bf81b12d6d335127270c8ea3279aab3b13415eb10"),
}
DISPLAY_HOOK = (b"#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE\n"
                b"    endpoint.SetDisplayProvider(deskbot_studio_runtime::ReadDisplay);\n"
                b"#endif\n")
STATE_HOOK = (b"#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE\n"
              b"    endpoint.SetStateProvider(deskbot_studio_runtime::Read);\n"
              b"#endif\n")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": sha(raw)}


def pinned_reports():
    reports = {}
    for key, (name, digest) in REPORTS.items():
        raw = (ROOT / name).read_bytes()
        if sha(raw) != digest:
            raise ValueError("frozen build report changed: " + name)
        reports[key] = json.loads(raw)
    return reports


def normalized_source(raw, name, project):
    if name != "dependencies.lock":
        return raw
    touch = project / "components/espressif__touch_sensor_fsm"
    spellings = {str(touch).encode("utf-8"), touch.as_posix().encode("utf-8")}
    matches = [value for value in spellings if raw.count(value) == 1]
    if len(matches) != 1:
        raise ValueError("local touch component path differs")
    return raw.replace(matches[0], b"components/espressif__touch_sensor_fsm")


def verify_sources(project, manifest):
    for name, digest in manifest.items():
        path = (project / name).resolve()
        if not path.is_relative_to(project.resolve()):
            raise ValueError("source path outside project")
        if sha(normalized_source(path.read_bytes(), name, project)) != digest:
            raise ValueError("frozen source changed: " + name)


def usb_candidate(accepted):
    if sha(accepted) != "98ebbf6f911348aa56b0d0fd14bebd90b96a2d2e9f75146227a68a7024381479":
        raise ValueError("expected accepted E3c-3a USB source")
    if accepted.count(STATE_HOOK) != 1 or DISPLAY_HOOK in accepted:
        raise ValueError("unexpected provider hook")
    return accepted.replace(STATE_HOOK, STATE_HOOK + DISPLAY_HOOK)


def candidate_sources(reports=None):
    reports = reports or pinned_reports()
    old, display = reports["accepted"]["source_sha256"], reports["display"]["source_sha256"]
    verify_sources(ACCEPTED / "p", old)
    verify_sources(HISTORY / "p", display)
    source = {name: (HISTORY / "p" / name).read_bytes() for name in display}
    source[USB] = usb_candidate((ACCEPTED / "p" / USB).read_bytes())
    manifest = {name: sha(normalized_source(raw, name, HISTORY / "p"))
                for name, raw in source.items()}
    delta = {name for name in old.keys() | manifest.keys() if old.get(name) != manifest.get(name)}
    if delta != DISPLAY_FILES:
        raise ValueError("unexpected display delta: " + repr(sorted(delta)))
    return source, manifest
