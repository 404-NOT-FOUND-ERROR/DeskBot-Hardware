#!/usr/bin/env python3
"""Prepare default/single-eye resources while preserving every E1.45 member."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol import studio_mirror_options as mirrors
from protocol import studio_resource_package as packages

WORK=ROOT/'tmp/studio-layout-manifest-e3f9a'
PUBLIC=ROOT/'artifacts/studio-layout-manifest-e3f9a'
BASE=ROOT/'tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin'


def prepare():
    original=BASE.read_bytes()
    if packages.sha(original)!='e5ac78a187866cff05a16e5fd223c934a45d6cb4bf13dfcd1766cc93db805748':
        raise ValueError('E1.45 reference changed')
    parser,packer=packages.load_legacy_tools()
    before=parser.parse_package_bytes(packages.extract_package(original,0x800000),modern=False)['members']
    index=json.loads(before['index.json'])
    catalog=packages.load_catalog();draft=deepcopy(catalog['initial']);draft['schema']=mirrors.SCHEMA
    for role in draft['roles']:role['mirror_overrides']={}
    role=next(r for r in draft['roles'] if r['id']==draft['selected_role_id'])
    # The test candidate starts from the actual installed semantic bindings.
    role['bindings']={row['name']:row['file'] for row in index['emoji_collection']}|{'listen':'listen.eaf'}
    packages.validate_draft(draft,catalog)
    records=[]
    for label,overrides in [('default',{}),('single-eye',{'winking.eaf':False})]:
        role['mirror_overrides']=overrides
        report,package,image=packages.assemble(draft,catalog,packages.load_target(),packages.BASE_IMAGE.read_bytes(),
                                             {'shy_candidate.eaf':before['shy_candidate.eaf']})
        after=parser.parse_package_bytes(package,modern=False)['members']
        if (set(after)!=set(before)|{mirrors.FILE} or any(after[n]!=raw for n,raw in before.items())
                or packer.build_legacy_package(after)!=package):
            raise ValueError('E1.45 member preservation failed')
        for name,raw in [(label+'-assets-8m.bin',image),(label+'-draft.json',packages.json_bytes(draft)),
                         (label+'-package-report.json',packages.json_bytes(report))]:
            path=WORK/name
            if path.exists() and path.read_bytes()!=raw:raise ValueError('retained candidate changed: '+name)
            path.write_bytes(raw)
        records.append(dict(label=label,image_file=(WORK/(label+'-assets-8m.bin')).relative_to(ROOT).as_posix(),
            image_sha256=packages.sha(image),image_bytes=len(image),package_bytes=len(package),
            soft_limit_free_bytes=7*1024*1024-len(package),manifest_sha256=report['layout_manifest']['sha256'],
            manifest_resources=len(report['layout_manifest']['value']['assets']),mirror_overrides=overrides,
            all_original_members_byte_equal=True))
    result=dict(schema='deskbot.layout_manifest_candidates/v1',edition='E3f-9a',result='offline_resources_ready',
                baseline='E1.45',baseline_sha256=packages.sha(original),required_firmware='E3f-9a',
                candidates=records,device_written=False,hardware_acceptance='pending')
    PUBLIC.mkdir(parents=True,exist_ok=True)
    (PUBLIC/'resource-candidates.json').write_bytes(packages.json_bytes(result))
    return result


if __name__=='__main__':
    WORK.mkdir(parents=True,exist_ok=True)
    print(json.dumps(prepare(),ensure_ascii=True))
