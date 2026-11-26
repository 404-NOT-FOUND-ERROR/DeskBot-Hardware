"""Verify archived products and current regeneration as distinct byte endpoints."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RECORD = "artifacts/publication-20261127/regeneration.json"
BUILDERS = ("e2c", "e3a", "e3b", "e3c", "e3d", "e3d2", "e3e", "e3f2")
IMAGE_REQUIRED = {
    "e3d": {"edition": "E3d-1", "result": "saved_directory_and_offline_contract_verified",
            "live": False, "device_written": False},
    "e3d2": {"edition": "E3d-2", "result": "offline_workbench_generated",
             "device_written": False, "automatic_device_commands": False},
    "e3e": {"edition": "E3e", "result": "offline_role_route_generated",
            "automatic_device_commands": False},
    "e3f2": {"edition": "E3f-2", "result": "offline_role_overview_generated",
             "device_written": False, "automatic_device_commands": False},
}
SOURCE_REPLACEMENT = {
    "firmware/body/main/display/emote_display.cc":
        "artifacts/deskbot-studio-e3d1-20261105/emote-actions-source.cc"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def report_delta(saved, fresh):
    """Results stay identical; register only source metadata and parent digests."""
    old, new = json.loads(saved), json.loads(fresh)
    metadata = {"sources", "parent_report"}
    if {k:v for k,v in old.items() if k not in metadata} != {k:v for k,v in new.items() if k not in metadata}:
        raise ValueError("historical report result or scope changed")
    before = {r["path"]:r for r in old.get("sources", [])}
    after = {r["path"]:r for r in new.get("sources", [])}
    if len(before) != len(old.get("sources", [])) or len(after) != len(new.get("sources", [])):
        raise ValueError("duplicate source path")
    changes = []
    for name, row in before.items():
        target = name if name in after else SOURCE_REPLACEMENT.get(name)
        if target not in after:
            raise ValueError("historical source removed: " + name)
        other = after[target]
        if {k:v for k,v in row.items() if k not in {"path", "sha256"}} != {k:v for k,v in other.items() if k not in {"path", "sha256"}}:
            raise ValueError("source metadata shape changed")
        if row != other:
            reason = ("Use the preserved action-binding source for the historical package audit."
                if target != name else "Current shared source differs from the retained historical input.")
            changes.append(dict(before=row, after=other, reason=reason))
    replaced = {SOURCE_REPLACEMENT[n] for n in before if n not in after}
    for name in sorted(after.keys() - before.keys() - replaced):
        if name != "scripts/deskbot-studio-play-readiness.js":
            raise ValueError("unregistered source addition: " + name)
        changes.append(dict(before=None, after=after[name], reason="Share the explicit playback readiness state machine."))
    for row in after.values():
        if sha((ROOT/row["path"]).read_bytes()) != row["sha256"]:
            raise ValueError("generated source digest differs: " + row["path"])
    parent = None
    if old.get("parent_report") != new.get("parent_report"):
        if not all(isinstance(d.get("parent_report"),str) and len(d["parent_report"])==64 for d in (old,new)):
            raise ValueError("parent report shape changed")
        parent = dict(before=old["parent_report"], after=new["parent_report"],
            reason="The parent generator records the current source metadata.")
    return dict(sources=changes, parent_report=parent)


def load_records(root=None):
    root=Path(root or ROOT).resolve()
    data=json.loads((root/RECORD).read_bytes())
    if data.get("schema") != "deskbot.archived_regeneration/v1":
        raise ValueError("regeneration schema differs")
    rows={}
    for row in data["products"]:
        key=(row["path"], row["variant"])
        if key in rows or row["variant"] not in {"public","local"}:
            raise ValueError("duplicate or invalid regeneration record")
        rows[key]=row
    return rows


def product_matches(saved, fresh, public_path, local=False):
    path=Path(public_path).resolve().relative_to(ROOT.resolve()).as_posix()
    records = load_records() if (ROOT / RECORD).is_file() else {}
    row = records.get((path, "local" if local else "public"))
    if row is None:
        return saved == fresh
    if sha(saved)!=row["archived_sha256"] or sha(fresh)!=row["generated_sha256"]:
        return False
    return not path.endswith("report.json") or report_delta(saved,fresh)==row.get("report_delta")


def check_image_archive(previous, name, folder, scope):
    """Check the retained public endpoints when the original image is unavailable."""
    rows = []
    for product in ("index.html", "report.json"):
        path = (folder / product).relative_to(ROOT.resolve()).as_posix()
        saved = (folder / product).read_bytes()
        row = previous.get((path, "public"))
        if (
            row is None or row["builder"] != name
            or sha(saved) != row["archived_sha256"]
            or not re.fullmatch(r"[0-9a-f]{64}", row["generated_sha256"])
            or row["archived_sha256"] == row["generated_sha256"]
        ):
            raise ValueError("public archived endpoint differs: " + path)
        if product == "report.json":
            report = json.loads(saved)
            for field, value in {"hardware_accessed": False, **scope}.items():
                if field not in report or type(report[field]) is not type(value) or report[field] != value:
                    raise ValueError("public archived report scope differs: " + path + " " + field)
        rows.append(row)
    return rows


def products(check=False):
    from protocol.studio_resource_package import BASE_IMAGE

    previous=load_records() if (ROOT/RECORD).is_file() else {}
    image_available = BASE_IMAGE.is_file()
    if not check and not image_available:
        raise ValueError("recording regeneration endpoints requires the retained E1.18 image")
    rows=[]
    for edition in BUILDERS:
        name="scripts/build-deskbot-studio-"+edition+".py"
        spec=importlib.util.spec_from_file_location("regenerate_"+edition,ROOT/name)
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        for local in (False,True):
            folder=builder.LOCAL if local else builder.PUBLIC
            # Local rows are kept for later reproduction with the retained media.
            if local and (not folder.is_dir() or (edition in IMAGE_REQUIRED and not image_available)):
                rows.extend(row for (path,variant),row in previous.items()
                    if variant=="local" and row["builder"]==name)
                continue
            if edition in IMAGE_REQUIRED and not image_available:
                scope = IMAGE_REQUIRED[edition]
                rows.extend(check_image_archive(previous, name, folder, scope))
                print(f"{scope['edition']}: public archive verified. Regeneration needs the local E1.18 image.")
                continue
            for product,fresh in builder.products(local).items():
                saved=(folder/product).read_bytes()
                if saved==fresh: continue
                row=dict(builder=name,path=(builder.PUBLIC/product).relative_to(ROOT.resolve()).as_posix(),
                    variant="local" if local else "public", archived_sha256=sha(saved),generated_sha256=sha(fresh),
                    reason=("Current shared playback UI and source records; original stage artifact retained."))
                if product=="report.json":row["report_delta"]=report_delta(saved,fresh)
                elif product!="index.html":raise ValueError("unregistered product delta: "+product)
                rows.append(row)
    return dict(schema="deskbot.archived_regeneration/v1", hardware_accessed=False,
        historical_results_rewritten=False, products=rows)


def main():
    import argparse
    import sys

    sys.path.insert(0, str(ROOT))
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--check",action="store_true")
    args=parser.parse_args();data=products(args.check)
    raw=(json.dumps(data,indent=2,ensure_ascii=False)+"\n").encode()
    if args.check:
        if (ROOT/RECORD).read_bytes()!=raw:raise ValueError("regeneration record differs")
    else:
        (ROOT/RECORD).parent.mkdir(parents=True,exist_ok=True);(ROOT/RECORD).write_bytes(raw)
    print("Archive records " + ("checked: " if args.check else "recorded: ") + str(len(data["products"])))

if __name__=="__main__":main()
