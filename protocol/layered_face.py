"""Pinned AFM1 -> native 360 DFM1, with independently addressable sprite rows.

DFM1 uses a 64-byte header, 24-byte sprites, 48-byte clips and 100-byte
keyframes (five 20-byte refs). All integers are little endian. Rectangles
retain fractional 0.75 coordinates; row RLE never crosses a row boundary.
"""
from pathlib import Path
import hashlib
import json
import math
import struct

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'assets/layered-face/source'
COMMIT = '22bb4746c0d115dea62b15ff5adf25725cb7d512'
AFM_SHA = 'd749743e83221e5b751c0f86ee2b5aad0bfba7d2bdd182ff599560bb38892593'
CONTINUITY_SOURCE = ROOT / 'assets/layered-face/continuity-20261123'
CONTINUITY_SHA = 'f97deb06407f1549c142e81da1455a9380c9649f7fda74eff31ac21ea565aa7e'
MANIFEST_SHA = {AFM_SHA: '8a4a623d465065c4eb2c1e7a2cb2486b83bccd4e32748fd942a2cc41b40d664b',
    CONTINUITY_SHA: '1289d30cd40fcdd03ac22bcb9f359fb8187df2e082968a1b783bb43af71b3fde'}
SIDE, FPS, TRANSITION_MS = 360, 25, 360
CONTINUOUS = {'neutral', 'idle', 'listening', 'thinking', 'sleepy'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def resolve_mirrors(names, aliases, overrides):
    """Visual aliases share orientation, while their semantic timing stays separate."""
    if any(k not in names or type(v) is not bool for k, v in overrides.items()):
        raise ValueError('unknown_mirror_override')
    resolved = {name: overrides.get(name, False) for name in names}
    for alias, target in aliases.items():
        value = overrides.get(target, overrides.get(alias, False))
        if alias in overrides and target in overrides and overrides[alias] != overrides[target]:
            raise ValueError('conflicting_alias_mirror')
        resolved[alias] = resolved[target] = value
    return resolved


def decode_afm(raw, sprite):
    """Reject partial pairs, overlong runs and palette indices before resizing."""
    w, h, offset, size = (sprite[k] for k in ('w', 'h', 'offset', 'bytes'))
    if not (0 < w <= 480 and 0 < h <= 480 and offset >= 0 and 0 < size <= len(raw)-16-offset):
        raise ValueError('source_sprite_bounds')
    data, pixels, at = raw[16+offset:16+offset+size], bytearray(), 0
    palette = sprite['palette']
    while at < len(data):
        token = data[at]; at += 1
        if palette is None:
            count, value = (token & 15)+1, token >> 4
            if count == 16:
                if at == len(data): raise ValueError('source_partial_run')
                count += data[at]; at += 1
        else:
            if at == len(data): raise ValueError('source_partial_pair')
            count, value = token, data[at]; at += 1
            if value >= len(palette): raise ValueError('source_palette_index')
        if not count or len(pixels)+count > w*h: raise ValueError('source_run_bounds')
        pixels.extend(bytes([value])*count)
    if len(pixels) != w*h or sha(pixels) != sprite['sha256']:
        raise ValueError('source_pixel_digest')
    return bytes(pixels)


def row_encode(pixels, w, h, channels):
    offsets, payload = [], bytearray()
    for y in range(h):
        offsets.append(len(payload))
        row = pixels[y*w*channels:(y+1)*w*channels]
        x = 0
        while x < w:
            value = row[x*channels:(x+1)*channels]
            n = 1
            limit = 271 if channels == 1 else 255
            while x+n < w and n < limit and row[(x+n)*channels:(x+n+1)*channels] == value:
                n += 1
            if channels == 1:
                payload.append((value[0] << 4) | min(15, n-1))
                if n >= 16: payload.append(n-16)
            else:
                payload.extend(bytes([n])+value)
            x += n
    offsets.append(len(payload))
    return struct.pack('<'+'I'*len(offsets), *offsets), bytes(payload)


def build(source=SOURCE, mirror=None, expected_sha=AFM_SHA):
    source = Path(source)
    manifest_raw = (source/'manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    filename = manifest.get('binary', 'face-motion.afm')
    if Path(filename).name != filename or expected_sha not in (AFM_SHA, CONTINUITY_SHA):
        raise ValueError('pinned_source_required')
    raw = (source/filename).read_bytes()
    if (sha(manifest_raw) != MANIFEST_SHA[expected_sha] or sha(raw) != expected_sha
            or manifest['binary_sha256'] != expected_sha or raw[:4] != b'AFM1'
            or struct.unpack_from('<III', raw, 4) != (1, len(manifest['sprites']), len(raw)-16)
            or manifest['canvas'] != 480 or manifest['fps'] != FPS):
        raise ValueError('pinned_source_changed')
    from PIL import Image
    mirror = mirror or {}
    sprites, refs, seen, planes = [], {}, {}, []
    reflected_hits = reflected_refs = 0
    for i, s in enumerate(manifest['sprites']):
        indices = decode_afm(raw, s)
        w, h = max(1, round(s['w']*.75)), max(1, round(s['h']*.75))
        if s['palette'] is None:
            image = Image.frombytes('L', (s['w'],s['h']), bytes(v*17 for v in indices))
            pixels = bytes((v*15+127)//255 for v in image.resize((w,h), Image.Resampling.LANCZOS).tobytes())
            channels = 1
        else:
            rgba = bytes(v for index in indices for v in s['palette'][index])
            # Pillow RGBa explicitly resizes premultiplied channels: transparent
            # borders cannot introduce dark/colored fringes into the accents.
            image = Image.frombytes('RGBA',(s['w'],s['h']),rgba).convert('RGBa')
            pixels = image.resize((w,h),Image.Resampling.LANCZOS).tobytes()
            channels = 4
            # Filter overshoot is clamped to the premultiplied-alpha domain.
            pixels = bytes(v if j%4 == 3 else min(v,pixels[j-j%4+3]) for j,v in enumerate(pixels))
        reflected = b''.join(b''.join(pixels[(y*w+x)*channels:(y*w+x+1)*channels]
                                    for x in range(w-1,-1,-1)) for y in range(h))
        flip = reflected < pixels
        canonical = reflected if flip else pixels
        key = (w,h,channels,canonical)
        if key not in seen:
            seen[key] = len(sprites)
            rows, encoded = row_encode(canonical,w,h,channels)
            sprites.append((w,h,channels,rows,encoded))
            planes.append(canonical)
        else:
            reflected_hits += int(flip)
        refs[i] = (seen[key], int(flip))
        reflected_refs += int(flip)
    keys, clips = [], []
    for semantic, clip in manifest['clips'].items():
        first = len(keys)
        for frame in clip['keyframes']:
            key = bytearray()
            for ref in [*frame['layers'],frame['accent']]:
                if ref is None:
                    key.extend(struct.pack('<HBB4f',65535,0,0,0,0,0,0))
                else:
                    index, flip = refs[ref[0]]
                    key.extend(struct.pack('<HBB4f',index,flip ^ ref[5],0,*(v*.75 for v in ref[1:5])))
            keys.append(bytes(key))
        clips.append((semantic,first,len(clip['keyframes']),clip['body_frames'],clip['color']))
    # Resolve visual aliases once. They share all keyframes while retaining the
    # requesting semantic's existing continuous/one-shot contract.
    aliases = manifest.get('aliases', {'neutral': 'idle'})
    for semantic, target in aliases.items():
        original = next((c for c in clips if c[0] == target), None)
        if original is None or any(c[0] == semantic for c in clips):
            raise ValueError('invalid_source_alias')
        clips.append((semantic, *original[1:]))
    effective_mirrors = resolve_mirrors([c[0] for c in clips], aliases, mirror)
    sprite_at = 64
    clip_at = sprite_at+24*len(sprites)
    key_at = clip_at+48*len(clips)
    payload_at = key_at+100*len(keys)
    records, payload = bytearray(), bytearray()
    for w,h,channels,rows,encoded in sprites:
        row_at = payload_at+len(payload); payload.extend(rows)
        data_at = payload_at+len(payload); payload.extend(encoded)
        records.extend(struct.pack('<HHB3xIIII',w,h,channels,row_at,data_at,len(encoded),w*h*channels))
    clip_bytes = b''.join(struct.pack('<32sIIHBB3Bx',name.encode(),first,count,body,
        name in CONTINUOUS,effective_mirrors[name],*bytes.fromhex(color[1:]))
        for name,first,count,body,color in clips)
    flags = int(manifest.get('clip_pupils', False)) | (int(manifest.get('exit_from_last', False)) << 1)
    header = struct.pack('<4sHHHH13I',b'DFM1',1,SIDE,FPS,TRANSITION_MS,payload_at+len(payload),
        len(sprites),sprite_at,len(clips),clip_at,len(keys),key_at,payload_at,len(payload),flags,0,0,0)
    result = header+records+clip_bytes+b''.join(keys)+payload
    report = dict(schema='deskbot.layered_face_build/v1',source_commit=COMMIT if expected_sha==AFM_SHA else None,
        source_revision='local-contour-20261123' if expected_sha==CONTINUITY_SHA else COMMIT,source_afm_sha256=expected_sha,
        source_files={p.name:sha(p.read_bytes()) for p in sorted(source.iterdir()) if p.is_file()},
        binary_sha256=sha(result),binary_bytes=len(result),canvas=SIDE,master_canvas=480,fps=FPS,
        transition_ms=TRANSITION_MS,semantics=len(clips),keyframes=len(keys),sprites=len(sprites),
        exact_reuses=len(manifest['sprites'])-len(sprites),canonical_reflected=reflected_refs,
        reflected_reuse_hits=reflected_hits,mirror_overrides=mirror,aliases=aliases,flags=flags,
        snapshot_workspace_bytes=2*SIDE*SIDE*8,device_written=False)
    return result, report


def package(binary, report):
    """Keep shared fonts/icons/wake models, and one tiny EAF for old UI owners."""
    from protocol import studio_resource_package as p
    from protocol import studio_mirror_options as m
    parser, packer = p.load_legacy_tools()
    baseline = ROOT/'artifacts/airboat-role-20261121/package/assets-8m.bin'
    before = parser.parse_package_bytes(p.extract_package(baseline.read_bytes(),0x800000),modern=False)['members']
    index = json.loads(before['index.json'])
    neutral = next(row['file'] for row in index['emoji_collection'] if row['name'] == 'neutral')
    shared = {index['srmodels'],index['text_font'],*[row['file'] for row in index['icon_collection']]}
    members = {name:raw for name,raw in before.items() if (not name.endswith('.eaf') or name in shared) and
               name not in ('deskbot-roles.json',m.FILE)}
    # Only the small idle legacy clip is retained for startup and dialogs;
    # the mounted role uses DFM1, never these EAF frames as its body animation.
    members['face_idle.eaf'] = before[neutral]
    for row in index['emoji_collection']:
        row['file'] = 'face_idle.eaf'
        row['eaf'].update(fps=20,loop=True)
    members['index.json'] = p.json_bytes(index)
    layout = dict(schema='deskbot.asset_layouts/v1',role_id='role-pack',assets=[dict(
        resource_id='face_idle.eaf',resource_sha256=sha(members['face_idle.eaf']),width=360,height=360,
        layout_id='full_frame',mirror=False,source='automatic')])
    layout_raw = p.canonical(layout)
    graph = dict(schema='deskbot.layered_role/v1',package_id='role-pack',
        catalog_id=report['binary_sha256'],default_role_id='airboat-face-v1',
        layout=dict(file=m.FILE,sha256=sha(layout_raw),role_id='role-pack'),
        renderer=dict(file='face-motion.dfm',sha256=sha(binary),canvas=SIDE,format='DFM1',
            source_commit=report.get('source_commit'),source_afm_sha256=report['source_afm_sha256']),
        required_capabilities=['layered_face_v1','current_pose_transition_v1'],
        roles=[dict(id='airboat-face-v1',name='新角色 · 高清分层',expressions=29)])
    graph_raw = p.canonical(graph)
    members.update({'face-motion.dfm':binary,m.FILE:layout_raw,'deskbot-roles.json':graph_raw})
    dependencies = [index['srmodels'],index['text_font']]+[row['file'] for key in
        ('emoji_collection','icon_collection') for row in index[key]]
    if any(name not in members for name in dependencies): raise ValueError('shared_asset_missing')
    data = packer.build_legacy_package(members)
    if len(data) > 0x800000: raise ValueError('resource_partition_exceeded')
    if parser.parse_package_bytes(data,modern=False)['members'] != members: raise ValueError('package_round_trip')
    files = {'assets-8m.bin':data+b'\xff'*(0x800000-len(data)),'deskbot-roles.json':graph_raw,
             m.FILE:layout_raw,'face-motion.dfm':binary}
    result = dict(report,package_bytes=len(data),partition_free_bytes=0x800000-len(data),
        image_sha256=sha(files['assets-8m.bin']),graph_sha256=sha(graph_raw),role_ids=['airboat-face-v1'],
        required_capabilities=graph['required_capabilities'],download_sha256={n:sha(raw) for n,raw in files.items()})
    return files,result
