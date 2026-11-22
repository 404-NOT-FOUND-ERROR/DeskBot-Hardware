"""Unified role authoring and deterministic E1.45-based packages; no device I/O."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from protocol import studio_resource_package as packages
from protocol import studio_mirror_options as mirrors
from protocol.role_activation import load_roles, SEMANTICS, REGISTRY
from protocol.studio_role_layout import load_requirements, playback

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin'
BASE_SHA = 'e5ac78a187866cff05a16e5fd223c934a45d6cb4bf13dfcd1766cc93db805748'
SCHEMA = 'deskbot.role_pack_draft/v1'
GRAPH_FILE = 'deskbot-roles.json'
MAX_REQUEST = 32 * 1024
NAME = re.compile(r'[A-Za-z0-9_]+\.eaf')
PHASES = ('entry', 'loop', 'exit')


def exact(value, keys):
    return type(value) is dict and set(value) == set(keys)


class Library:
    def __init__(self):
        self.parser, self.packer = packages.load_legacy_tools()
        self.target = packages.load_target()
        raw = BASE.read_bytes()
        if packages.sha(raw) != BASE_SHA:
            raise ValueError('E1.45 资源基线摘要不同')
        self.base_package = packages.extract_package(raw, self.target['assets']['bytes'])
        self.before = self.parser.parse_package_bytes(self.base_package, modern=False)['members']
        if self.packer.build_legacy_package(self.before) != self.base_package:
            raise ValueError('E1.45 资源包往返校验失败')
        old = packages.load_catalog()
        registry = json.loads(REGISTRY.read_bytes())
        self.selected_only = registry.get('installation') == 'selected_role_only'
        self.prefix = registry['composable'].get('asset_prefix', 'grok')
        runtime, self.media = load_roles()
        requirements = load_requirements(old)
        assets, expressions, self.blobs = {}, {}, {}
        for name, asset in old['assets'].items():
            if asset['object_kind'] == 'listen':
                continue
            raw = self.before[name]
            if packages.sha(raw) != asset['deployed_sha256']:
                raise ValueError('原角色资源摘要不同：' + name)
            layout = playback(old, 'neutral', name, requirements)['layout_id']
            assets[name] = dict(id=name, name=name, label=asset['label'], mode='single',
                sha256=packages.sha(raw), bytes=len(raw), frames=asset['frame_count'],
                width=asset['frame_width'], height=asset['frame_height'], mirror=layout == 'eye_anim',
                tail_hold_ticks=asset.get('tail_hold_ticks', 1),
                source_asset_id=name, atlas_format=dict(columns=asset['columns'],
                    width=asset['frame_width'], height=asset['frame_height']), atlas_offset=0)
            self.blobs[name] = raw
            expressions[name] = dict(id=name, label=asset['label'], mode='single',
                resources=dict(single=name), fps=asset['fps'], loop=asset['loop'])
        manifest_path = ROOT / registry['composable']['manifest']
        manifest = json.loads(manifest_path.read_bytes())
        for state, expression in manifest['expressions'].items():
            self.media['role-media/' + state + '.png'] = manifest_path.parent / expression['atlas']
            ids = {}
            for phase in PHASES:
                clip = expression['clips'][phase]
                name = f'{self.prefix}_{state}_{phase}.eaf'
                raw = (manifest_path.parent / clip['file']).read_bytes()
                if packages.sha(raw) != clip['sha256'] or len(raw) != clip['bytes']:
                    raise ValueError('角色片段摘要不同')
                assets[name] = dict(id=name, name=Path(clip['file']).name, mode='composable',
                    label=state + ' · ' + phase, sha256=clip['sha256'], bytes=len(raw),
                    frames=clip['frames'], width=360, height=360, mirror=False,
                    atlas='role-media/' + state + '.png', atlas_offset=clip['atlas_offset'],
                    atlas_format=dict(columns=manifest['atlas']['columns'], width=manifest['atlas']['size'],
                                      height=manifest['atlas']['size']),
                    first_rgba_sha256=clip['first_rgba_sha256'], last_rgba_sha256=clip['last_rgba_sha256'])
                self.blobs[name] = raw
                ids[phase] = name
            label = expression['label']
            expressions[self.prefix + '-' + state] = dict(id=self.prefix + '-' + state, label=label, mode='composable',
                resources=ids, fps=manifest['fps'], loop=True)
        for name, asset in assets.items():
            info = self.parser.parse_eaf_bytes(self.blobs[name])
            if (info['image_frame_count'] != asset['frames'] or info['dimensions'] != [
                    dict(width=asset['width'], height=asset['height'])]):
                raise ValueError('EAF 帧数或尺寸不同：' + name)
        initial_roles = []
        for role in runtime['roles']:
            mode = next(iter(role['expressions'].values()))['mode']
            bindings = {}
            for semantic, item in role['expressions'].items():
                bindings[semantic] = dict(expression_id=item['resource_id'] if mode == 'single' else self.prefix + '-' + item['state'],
                                          fps=item['fps'], loop=item['loop'])
            initial_roles.append(dict(id=role['id'], name=role['name'], mode=mode,
                                      bindings=bindings, mirror_overrides={}))
        # Reserve original layouts before assigning role variants: legacy index stays byte-identical.
        index = json.loads(self.before['index.json'])
        old_role = dict(id='role-pack', mirror_overrides={}, bindings={row['name']: row['file']
                       for row in index['emoji_collection']} | {'listen': 'listen.eaf'})
        self.base_layout = mirrors.manifest(old_role, old)
        self.catalog = dict(schema='deskbot.role_pack_catalog/v1', semantics=list(SEMANTICS),
            default_role_id=runtime['default_role_id'],
            labels={s: runtime['roles'][0]['expressions'][s]['label'] for s in SEMANTICS},
            assets=assets, expressions=expressions,
            roles=[dict(id=r['id'], name=r['name'], mode=r['mode']) for r in initial_roles],
            base_image_sha256=BASE_SHA, base_package_bytes=len(self.base_package),
            partition_bytes=self.target['assets']['bytes'], soft_limit_bytes=self.target['assets']['soft_limit_bytes'])
        self.catalog['installation'] = registry.get('installation', 'all_roles')
        self.catalog['complete_expressions'] = {self.prefix + '-' + state: state for state in manifest['expressions']}
        self.catalog['catalog_id'] = packages.sha(packages.canonical(self.catalog))
        self.initial = dict(schema=SCHEMA, catalog_id=self.catalog['catalog_id'],
            selected_role_id=runtime['default_role_id'], roles=initial_roles)
        self.validate(self.initial)

    def validate(self, draft):
        cat = self.catalog
        if (not exact(draft, ('schema', 'catalog_id', 'selected_role_id', 'roles'))
                or draft['schema'] != SCHEMA or draft['catalog_id'] != cat['catalog_id']
                or type(draft['roles']) is not list or len(draft['roles']) != 2):
            raise ValueError('请选择当前目录的两角色草稿')
        expected = {r['id']: r for r in cat['roles']}
        ids = set()
        for role in draft['roles']:
            if (not exact(role, ('id', 'name', 'mode', 'bindings', 'mirror_overrides'))
                    or type(role['id']) is not str or role['id'] not in expected or role['id'] in ids
                    or role['mode'] != expected[role['id']]['mode']):
                raise ValueError('角色编号、类型或数量不同')
            ids.add(role['id'])
            if (type(role['name']) is not str or not role['name'].strip() or len(role['name']) > 40
                    or any(ord(c) < 32 for c in role['name'])):
                raise ValueError('角色名称应为 1–40 个可显示字符')
            overrides = role['mirror_overrides']
            if type(overrides) is not dict or len(overrides) > len(cat['assets']):
                raise ValueError('镜像选项格式无效')
            for name, value in overrides.items():
                if (name not in cat['assets'] or cat['assets'][name]['mode'] != role['mode']
                        or type(value) is not bool):
                    raise ValueError('镜像选项应对应角色的已登记资源')
            if not exact(role['bindings'], SEMANTICS):
                raise ValueError('请连接六项日常语义')
            home = set()
            for semantic, binding in role['bindings'].items():
                if (not exact(binding, ('expression_id', 'fps', 'loop'))
                        or type(binding['expression_id']) is not str
                        or binding['expression_id'] not in cat['expressions']):
                    raise ValueError('表情引用不在素材目录中：' + semantic)
                item = cat['expressions'][binding['expression_id']]
                if item['mode'] != role['mode']:
                    raise ValueError('请选用同类型的完整动画组合')
                if type(binding['fps']) is not int or not 1 <= binding['fps'] <= 60 or type(binding['loop']) is not bool:
                    raise ValueError('帧率应为 1–60，循环应为布尔值')
                if item['mode'] == 'composable':
                    if not binding['loop']:
                        raise ValueError('三段动画使用持续循环，切换时退出')
                    entries = [cat['assets'][item['resources'][p]] for p in PHASES]
                    modes = {overrides.get(a['id'], a['mirror']) for a in entries}
                    if len(modes) != 1:
                        raise ValueError('请将进入、持续、退出三段设为相同镜像')
                    mode = next(iter(modes))
                    entry, loop, exit_ = entries
                    if not (entry['last_rgba_sha256'] == loop['first_rgba_sha256'] ==
                            loop['last_rgba_sha256'] == exit_['first_rgba_sha256']):
                        raise ValueError('三段动画接点不同')
                    # Equal endpoint pixels transformed by an equal layout remain equal, across states too.
                    home.update((a[edge], mode) for a, edge in ((entry, 'first_rgba_sha256'), (exit_, 'last_rgba_sha256')))
            if len(home) > 1:
                raise ValueError('请统一角色各表情的镜像，使共享姿态连续')
            if self.selected_only and role['mode'] == 'composable':
                complete = {overrides.get(a['id'], a['mirror']) for a in cat['assets'].values()
                            if a['mode'] == 'composable'}
                if len(complete) != 1:
                    raise ValueError('请统一完整角色所有表情的镜像选项')
        if type(draft['selected_role_id']) is not str or draft['selected_role_id'] not in ids:
            raise ValueError('当前编辑角色不存在')
        return deepcopy(draft)

    def assemble(self, draft):
        draft = self.validate(draft)
        if self.selected_only:
            from protocol.airboat_role_package import assemble_selected
            return assemble_selected(self, draft)
        members, layout = dict(self.before), deepcopy(self.base_layout)
        slots, records = {}, {row['resource_id']: row for row in layout['assets']}
        for row in layout['assets']:
            slots[(row['resource_sha256'], row['layout_id'])] = row['resource_id']
        graph_roles = []
        for role in sorted(draft['roles'], key=lambda r: r['id']):
            bindings = {}
            for semantic in SEMANTICS:
                binding = role['bindings'][semantic]
                expression = self.catalog['expressions'][binding['expression_id']]
                resources = {}
                for phase, asset_id in expression['resources'].items():
                    asset = self.catalog['assets'][asset_id]
                    manual = asset_id in role['mirror_overrides']
                    mirror = role['mirror_overrides'].get(asset_id, asset['mirror'])
                    mode = 'eye_anim' if mirror else 'full_frame'
                    key = (asset['sha256'], mode)
                    resource = slots.get(key)
                    if resource is None:
                        # Different layouts use distinct physical members, even for equal pixel bytes.
                        resource = 'r_' + asset['sha256'][:20] + ('_m.eaf' if mirror else '_f.eaf')
                        if resource in members:
                            raise ValueError('资源短摘要冲突，请扩展命名方案')
                        members[resource] = self.blobs[asset_id]
                        records[resource] = dict(resource_id=resource, resource_sha256=asset['sha256'],
                            width=asset['width'], height=asset['height'], layout_id=mode, mirror=mirror,
                            source='manual' if manual else 'automatic')
                        slots[key] = resource
                    record = records[resource]
                    resources[phase] = dict(resource_id=resource, sha256=asset['sha256'],
                        frames=asset['frames'], layout_id=mode, mirror=mirror,
                        mirror_source='manual' if manual else 'automatic', source_asset_id=asset_id,
                        tail_hold_ticks=asset.get('tail_hold_ticks', 0))
                    if record['width'] != asset['width'] or record['height'] != asset['height']:
                        raise ValueError('共享资源的尺寸不同')
                bindings[semantic] = dict(mode=expression['mode'], fps=binding['fps'], loop=binding['loop'],
                    on_complete='hold_last_frame' if not binding['loop'] else 'repeat', resources=resources)
            graph_roles.append(dict(id=role['id'], name=role['name'], expressions=bindings))
        layout['assets'] = [records[n] for n in sorted(records)]
        if len(layout['assets']) > 64:
            raise ValueError('布局清单超过 64 项，请减少独立资源')
        for row in layout['assets']:
            if not NAME.fullmatch(row['resource_id']) or len(row['resource_id']) > 31:
                raise ValueError('资源名应为 31 字符以内的 EAF 文件名')
        layout_raw = packages.json_bytes(layout)
        if len(layout_raw) > 32768:
            raise ValueError('布局清单超过 32 KiB')
        graph = dict(schema='deskbot.role_graph/v1', package_id='role-pack',
            catalog_id=self.catalog['catalog_id'], default_role_id=self.catalog['default_role_id'],
            layout=dict(file=mirrors.FILE, role_id='role-pack', sha256=packages.sha(layout_raw)),
            required_capabilities=['asset_layout_manifest_v1', 'role_graph_v1', 'role_phase_scheduler_v1'],
            transition=dict(policy='exit_home_entry', pending='latest_target_only', interruption='finish_current_phase'),
            roles=graph_roles)
        graph_raw = packages.json_bytes(graph)
        if len(graph_raw) > 32768:
            raise ValueError('角色图超过 32 KiB')
        members[mirrors.FILE], members[GRAPH_FILE] = layout_raw, graph_raw
        package = self.packer.build_legacy_package(members)
        if len(package) > self.target['assets']['soft_limit_bytes']:
            raise ValueError('角色包超过 7 MiB 建议容量，请减少独立资源或镜像变体')
        image = package + b'\xff' * (self.target['assets']['bytes'] - len(package))
        parsed = self.parser.parse_package_bytes(packages.extract_package(image, len(image)), modern=False)['members']
        if (parsed != members or self.packer.build_legacy_package(parsed) != package
                or any(parsed[n] != raw for n, raw in self.before.items())):
            raise ValueError('生成包的原资源或往返核对失败')
        count = int.from_bytes(package[:4], 'little')
        offsets = {}
        for i in range(count):
            at = 12 + i * 44
            name = package[at:at+32].split(b'\0', 1)[0].decode('utf-8')
            size = int.from_bytes(package[at+32:at+36], 'little')
            offset = int.from_bytes(package[at+36:at+40], 'little')
            offsets[name] = (size, offset)
        # Verify actual extracted references against both graph and layout, beyond the draft model.
        extracted_layout = {r['resource_id']: r for r in json.loads(parsed[mirrors.FILE])['assets']}
        extracted_graph = json.loads(parsed[GRAPH_FILE])
        for role in extracted_graph['roles']:
            for expression in role['expressions'].values():
                for item in expression['resources'].values():
                    row = extracted_layout[item['resource_id']]
                    raw = parsed[item['resource_id']]
                    if (packages.sha(raw) != item['sha256'] or row['resource_sha256'] != item['sha256']
                            or row['mirror'] != item['mirror'] or row['layout_id'] != item['layout_id']):
                        raise ValueError('角色图与布局、资源内容不一致')
        layout_groups = {}
        for row in extracted_layout.values():
            layout_groups.setdefault(row['resource_sha256'], {}).setdefault(row['layout_id'], []).append(row['resource_id'])
        for modes in layout_groups.values():
            for left, right in (('eye_anim', 'full_frame'),):
                for a in modes.get(left, ()):
                    for b in modes.get(right, ()):
                        if a in offsets and b in offsets and offsets[a][1] == offsets[b][1]:
                            raise ValueError('不同镜像布局必须占用独立资源区间')
        new_names = sorted(set(members) - set(self.before) - {GRAPH_FILE, mirrors.FILE})
        report = dict(schema='deskbot.role_package_report/v1', result='offline_package_verified',
            draft_sha256=packages.sha(packages.canonical(draft)), package_sha256=packages.sha(package),
            image_sha256=packages.sha(image), image_bytes=len(image), package_bytes=len(package),
            base_bytes=len(self.base_package), added_eaf_bytes=sum(len(members[n]) for n in new_names),
            config_bytes=len(layout_raw) + len(graph_raw), table_growth_bytes=(len(members) - len(self.before))*46,
            soft_limit_bytes=self.target['assets']['soft_limit_bytes'], partition_bytes=len(image),
            soft_free_bytes=self.target['assets']['soft_limit_bytes']-len(package), partition_free_bytes=len(image)-len(package),
            role_ids=[r['id'] for r in graph_roles], layout_resources=len(layout['assets']), added_eaf_files=len(new_names),
            all_baseline_members_preserved=True, unpack_verified=True, device_written=False,
            device_compatible=False, device_stage='设备角色图与三段调度接入后安装',
            required_capabilities=graph['required_capabilities'])
        report['download_sha256'] = {name: packages.sha(raw) for name, raw in {
            'assets-8m.bin': image, 'deskbot-roles.json': graph_raw,
            'deskbot-layout.json': layout_raw, 'role-draft.json': packages.json_bytes(draft)}.items()}
        return report, {'assets-8m.bin': image, 'deskbot-roles.json': graph_raw,
                        'deskbot-layout.json': layout_raw, 'role-draft.json': packages.json_bytes(draft)}


class PackageStore:
    """Keep one immutable directory per complete draft; HTTP never accepts output paths."""
    def __init__(self, library, output):
        self.library, self.output, self.lock = library, Path(output), threading.RLock()

    def generate(self, draft):
        with self.lock:
            report, files = self.library.assemble(draft)
            key = report['draft_sha256']
            report['downloads'] = {name: f'/role-packages/{key}/{name}' for name in (*files, 'report.json')}
            files['report.json'] = packages.json_bytes(report)
            files['checksums.json'] = packages.json_bytes({name: packages.sha(raw) for name, raw in files.items()})
            folder = self.output / key
            folder.mkdir(parents=True, exist_ok=True)
            for name, raw in files.items():
                path = folder / name
                if path.exists() and path.read_bytes() != raw:
                    raise ValueError('同一草稿的留存包内容不同')
                if not path.exists():
                    # Commit whole files; an interrupted write leaves only a reusable temporary path.
                    handle, temporary = tempfile.mkstemp(prefix='.pack-', suffix='.tmp', dir=folder)
                    try:
                        with os.fdopen(handle, 'wb') as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
            return report

    def download(self, key, name):
        if (not re.fullmatch(r'[0-9a-f]{64}', key) or name not in
                ('assets-8m.bin', 'deskbot-roles.json', 'deskbot-layout.json', 'role-draft.json', 'report.json')):
            raise ValueError('下载文件不存在')
        folder = self.output / key
        checksums = json.loads((folder / 'checksums.json').read_bytes())
        raw = (folder / name).read_bytes()
        if packages.sha(raw) != checksums.get(name):
            raise ValueError('留存下载文件摘要不同')
        return raw
