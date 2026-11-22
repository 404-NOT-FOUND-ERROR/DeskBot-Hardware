"""Convert reviewed GIFs into DeskBot EAF clips, decoded atlases and a source lock."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.airboat_role_assets import (FPS, SIDE, COLORS, ROLE_ID, LABELS, container,
    digest, image_record, normalize_seams)
from protocol.eaf_preview import decode_eaf

COMMIT = "0d087e8dfe2925c96374a5bc07c3cebf9ae5a258"


def sample(path):
    frames, durations = [], []
    with Image.open(path) as gif:
        for image in ImageSequence.Iterator(gif):
            frames.append(image.convert("RGB").resize((SIDE, SIDE), Image.Resampling.LANCZOS))
            durations.append(image.info.get("duration", 40))
    result, index, end = [], 0, durations[0]
    for at in range(0, sum(durations), 1000//FPS):
        while index < len(frames)-1 and at >= end:
            index += 1
            end += durations[index]
        result.append(frames[index])
    return result


def build(source, dest, states=None):
    source, dest = source.resolve(), dest.resolve()
    upstream = json.loads((source/"transitions/manifest.json").read_bytes())
    if set(upstream["entries"]) != set(LABELS):
        raise ValueError("expected the reviewed 29-expression set")
    for name in ("clips", "previews"):
        (dest/name).mkdir(parents=True, exist_ok=True)
    cache = {}
    def encode(image):
        key = digest(image.tobytes())
        if key not in cache:
            cache[key] = image_record(image)
        return cache[key]
    home = encode(sample(source/upstream["entries"]["idle"]["entry"]["file"])[0])
    manifest = dict(schema="deskbot.composable_role_clips/v1", role_id=ROLE_ID, label="新表情角色",
        source=dict(project="ESP AirBoat", commit=COMMIT, files={}), fps=FPS,
        display=dict(width=SIDE, height=SIDE, shape="circle", layout="full_face", mirror=False),
        encoding=dict(bit_depth=4, palette_colors=COLORS),
        atlas=dict(columns=8, size=120), expressions={})
    for state, row in upstream["entries"].items():
        if states and state not in states:
            continue
        phases = {}
        for phase in ("entry", "loop", "exit"):
            path = source/row[phase]["file"]
            if not path.resolve().is_relative_to(source):
                raise ValueError("source outside the animation library")
            manifest["source"]["files"][row[phase]["file"]] = digest(path.read_bytes())
            phases[phase] = [encode(image) for image in sample(path)]
        phases = normalize_seams(phases["entry"], phases["loop"], phases["exit"], home)
        all_frames, clips = [], {}
        for phase, records in phases.items():
            file = f"clips/{state}-{phase}.eaf"
            raw = container(records)
            (dest/file).write_bytes(raw)
            decoded = decode_eaf(dest/file)
            clips[phase] = dict(file=file, sha256=digest(raw), bytes=len(raw), frames=len(records),
                duration_ms=len(records)*50, atlas_offset=len(all_frames),
                first_rgba_sha256=digest(decoded.frames[0]), last_rgba_sha256=digest(decoded.frames[-1]))
            all_frames.extend(decoded.frames)
        atlas = Image.new("RGBA", (8*120, ((len(all_frames)+7)//8)*120))
        for index, raw in enumerate(all_frames):
            atlas.paste(Image.frombytes("RGBA", (SIDE, SIDE), raw).resize((120,120), Image.Resampling.LANCZOS),
                        ((index%8)*120, (index//8)*120))
        file = f"previews/{state}.png"
        atlas.save(dest/file, optimize=True)
        manifest["expressions"][state] = dict(label=LABELS[state], atlas=file, clips=clips)
        print(json.dumps(dict(state=state, bytes=sum(c["bytes"] for c in clips.values())), ensure_ascii=True), flush=True)
    manifest["capacity"] = dict(expressions=len(manifest["expressions"]),
        eaf_bytes=sum(c["bytes"] for e in manifest["expressions"].values() for c in e["clips"].values()))
    (dest/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT/"artifacts/airboat-role-20261121")
    parser.add_argument("--states", nargs="+")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.states)["capacity"]))
