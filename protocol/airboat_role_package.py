"""Build one complete resident role while retaining the original package on the PC."""
from copy import deepcopy
import json

from protocol import studio_resource_package as p
from protocol import studio_mirror_options as mirrors
from protocol.studio_role_package import GRAPH_FILE, PHASES


def assemble_selected(library, draft):
    role = next(r for r in draft['roles'] if r['id'] == draft['selected_role_id'])
    original = role['mode'] == 'single'
    # Fonts, icons and wake-word models are shared runtime dependencies of the
    # index. Removing a character removes only its expression EAFs.
    members = dict(library.before) if original else {name:raw for name,raw in library.before.items()
        if not name.endswith('.eaf') or name == 'listen.eaf'}
    records = {r['resource_id']: deepcopy(r) for r in library.base_layout['assets']
               if r['resource_id'] in members}
    bindings = deepcopy(role['bindings'])
    if not original:
        # Keep all authored expressions resident, beyond the six daily bindings.
        for expression_id, semantic in library.catalog['complete_expressions'].items():
            bindings.setdefault(semantic, dict(expression_id=expression_id, fps=20, loop=True))
    graph_expressions, slots = {}, {}
    for semantic, binding in bindings.items():
        expression = library.catalog['expressions'][binding['expression_id']]
        resources = {}
        for phase, asset_id in expression['resources'].items():
            asset = library.catalog['assets'][asset_id]
            raw = library.blobs[asset_id]
            mirror = role['mirror_overrides'].get(asset_id, asset['mirror'])
            layout = 'eye_anim' if mirror else 'full_frame'
            key = (asset['sha256'], layout)
            name = slots.get(key)
            if name is None:
                # Preserve original physical names/layouts when possible.
                name = asset_id if original and asset_id in records and records[asset_id]['mirror'] == mirror else (
                    'r_'+asset['sha256'][:20]+('_m.eaf' if mirror else '_f.eaf'))
                if name in members and members[name] != raw:
                    raise ValueError('资源摘要前缀重复')
                members[name], slots[key] = raw, name
                records[name] = dict(resource_id=name, resource_sha256=asset['sha256'], width=asset['width'],
                    height=asset['height'], layout_id=layout, mirror=mirror,
                    source='manual' if asset_id in role['mirror_overrides'] else 'automatic')
            resources[phase] = dict(resource_id=name, sha256=asset['sha256'], frames=asset['frames'],
                layout_id=layout, mirror=mirror, mirror_source=records[name]['source'], source_asset_id=asset_id,
                tail_hold_ticks=asset.get('tail_hold_ticks', 0))
        graph_expressions[semantic] = dict(mode=expression['mode'], fps=binding['fps'], loop=binding['loop'],
            on_complete='repeat' if binding['loop'] else 'hold_last_frame', resources=resources)
    if not original:
        # Legacy lookups use the installed role's loops, so a cloud emotion never
        # falls back to a removed character. Listening retains its own UI owner.
        index = json.loads(members['index.json'])
        for row in index['emoji_collection']:
            semantic = {'scared':'shocked', 'sleep':'sleepy'}.get(row['name'], row['name'])
            expression = graph_expressions.get(semantic, graph_expressions['neutral'])
            row['file'] = expression['resources']['loop']['resource_id']
            row['eaf']['fps'], row['eaf']['loop'] = expression['fps'], True
        members['index.json'] = p.json_bytes(index)
    # Verify all index dependencies, including non-expression resources.
    index = json.loads(members['index.json'])
    referenced = [index['srmodels'],index['text_font']]
    referenced += [r['file'] for key in ('emoji_collection','icon_collection') for r in index[key]]
    if any(name not in members for name in referenced):
        raise ValueError('角色包缺少界面或语音公共资源')
    layout = dict(schema='deskbot.asset_layouts/v1', role_id='role-pack',
                  assets=[records[n] for n in sorted(records)])
    layout_raw = p.canonical(layout)
    graph = dict(schema='deskbot.role_graph/v1', package_id='role-pack', catalog_id=library.catalog['catalog_id'],
        default_role_id=role['id'], layout=dict(file=mirrors.FILE, role_id='role-pack', sha256=p.sha(layout_raw)),
        required_capabilities=['asset_layout_manifest_v1','role_graph_v1','role_phase_scheduler_v1'],
        transition=dict(policy='exit_home_entry',pending='latest_target_only',interruption='finish_current_phase'),
        roles=[dict(id=role['id'], name=role['name'], expressions=graph_expressions)])
    graph_raw = p.canonical(graph)
    if len(layout['assets']) > 128 or len(layout_raw) > 32768 or len(graph_raw) > 65536:
        raise ValueError('角色图或布局超出播放器容量')
    members[mirrors.FILE], members[GRAPH_FILE] = layout_raw, graph_raw
    package = library.packer.build_legacy_package(members)
    limit = library.target['assets']['bytes']
    if len(package) > limit:
        raise ValueError('完整角色超出 8 MiB 资源区')
    image = package + b'\xff'*(limit-len(package))
    unpacked = library.parser.parse_package_bytes(package, modern=False)['members']
    if unpacked != members or library.packer.build_legacy_package(unpacked) != package:
        raise ValueError('角色包往返核对失败')
    eafs = [n for n in members if n.endswith('.eaf')]
    report = dict(schema='deskbot.role_package_report/v1', result='offline_package_verified',
        draft_sha256=p.sha(p.canonical(draft)), package_sha256=p.sha(package), image_sha256=p.sha(image),
        image_bytes=limit, package_bytes=len(package), base_bytes=0 if not original else len(library.base_package),
        added_eaf_bytes=sum(len(members[n]) for n in eafs), added_eaf_files=len(eafs),
        config_bytes=len(layout_raw)+len(graph_raw), table_growth_bytes=0,
        soft_limit_bytes=library.target['assets']['soft_limit_bytes'], partition_bytes=limit,
        soft_free_bytes=library.target['assets']['soft_limit_bytes']-len(package), partition_free_bytes=limit-len(package),
        role_ids=[role['id']], resident_expressions=len(graph_expressions), installation='selected_role_only',
        layout_resources=len(layout['assets']), all_baseline_members_preserved=original,
        original_role_retained_on_computer=True, unpack_verified=True, device_written=False,
        device_compatible=False, device_stage='安装配套播放器后可使用', required_capabilities=graph['required_capabilities'])
    files = {'assets-8m.bin':image, GRAPH_FILE:graph_raw,'deskbot-layout.json':layout_raw,
             'role-draft.json':p.json_bytes(draft)}
    report['download_sha256'] = {n:p.sha(raw) for n,raw in files.items()}
    return report, files
