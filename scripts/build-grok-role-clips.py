#!/usr/bin/env python3
"""Build six composable circular expressions and their EAF-decoded node preview."""
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
from protocol.grok_round_assets import circle_mask, encode_gray_frames, expected_rgba, read_gray_stream
from protocol.grok_role_clips import FPS, LABELS, PHASES, STATES, capacity, frame_digest, verify_seams

OUTPUT = ROOT / 'artifacts/grok-role-clips-20261111'
TEMP = ROOT / 'tmp/grok-role-clips-20261111'
LIBRARY = ROOT / 'artifacts/grok-round-eaf-20261111'
E145 = ROOT / 'tmp/expression-e1-45-staging-20261104'
GENERATORS = ['scripts/grok-role-clips-render.cc', 'scripts/build-grok-role-clips.py',
              'protocol/grok_role_clips.py', 'scripts/grok-role-scheduler.js',
              'scripts/grok-role-clips.template.html']


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_renderer() -> tuple[Path, dict]:
    spec = importlib.util.spec_from_file_location('grok_source_builder', ROOT / 'scripts/build-grok-round-eaf.py')
    source_builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_builder)
    source = source_builder.verify_source()
    core = source_builder.SOURCE / 'firmware/BotAnimationReference/src'
    compiler = Path(os.environ.get('DESKBOT_ZIG', str(ROOT.parent.parent / 'tmp/zig-x86_64-windows-0.15.2/zig.exe')))
    if not compiler.is_file():
        raise ValueError('set DESKBOT_ZIG to the installed Zig executable')
    directory = TEMP / 'build'
    directory.mkdir(parents=True, exist_ok=True)
    binary, lock = directory / 'grok-role-clips-render.exe', directory / 'build.json'
    command = [str(compiler), 'c++', '-std=c++17', '-O2', '-DBOT_MAX_WIDTH=360', '-DBOT_MAX_HEIGHT=360',
               '-I', str(core), str(ROOT / 'scripts/grok-role-clips-render.cc'),
               *map(str, sorted(core.glob('Bot*.cpp'))), '-o', str(binary)]
    inputs = dict(source_commit=source['commit'], command=command,
                  source_core={i['path']: i['sha256'] for i in source['files'] if i['path'].startswith('firmware/')},
                  compiler_sha256=digest(compiler.read_bytes()),
                  adapter_sha256=digest((ROOT / GENERATORS[0]).read_bytes()))
    if binary.is_file() and lock.is_file():
        previous = json.loads(lock.read_text(encoding='utf-8'))
        if previous['inputs'] == inputs and previous['binary_sha256'] == digest(binary.read_bytes()):
            return binary, previous
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
    (directory / 'build.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    process.check_returncode()
    record = dict(inputs=inputs, binary_sha256=digest(binary.read_bytes()))
    lock.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return binary, record


def boundary_delta(frames, at_start: bool) -> float:
    left, right = frames[:2] if at_start else frames[-2:]
    a, b = [np.frombuffer(raw, dtype=np.uint8).reshape(360, 360, 4) for raw in (left, right)]
    # Composite transparent RGBA over the round display's black background.
    visible = [x[..., :3].astype(np.uint16) * x[..., 3:4] // 255 for x in (a, b)]
    return round(float(np.abs(visible[1].astype(np.int16) - visible[0].astype(np.int16)).mean()), 6)


def package_projection(unique_files: dict[str, bytes]) -> dict:
    """Measure the actual package format in memory; the resulting package is discarded."""
    staging = json.loads((E145 / 'report.json').read_text(encoding='utf-8'))
    image = (E145 / 'candidate-assets-8m.bin').read_bytes()
    if len(image) != staging['partition']['bytes'] or digest(image) != staging['candidate']['partition_sha256']:
        raise ValueError('E1.45 capacity reference differs')
    package_size = int.from_bytes(image[8:12], 'little') + 12
    base = image[:package_size]
    if (package_size != 5_981_260 or digest(base) != staging['candidate']['package_sha256'] or
            image[package_size:] != b'\xff' * (len(image) - package_size)):
        raise ValueError('E1.45 package framing or padding differs')
    resource_module = original_package_module()
    parser = resource_module.AUDIT
    members = parser.parse_package_bytes(base, modern=False)['members']
    if members.get('index.json') is None:
        raise ValueError('E1.45 package has no index')
    index = json.loads(members['index.json'])
    rows = index.get('emoji_collection')
    if not isinstance(rows, list) or any(row.get('name', '').startswith('grok_') for row in rows):
        raise ValueError('role clip capacity aliases already exist in the baseline')
    projected = dict(members)
    logical_assets = []
    for asset_key, eaf in sorted(unique_files.items()):
        state, phase = asset_key.split('/')
        file_name = f'grok-{state}-{phase}.eaf'
        semantic = f'grok_{state}_{phase}'
        logical_assets.append(dict(asset_key=asset_key, semantic=semantic, file=file_name,
                                   loop=phase == 'loop', fps=FPS, sha256=digest(eaf), bytes=len(eaf)))
        projected[file_name] = eaf
        rows.append(dict(name=semantic, file=file_name, eaf=dict(loop=phase == 'loop', fps=FPS)))
    projected['index.json'] = (json.dumps(index, ensure_ascii=False, indent=4) + '\n').encode('utf-8')
    candidate = resource_module.build_legacy_package(projected)
    parsed = parser.parse_package_bytes(candidate, modern=False)['members']
    if (parsed != projected or any(parsed[name] != raw for name, raw in members.items() if name != 'index.json') or
            len(candidate) < len(base)):
        raise ValueError('capacity package projection did not preserve the E1.45 members')
    return dict(mode='offline_capacity_only_projection', exact=True, emitted_package=False,
                base_edition='E1.45', base_package_bytes=len(base), base_package_sha256=digest(base),
                added_unique_eaf_files=len(unique_files), added_eaf_bytes=sum(map(len, unique_files.values())),
                projected_package_bytes=len(candidate), package_overhead_bytes=len(candidate) - len(base) - sum(map(len, unique_files.values())),
                soft_budget_bytes=7 * 1024 * 1024, partition_bytes=8 * 1024 * 1024,
                soft_remaining_bytes=7 * 1024 * 1024 - len(candidate),
                partition_remaining_bytes=8 * 1024 * 1024 - len(candidate),
                fits_soft_budget=len(candidate) <= 7 * 1024 * 1024,
                fits_partition=len(candidate) <= 8 * 1024 * 1024,
                includes_existing_profile_and_flat_clip_index=True,
                includes_future_role_graph_schema=False,
                device_package_eligible=False, logical_clip_index=logical_assets)


def original_package_module():
    spec = importlib.util.spec_from_file_location('grok_role_package_capacity', ROOT / 'scripts/audit-expression-package-candidate.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build() -> dict:
    binary, native_build = build_renderer()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'clips').mkdir(exist_ok=True)
    (OUTPUT / 'previews').mkdir(exist_ok=True)
    files, unique_paths, unique_raw, endpoints, expressions = {}, {}, {}, {}, {}
    total_frames = 0
    poster_sheet = Image.new('RGB', (3 * 180, 2 * 180))
    mask = circle_mask(360, 360)
    for state_index, state in enumerate(STATES):
        directory = TEMP / 'raw' / state
        directory.mkdir(parents=True, exist_ok=True)
        native = subprocess.run([str(binary), state, str(directory)],
                                capture_output=True, text=True, check=True, timeout=120)
        native_report = json.loads(native.stdout)
        if (native_report['state'] != state or native_report['shape'] != 'blob' or
                native_report['fps'] != FPS or native_report['mirror'] or native_report['particles'] or
                native_report['renderer_overflows'] or native_report['native_last_tick'] >= 150):
            raise ValueError('native clip contract differs')
        atlas = Image.new('RGB', (10 * 180, 8 * 180))
        offset = 0
        endpoints[state] = {}
        item = dict(label=LABELS[state], upstream_state=state, native=native_report,
                    atlas=f'previews/{state}.png', clips={})
        try:
            for phase, count in PHASES.items():
                raw_path = directory / f'{phase}.gray'
                try:
                    gray, alpha, fps = read_gray_stream(raw_path.read_bytes())
                finally:
                    raw_path.unlink(missing_ok=True)
                if fps != FPS or len(gray) != count or native_report[f'{phase}_frames'] != count:
                    raise ValueError('native phase duration differs')
                clipped = int(np.count_nonzero(alpha[:, ~mask]))
                gray[:, ~mask] = 0
                alpha[:, ~mask] = 0
                wire, encoding = encode_gray_frames(gray, alpha)
                sha = digest(wire)
                files[f'{state}/{phase}'] = wire
                unique_raw.setdefault(sha, (f'{state}/{phase}', wire))
                if sha not in unique_paths:
                    unique_paths[sha] = f'clips/{state}-{phase}.eaf'
                    (OUTPUT / unique_paths[sha]).write_bytes(wire)
                animation = decode_eaf(OUTPUT / unique_paths[sha], fps=FPS)
                expected = expected_rgba(gray, alpha)
                if (animation.bit_depth != 4 or animation.metadata_frame_count != 1 or
                        len(animation.frames) != count):
                    raise ValueError('EAF metadata or image-frame count differs')
                for frame_index, frame in enumerate(animation.frames):
                    actual = np.frombuffer(frame, dtype=np.uint8).reshape(360, 360, 4)
                    if not np.array_equal(actual, expected[frame_index]):
                        raise ValueError(f'EAF pixels differ: {state}/{phase}/{frame_index}')
                    if np.any(actual[~mask]):
                        raise ValueError('decoded pixels extend outside the round screen')
                    if np.count_nonzero(actual[..., 3]) < 20_000:
                        raise ValueError('face unexpectedly disappeared')
                    thumbnail = Image.frombytes('RGBA', (360, 360), frame).convert('RGB').resize(
                        (180, 180), Image.Resampling.LANCZOS)
                    index = offset + frame_index
                    atlas.paste(thumbnail, (index % 10 * 180, index // 10 * 180))
                    if phase == 'loop' and frame_index == 0:
                        poster_sheet.paste(thumbnail, (state_index % 3 * 180, state_index // 3 * 180))
                endpoints[state][phase] = (animation.frames[0], animation.frames[-1])
                item['clips'][phase] = dict(file=unique_paths[sha], sha256=sha, bytes=len(wire), frames=count,
                                           duration_ms=count * 1000 // FPS, atlas_offset=offset,
                                           first_rgba_sha256=frame_digest(animation.frames[0]),
                                           last_rgba_sha256=frame_digest(animation.frames[-1]),
                                           first_adjacent_mean_rgb_delta=boundary_delta(animation.frames, True),
                                           last_adjacent_mean_rgb_delta=boundary_delta(animation.frames, False),
                                           circle_clipped_pixels=clipped, wire=encoding)
                offset += count
                total_frames += count
                del expected, gray, alpha, animation
        finally:
            for phase in PHASES:
                (directory / f'{phase}.gray').unlink(missing_ok=True)
            directory.rmdir()
        atlas.save(OUTPUT / item['atlas'], optimize=True)
        expressions[state] = item
        print(json.dumps(dict(state=state, frames=offset,
                              eaf_bytes=sum(c['bytes'] for c in item['clips'].values())), ensure_ascii=False), flush=True)
    # Only remove stale outputs from this generator's named clips/previews folders.
    expected_files = set(unique_paths.values())
    for path in (OUTPUT / 'clips').glob('*.eaf'):
        if path.relative_to(OUTPUT).as_posix() not in expected_files:
            path.unlink()
    seams = verify_seams(endpoints)
    storage = capacity(files)
    packaged_capacity = package_projection(dict(unique_raw.values()))
    poster_sheet.save(OUTPUT / 'poses.png', optimize=True)
    shutil.copy2(ROOT / 'scripts/grok-role-scheduler.js', OUTPUT / 'scheduler.js')
    (OUTPUT / 'source-notices').mkdir(exist_ok=True)
    for name in ('LICENSE', 'RIGHTS.md', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(LIBRARY / 'source-notices' / name, OUTPUT / 'source-notices' / name)
    manifest = dict(schema='deskbot.composable_role_clips/v1', role_id='grok-round-personal-v1',
                    label='圆团', source=dict(upstream='https://github.com/legendtired/grok-bot-study',
                                            commit=native_build['inputs']['source_commit'],
                                            usage_scope='personal_noncommercial_local_experiment',
                                            notices='source-notices'),
                    fps=FPS, display=dict(width=360, height=360, shape='circle', layout='full_face', mirror=False),
                    expressions=expressions, seams=seams, capacity=storage,
                    capacity_projection=packaged_capacity, image_frames=total_frames,
                    atlas=dict(size=180, columns=10), native_build=native_build,
                    all_frames_match_quantized_rgb565=True, candidate_stage='computer_composable_package',
                    switching=dict(entry_pending='exit_at_pose', loop_pending='exit_at_loop_boundary',
                                   exit_pending='enter_latest_at_home', duplicate='continue',
                                   max_loop_boundary_wait_ms=2450, exit_duration_ms=550,
                                   entry_duration_ms=550),
                    generator_sha256={name: digest((ROOT / name).read_bytes()) for name in GENERATORS},
                    device_operations=[])
    (OUTPUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    template = (ROOT / 'scripts/grok-role-clips.template.html').read_text(encoding='utf-8')
    (OUTPUT / 'index.html').write_text(
        template.replace('__MANIFEST_JSON__', json.dumps(manifest, ensure_ascii=False).replace('</', '<\\/')),
        encoding='utf-8')
    (TEMP / 'raw').rmdir()
    qa = TEMP / 'qa'
    qa.mkdir(exist_ok=True)
    (qa / 'verification.json').write_text(json.dumps(dict(
        result='passed', image_frames=total_frames, exact_eaf_roundtrip=True,
        circle_mask=True, mirror=False, seams=seams, capacity=storage,
        capacity_projection=packaged_capacity,
        motion_note='quintic geometry endpoints plus adjacent-frame deltas; visual review uses the node preview'
    ), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(result='passed', frames=total_frames, capacity=storage,
                          packaged_capacity=packaged_capacity,
                          preview=str(OUTPUT / 'index.html')), ensure_ascii=False), flush=True)
    return manifest


if __name__ == '__main__':
    build()
