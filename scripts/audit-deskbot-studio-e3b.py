#!/usr/bin/env python3
"""Compare the isolated E3b inputs and disabled image with the E2b release."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.storage_capacity import image_facts
from protocol.studio_resource_package import json_bytes

PUBLIC=ROOT/'artifacts/deskbot-studio-e3b-20261104'
EXPECTED={'main/Kconfig.projbuild','main/boards/esp_vocat/dev_tools.cc',
          'main/boards/esp_vocat/studio_device_link.h','main/boards/esp_vocat/studio_device_link.cc'}


def audit():
    before=json.loads((ROOT/'artifacts/deskbot-studio-e2b-20261104/firmware-build-report.json').read_bytes())
    after=json.loads((PUBLIC/'firmware-build-report.json').read_bytes())
    old,new=before['source_sha256'],after['source_sha256']
    changed=[name for name in sorted(old.keys()|new.keys()) if old.get(name)!=new.get(name)]
    if set(changed)!=EXPECTED: raise ValueError('unexpected source delta: '+repr(changed))
    for name,digest in new.items():
        if hashlib.sha256((ROOT/'firmware/body'/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('current source differs: '+name)
    reference=(ROOT/'tmp/e2b-release-20261104/o/xiaozhi.bin').read_bytes()
    disabled=(ROOT/'tmp/e3b-identity-20261104/off/xiaozhi.bin').read_bytes()
    ref_facts,off_facts=image_facts(reference),image_facts(disabled)
    if len(reference)!=len(disabled): raise ValueError('disabled image size differs')
    if [(s['load_address'],s['bytes']) for s in ref_facts['segments']]!=[
            (s['load_address'],s['bytes']) for s in off_facts['segments']]:
        raise ValueError('disabled image segment layout differs')
    first=off_facts['segments'][0]['file_offset']
    if struct.unpack_from('<I',disabled,first)[0]!=0xABCD5432:
        raise ValueError('App description magic')
    # esp_app_desc_t: ELF SHA-256 starts 144 bytes into the first segment.
    elf_hash=first+144
    allowed=set(range(elf_hash,elf_hash+32))|{off_facts['checksum_offset']}|set(range(len(disabled)-32,len(disabled)))
    differences=[i for i,(a,b) in enumerate(zip(reference,disabled)) if a!=b]
    if not set(differences)<=allowed: raise ValueError('disabled executable or data changed')
    for enabled,label in ((False,'off'),(True,'on')):
        row=next(r for r in after['builds'] if r['enabled']==enabled)
        if row.get('printf_portability',{}).get('status')!='pass': raise ValueError('nano printf check required')
        for name,item in row['artifacts'].items():
            raw=(ROOT/'tmp/e3b-identity-20261104'/label/name).read_bytes()
            if len(raw)!=item['bytes'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:
                raise ValueError('saved artifact changed: '+label+'/'+name)
    return {'schema':'deskbot.studio_identity_audit/v1','edition':'E3b','result':'passed',
            'source_delta':changed,'source_files_before':len(old),'source_files_after':len(new),
            'disabled_comparison':{'reference':'E2b','bytes':len(disabled),'differing_bytes':len(differences),
                'elf_hash_range':[elf_hash,elf_hash+32],'only_elf_hash_and_image_checksums_changed':True,
                'executable_and_other_data_equal':True},
            'protected_artifacts_equal':all(all(r['protected_matches'].values()) for r in after['builds']),
            'revision':after['revision'],'printf_portability_passed':True,
            'device_written':False,'hardware_accessed':False,'hardware_acceptance':'pending'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();raw=json_bytes(audit());path=PUBLIC/'firmware-audit.json'
    if args.check:
        if path.read_bytes()!=raw: raise ValueError('audit report differs')
    else: path.write_bytes(raw)
    print('E3b firmware audit passed: four source changes; disabled code and data preserved')
