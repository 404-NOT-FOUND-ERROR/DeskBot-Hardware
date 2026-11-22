"""App-only PSRAM display correction on the installed two-role package."""
import json
from pathlib import Path
import re

from protocol import studio_app_deployment as app
from protocol import studio_role_query_fix_e3f9d1 as baseline
from protocol.storage_capacity import image_facts

ROOT=Path(__file__).resolve().parents[1]
EDITION,ROLLBACK_TARGET='E3f-9d.2','E3f-9d.1'
SOURCE=Path('tmp/display-psram-e3f9d2/on/xiaozhi.bin')
BUILD_REPORT=Path('artifacts/display-psram-e3f9d2/firmware-build-report.json')
RELEASE=BUILD_REPORT.with_name('release-evidence.json')
RELEASE_SHA='42be5818a1ac56a1f61db3a03d0f430ece37878a84e0e46e8b24e86e14e2cc19'
APP_SIZE=4540224
APP_SHA='8140832e609b60224da75bdba269b1bcfbe80777a80277496a3baf6bcec7889a'
APP_ELF_SHA='14f34b0f3bbf9fc66b36f2c77e624a48661c8733a6a3a283b6f720cc7bdbd81a'
ORIGINAL_SIZE,ORIGINAL_SHA=baseline.APP_SIZE,baseline.APP_SHA
IDENTITY_SHA=baseline.IDENTITY_SHA
REFERENCE=Path('tmp/e3c-live-20261112-e3f9d1-live1')
REFERENCE_SHA='67626fcfbf650959616421bfbf31c2fff7655c29495a8c33eccb98744225a15c'
STATIC_SHA='e470f0211d24d219614f54a8b3ab542191805d1d961abfc84bf16a5c11c4172a'


def load_reference(root=ROOT):
    saved=baseline.check_plan(app.load(root/REFERENCE),root=root)
    receipt=json.loads((root/REFERENCE/'install-result.json').read_bytes())
    journal=app.Journal(root/REFERENCE,saved).load()
    if (receipt.get('result')!='candidate_installed' or not journal or journal['phase']!='installed'
            or not receipt['readback']['app_byte_equal'] or not receipt['boot']['firmware_matches_candidate']):
        raise ValueError('installed_E3f9d1_receipt_required')
    end=app.START+len(saved.candidate)
    raw=saved.snapshot[:app.START]+saved.candidate+saved.snapshot[end:]
    if app.sha(raw)!=REFERENCE_SHA:
        raise ValueError('E3f9d1_reference_changed')
    return dict(snapshot=raw,identity_sha256=IDENTITY_SHA,report=dict(edition=ROLLBACK_TARGET,resources='two-role-v1'))


def check_current(raw,reference):
    old=reference['snapshot']
    if (type(raw) is not bytes or len(raw)!=app.FLASH or len(old)!=app.FLASH
            or raw[:0x9000]!=old[:0x9000] or raw[0xD000:]!=old[0xD000:]
            or app.sha(raw[app.START:app.START+ORIGINAL_SIZE])!=ORIGINAL_SHA):
        raise ValueError('installed_E3f9d1_snapshot_required')


def reviewed_candidate(raw=None,root=ROOT):
    raw=(root/SOURCE).read_bytes() if raw is None else raw
    if len(raw)!=APP_SIZE or app.sha(raw)!=APP_SHA:
        raise ValueError('reviewed_display_App_changed')
    facts=image_facts(raw);start=facts['segments'][0]['file_offset']
    if facts['configured_flash_bytes']!=app.FLASH or raw[start+144:start+176].hex()!=APP_ELF_SHA:
        raise ValueError('display_App_profile_changed')
    return raw


def check_plan(plan,*,candidate=True,root=ROOT):
    plan.validate();m=plan.metadata
    if (m['edition']!='E3c' or m['identity_sha256']!=IDENTITY_SHA
            or m['original_bytes']!=ORIGINAL_SIZE or m['candidate_bytes']!=APP_SIZE
            or m['candidate_sha256']!=APP_SHA):
        raise ValueError('display_plan_changed')
    # Recovery needs only this run's retained bytes; NVS belongs to the device.
    if (app.sha(plan.snapshot[:0x9000]+plan.snapshot[0xD000:])!=STATIC_SHA
            or app.sha(plan.snapshot[app.START:app.START+ORIGINAL_SIZE])!=ORIGINAL_SHA):
        raise ValueError('display_saved_baseline_changed')
    if candidate:
        check_current(plan.snapshot,load_reference(root));reviewed_candidate(plan.image,root)
    return plan


def prepare_plan(snapshot,identity,image,root=ROOT):
    check_current(snapshot,load_reference(root))
    return check_plan(app.prepare(snapshot,reviewed_candidate(image,root),identity,ORIGINAL_SIZE,edition='E3c'),root=root)


def check_build_evidence(root=ROOT):
    raw=(root/RELEASE).read_bytes()
    if app.sha(raw)!=RELEASE_SHA:
        raise ValueError('display_release_changed')
    release=json.loads(raw)
    if release.get('result')!='approved' or release.get('edition')!=EDITION:
        raise ValueError('approved_display_release_required')
    profile=(root/'protocol/display_psram_e3f9d2.py').read_bytes()
    normalized=re.sub(rb"RELEASE_SHA='[a-f0-9]{64}'",b"RELEASE_SHA='pending'",profile)
    if app.sha(normalized)!=release['normalized_profile_sha256']:
        raise ValueError('display_profile_changed')
    for name,digest in release['files'].items():
        if app.sha((root/name).read_bytes())!=digest:
            raise ValueError('display_release_input_changed: '+name)
    build=json.loads((root/BUILD_REPORT).read_bytes())
    if (build.get('edition')!=EDITION or build.get('result')!='build_verified'
            or build['source_delta']!=['main/boards/esp_vocat/esp_vocat.cc']
            or build['effective_config_equal'] is not True or build['internal_static_delta_bytes']!=0
            or not all(build['protected_equal'].values())
            or build['artifacts']['xiaozhi.bin']!=dict(bytes=APP_SIZE,sha256=APP_SHA)):
        raise ValueError('verified_display_build_required')
    return dict(image=reviewed_candidate(root=root),candidate=dict(variant='display-psram',bytes=APP_SIZE,
        sha256=APP_SHA,app_elf_sha256=APP_ELF_SHA),build_report_sha256=app.sha((root/BUILD_REPORT).read_bytes()),
        offline_report_sha256=release['files'][BUILD_REPORT.with_name('offline-validation.json').as_posix()],
        path_audit_sha256=release['files'][BUILD_REPORT.with_name('peer-review.json').as_posix()])
