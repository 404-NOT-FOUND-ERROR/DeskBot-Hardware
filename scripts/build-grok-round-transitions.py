#!/usr/bin/env python3
"""Build a synchronized EAF-decoded comparison of isolated clips and one continuous engine."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.eaf_preview import decode_eaf
from protocol.grok_round_assets import circle_mask, encode_gray_frames, expected_rgba
from protocol.grok_round_assets import read_gray_stream
from protocol.grok_round_transitions import DEFAULT_TIMELINE, boundary_metrics, validate_timeline

OUTPUT = ROOT / 'artifacts/grok-round-transitions-20261111'
TEMP = ROOT / 'tmp/grok-round-transitions-20261111'
LIBRARY = ROOT / 'artifacts/grok-round-eaf-20261111'
FPS = 20


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def original_builder():
    spec = importlib.util.spec_from_file_location('grok_round_original_builder', ROOT / 'scripts/build-grok-round-eaf.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_renderer() -> Path:
    original = original_builder()
    source = original.verify_source()
    core = original.SOURCE / 'firmware/BotAnimationReference/src'
    compiler = Path(os.environ.get('DESKBOT_ZIG', str(ROOT.parent.parent / 'tmp/zig-x86_64-windows-0.15.2/zig.exe')))
    if not compiler.is_file():
        raise ValueError('set DESKBOT_ZIG to the installed Zig executable')
    build_dir = TEMP / 'build'
    build_dir.mkdir(parents=True, exist_ok=True)
    binary = build_dir / 'grok-round-transition-render.exe'
    lock = build_dir / 'build.json'
    command = [str(compiler), 'c++', '-std=c++17', '-O2', '-DBOT_MAX_WIDTH=360', '-DBOT_MAX_HEIGHT=360',
               '-I', str(core), str(ROOT / 'scripts/grok-round-transition-render.cc'),
               *map(str, sorted(core.glob('Bot*.cpp'))), '-o', str(binary)]
    inputs = dict(source_commit=source['commit'],
                  source_core={i['path']: i['sha256'] for i in source['files'] if i['path'].startswith('firmware/')},
                  compiler_sha256=digest(compiler.read_bytes()),
                  adapter_sha256=digest((ROOT / 'scripts/grok-round-transition-render.cc').read_bytes()), command=command)
    if lock.is_file() and binary.is_file():
        previous = json.loads(lock.read_text(encoding='utf-8'))
        if previous['inputs'] == inputs and previous['binary_sha256'] == digest(binary.read_bytes()):
            return binary
    built = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
    (build_dir / 'build.log').write_text(built.stdout + built.stderr, encoding='utf-8')
    built.check_returncode()
    lock.write_text(json.dumps(dict(inputs=inputs, binary_sha256=digest(binary.read_bytes())),
                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return binary


def write_atlas(frames, path: Path) -> None:
    # A 180px preview atlas keeps both synchronized previews small in browser memory.
    columns, size = 10, 180
    atlas = Image.new('RGB', (columns * size, ((len(frames) + columns - 1) // columns) * size))
    for index, frame in enumerate(frames):
        image = Image.frombytes('RGBA', (360, 360), frame).convert('RGB')
        atlas.paste(image.resize((size, size), Image.Resampling.LANCZOS),
                    (index % columns * size, index // columns * size))
    atlas.save(path, optimize=True)


def decoded_array(frames) -> np.ndarray:
    return np.frombuffer(b''.join(frames), dtype=np.uint8).reshape(len(frames), 360, 360, 4)


def build() -> dict:
    binary = build_renderer()
    segments = validate_timeline(DEFAULT_TIMELINE, FPS)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw_path = TEMP / 'continuous.gray'
    try:
        native_run = subprocess.run([str(binary), str(FPS), str(raw_path),
                                     *[f'{s["state"]}:{s["frames"]}' for s in segments]],
                                    capture_output=True, text=True, check=True, timeout=120)
        native = json.loads(native_run.stdout)
        gray, alpha, fps = read_gray_stream(raw_path.read_bytes())
    finally:
        raw_path.unlink(missing_ok=True)
    if (native['segments'] != segments or native['motion_ticks'] != len(gray) * (60 // FPS) or
            native['reset_count'] != 1 or fps != FPS or native['motion_overflows'] or native['renderer_overflows']):
        raise ValueError('continuous native timeline differs from the requested timeline')
    mask = circle_mask(360, 360)
    gray[:, ~mask] = 0
    alpha[:, ~mask] = 0
    raw, wire = encode_gray_frames(gray, alpha)
    eaf_path = OUTPUT / 'continuous.eaf'
    eaf_path.write_bytes(raw)
    continuous = decode_eaf(eaf_path, fps=FPS)
    expected = expected_rgba(gray, alpha)
    if (len(continuous.frames) != len(gray) or continuous.metadata_frame_count != 1 or continuous.bit_depth != 4):
        raise ValueError('continuous EAF metadata differs')
    for index, frame in enumerate(continuous.frames):
        actual = np.frombuffer(frame, dtype=np.uint8).reshape(360, 360, 4)
        if not np.array_equal(actual, expected[index]):
            raise ValueError(f'continuous EAF pixels differ at frame {index}')
    del expected, gray, alpha
    source_manifest = json.loads((LIBRARY / 'manifest.json').read_text(encoding='utf-8'))
    isolated, input_hashes = [], {}
    for segment in segments:
        state = segment['state']
        item = next(i for i in source_manifest['expressions'] if i['upstream_state'] == state)
        path = LIBRARY / item['eaf']
        if digest(path.read_bytes()) != item['sha256']:
            raise ValueError(f'isolated source changed: {state}')
        animation = decode_eaf(path, fps=FPS)
        isolated.extend(animation.frames[:segment['frames']])
        input_hashes[item['eaf']] = item['sha256']
        del animation
    write_atlas(isolated, OUTPUT / 'isolated.png')
    write_atlas(continuous.frames, OUTPUT / 'continuous.png')
    continuous_seams = boundary_metrics(decoded_array(continuous.frames), segments)
    isolated_seams = boundary_metrics(decoded_array(isolated), segments)
    report = dict(schema='deskbot.grok_round_transition_sample/v1', source_commit=source_manifest['source']['commit'],
                  result='passed', scope='one fixed timeline with full-frame EAF decode verification',
                  fps=FPS, frames=len(continuous.frames), duration_ms=len(continuous.frames)*1000//FPS,
                  timeline=segments, native=native, wire=wire, eaf_bytes=len(raw), eaf_sha256=digest(raw),
                  all_frame_pixels_match_rgb565=True, sample_loopable=False,
                  all_pairwise_transitions_validated=False, device_operations=[],
                  boundaries=dict(isolated=isolated_seams, continuous=continuous_seams),
                  isolated_input_sha256=input_hashes,
                  atlas=dict(size=180, columns=10),
                  generator_sha256={name:digest((ROOT/name).read_bytes()) for name in
                                    ['scripts/grok-round-transition-render.cc', 'scripts/build-grok-round-transitions.py',
                                     'protocol/grok_round_transitions.py', 'scripts/grok-round-transitions.template.html']},
                  native_build=json.loads((TEMP/'build/build.json').read_text(encoding='utf-8')))
    for name in ['LICENSE', 'RIGHTS.md', 'THIRD_PARTY_NOTICES.md']:
        (OUTPUT/'source-notices').mkdir(exist_ok=True)
        shutil.copy2(LIBRARY/'source-notices'/name, OUTPUT/'source-notices'/name)
    template = (ROOT / 'scripts/grok-round-transitions.template.html').read_text(encoding='utf-8')
    (OUTPUT/'index.html').write_text(template.replace('__REPORT_JSON__', json.dumps(report, ensure_ascii=False).replace('</', '<\\/')), encoding='utf-8')
    (OUTPUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(result=report['result'], frames=report['frames'], eaf_bytes=len(raw),
                          boundaries=report['boundaries'], preview=str(OUTPUT/'index.html')), ensure_ascii=False), flush=True)
    return report


if __name__ == '__main__':
    build()
