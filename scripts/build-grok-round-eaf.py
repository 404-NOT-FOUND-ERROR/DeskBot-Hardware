#!/usr/bin/env python3
"""Build a local circular-expression library from the pinned C++ reference."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.eaf_preview import decode_eaf
from protocol.grok_round_assets import (
    ROUND_STATES, EXCLUDED_STATES, circle_mask, encode_gray_frames, expected_rgba, read_gray_stream,
)

COMMIT = 'dfe11400a51ae4b36948450e2bf8944251dcfeca'
SOURCE = ROOT / 'tmp/grok-bot-review-20261111/source'
DEFAULT_OUTPUT = ROOT / 'artifacts/grok-round-eaf-20261111'
TEMP = ROOT / 'tmp/grok-round-eaf-20261111'
PREFERRED = ['idle', 'happy', 'sad', 'shy', 'confused', 'drowsy']
FPS, FRAME_COUNT = 20, 160


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_source() -> dict:
    manifest = json.loads((SOURCE.parent / 'download-manifest.json').read_text(encoding='utf-8'))
    if manifest['commit'] != COMMIT:
        raise ValueError('source commit differs from the selected reference')
    for entry in manifest['files']:
        raw = (SOURCE / entry['path']).read_bytes()
        if len(raw) != entry['bytes'] or digest(raw) != entry['sha256']:
            raise ValueError(f'source changed: {entry["path"]}')
    return manifest


def build_renderer(source: dict) -> Path:
    """Reuse only a binary whose build inputs and stored digest still match."""
    build_dir = TEMP / 'build'
    build_dir.mkdir(parents=True, exist_ok=True)
    binary, lock = build_dir / 'grok-round-render.exe', build_dir / 'build.json'
    compiler = Path(os.environ.get('DESKBOT_ZIG', str(ROOT.parent.parent / 'tmp/zig-x86_64-windows-0.15.2/zig.exe')))
    if not compiler.is_file():
        raise ValueError('set DESKBOT_ZIG to the installed Zig executable')
    inputs = {entry['path']: entry['sha256'] for entry in source['files']
              if entry['path'].startswith('firmware/BotAnimationReference/src/')}
    inputs['adapter'] = digest((ROOT / 'scripts/grok-round-render.cc').read_bytes())
    inputs['compiler'] = digest(compiler.read_bytes())
    core = SOURCE / 'firmware/BotAnimationReference/src'
    command = [str(compiler), 'c++', '-std=c++17', '-O2', '-DBOT_MAX_WIDTH=360',
               '-DBOT_MAX_HEIGHT=360', '-I', str(core), str(ROOT / 'scripts/grok-round-render.cc'),
               *[str(path) for path in sorted(core.glob('Bot*.cpp'))], '-o', str(binary)]
    inputs['command'] = command
    if binary.is_file() and lock.is_file():
        prior = json.loads(lock.read_text(encoding='utf-8'))
        if prior['inputs'] == inputs and prior['binary_sha256'] == digest(binary.read_bytes()):
            return binary
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
    (build_dir / 'build.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        raise ValueError('desktop exporter build failed; see tmp/grok-round-eaf-20261111/build/build.log')
    lock.write_text(json.dumps({'inputs': inputs, 'command': command, 'binary_sha256': digest(binary.read_bytes())},
                                ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return binary


def export_one(binary: Path, output: Path, state: str, label: str) -> dict:
    temp = TEMP / f'{state}.gray'
    try:
        native = subprocess.run([str(binary), state, str(FRAME_COUNT), str(FPS), str(temp)],
                                check=True, capture_output=True, text=True, timeout=180)
        native_report = json.loads(native.stdout)
        gray, alpha, fps = read_gray_stream(temp.read_bytes())
    finally:
        # Only remove the single raw export just created for this state.
        if temp.exists():
            temp.unlink()
    if fps != FPS or len(gray) != FRAME_COUNT or native_report['shape'] != 'blob':
        raise ValueError('native timing or circular shape mismatch')
    if native_report['motion_ticks'] != FRAME_COUNT * (60 // FPS):
        raise ValueError('motion simulation drifted from the original 60 Hz timeline')
    mask = circle_mask(360, 360)
    clipped = int(np.count_nonzero(gray[:, ~mask]))
    brightest_clipped = int(gray[:, ~mask].max(initial=0))
    gray[:, ~mask] = 0
    alpha[:, ~mask] = 0
    raw, wire = encode_gray_frames(gray, alpha)
    eaf_path = output / 'eaf' / f'grok_round_{state}.eaf'
    eaf_path.write_bytes(raw)
    animation = decode_eaf(eaf_path, fps=fps)
    if ((animation.width, animation.height, len(animation.frames), animation.metadata_frame_count)
            != (360, 360, FRAME_COUNT, 1) or animation.bit_depth != 4):
        raise ValueError(f'{state}: EAF round-trip metadata differs')
    # Compare all pixels, not only sampled preview frames; the target color format is RGB565.
    expected = expected_rgba(gray, alpha)
    for number, frame in enumerate(animation.frames):
        actual = np.frombuffer(frame, dtype=np.uint8).reshape(360, 360, 4)
        if not np.array_equal(actual, expected[number]):
            raise ValueError(f'{state}: EAF round-trip pixel mismatch at frame {number}')
    images = [Image.frombytes('RGBA', (360, 360), frame).convert('RGB') for frame in animation.frames]
    poster_number = 2 * FPS
    images[poster_number].save(output / 'preview' / f'{state}.png', optimize=True)
    images[0].save(output / 'preview' / f'{state}.gif', save_all=True, append_images=images[1:],
                   duration=1000 // FPS, loop=0, disposal=2, optimize=False)
    # GIFs repeat in the gallery; the EAF playback recommendation is one-shot.
    poster_pixels = np.frombuffer(animation.frames[poster_number], dtype=np.uint8).reshape(360, 360, 4)
    errors = np.abs(expected[..., :3].astype(np.int16) - gray[..., None].astype(np.int16))
    mean_error, max_error = float(errors.mean()), int(errors.max())
    if mean_error > 2 or max_error > 17:
        raise ValueError(f'{state}: gray quantization error exceeds the conversion budget')
    report = {
        'id': f'grok_round_{state}', 'name': label, 'upstream_state': state, 'shape': 'blob',
        'eaf': f'eaf/{eaf_path.name}', 'preview': f'preview/{state}.gif', 'poster': f'preview/{state}.png',
        'bytes': len(raw), 'sha256': digest(raw), 'source_gray_sha256': digest(gray.tobytes()),
        'source_alpha_sha256': digest(alpha.tobytes()),
        'fps': FPS, 'duration_ms': FRAME_COUNT * 1000 // FPS, 'loop': False,
        'gallery_repeats': True, 'width': 360, 'height': 360,
        'layout': {'mode': 'full_frame', 'mirror': False, 'align': 'center', 'x': 0, 'y': 0},
        'wire': wire, 'native': native_report,
        'quality': {'all_frame_pixels_match_rgb565': True,
                    'mean_rgb_channel_error_from_gray8': round(mean_error, 5),
                    'max_rgb_channel_error_from_gray8': max_error,
                    'circle_mask_removed_nonblack_pixels': clipped,
                    'circle_mask_removed_brightest_gray': brightest_clipped,
                    'source_frame_differences': len({digest(frame.tobytes()) for frame in gray}),
                    'poster_has_visible_face': bool(poster_pixels[..., :3].max())},
    }
    (output / 'reports' / f'{state}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def contact_sheet(output: Path, expressions: list[dict]) -> None:
    columns, cell_width, cell_height = 6, 180, 212
    rows = (len(expressions) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * cell_width, rows * cell_height), '#12151b')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=13)
    for number, item in enumerate(expressions):
        x, y = number % columns * cell_width, number // columns * cell_height
        with Image.open(output / item['poster']) as poster:
            sheet.paste(poster.resize((172, 172), Image.Resampling.LANCZOS), (x + 4, y + 4))
        draw.text((x + 10, y + 180), item['upstream_state'], fill='#f2f4fa', font=font)
        draw.text((x + 10, y + 196), f'{item["bytes"]/1024:.0f} KiB', fill='#99a5b8', font=font)
    sheet.save(output / 'contact-sheet.png', optimize=True)


def build(output: Path, states: list[str]) -> dict:
    output = output.resolve()
    output.relative_to(ROOT / 'artifacts')
    source = verify_source()
    binary = build_renderer(source)
    for directory in ['eaf', 'preview', 'reports', 'source-notices']:
        (output / directory).mkdir(parents=True, exist_ok=True)
    expressions = []
    for state in states:
        print(f'Rendering {state} ...', flush=True)
        result = export_one(binary, output, state, ROUND_STATES[state])
        expressions.append(result)
        print(f'Created {result["eaf"]}: {result["bytes"]:,} B; all {FRAME_COUNT} frames verified', flush=True)
    for name in ['LICENSE', 'RIGHTS.md', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy2(SOURCE / name, output / 'source-notices' / name)
    source_core = {entry['path']: entry['sha256'] for entry in source['files']
                   if entry['path'].startswith('firmware/BotAnimationReference/src/')}
    manifest = {
        'schema': 'deskbot.grok_round_library/v1', 'name': '圆形表情 · 个人实验素材',
        'source': {'repository': 'https://github.com/legendtired/grok-bot-study', 'commit': COMMIT,
                   'shape': 'blob', 'renderer': 'C++ Gray8', 'core_sha256': source_core,
                   'usage_scope': 'personal_noncommercial_local_experiment',
                   'license': 'source-notices/LICENSE', 'rights': 'source-notices/RIGHTS.md'},
        'export': {'fps': FPS, 'image_frames_per_clip': FRAME_COUNT, 'width': 360, 'height': 360,
                   'seed': 20261111, 'bot_size': 256, 'simulation_hz': 60, 'mirror': False,
                   'loop': False, 'gallery_repeats': True},
        'expressions': expressions, 'excluded_non_face_states': EXCLUDED_STATES,
        'recommended_first_role_set': [state for state in PREFERRED if state in states],
        'capacity': {'all_eaf_bytes': sum(item['bytes'] for item in expressions),
                     'first_role_set_bytes': sum(item['bytes'] for item in expressions if item['upstream_state'] in PREFERRED),
                     'active_partition_bytes': 8 * 1024 ** 2, 'publish_soft_budget_bytes': 7 * 1024 ** 2,
                     'current_e145_content_bytes': 5981260},
        'device_status': 'offline_candidates_need_layout_registration_and_package_selection',
        'generator_sha256': {file: digest((ROOT / file).read_bytes()) for file in
                              ['scripts/grok-round-render.cc', 'scripts/build-grok-round-eaf.py',
                               'protocol/grok_round_assets.py']},
        'native_build': json.loads((TEMP / 'build/build.json').read_text(encoding='utf-8')),
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    contact_sheet(output, expressions)
    template = (ROOT / 'scripts/grok-round-gallery.template.html').read_text(encoding='utf-8')
    (output / 'index.html').write_text(template.replace('__MANIFEST_JSON__', json.dumps(manifest, ensure_ascii=False).replace('</', '<\\/')), encoding='utf-8')
    print(json.dumps({'expression_count': len(expressions), 'capacity': manifest['capacity'],
                      'preview': str(output / 'index.html')}, ensure_ascii=False), flush=True)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--states', nargs='+', choices=list(ROUND_STATES), default=list(ROUND_STATES))
    arguments = parser.parse_args()
    build(arguments.output, arguments.states)
