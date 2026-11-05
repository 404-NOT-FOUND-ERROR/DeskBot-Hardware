"""Pinned USB-drain candidate, with the installed E3c-7 as exact rollback."""
import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_e3c_deployment as previous
from protocol.storage_capacity import image_facts

ROOT = Path(__file__).resolve().parents[1]
APP_SHA = '5ccae76053ddf0994b89652247d0510fbebe2864aa84d7b22707156952a315f4'
APP_SIZE = 4510480
SOURCE = Path('tmp/e3c8-usb-drain-20261105/on/xiaozhi.bin')
ORIGINAL_SHA, ORIGINAL_SIZE = previous.APP_SHA, previous.APP_SIZE
PREVIOUS_RUN = 'e3c-live-20261105-secondary'
PREVIOUS_PLAN = 'd407d709aafa200a144caf01ceea5bf9dd7b654b2a172fad98878ce2f955d84b'
EVIDENCE = {
    'install-result.json': 'a5b5183122d8c838cfc9cd98fad9b95475a2c3f17bdd353dac5b6fb15b5dc90a',
    'restart-result.json': 'c1286174a36bec40cb3af626a4e11fc445d2abe4a7dc3c24bee9f8c7f9675bbf',
}
check_current = previous.check_current


def reviewed_candidate(raw):
    if len(raw) != APP_SIZE or tx.sha(raw) != APP_SHA:
        raise ValueError('reviewed E3c-8 candidate changed')
    if image_facts(raw)['configured_flash_bytes'] != tx.FLASH:
        raise ValueError('E3c-8 image capacity differs')
    return raw


def load_reference(root=ROOT):
    folder = root / 'tmp' / PREVIOUS_RUN
    plan = previous.check_plan(tx.load(folder))
    state = tx.Journal(folder, plan).load()
    if plan.id != PREVIOUS_PLAN or state is None or state['phase'] != 'installed':
        raise ValueError('verified installed E3c-7 plan required')
    rows = {}
    for name, digest in EVIDENCE.items():
        raw = (folder / name).read_bytes()
        if tx.sha(raw) != digest:
            raise ValueError('E3c-7 deployment evidence differs: ' + name)
        rows[name] = json.loads(raw)
    install, restart = rows['install-result.json'], rows['restart-result.json']
    if (install['result'] != 'candidate_installed' or restart['result'] != 'restart_verified'
            or not all(install['readback'][k] for k in ('app_byte_equal', 'static_outside_app_equal', 'nvs_equal'))
            or not restart['boot_id_changed'] or not restart['device_id_stable']):
        raise ValueError('verified E3c-7 readback and restart required')
    end = tx.START + len(plan.rollback)
    snapshot = plan.snapshot[:tx.START] + plan.candidate + plan.snapshot[end:]
    return {'identity_sha256': plan.metadata['identity_sha256'], 'snapshot': snapshot,
        'report': {'path': (Path('tmp') / PREVIOUS_RUN / 'install-result.json').as_posix(),
                   'sha256': EVIDENCE['install-result.json']},
        'original_bytes': ORIGINAL_SIZE, 'original_sha256': ORIGINAL_SHA}


def check_plan(plan, *, candidate=True):
    plan.validate()
    if (plan.metadata['edition'] != 'E3c' or plan.metadata['original_bytes'] != ORIGINAL_SIZE
            or tx.sha(plan.snapshot[tx.START:tx.START + ORIGINAL_SIZE]) != ORIGINAL_SHA):
        raise ValueError('E3c-8 plan must retain the installed E3c-7 App')
    if candidate:
        if not isinstance(plan, tx.Plan):
            raise ValueError('complete candidate plan required')
        reviewed_candidate(plan.image)
    return plan
