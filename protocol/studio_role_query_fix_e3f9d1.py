"""App-only correction on the installed E3f-9d two-role package."""
import json
from pathlib import Path
import re

from protocol import studio_app_deployment as app
from protocol import studio_role_release_e3f9d as baseline
from protocol.storage_capacity import image_facts

ROOT=Path(__file__).resolve().parents[1]
EDITION,ROLLBACK_TARGET='E3f-9d.1','E3f-9d'
SOURCE=Path('tmp/studio-role-query-fix-e3f9d1/xiaozhi.bin')
BUILD_REPORT=Path('artifacts/studio-role-query-fix-e3f9d1/firmware-build-report.json')
RELEASE=BUILD_REPORT.with_name('release-evidence.json')
RELEASE_SHA='428d72fcd4b571cce97279c0387c77004551e7d8caa0bf262793cc1f146dc85a'
APP_SIZE=4540224
APP_SHA='606bc0f36061b861cf7d032cc6d15debc12c2e6aabc0dd495cdb70e9480790c6'
APP_ELF_SHA='f5d0757e1f049e5a50b3c1d0e6bc292739975d12a41c257c15b9325783340970'
ORIGINAL_SIZE,ORIGINAL_SHA=baseline.APP_SIZE,baseline.APP_SHA
IDENTITY_SHA=baseline.IDENTITY_SHA
REFERENCE=Path('tmp/e3c-live-20261112-e3f9d-live1-resources')
REFERENCE_SHA='ec7476ce5059eb0ee2e6730ccabcf13b9f521e2acfe3f131bb6360c9ab38700f'
STATIC_SHA='29883d0b4d64c5a819a0ebc294cb56cdc5c48a24eda9712f4e0d525e3e3fe179'


def load_reference(root=ROOT):
    saved=baseline.load_resources(root/REFERENCE,recovery=True)
    image=(root/REFERENCE/'candidate-assets-8m.bin').read_bytes()
    if app.sha(image)!=baseline.RESOURCE_SHA:raise ValueError('installed_role_resources_changed')
    start=0x1000000
    raw=saved.snapshot[:start]+image+saved.snapshot[start+len(image):]
    if app.sha(raw)!=REFERENCE_SHA:raise ValueError('installed_role_reference_changed')
    receipt=json.loads((root/REFERENCE/'install-result.json').read_bytes())
    state=json.loads((root/REFERENCE/'resource-state.json').read_bytes())
    if receipt.get('result')!='candidate_resources_installed' or receipt.get('readback_sha256')!=REFERENCE_SHA:
        raise ValueError('installed_role_receipt_required')
    if state.get('phase')!='installed' or state.get('readback_sha256')!=REFERENCE_SHA:
        raise ValueError('installed_role_state_required')
    return dict(snapshot=raw,identity_sha256=IDENTITY_SHA,report=dict(edition=ROLLBACK_TARGET,resources='two-role-v1'))


def check_current(raw,reference):
    old=reference['snapshot']
    if (type(raw) is not bytes or len(raw)!=app.FLASH or len(old)!=app.FLASH
            or raw[:0x9000]!=old[:0x9000] or raw[0xD000:]!=old[0xD000:]
            or app.sha(raw[app.START:app.START+ORIGINAL_SIZE])!=ORIGINAL_SHA):
        raise ValueError('installed_E3f9d_two_role_snapshot_required')


def reviewed_candidate(raw=None,root=ROOT):
    raw=(root/SOURCE).read_bytes() if raw is None else raw
    if len(raw)!=APP_SIZE or app.sha(raw)!=APP_SHA:raise ValueError('reviewed_role_fix_App_changed')
    facts=image_facts(raw);start=facts['segments'][0]['file_offset']
    if facts['configured_flash_bytes']!=app.FLASH or raw[start+144:start+176].hex()!=APP_ELF_SHA:
        raise ValueError('role_fix_App_profile_changed')
    return raw


def check_plan(plan,*,candidate=True,root=ROOT):
    plan.validate();m=plan.metadata
    if (m['edition']!='E3c' or m['identity_sha256']!=IDENTITY_SHA
            or m['original_bytes']!=ORIGINAL_SIZE or m['candidate_bytes']!=APP_SIZE
            or m['candidate_sha256']!=APP_SHA):raise ValueError('role_fix_plan_changed')
    # Recovery is self contained: only this run's saved bytes and pinned
    # baseline are needed; build outputs and earlier candidates may be absent.
    if (app.sha(plan.snapshot[:0x9000]+plan.snapshot[0xD000:])!=STATIC_SHA
            or app.sha(plan.snapshot[app.START:app.START+ORIGINAL_SIZE])!=ORIGINAL_SHA):
        raise ValueError('role_fix_saved_baseline_changed')
    if candidate:
        check_current(plan.snapshot,load_reference(root));reviewed_candidate(plan.image,root)
    return plan


def prepare_plan(snapshot,identity,image,root=ROOT):
    check_current(snapshot,load_reference(root))
    return check_plan(app.prepare(snapshot,reviewed_candidate(image,root),identity,ORIGINAL_SIZE,edition='E3c'),root=root)


def check_build_evidence(root=ROOT):
    raw=(root/RELEASE).read_bytes()
    if app.sha(raw)!=RELEASE_SHA:raise ValueError('role_fix_release_changed')
    release=json.loads(raw)
    if release.get('result')!='approved' or release.get('edition')!=EDITION:raise ValueError('approved_role_fix_required')
    profile=(root/'protocol/studio_role_query_fix_e3f9d1.py').read_bytes()
    normalized=re.sub(rb"RELEASE_SHA='[a-f0-9]{64}'",b"RELEASE_SHA='pending'",profile)
    if app.sha(normalized)!=release['normalized_profile_sha256']:raise ValueError('role_fix_profile_changed')
    for name,digest in release['files'].items():
        if app.sha((root/name).read_bytes())!=digest:raise ValueError('role_fix_input_changed: '+name)
    build=json.loads((root/BUILD_REPORT).read_bytes())
    if (build.get('edition')!=EDITION or build.get('result')!='off_on_builds_verified'
            or build['disabled_comparison']['executable_and_data_equal'] is not True
            or not all(row['printf_portability']['status']=='pass' for row in build['builds'])
            or build['builds'][1]['artifacts']['xiaozhi.bin']!=dict(bytes=APP_SIZE,sha256=APP_SHA)):
        raise ValueError('paired_nano_safe_build_required')
    return dict(image=reviewed_candidate(root=root),candidate=dict(variant='role-query-fix',bytes=APP_SIZE,
        sha256=APP_SHA,app_elf_sha256=APP_ELF_SHA),build_report_sha256=app.sha((root/BUILD_REPORT).read_bytes()),
        offline_report_sha256=release['files'][BUILD_REPORT.with_name('offline-validation.json').as_posix()],
        path_audit_sha256=release['files'][BUILD_REPORT.with_name('peer-review.json').as_posix()])
