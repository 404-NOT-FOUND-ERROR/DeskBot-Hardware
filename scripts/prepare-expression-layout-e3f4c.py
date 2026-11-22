#!/usr/bin/env python3
"""Build a same-source layout comparison from the verified E1.18 snapshot."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_resource_package as package
from protocol.eaf_preview import compose_display_frame, decode_eaf, write_rgba_atlas
from protocol.studio_expression_layout import inspect_layout
from protocol.studio_installed_catalog import validate_manifest

OUTPUT = ROOT / 'artifacts/expression-layout-e3f4c'
DIRECTORY = ROOT / 'artifacts/deskbot-studio-e3d1-20261105/installed-directory.json'
REQUIREMENTS = ROOT / 'protocol/expression-layout-requirements-v1.json'
TEMPLATE = ROOT / 'scripts/expression-layout-preview.html'
SOURCES = ('protocol/studio_expression_layout.py', 'protocol/expression-layout-requirements-v1.json',
           'protocol/eaf_preview.py', 'scripts/prepare-expression-layout-e3f4c.py',
           'scripts/expression-layout-preview.html', 'protocol/studio_installed_catalog.py')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def relative(path):
    return path.relative_to(ROOT).as_posix()


def verified_members():
    raw = package.BASE_IMAGE.read_bytes()
    if sha(raw) != package.BASE_IMAGE_SHA:
        raise ValueError('layout_baseline_image_changed')
    blob = package.extract_package(raw, 0x800000)
    if sha(blob) != package.BASE_PACKAGE_SHA:
        raise ValueError('layout_baseline_package_changed')
    reader, _ = package.load_legacy_tools()
    return reader.parse_package_bytes(blob, modern=False)['members']


def build(output=OUTPUT):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    members = verified_members()
    index = json.loads(members['index.json'])
    manifest = validate_manifest(json.loads(DIRECTORY.read_bytes()))
    if (manifest['package_sha256'] != package.BASE_PACKAGE_SHA
            or manifest['index_sha256'] != sha(members['index.json'])):
        raise ValueError('layout_directory_snapshot_changed')
    requirements = json.loads(REQUIREMENTS.read_bytes())
    if requirements['schema'] != 'deskbot.asset_layout_requirements/v1':
        raise ValueError('layout_requirements_version_changed')
    requirements = {row['resource_id']: row for row in requirements['assets']}
    catalog = package.load_catalog()
    layouts = {row['id']: row for row in manifest['entries'] if row['kind'] == 'layout'}
    eye = next(row for row in index['layout'] if row['name'] == 'eye_anim')
    if {k: layouts['eye_anim'][k] for k in ('align', 'x', 'y', 'mirror')} != dict(
            align=eye['align'], x=eye['x'], y=eye['y'], mirror=eye['anim']['mirror']):
        raise ValueError('layout_index_geometry_changed')
    previews = []
    # Extract only for the decoder. Temporary EAF copies stay inside the project
    # and are removed when decoding finishes; the retained evidence is the atlas.
    with tempfile.TemporaryDirectory(prefix='layout-e3f4c-', dir=ROOT / 'tmp') as scratch:
        for semantic, label in (('eat', '进食'), ('neutral', '平静')):
            entry = next(row for row in manifest['entries'] if row['kind'] == 'expression' and row['id'] == semantic)
            name = entry['resource_id']
            source = next(row for row in index['emoji_collection'] if row['name'] == semantic)
            if source != dict(name=semantic, file=name, eaf=dict(fps=entry['fps'], loop=entry['loop'])):
                raise ValueError('layout_index_binding_changed')
            raw = members[name]
            if len(raw) != entry['bytes'] or sha(raw) != entry['resource_sha256']:
                raise ValueError('layout_resource_bytes_changed')
            plan = inspect_layout(catalog['assets'][name], entry, layouts[entry['layout_id']], requirements[name])
            source_path = Path(scratch) / name
            source_path.write_bytes(raw)
            animation = decode_eaf(source_path, fps=entry['fps'])
            if (animation.width, animation.height, len(animation.frames)) != (
                    plan['requirement']['width'], plan['requirement']['height'], catalog['assets'][name]['frame_count']):
                raise ValueError('layout_decoded_geometry_changed')
            # The current renderer geometry is validated above, then composed by
            # the existing device-matching mirror/clipping model.
            if plan['current'] != dict(layout_id='eye_anim', align='GFX_ALIGN_LEFT_MID', x=10, y=10, mirror='auto'):
                raise ValueError('layout_current_preview_not_supported')
            current = tuple(compose_display_frame(frame, animation.width, animation.height, object_kind='eye')
                            for frame in animation.frames)
            target_kind = 'full_screen' if plan['requirement']['mode'] == 'full_frame' else 'eye'
            target = tuple(compose_display_frame(frame, animation.width, animation.height, object_kind=target_kind)
                           for frame in animation.frames)
            differences = [sum(a != b for a, b in zip(left, right)) for left, right in zip(current, target)]
            preview = dict(semantic_id=semantic, label=label, plan=plan,
                           image_frames=len(animation.frames), stored_frames=animation.stored_frame_count,
                           metadata_frames=animation.metadata_frame_count, fps=animation.frame_rate,
                           sample_frame=max(range(len(differences)), key=differences.__getitem__),
                           comparison='different' if any(differences) else 'identical')
            for kind, frames in (('current', current), ('target', target)):
                path = output / f'{semantic}-{kind}.png'
                atlas = write_rgba_atlas(path, frames, 360, 360)
                preview[kind] = dict(file=path.name, sha256=sha(path.read_bytes()), **atlas)
            previews.append(preview)
    report = dict(schema='deskbot.expression_layout_preview/v1', edition='E3f-4c',
        result='offline_layout_comparison_built',
        sources=dict(image=dict(path=relative(package.BASE_IMAGE), sha256=package.BASE_IMAGE_SHA),
            directory=dict(path=relative(DIRECTORY), sha256=sha(DIRECTORY.read_bytes()), catalog_id=manifest['catalog_id']),
            code={name: sha((ROOT/name).read_bytes()) for name in SOURCES}),
        previews=previews, hardware_accessed=False, device_written=False, firmware_changed=False,
        device_layout='E1.18 shared eye_anim; per-asset routing is the next firmware increment')
    (output/'report.json').write_bytes(package.json_bytes(report))
    data = json.dumps(report, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    template = TEMPLATE.read_text(encoding='utf-8')
    if template.count('__LAYOUT_DATA__') != 1:
        raise ValueError('layout_preview_template_changed')
    (output/'index.html').write_text(template.replace('__LAYOUT_DATA__', data), encoding='utf-8')
    return report


if __name__ == '__main__':
    report = build()
    print(json.dumps(dict(result=report['result'], frames={r['semantic_id']: r['image_frames'] for r in report['previews']},
                          device_written=False)))
